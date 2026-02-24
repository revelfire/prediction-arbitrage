# Implementation Plan: Trend Alerting

**Branch**: `005-trend-alerting` | **Date**: 2026-02-24 | **Spec**: [spec.md](spec.md)

## Summary

Add a trend detection engine to the watch loop. After each scan, compare current results against a rolling window of recent scans to detect spread convergence, divergence, new highs, disappearances, and scanner health anomalies. Dispatch alerts via the existing Slack/Discord webhook infrastructure.

## Technical Context

**New Dependencies**: None (uses existing httpx, pydantic, structlog, asyncpg)
**New Modules**: `notifications/trend_detector.py`, `notifications/alert_webhook.py`
**New Models**: `TrendAlert`, `TrendAlertConfig`, `AlertType` enum
**New Table**: `trend_alerts` (migration 010)
**New CLI**: `arb-scanner alerts` command

## Constitution Check

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Human-in-the-Loop | PASS | Alerts inform, never act |
| II. Pydantic at Every Boundary | PASS | TrendAlert model, TrendAlertConfig |
| III. Async-First I/O | PASS | Webhook dispatch + DB persistence async |
| IV. Structured Logging | PASS | structlog for all trend events |
| V. Two-Pass Matching | PASS | Unchanged — trend layer sits after matching |
| VI. Configuration Over Code | PASS | All thresholds in config.yaml |

## Project Structure (new/modified files)

```text
src/arb_scanner/
├── models/
│   ├── analytics.py         # EXTEND: add TrendAlert model, AlertType enum
│   └── config.py            # EXTEND: add TrendAlertConfig, add to Settings
├── notifications/
│   ├── trend_detector.py    # NEW: TrendDetector class with rolling window
│   └── alert_webhook.py     # NEW: Slack/Discord payload builders for alerts
├── cli/
│   ├── watch.py             # MODIFY: wire TrendDetector into watch loop
│   └── alert_commands.py    # NEW: `alerts` CLI command
│   └── app.py               # MODIFY: register alerts command
├── storage/
│   ├── _analytics_queries.py  # EXTEND: add INSERT_TREND_ALERT, GET_RECENT_ALERTS
│   ├── analytics_repository.py # EXTEND: add insert_trend_alert(), get_recent_alerts()
│   └── migrations/
│       └── 010_create_trend_alerts.sql  # NEW

config.example.yaml          # EXTEND: add trend_alerts section

tests/
├── unit/
│   ├── test_trend_detector.py    # NEW: ~15 tests
│   └── test_alert_webhook.py     # NEW: ~6 tests
├── integration/
│   └── test_trend_pipeline.py    # NEW: ~8 tests
```

## Key Technical Decisions

### 1. In-Memory Rolling Window (not DB queries)

The `TrendDetector` holds a `collections.deque(maxlen=window_size)` of `ScanSnapshot` dataclasses — lightweight records of `{pair_key: spread_pct}` per scan. No DB queries for trend computation. The window fills from scratch each time `watch` starts; no warm-up from historical data (keeps it simple, avoids stale data edge cases).

### 2. TrendDetector Is Stateful, Watch Loop Owns It

Create `TrendDetector` once at watch start. Feed it each scan's results via `detector.ingest(scan_result)`. It returns `list[TrendAlert]` to dispatch. The detector manages:
- Rolling window (deque of per-scan spread maps)
- Cooldown tracker (dict of `(alert_type, pair_key) -> last_fired_at`)
- Consecutive failure counter (for health alerts)

### 3. Pair Key Encoding

Pairs are keyed as `f"{poly_event_id}/{kalshi_event_id}"` — same format as the `--pair` flag on the existing `history` command.

### 4. Alert Types as Enum

```python
class AlertType(str, Enum):
    CONVERGENCE = "convergence"
    DIVERGENCE = "divergence"
    NEW_HIGH = "new_high"
    DISAPPEARED = "disappeared"
    HEALTH_CONSECUTIVE_FAILURES = "health_consecutive_failures"
    HEALTH_ZERO_OPPS = "health_zero_opps"
```

