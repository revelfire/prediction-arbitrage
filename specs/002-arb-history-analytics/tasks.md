# Tasks: Persistent Arb History + Analytics

**Input**: Design documents from `/specs/002-arb-history-analytics/`
**Prerequisites**: plan.md (required), spec.md (required), contracts/cli.md
**Depends on**: `001-arb-scanner-core` (complete, branch merged)

**Tests**: Included — every new module has a corresponding test file. Existing tests must not break.

**Organization**: Tasks grouped by implementation layer. Models and queries first (foundation), then repository, then CLI/formatting, then integration.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- Include exact file paths in descriptions

## Path Conventions

- Source: `src/arb_scanner/`
- Tests: `tests/`
- Migrations: `src/arb_scanner/storage/migrations/`

## Autonomous Execution Notes

- Do NOT pause between tasks — execute sequentially within phases, parallel where marked [P]
- Fix quality gate failures immediately — do not ask for human input
- Run all 5 quality gates after each phase completes
- All existing 232 tests MUST continue to pass after every phase

---

## Phase 1: Foundation (Models + Queries + Migration)

**Purpose**: Analytics Pydantic models, SQL queries, and the price snapshots migration. No changes to existing files in this phase (except `models/__init__.py` re-exports).

- [x] T001 [US1,US2,US3] Create `src/arb_scanner/models/analytics.py` with four Pydantic models: `SpreadSnapshot` (detected_at: datetime, net_spread_pct: Decimal, annualized_return: Decimal | None, depth_risk: bool, max_size: Decimal), `PairSummary` (poly_event_id: str, kalshi_event_id: str, peak_spread: Decimal, min_spread: Decimal, avg_spread: Decimal, total_detections: int, first_seen: datetime, last_seen: datetime), `HourlyBucket` (hour: datetime, avg_spread: Decimal, max_spread: Decimal, detection_count: int), `ScanHealthSummary` (hour: datetime, scan_count: int, avg_duration_s: float, total_llm_calls: int, total_opps: int, total_errors: int). All with docstrings and type hints.
- [x] T002 [P] [US1,US2,US3] Create `src/arb_scanner/storage/_analytics_queries.py` with SQL constants: `GET_SPREAD_HISTORY` (per-pair time series from arb_opportunities filtered by poly_event_id, kalshi_event_id, detected_at >= $3, ORDER BY detected_at DESC), `GET_PAIR_SUMMARIES` (GROUP BY poly/kalshi event IDs with MAX/MIN/AVG/COUNT on net_spread_pct, filtered by detected_at >= $1), `GET_HOURLY_BUCKETS` (date_trunc hour bucketing of arb_opportunities with avg/max spread and count, filtered by time window), `GET_SCAN_HEALTH` (date_trunc hour bucketing of scan_logs with avg duration, sum LLM calls, sum opps, error count), `GET_RECENT_SCAN_LOGS` (SELECT * from scan_logs ORDER BY started_at DESC LIMIT $1), `GET_OPPS_DATE_RANGE` (like GET_RECENT_OPPS but with WHERE detected_at >= $1 AND detected_at < $2, optional LIMIT), `GET_TICKETS_DATE_RANGE` (like GET_TICKETS_WITH_OPPS but with date range on created_at), `GET_MATCHES_DATE_RANGE` (like GET_ALL_MATCHES but with WHERE matched_at >= $1), `INSERT_SNAPSHOT` (INSERT into market_price_snapshots), `GET_PRICE_HISTORY` (SELECT from market_price_snapshots by venue/event_id/time range).
- [x] T003 [P] [US4] Create `src/arb_scanner/storage/migrations/008_create_price_snapshots.sql` with the market_price_snapshots table schema: id BIGSERIAL PRIMARY KEY, venue TEXT NOT NULL, event_id TEXT NOT NULL, yes_bid DECIMAL(10,4), yes_ask DECIMAL(10,4), no_bid DECIMAL(10,4), no_ask DECIMAL(10,4), volume_24h DECIMAL(16,2) DEFAULT 0, snapshotted_at TIMESTAMPTZ DEFAULT NOW(). Create index on (venue, event_id, snapshotted_at DESC).
- [x] T004 [P] Extend `src/arb_scanner/models/__init__.py` to re-export all four analytics models from `analytics.py`. Add to `__all__`.
- [x] T005 [P] Create `tests/unit/test_analytics_models.py` with tests for all four analytics models: valid construction, field types (Decimal precision), boundary values, required vs optional fields. Target: ~20 tests.

