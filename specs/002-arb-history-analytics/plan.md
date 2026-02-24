# Implementation Plan: Persistent Arb History + Analytics

**Branch**: `002-arb-history-analytics` | **Date**: 2026-02-24 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-arb-history-analytics/spec.md`
**Depends on**: `001-arb-scanner-core` (merged)

## Summary

Add historical analytics and time-series querying to the existing arb scanner. Builds entirely on the existing append-only `arb_opportunities` and `scan_logs` tables. Introduces a new `market_price_snapshots` table for per-market price tracking. Adds two new CLI commands (`history`, `stats`) and date-range filtering on existing commands. All changes are additive — no existing schemas, queries, or tests are modified.

## Technical Context

**Language/Version**: Python 3.11+ (same as 001)
**New Dependencies**: None — all work uses existing asyncpg, pydantic, typer, structlog
**Storage**: PostgreSQL 15+ (extends existing schema with migration 008)
**Testing**: pytest + pytest-asyncio (same patterns as 001)
**Constraints**: All existing tests must continue to pass. No schema modifications to existing tables.

## Constitution Check

*GATE: All principles verified. No violations.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Human-in-the-Loop | PASS | Read-only analytics — no new trading or execution logic |
| II. Pydantic at Every Boundary | PASS | New models: SpreadSnapshot, PairSummary, HourlyBucket, ScanHealthSummary |
| III. Async-First I/O | PASS | All new repository methods use asyncpg via existing pool |
| IV. Structured Logging | PASS | New CLI commands and repository methods use structlog |
| V. Two-Pass Matching | N/A | Feature does not touch matching pipeline |
| VI. Configuration Over Code | PASS | No new hardcoded values; query parameters passed from CLI flags |

## Project Structure (new/modified files only)

```text
src/arb_scanner/
├── models/
│   ├── analytics.py          # NEW: SpreadSnapshot, PairSummary, HourlyBucket, ScanHealthSummary
│   └── __init__.py           # EXTEND: re-export new models
├── storage/
│   ├── _queries.py           # EXTEND: add analytics SQL constants
│   ├── _analytics_queries.py # NEW: analytics-specific SQL (keep _queries.py under 300 lines)
│   ├── repository.py         # EXTEND: add analytics read methods
│   └── migrations/
│       └── 008_create_price_snapshots.sql  # NEW: market_price_snapshots table
├── notifications/
│   └── reporter.py           # EXTEND: add history/stats formatting functions
├── cli/
│   ├── app.py                # EXTEND: add `history` and `stats` commands, --since/--until on report
│   └── _helpers.py           # EXTEND: add date parsing helper

tests/
├── unit/
│   ├── test_analytics_models.py  # NEW: analytics Pydantic model tests
│   └── test_analytics_format.py  # NEW: reporter formatting tests for analytics output
├── integration/
│   └── test_analytics_repo.py    # NEW: repository analytics query tests (DB-dependent)
└── fixtures/
    └── arb_opportunities_history.json  # NEW: test fixtures for analytics queries
