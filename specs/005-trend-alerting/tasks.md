# Tasks: Trend Alerting

**Input**: `/specs/005-trend-alerting/spec.md`, `/specs/005-trend-alerting/plan.md`
**Depends on**: `004-live-api-testing` (complete)

## Autonomous Execution Notes

- Fix bugs as you find them
- Greenfield pre-1.0 — modify existing code directly
- All 323 existing mocked tests MUST continue to pass
- Trend tests use mocked data, no live API calls

---

## Phase 1: Models + Config + Migration

- [x] T001 [P] Add `AlertType` enum and `TrendAlert` model to `src/arb_scanner/models/analytics.py`. AlertType: `convergence`, `divergence`, `new_high`, `disappeared`, `health_consecutive_failures`, `health_zero_opps`. TrendAlert fields: `alert_type` (AlertType), `poly_event_id` (str | None), `kalshi_event_id` (str | None), `spread_before` (Decimal | None), `spread_after` (Decimal | None), `message` (str), `dispatched_at` (datetime). Re-export from `models/__init__.py`.
- [x] T002 [P] Add `TrendAlertConfig` to `src/arb_scanner/models/config.py`: fields `enabled` (bool, default True), `window_size` (int, default 10), `convergence_threshold_pct` (float, default 0.25), `divergence_threshold_pct` (float, default 0.50), `cooldown_minutes` (int, default 15), `max_consecutive_failures` (int, default 3), `zero_opp_alert_scans` (int, default 5). Add `trend_alerts: TrendAlertConfig` to `Settings` with default.
- [x] T003 [P] Create `src/arb_scanner/storage/migrations/010_create_trend_alerts.sql`: CREATE TABLE `trend_alerts` with `id` BIGSERIAL PK, `alert_type` TEXT NOT NULL, `poly_event_id` TEXT, `kalshi_event_id` TEXT, `spread_before` NUMERIC(10,6), `spread_after` NUMERIC(10,6), `message` TEXT NOT NULL, `dispatched_at` TIMESTAMPTZ NOT NULL DEFAULT NOW(). Add indexes on `(dispatched_at DESC)` and `(alert_type, dispatched_at DESC)`.
- [x] T004 [P] Extend `config.example.yaml` with `trend_alerts` section: enabled, window_size, convergence_threshold_pct, divergence_threshold_pct, cooldown_minutes, max_consecutive_failures, zero_opp_alert_scans.

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 2: Trend Detection Engine

- [x] T005 Create `src/arb_scanner/notifications/trend_detector.py` with `TrendDetector` class:
  - Constructor takes `TrendAlertConfig`
  - Internal state: `_window: deque[dict[str, Decimal]]` (maxlen=window_size), `_cooldowns: dict[tuple[str, str], datetime]`, `_consecutive_failures: int`, `_consecutive_zero_opps: int`
  - Public method `ingest(scan_result: dict[str, Any]) -> list[TrendAlert]`: extracts `_raw_opps`, calls `_update_window()`, runs all 5 detectors, applies cooldown, returns filtered alerts
  - `_update_window(opps)`: builds `{pair_key: net_spread_pct}` dict, appends to deque
  - `_pair_key(opp)`: returns `f"{poly_event_id}/{kalshi_event_id}"`
  - `_rolling_avg(pair_key)`: computes mean spread across window entries where pair is present
  - `_rolling_max(pair_key)`: computes max spread across window entries
  - Helper `_pairs_in_window(min_count: int)`: returns set of pair_keys seen in >= min_count scans
- [x] T006 Add convergence detection to TrendDetector: `_detect_convergence() -> list[TrendAlert]`. For each pair in current scan, if rolling_avg exists and current_spread < rolling_avg * (1 - threshold), emit convergence alert with spread_before=rolling_avg, spread_after=current_spread.
- [x] T007 Add divergence detection: `_detect_divergence() -> list[TrendAlert]`. If current_spread > rolling_avg * (1 + threshold), emit divergence alert.
- [x] T008 Add new high detection: `_detect_new_highs() -> list[TrendAlert]`. If current_spread > rolling_max for that pair, emit new_high alert.
- [x] T009 Add disappeared detection: `_detect_disappeared() -> list[TrendAlert]`. For pairs in >=3 of last N scans but absent from current scan, emit disappeared alert.
- [x] T010 Add health detection: `_detect_health(scan_result) -> list[TrendAlert]`. Track consecutive failures (scan has errors and zero opps) and consecutive zero-opp scans. Fire health alert when thresholds exceeded. Reset counters on successful scan with opps.
- [x] T011 Add cooldown filtering: `_apply_cooldown(alerts) -> list[TrendAlert]`. Check `(alert_type.value, pair_key)` against `_cooldowns` dict. Filter out alerts within cooldown_minutes of last fire. Update cooldown timestamps for passing alerts.
- [x] T012 [P] Create `tests/unit/test_trend_detector.py` (~15 tests):
  - Test empty window returns no alerts
  - Test window fills correctly with deque maxlen
  - Test convergence detected when spread drops 25%+ from avg
  - Test convergence NOT detected when spread drops <25%
  - Test divergence detected when spread rises 50%+ from avg
  - Test divergence NOT detected when spread rises <50%
  - Test new_high detected when spread exceeds window max
  - Test new_high NOT detected when spread equals window max
  - Test disappeared when pair in 3+ scans then absent
  - Test disappeared NOT fired when pair in <3 scans
  - Test health alert after consecutive failures
  - Test health alert after consecutive zero-opp scans
  - Test health counters reset on good scan
  - Test cooldown blocks duplicate alerts within window
  - Test cooldown allows alert after cooldown expires

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 3: Alert Webhooks + Dispatch

