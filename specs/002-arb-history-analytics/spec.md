# Feature Specification: Persistent Arb History + Analytics

**Feature**: `002-arb-history-analytics` | **Date**: 2026-02-24 | **Status**: Draft
**Depends on**: `001-arb-scanner-core` (complete)

## Problem Statement

The scanner currently operates in a "snapshot" mode — each scan cycle detects opportunities, persists them, and moves on. But there's no way to answer questions like "which market pairs are consistently mispriced?", "is this spread widening or narrowing?", or "how has scanner performance changed over time?". The `markets` table is a mutable upsert that destroys price history. The `report` command only fetches the last N rows with no date filtering. `scan_logs` has a write path but no read path.

Operators need historical context to make informed trading decisions and to tune scanner parameters.

## User Stories

### US1: Spread History (P1)
**As a** market operator, **I want to** see how the arbitrage spread for a specific market pair has changed over time, **so that** I can distinguish persistent mispricings from transient blips.

**Acceptance criteria:**
- Query spread history for a specific `(poly_event_id, kalshi_event_id)` pair
- Results include `detected_at`, `net_spread_pct`, `annualized_return`, `depth_risk`, `max_size`
- Supports `--hours` flag to limit time window (default: 24h)
- CLI output as table or JSON

### US2: Aggregate Statistics (P1)
**As a** market operator, **I want to** see aggregated statistics across all detected opportunities, **so that** I can identify which pairs are most frequently mispriced and which have the best peak spreads.

**Acceptance criteria:**
- Per-pair summary: peak spread, min spread, avg spread, detection count, first seen, last seen
- Time-bucketed aggregation (hourly) with avg/max spread and detection count
- Supports `--hours` flag for time window
- Sorted by peak spread descending by default

### US3: Scanner Health Dashboard (P1)
**As a** system operator, **I want to** view scanner performance metrics over time, **so that** I can identify degradation, tune parameters, and monitor LLM costs.

**Acceptance criteria:**
- Scan cycle metrics: avg duration, total LLM evaluations, total opportunities found, error count
- Time-bucketed (hourly) with throughput trends
- Read path for `scan_logs` table (currently write-only)
- CLI command with `--hours` flag

### US4: Market Price Snapshots (P2)
**As a** market analyst, **I want** market prices to be recorded over time (not just overwritten), **so that** I can analyze price movements independently of arb detection.

**Acceptance criteria:**
- New `market_price_snapshots` table (append-only, not replacing existing `markets` upsert)
- Snapshot recorded alongside every `upsert_market` call during scan
- Queryable by `(venue, event_id)` with time range
- Not required for US1-US3 (those use `arb_opportunities` which is already append-only)

### US5: Date-Range Filtering on Existing Commands (P1)
**As a** market operator, **I want to** filter the existing `report` and `match-audit` commands by date range, **so that** I can look at specific time windows instead of just "last N rows".

**Acceptance criteria:**
- `report --since 2026-02-20 --until 2026-02-24` filters by `detected_at`
- `match-audit --since ...` filters by match timestamp
- `--since` and `--until` are optional; `--last N` still works as before
- ISO 8601 date/datetime format accepted

## Functional Requirements

### FR-001: Spread History Query
The system MUST provide a repository method and SQL query to fetch arb opportunity history for a specific `(poly_event_id, kalshi_event_id)` pair, ordered by `detected_at DESC`, with optional time-window filtering.

### FR-002: Pair Summary Aggregation
The system MUST provide a repository method that returns per-pair aggregated statistics: `peak_spread`, `min_spread`, `avg_spread`, `total_detections`, `first_seen`, `last_seen`, sorted by `peak_spread DESC`.

### FR-003: Hourly Bucketed Spread Aggregation
The system MUST provide a repository method that buckets arb opportunities by hour using `date_trunc('hour', detected_at)`, returning `avg_spread`, `max_spread`, and `detection_count` per bucket per pair, filtered by time window.

### FR-004: Scan Log Read Path
The system MUST provide repository methods to:
- Fetch recent scan logs with pagination
- Aggregate scan logs by hour: avg duration, total LLM evaluations, total opportunities found, total errors, scan count

### FR-005: Market Price Snapshot Table
The system MUST create a new `market_price_snapshots` table via migration (008) that records append-only price snapshots with `(venue, event_id, yes_bid, yes_ask, no_bid, no_ask, volume_24h, snapshotted_at)`. Indexed on `(venue, event_id, snapshotted_at DESC)`.

### FR-006: Snapshot Recording
The system MUST record a market price snapshot every time `upsert_market` is called during a scan cycle. This is an additive call alongside the existing upsert, not a replacement.

### FR-007: CLI `history` Command
The system MUST provide `arb-scanner history --pair POLY_ID/KALSHI_ID --hours 24 --format table|json` that displays spread history for a specific pair.

### FR-008: CLI `stats` Command
The system MUST provide `arb-scanner stats --hours 24 --format table|json` that displays:
- Per-pair summary table (top N by peak spread)
- Scanner health metrics (scan count, avg duration, LLM calls, error rate)

### FR-009: Date-Range Filtering
The system MUST extend `report` and `match-audit` commands with `--since` and `--until` options accepting ISO 8601 dates. These filters are additive to existing `--last` behavior.

### FR-010: Analytics Pydantic Models
The system MUST define Pydantic models for all analytics results:
- `SpreadSnapshot` (single data point in time series)
- `PairSummary` (aggregated stats for one pair)
- `HourlyBucket` (time-bucketed aggregation row)
- `ScanHealthSummary` (aggregated scan metrics)

## Non-Functional Requirements

### NFR-001: Query Performance
All analytics queries MUST complete within 500ms for up to 100,000 rows in `arb_opportunities` and 10,000 rows in `scan_logs`. This is achieved via existing indexes plus the new snapshot index.

### NFR-002: No Schema Breakage
All changes MUST be additive — new tables, new columns with defaults, new queries. No existing table schemas or queries are modified.

### NFR-003: Backward Compatibility
Existing `scan`, `watch`, `report`, `match-audit`, `migrate` commands MUST continue to work identically when new flags are not used.

## Success Criteria

- SC-001: `arb-scanner history --pair X/Y --hours 24` returns time-series data for a known pair
- SC-002: `arb-scanner stats --hours 24` returns per-pair summary and scanner health
- SC-003: `arb-scanner report --since 2026-02-20` returns date-filtered results
- SC-004: All new queries perform within 500ms on test dataset
- SC-005: All new code passes existing quality gates (ruff, mypy --strict, 70% coverage)
- SC-006: Market price snapshots are recorded during every scan cycle
- SC-007: Existing tests continue to pass without modification

## Out of Scope

- Real-time trend alerting (e.g., "spread widening" notifications) — future feature
- Charting / visualization / web UI — CLI tables and JSON only
- Data export to CSV/Parquet — JSON output is sufficient for now
- Retention policies / data pruning — future concern at scale