**Quality gate**: Run all 5 gates. Existing 232 tests + new model tests must pass.

---

## Phase 2: Repository Layer (Analytics Read Methods)

**Purpose**: Add analytics query methods to the repository. Extract analytics methods into a separate class to keep `repository.py` under 300 lines.

- [x] T006 [US1] Create `src/arb_scanner/storage/analytics_repository.py` with class `AnalyticsRepository` (takes asyncpg.Pool in __init__). Add method `get_spread_history(poly_id: str, kalshi_id: str, since: datetime) -> list[SpreadSnapshot]` — executes GET_SPREAD_HISTORY, maps rows to SpreadSnapshot models.
- [x] T007 [US2] Add method `get_pair_summaries(since: datetime) -> list[PairSummary]` to `AnalyticsRepository` — executes GET_PAIR_SUMMARIES, maps rows to PairSummary models.
- [x] T008 [US2] Add method `get_hourly_buckets(since: datetime) -> list[HourlyBucket]` to `AnalyticsRepository` — executes GET_HOURLY_BUCKETS, maps rows to HourlyBucket models.
- [x] T009 [US3] Add method `get_scan_health(since: datetime) -> list[ScanHealthSummary]` to `AnalyticsRepository` — executes GET_SCAN_HEALTH, maps rows. Also add `get_recent_scan_logs(limit: int) -> list[dict]` for raw scan log access.
- [x] T010 [US5] Add methods to `AnalyticsRepository`: `get_opportunities_date_range(since: datetime, until: datetime | None, limit: int) -> list[dict]`, `get_tickets_date_range(since: datetime, until: datetime | None, limit: int) -> list[dict]`, `get_matches_date_range(since: datetime, min_confidence: float) -> list[dict]`.
- [x] T011 [US4] Add methods to `AnalyticsRepository`: `insert_market_snapshot(market: Market) -> None` — executes INSERT_SNAPSHOT. `get_price_history(venue: str, event_id: str, since: datetime) -> list[dict]` — executes GET_PRICE_HISTORY.
- [x] T012 [P] Create `tests/integration/test_analytics_repo.py` with DB-dependent tests (skipif no DATABASE_URL) for all AnalyticsRepository methods: spread history returns SpreadSnapshot models, pair summaries aggregate correctly, hourly buckets use date_trunc, scan health reads scan_logs, date range filtering works, snapshot insert and retrieval. Target: ~15 tests.

**Quality gate**: Run all 5 gates. All tests pass.

---

## Phase 3: Formatting + CLI Commands

**Purpose**: Reporter formatting for analytics output, date parsing helpers, new CLI commands, and extensions to existing commands.

- [x] T013 [US1] Add `format_spread_history(pair_label: str, snapshots: list[SpreadSnapshot]) -> str` to `src/arb_scanner/notifications/reporter.py` — ASCII table with columns: DETECTED_AT, NET_SPREAD, ANNUALIZED, DEPTH_RISK, MAX_SIZE. Include header with pair label and data point count.
- [x] T014 [US2,US3] Add `format_stats_report(summaries: list[PairSummary], health: list[ScanHealthSummary], top_n: int) -> str` to `src/arb_scanner/notifications/reporter.py` — Two sections: "Top Pairs by Peak Spread" table and "Scanner Health" table per contracts/cli.md format.
- [x] T015 [P] [US5] Add `parse_iso_datetime(value: str) -> datetime` helper to `src/arb_scanner/cli/_helpers.py` — accepts ISO 8601 date (YYYY-MM-DD) or datetime (YYYY-MM-DDTHH:MM:SS), returns timezone-aware UTC datetime. Raises typer.BadParameter on invalid format.
- [x] T016 [US1] Add `history` command to `src/arb_scanner/cli/app.py`: options `--pair` (required str), `--hours` (int, default 24), `--format` (table|json). Parse pair as `POLY_ID/KALSHI_ID`, connect to DB via `_helpers.py`, call `AnalyticsRepository.get_spread_history()`, format via reporter or JSON, print to stdout.
- [x] T017 [US2,US3] Add `stats` command to `src/arb_scanner/cli/app.py`: options `--hours` (int, default 24), `--top` (int, default 10), `--format` (table|json). Connect to DB, call `get_pair_summaries()` and `get_scan_health()`, format via reporter, print to stdout.
- [x] T018 [US5] Extend `report` command in `src/arb_scanner/cli/app.py` with `--since` and `--until` options (str, optional). When provided, use `AnalyticsRepository.get_opportunities_date_range()` and `get_tickets_date_range()` instead of existing `get_recent_opportunities()`. Maintain backward compatibility: no flags = existing behavior.
- [x] T019 [US5] Extend `match-audit` command in `src/arb_scanner/cli/app.py` with `--since` option (str, optional). When provided, use `AnalyticsRepository.get_matches_date_range()` instead of existing `get_all_matches()`.