- [x] T013 Create `src/arb_scanner/notifications/alert_webhook.py`:
  - `build_trend_slack_payload(alert: TrendAlert) -> dict[str, Any]`: Slack Block Kit with emoji per AlertType (convergence=chart_with_downwards_trend, divergence=chart_with_upwards_trend, new_high=trophy, disappeared=ghost, health=warning). Header shows alert type, fields show pair, spread before/after, message.
  - `build_trend_discord_payload(alert: TrendAlert) -> dict[str, Any]`: Discord embed with color per AlertType (convergence=Yellow 16776960, divergence=Green 3066993, new_high=Gold 15844367, disappeared=Gray 9807270, health=Red 15158332).
  - `dispatch_trend_alert(alert, *, slack_url, discord_url, client)`: async fire-and-forget, same pattern as existing `dispatch_webhook()`.
- [x] T014 [P] Create `tests/unit/test_alert_webhook.py` (~6 tests):
  - Test Slack payload structure for each alert type (convergence, divergence, health)
  - Test Discord payload has correct color per alert type
  - Test dispatch calls _post_webhook for each configured URL
  - Test dispatch with no URLs configured does nothing

**Quality gate**: All 5 gates.

---

## Phase 4: Watch Loop Integration + DB Persistence

- [x] T015 Extend `src/arb_scanner/storage/_analytics_queries.py`: add `INSERT_TREND_ALERT` query (insert into trend_alerts) and `GET_RECENT_ALERTS` query (select from trend_alerts with optional alert_type filter, ordered by dispatched_at DESC, limit N).
- [x] T016 Extend `src/arb_scanner/storage/analytics_repository.py`: add `insert_trend_alert(alert: TrendAlert) -> None` and `get_recent_alerts(limit: int, alert_type: str | None) -> list[TrendAlert]` methods.
- [x] T017 Modify `src/arb_scanner/cli/watch.py`: create `TrendDetector` at watch start (when `config.trend_alerts.enabled`). After each scan, call `detector.ingest(result)`, dispatch alerts via `dispatch_trend_alert()`, persist via `insert_trend_alert()` (fire-and-forget). Log alert counts per cycle.
- [x] T018 [P] Create `tests/integration/test_trend_pipeline.py` (~8 tests):
  - Test watch loop with TrendDetector wired in (mock scan results)
  - Test detector + webhook dispatch integration (mock httpx)
  - Test alert persistence (mock DB)
  - Test trend alerting disabled when config.trend_alerts.enabled=False
  - Test no alerts during cold start (window filling)
  - Test multiple alert types fire in same cycle
  - Test cooldown prevents re-dispatch across cycles
  - Test health alert fires after N consecutive failures

**Quality gate**: All 5 gates.

---

## Phase 5: CLI + Polish

- [x] T019 Create `src/arb_scanner/cli/alert_commands.py` with `alerts` command: `--last N` (default 20), `--type` (optional filter by alert_type), `--format table|json`. Reads from DB via `get_recent_alerts()`. Register in `app.py`.
- [x] T020 Extend `src/arb_scanner/notifications/reporter.py`: add `format_alerts_table(alerts: list[TrendAlert]) -> str` for Markdown table rendering of alerts (type, pair, spread change, message, time).
- [x] T021 Run full quality gate suite. Fix any failures. Verify coverage >=70%.
- [x] T022 Update CLAUDE.md: add trend alerting section, note TrendAlertConfig, document `alerts` command.

**Quality gate**: All 5 gates green. Final verification.

---

## Total: 22 tasks across 5 phases
