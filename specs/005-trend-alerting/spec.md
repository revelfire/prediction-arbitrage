# Feature Specification: Trend Alerting

**Feature**: `005-trend-alerting` | **Date**: 2026-02-24 | **Status**: Draft
**Depends on**: `002-arb-history-analytics` (complete), `004-live-api-testing` (complete)

## Problem Statement

The watch loop fires a webhook only when a *new* arb opportunity is first detected. Once alerted, the operator has no visibility into how that spread is evolving — is it converging (closing, act now), diverging (growing, more profitable), or gone stale? The operator also gets no warning when scanner health degrades (e.g., API failures, zero opportunities across many scans). All trend analysis requires manually running `history` and `stats` CLI commands.

## Solution

Add a trend detection engine that runs inside the watch loop after each scan. It compares the current scan's results against a rolling window of recent scans to detect spread convergence, divergence, new highs, opportunity disappearance, and scanner health anomalies. Alerts dispatch through the existing Slack/Discord webhook infrastructure with distinct payload formats per alert type.

## User Stories

### US1: Spread Convergence Alert (P1)
**As a** market operator, **I want** to be alerted when a tracked spread is shrinking toward zero, **so that** I can execute before the arbitrage window closes.

### US2: Spread Divergence Alert (P1)
**As a** market operator, **I want** to be alerted when a spread widens significantly from its recent average, **so that** I can evaluate a larger position.

### US3: Opportunity Disappeared Alert (P2)
**As a** market operator, **I want** to know when a previously profitable pair drops below the min spread threshold, **so that** I can close or avoid stale positions.

### US4: New High Alert (P2)
**As a** market operator, **I want** to be alerted when a pair hits a new all-time high spread, **so that** I can prioritize the most profitable opportunities.

### US5: Scanner Health Alert (P2)
**As a** system operator, **I want** to be alerted when the scanner has consecutive failures or zero opportunities over an extended period, **so that** I can investigate API issues or config problems.

## Functional Requirements

### FR-001: Trend Detection Engine
The system MUST implement a `TrendDetector` class that accepts the current scan results and a rolling window of recent results. It MUST compute per-pair spread deltas and classify trends as converging, diverging, stable, new_high, or disappeared.

### FR-002: Rolling Window State
The `TrendDetector` MUST maintain an in-memory rolling window of the last N scan results (configurable, default 10 scans). The window is populated during the watch loop — no DB queries required for trend detection. On watch loop start, the window is empty and fills over time.

### FR-003: Convergence Detection
The system MUST detect convergence when a pair's spread decreases by more than `convergence_threshold_pct` (default 25%) relative to its rolling average over the window.

### FR-004: Divergence Detection
The system MUST detect divergence when a pair's spread increases by more than `divergence_threshold_pct` (default 50%) relative to its rolling average over the window.

### FR-005: Disappeared Opportunity Detection
The system MUST detect when a pair that was present in >=3 of the last N scans is absent from the current scan (no longer above min spread).

### FR-006: New High Detection
The system MUST detect when a pair's current spread exceeds the maximum spread seen in the rolling window.

### FR-007: Scanner Health Alerts
The system MUST detect health anomalies: (a) consecutive scan failures >= `max_consecutive_failures` (default 3), (b) zero opportunities for >= `zero_opp_alert_scans` (default 5) consecutive scans.

### FR-008: Alert Cooldown
Each alert type + pair combination MUST have a cooldown period (default 15 minutes) to prevent alert fatigue. No duplicate alerts within the cooldown window.

### FR-009: Alert Dispatch
Alerts MUST dispatch through the existing `dispatch_webhook()` infrastructure with Slack Block Kit and Discord embed payloads. Each alert type MUST have a distinct emoji/color to differentiate from standard opportunity alerts.

### FR-010: Alert Config
The system MUST add `TrendAlertConfig` to settings with fields: `enabled` (bool, default true), `window_size` (int, default 10), `convergence_threshold_pct` (float, default 0.25), `divergence_threshold_pct` (float, default 0.50), `cooldown_minutes` (int, default 15), `max_consecutive_failures` (int, default 3), `zero_opp_alert_scans` (int, default 5).

### FR-011: Alert Persistence
Alerts MUST be persisted to a `trend_alerts` table for audit trail. Fields: `id`, `alert_type`, `poly_event_id`, `kalshi_event_id`, `spread_before`, `spread_after`, `message`, `dispatched_at`.

### FR-012: CLI Command
The system MUST add an `alerts` CLI command that lists recent trend alerts from the DB, with `--last N` (default 20) and `--type` (filter by alert type) options.

## Success Criteria

- SC-001: Watch loop with trend alerting enabled detects convergence/divergence in synthetic test data
- SC-002: Alert cooldown prevents duplicate alerts within the configured window
- SC-003: Slack/Discord payloads render correctly with distinct formatting per alert type
- SC-004: Scanner health alerts fire after configured consecutive failures
- SC-005: All existing 323 mocked tests still pass
- SC-006: `uv run pytest` default run skips live tests, trend tests use mocked data
- SC-007: All quality gates pass (ruff, mypy --strict, 70% coverage)

## Out of Scope

- WhatsApp, email, SMS, or Telegram notifications
- ML-based trend prediction or forecasting
- Automated position management based on trends
- Historical trend backfilling from existing DB data on startup