```

## Key Technical Decisions

### 1. Separate `_analytics_queries.py` module

The existing `_queries.py` has 10 query constants (~113 lines). The analytics feature adds ~8 new query constants with longer SQL (GROUP BY, date_trunc, window functions). A separate module keeps both under the 300-line limit and cleanly separates concerns.

### 2. Analytics models in `models/analytics.py`

Four new Pydantic models for analytics results. These are read-only output models (not persisted), but they enforce type safety at the repository-to-CLI boundary per Constitution Principle II. They don't extend or modify any existing model.

### 3. Date-range filtering via optional parameters

Existing queries (`GET_RECENT_OPPS`, `GET_TICKETS_WITH_OPPS`, `GET_ALL_MATCHES`) use positional parameters. Rather than modifying those queries (which would risk breaking existing callers), we add *new* query variants (`GET_OPPS_DATE_RANGE`, `GET_TICKETS_DATE_RANGE`, `GET_MATCHES_DATE_RANGE`) and new repository methods that accept `since`/`until` parameters. The existing methods remain untouched.

### 4. Price snapshots as a write-alongside pattern

`insert_market_snapshot` is called from the orchestrator alongside `upsert_market`, not as a replacement. The orchestrator already iterates markets in a loop — we add one more async call per market. At 16 markets/scan (8 per venue), this adds ~16 DB round trips per cycle, negligible against the 60s scan interval.

### 5. No retention policy in this feature

The spec explicitly puts data pruning out of scope. The `market_price_snapshots` table will grow at ~23k rows/day at 60s intervals. At ~200 bytes/row, that's ~4.6 MB/day — manageable for months before requiring cleanup.

## Data Flow

### `arb-scanner history --pair POLY_ID/KALSHI_ID --hours 24`
```
CLI parses pair ID and hours flag
  → Repository.get_spread_history(poly_id, kalshi_id, since)
    → SQL: SELECT from arb_opportunities WHERE pair AND detected_at >= since
  → Reporter.format_spread_history(snapshots)
  → stdout (table or JSON)
```

### `arb-scanner stats --hours 24`
```
CLI parses hours flag
  → Repository.get_pair_summaries(since)         # per-pair aggregates
  → Repository.get_scan_health_summary(since)    # scan log aggregates
  → Reporter.format_stats_report(summaries, health)
  → stdout (table or JSON)
```

### `arb-scanner report --since DATE --until DATE`
```
CLI parses dates (ISO 8601), falls back to --last N if no dates
  → Repository.get_opportunities_date_range(since, until, limit)
  → Repository.get_tickets_date_range(since, until, limit)
  → (existing reporter formatting)
```

## SQL Design

### Migration 008: `market_price_snapshots`
```sql
CREATE TABLE IF NOT EXISTS market_price_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    venue          TEXT NOT NULL,
    event_id       TEXT NOT NULL,
    yes_bid        DECIMAL(10,4) NOT NULL,
    yes_ask        DECIMAL(10,4) NOT NULL,
    no_bid         DECIMAL(10,4) NOT NULL,
    no_ask         DECIMAL(10,4) NOT NULL,
    volume_24h     DECIMAL(16,2) NOT NULL DEFAULT 0,
    snapshotted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_snapshots_venue_event_time
    ON market_price_snapshots (venue, event_id, snapshotted_at DESC);
```

### Key Analytics Queries

**Spread history for a pair:**
```sql
SELECT detected_at, net_spread_pct, annualized_return, depth_risk, max_size
FROM arb_opportunities
WHERE poly_event_id = $1 AND kalshi_event_id = $2 AND detected_at >= $3
ORDER BY detected_at DESC;
```

**Per-pair summary:**
```sql
SELECT poly_event_id, kalshi_event_id,
       MAX(net_spread_pct) AS peak_spread,
       MIN(net_spread_pct) AS min_spread,
       AVG(net_spread_pct) AS avg_spread,
       COUNT(*) AS total_detections,
       MIN(detected_at) AS first_seen,
       MAX(detected_at) AS last_seen
FROM arb_opportunities
WHERE detected_at >= $1
GROUP BY poly_event_id, kalshi_event_id
ORDER BY peak_spread DESC;
```

**Hourly scan health:**
```sql
SELECT date_trunc('hour', started_at) AS hour,
       COUNT(*) AS scan_count,
       AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) AS avg_duration_s,
       SUM(llm_evaluations) AS total_llm_calls,
       SUM(opportunities_found) AS total_opps,
       SUM(jsonb_array_length(errors::jsonb)) AS total_errors
FROM scan_logs
WHERE started_at >= $1
GROUP BY 1
ORDER BY 1 DESC;
```

## Complexity Tracking

> No constitution violations. No complexity justifications needed.
> All changes are additive to existing codebase.