### 5. Threshold Computation

- **Convergence**: current spread < rolling_avg * (1 - convergence_threshold_pct). Default 25% drop from avg.
- **Divergence**: current spread > rolling_avg * (1 + divergence_threshold_pct). Default 50% rise from avg.
- **New High**: current spread > max(window spreads) for that pair.
- **Disappeared**: pair was in >=3 of the last N scans but absent in current scan.
- **Health**: consecutive_failures >= max_consecutive_failures, or consecutive_zero_opps >= zero_opp_alert_scans.

### 6. Webhook Payloads per Alert Type

Each alert type gets a distinct emoji and color in the Slack/Discord payload:

| Alert Type | Slack Emoji | Discord Color | Header |
|-----------|-------------|---------------|--------|
| convergence | :chart_with_downwards_trend: | Yellow (16776960) | Spread Converging |
| divergence | :chart_with_upwards_trend: | Green (3066993) | Spread Diverging |
| new_high | :trophy: | Gold (15844367) | New High Spread |
| disappeared | :ghost: | Gray (9807270) | Opportunity Disappeared |
| health_* | :warning: | Red (15158332) | Scanner Health Alert |

### 7. Alert Cooldown Implementation

Dict of `(AlertType, pair_key) -> datetime` tracking last fire time. Before dispatching, check `now - last_fired >= cooldown_minutes`. Health alerts use `pair_key=""` (global, not per-pair).

## Data Flow

```
Watch Loop (each cycle)
  │
  ├─ run_scan() → result dict with _raw_opps
  │
  ├─ detector.ingest(result) → list[TrendAlert]
  │   ├─ _update_window(result)
  │   ├─ _detect_convergence()
  │   ├─ _detect_divergence()
  │   ├─ _detect_new_highs()
  │   ├─ _detect_disappeared()
  │   ├─ _detect_health_anomalies(result)
  │   └─ _apply_cooldown(alerts) → filtered alerts
  │
  ├─ dispatch_trend_alerts(alerts, notif_config)
  │   ├─ build_trend_slack_payload(alert)
  │   └─ build_trend_discord_payload(alert)
  │
  └─ persist_trend_alerts(alerts, config)  # fire-and-forget
```

## SQL Design

### Migration 010: Create trend_alerts table

```sql
CREATE TABLE IF NOT EXISTS trend_alerts (
    id            BIGSERIAL PRIMARY KEY,
    alert_type    TEXT NOT NULL,
    poly_event_id TEXT,
    kalshi_event_id TEXT,
    spread_before NUMERIC(10,6),
    spread_after  NUMERIC(10,6),
    message       TEXT NOT NULL,
    dispatched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trend_alerts_dispatched
    ON trend_alerts (dispatched_at DESC);

CREATE INDEX IF NOT EXISTS idx_trend_alerts_type
    ON trend_alerts (alert_type, dispatched_at DESC);
```

## TrendDetector API

```python
class TrendDetector:
    def __init__(self, config: TrendAlertConfig) -> None: ...
    def ingest(self, scan_result: dict[str, Any]) -> list[TrendAlert]: ...
    # Internal:
    def _update_window(self, opps: list[ArbOpportunity]) -> None: ...
    def _detect_convergence(self) -> list[TrendAlert]: ...
    def _detect_divergence(self) -> list[TrendAlert]: ...
    def _detect_new_highs(self) -> list[TrendAlert]: ...
    def _detect_disappeared(self) -> list[TrendAlert]: ...
    def _detect_health(self, scan_result: dict[str, Any]) -> list[TrendAlert]: ...
    def _apply_cooldown(self, alerts: list[TrendAlert]) -> list[TrendAlert]: ...
```

## Config YAML Addition

```yaml
trend_alerts:
  enabled: true
  window_size: 10
  convergence_threshold_pct: 0.25
  divergence_threshold_pct: 0.50
  cooldown_minutes: 15
  max_consecutive_failures: 3
  zero_opp_alert_scans: 5
```