**Quality gate**: Run all 5 gates. All tests pass.

---

## Phase 4: Snapshot Integration + Tests

**Purpose**: Wire snapshot recording into the scan pipeline, create test fixtures, and write all remaining tests.

- [x] T020 [US4] Modify `src/arb_scanner/cli/orchestrator.py` to call `AnalyticsRepository.insert_market_snapshot(market)` for each fetched market during scan cycle, alongside existing `upsert_market()`. Only when DB is available (not dry-run). Add structlog entry for snapshot count.
- [x] T021 [P] Create `tests/fixtures/arb_opportunities_history.json` with test data: 3 unique pairs, each with 5-10 time-stamped arb opportunities at varying spreads over a 48h window. Include one pair with declining spread and one with increasing spread for trend testing.
- [x] T022 [P] Create `tests/unit/test_analytics_format.py` with tests for `format_spread_history()` and `format_stats_report()`: correct table headers, data alignment, percentage formatting, empty data handling, pair label display. Target: ~12 tests.
- [x] T023 [P] Create `tests/unit/test_date_parsing.py` with tests for `parse_iso_datetime()`: valid date, valid datetime, datetime with timezone, invalid format, empty string. Target: ~8 tests.
- [x] T024 Create `tests/unit/test_analytics_cli.py` with CliRunner tests for `history` and `stats` commands (mocked DB): `--help` works, `--pair` format validation, `--hours` default, `--format json` output. Also test `report --since` and `match-audit --since`. Target: ~15 tests.

**Quality gate**: Run all 5 gates. Coverage must remain ≥70%.

---

## Phase 5: Polish + Verification

**Purpose**: Update help text, verify backward compatibility, run final quality gates.

- [x] T025 Verify all existing CLI commands still work identically: `arb-scanner scan --dry-run`, `arb-scanner report --help`, `arb-scanner match-audit --help`, `arb-scanner watch --help`, `arb-scanner migrate --help`. No regressions.
- [x] T026 Verify new CLI commands produce correct `--help` output: `arb-scanner history --help`, `arb-scanner stats --help`. Verify `--pair` format documented in help text.
- [x] T027 Run full quality gate suite: ruff check, ruff format, mypy --strict, pytest with coverage ≥70%. Fix any failures.
- [x] T028 Update `CLAUDE.md` to add `arb-scanner history` and `arb-scanner stats` to the Commands section. Add `analytics_repository.py` to Architecture section. Update `__init__.py` exports note.

**Quality gate**: All 5 gates green. Final verification.

---

## Task Dependency Graph

```
Phase 1: T001 ──┐
         T002 ──┤ (all parallel)
         T003 ──┤
         T004 ──┤
         T005 ──┘
              │
Phase 2: T006 → T007 → T008 → T009 → T010 → T011
         T012 (parallel with T006-T011, after Phase 1)
              │
Phase 3: T013 → T014 (sequential, same file)
         T015 (parallel, different file)
         T016 → T017 → T018 → T019 (sequential, same file)
              │
Phase 4: T020 (depends on T011, T016)
         T021, T022, T023 (parallel, independent files)
         T024 (depends on T016, T017, T018, T019)
              │
Phase 5: T025 → T026 → T027 → T028 (sequential)
```

## Total: 28 tasks across 5 phases

| Phase | Tasks | Purpose |
|-------|-------|---------|
| 1 | T001-T005 | Foundation: models, queries, migration |
| 2 | T006-T012 | Repository analytics methods |
| 3 | T013-T019 | Formatting + CLI commands |
| 4 | T020-T024 | Pipeline integration + tests |
| 5 | T025-T028 | Polish + verification |
