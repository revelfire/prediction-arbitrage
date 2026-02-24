# Tasks: Cross-Venue Arbitrage Scanner

**Input**: Design documents from `/specs/001-arb-scanner-core/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Included — spec requires ≥70% coverage and every module must have a corresponding test file.

**Organization**: Tasks grouped by user story. US1 (Single Scan) and US3 (LLM Matching) are both P1 and tightly coupled — they form the MVP together.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US3)
- Include exact file paths in descriptions

## Path Conventions

- Source: `src/arb_scanner/`
- Tests: `tests/`
- Config: project root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project scaffolding, dependencies, tooling, and configuration

- [x] T001 Create project structure: `pyproject.toml` with all dependencies (httpx, pydantic, anthropic, bm25s, asyncpg, typer, structlog, pyyaml, ruff, mypy, pytest, pytest-asyncio, pytest-cov), package `src/arb_scanner/` with `__init__.py` and `__main__.py`, `tests/` directory with `conftest.py`
- [x] T002 [P] Create `config.example.yaml` with all configuration sections (venues, claude, scanning, arb_thresholds, notifications, storage, logging) per spec in project root
- [x] T003 [P] Create `.gitignore` (Python defaults, `.env`, `data/`, `*.db`, `.specify/`, `.claude/`), `ruff.toml` (line-length=100, target Python 3.11), `mypy.ini` (strict mode)
- [x] T004 [P] Configure pre-commit hooks: ruff check + ruff format + mypy in `.pre-commit-config.yaml`
- [x] T005 Run `uv sync` to install all dependencies, verify `uv run ruff check` and `uv run mypy src/` both exit 0 on empty package

**Checkpoint**: Project builds, lints, and type-checks with zero errors on an empty package.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that ALL user stories depend on. No story work can begin until this phase is complete.

**CRITICAL**: Blocks all subsequent phases.

- [x] T006 Implement Pydantic data models in `src/arb_scanner/models/market.py`: `Venue` enum, `Market` model with all fields from data-model.md (bid/ask as Decimal, fee_model as string, raw_data as dict)
- [x] T007 [P] Implement Pydantic data models in `src/arb_scanner/models/matching.py`: `MatchResult` model with validation (confidence 0-1, safe_to_arb must be False when resolution_equivalent is False)
- [x] T008 [P] Implement Pydantic data models in `src/arb_scanner/models/arbitrage.py`: `ArbOpportunity` model (cost < 1.0 validation, buy_venue != sell_venue), `ExecutionTicket` model with status enum
- [x] T009 [P] Implement settings models in `src/arb_scanner/models/config.py`: `FeeSchedule`, `VenueConfig`, `ClaudeConfig`, `ScanConfig`, `NotificationConfig`, `Settings` dataclass
- [x] T010 Implement YAML config loader in `src/arb_scanner/config/loader.py`: load YAML, interpolate `${ENV_VAR}` references, validate with Pydantic Settings model, support `ARB_SCANNER_CONFIG` env var override
- [x] T011 [P] Implement structlog setup in `src/arb_scanner/utils/logging.py`: JSON output, configure processors, module+operation+ID context binding
- [x] T012 [P] Implement retry helper in `src/arb_scanner/utils/retry.py`: exponential backoff with jitter, configurable max retries, Retry-After header support
- [x] T013 [P] Implement rate limiter in `src/arb_scanner/utils/rate_limiter.py`: `asyncio.Semaphore`-based, per-venue configurable requests/second
- [x] T014 Implement PostgreSQL connection pool in `src/arb_scanner/storage/db.py`: asyncpg pool creation from `DATABASE_URL`, startup/shutdown lifecycle, health check
- [x] T015 Implement database migrations in `src/arb_scanner/storage/migrations/`: SQL files `001_create_markets.sql`, `002_create_match_results.sql`, `003_create_arb_opportunities.sql`, `004_create_execution_tickets.sql`, `005_create_scan_logs.sql`, `006_enable_pgvector.sql`. Include migration runner that applies in order and tracks applied migrations.
- [x] T016 Implement repository layer in `src/arb_scanner/storage/repository.py`: CRUD operations for all entities (upsert_market, upsert_match_result, insert_opportunity, insert_ticket, insert_scan_log, get_cached_match, get_recent_opportunities)
- [x] T017 [P] Write unit tests in `tests/unit/test_models.py`: validate all Pydantic model constraints (price ranges, enum values, cross-field validation), test Market normalization from both venue formats
- [x] T018 [P] Write unit tests in `tests/unit/test_config_loader.py`: test YAML loading, env var interpolation, missing required fields, default values
- [x] T019 Write integration tests in `tests/integration/test_repository.py`: test all CRUD operations against a real test PostgreSQL database, verify migration application, test cache TTL expiry

**Checkpoint**: All models validated, config loading works, database operations functional. Foundation ready for user story implementation.

---

## Phase 3: User Story 1 - Single Scan Cycle (Priority: P1) & User Story 3 - LLM Matching (Priority: P1) — MVP

**Goal**: Complete scan pipeline: ingest from both venues → BM25 pre-filter → Claude semantic matching → arb calculation → JSON output. These two stories are co-dependent and form the MVP together.

**Independent Test**: Run `arb-scanner scan --dry-run` with test fixtures and verify correct JSON output with calculated arb opportunities.

### Ingestion (US1)

- [x] T020 [US1] Implement abstract base client in `src/arb_scanner/ingestion/base.py`: async context manager, rate limiter integration, retry decorator, abstract `fetch_markets()` method returning `list[Market]`
- [x] T021 [US1] Implement Polymarket client in `src/arb_scanner/ingestion/polymarket.py`: Gamma API market discovery (`active=true&closed=false`, offset pagination), parse JSON-string fields (`clobTokenIds`, `outcomePrices`), map to Market model. CLOB API `/book?token_id=` for order book depth.
- [x] T022 [US1] Implement Kalshi client in `src/arb_scanner/ingestion/kalshi.py`: `GET /markets?status=open` with cursor pagination, parse `*_dollars` string fields to Decimal, compute asks from complement (`YES_ask = 1.00 - highest_NO_bid`), `GET /markets/{ticker}/orderbook` for depth. No auth needed.
- [x] T023 [P] [US1] Create test fixtures in `tests/fixtures/polymarket_markets.json` and `tests/fixtures/polymarket_orderbook.json`: realistic Gamma API and CLOB responses with 5-10 active markets
- [x] T024 [P] [US1] Create test fixtures in `tests/fixtures/kalshi_markets.json` and `tests/fixtures/kalshi_orderbook.json`: realistic Kalshi responses using `*_dollars` fields, including orderbook with ascending bid arrays
- [x] T025 [US1] Write tests in `tests/integration/test_polymarket_client.py`: use httpx MockTransport to mock Gamma API + CLOB responses, verify Market model mapping, pagination handling, JSON-string field parsing
- [x] T026 [US1] Write tests in `tests/integration/test_kalshi_client.py`: use httpx MockTransport to mock responses, verify `*_dollars` parsing, ask computation from complement, cursor pagination, rate limiting

### Matching (US3)

- [x] T027 [US3] Implement BM25 pre-filter in `src/arb_scanner/matching/prefilter.py`: build bm25s index from market titles with `method="bm25+"` and `stopwords="en"`, for each Polymarket title query against Kalshi corpus, return candidate pairs above configurable score threshold
- [x] T028 [US3] Implement Claude semantic matcher in `src/arb_scanner/matching/semantic.py`: batch candidate pairs (configurable batch_size, default 5), construct system prompt with MatchResult JSON schema, call `anthropic` SDK with `claude-sonnet-4-20250514`, parse structured JSON response into MatchResult models, handle malformed responses (safe_to_arb=False fallback)
- [x] T029 [US3] Implement match cache in `src/arb_scanner/matching/cache.py`: PostgreSQL-backed cache keyed on `(poly_event_id, kalshi_event_id)`, configurable TTL (default 24h), get/set/expire operations, skip LLM call on cache hit
- [x] T030 [P] [US3] Create test fixture in `tests/fixtures/claude_match_response.json`: realistic Claude API responses for 3 scenarios (high-confidence match, non-equivalent pair, ambiguous pair)
- [x] T031 [US3] Write unit tests in `tests/unit/test_prefilter.py`: verify BM25 index construction, test that known-matching titles score above threshold, test that unrelated titles score below, verify candidate pair reduction ≥80%
- [x] T032 [US3] Write tests in `tests/integration/test_semantic_matcher.py`: mock Claude API responses via httpx MockTransport, verify MatchResult parsing, test malformed response handling, test batching logic
- [x] T033 [US3] Write tests in `tests/integration/test_match_cache.py`: test cache hit/miss, TTL expiry with mocked time, cache invalidation

### Arb Engine (US1)

- [x] T034 [US1] Implement arb calculator in `src/arb_scanner/engine/calculator.py`: for each matched pair compute cost_per_contract (YES_ask + NO_ask across venues), gross_profit (1.00 - cost), apply venue-specific fee models (Polymarket: % on winnings `fee = rate × (1.00 - cost_side)`; Kalshi: `min(taker_fee, cap)` per contract), compute net_profit, net_spread_pct, max_size (min liquidity), annualized_return if expiry known, depth_risk flag
- [x] T035 [US1] Implement execution ticket generator in `src/arb_scanner/engine/tickets.py`: given ArbOpportunity, produce ExecutionTicket with two legs (venue, side, price, size), expected_cost, expected_profit, status="pending"
- [x] T036 [US1] Write unit tests in `tests/unit/test_calculator.py`: 5 parametrized hand-computed test cases covering both fee models, zero-profit edge case, negative-profit filtering, annualized return with/without expiry, depth_risk threshold
- [x] T037 [P] [US1] Write unit tests in `tests/unit/test_fee_models.py`: test Polymarket fee-on-winnings (fee base is profit not principal), test Kalshi per-contract with cap, verify fee=0 when spread is negative
- [x] T038 [P] [US1] Write unit tests in `tests/unit/test_tickets.py`: verify ticket leg construction, size constrained by min liquidity, status defaults to "pending"

### Scan Orchestrator & CLI (US1)

- [x] T039 [US1] Implement scan orchestrator in `src/arb_scanner/cli/orchestrator.py`: async pipeline — concurrent venue ingestion via `asyncio.gather()`, BM25 pre-filter, Claude matching (skip cache hits), arb calculation, persist to DB, return scan results. Accept dry-run flag to use fixtures instead of live APIs.
- [x] T040 [US1] Implement Typer CLI `scan` command in `src/arb_scanner/cli/app.py`: `arb-scanner scan [--dry-run] [--min-spread PCT] [--output json|table]`, call orchestrator, format output per contracts/cli.md JSON schema, exit codes (0=success, 1=error, 2=partial)
- [x] T041 [US1] Write e2e test in `tests/e2e/test_scan_pipeline.py`: mock all external APIs (both venues + Claude), run full scan pipeline, verify correct number of opportunities detected, verify fee calculations match hand-computed values, verify JSON output schema

**Checkpoint**: `uv run arb-scanner scan --dry-run` produces valid JSON with correctly calculated arb opportunities. MVP is functional.

---

## Phase 4: User Story 2 - Continuous Monitoring with Alerts (Priority: P2)

**Goal**: Watch mode with configurable polling interval and webhook notifications when opportunities exceed threshold.

**Independent Test**: Run `arb-scanner watch` with mocked APIs, introduce mispricing after second poll, verify webhook fires.

- [x] T042 [US2] Implement webhook dispatcher in `src/arb_scanner/notifications/webhook.py`: async POST to Slack/Discord webhook URLs, construct payloads per contracts/notifications.md, retry on failure (3 attempts with backoff), log failures and continue
- [x] T043 [US2] Implement Markdown report formatter in `src/arb_scanner/notifications/reporter.py`: format list of ArbOpportunity + ExecutionTicket as Markdown table sorted by net_spread_pct descending
- [x] T044 [US2] Implement watch loop in `src/arb_scanner/cli/orchestrator.py` (extend): add `run_watch()` method — repeated scan cycles at configurable interval, track previously-seen opportunity IDs to avoid re-alerting, fire webhook for new opportunities exceeding min_spread threshold, graceful shutdown on SIGINT/SIGTERM
- [x] T045 [US2] Implement Typer CLI `watch` command in `src/arb_scanner/cli/app.py` (extend): `arb-scanner watch [--interval SECS] [--min-spread PCT]`, signal handler for graceful shutdown
- [x] T046 [US2] Implement Typer CLI `report` command in `src/arb_scanner/cli/app.py` (extend): `arb-scanner report [--last N] [--format markdown|json]`, query recent opportunities from DB, format with reporter
- [x] T047 [P] [US2] Write tests in `tests/integration/test_webhook.py`: mock webhook endpoints with httpx MockTransport, verify Slack payload shape matches contract, verify Discord payload shape, verify retry on failure, verify fire-and-forget behavior
- [x] T048 [US2] Write tests for watch loop: mock scan orchestrator, run 3 cycles, verify deduplication (same opportunity not re-alerted), verify new opportunity triggers webhook

**Checkpoint**: Watch mode runs continuously, alerts fire on new opportunities, no re-alerts for stale opps.

---

## Phase 5: User Story 4 - Execution Ticket Generation (Priority: P2)

**Goal**: Structured execution tickets for each qualified arb. Human-readable report output.

**Independent Test**: Feed known ArbOpportunity, verify ticket contains correct venue/side/price/size for both legs.

*Note: ExecutionTicket model and basic generator already built in Phase 3 (T035, T038). This phase adds persistence and CLI integration.*

- [x] T049 [US4] Extend repository in `src/arb_scanner/storage/repository.py`: add `get_pending_tickets()`, `update_ticket_status()`, `expire_stale_tickets()` methods
- [x] T050 [US4] Wire ticket generation into scan orchestrator in `src/arb_scanner/cli/orchestrator.py`: after arb calculation, generate and persist ExecutionTicket for each opportunity above threshold
- [x] T051 [US4] Extend `report` command in `src/arb_scanner/cli/app.py`: include execution tickets in report output, show pending/approved/expired status
- [x] T052 [US4] Write tests for ticket persistence and lifecycle in `tests/integration/test_repository.py` (extend): test insert, get_pending, update_status, expire_stale

**Checkpoint**: Execution tickets generated, persisted, and visible in reports.

---

## Phase 6: User Story 5 - Match Audit Trail (Priority: P3)

**Goal**: Review all cached contract matches for transparency and quality verification.

**Independent Test**: Populate cache with known entries, run `arb-scanner match-audit`, verify all entries appear with scores and reasoning.

- [x] T053 [US5] Implement `match-audit` command in `src/arb_scanner/cli/app.py` (extend): `arb-scanner match-audit [--include-expired] [--min-confidence FLOAT]`, query match cache from DB, format as table with columns (poly_id, kalshi_id, confidence, equivalent, safe, reasoning truncated, expires), mark expired entries
- [x] T054 [US5] Extend repository in `src/arb_scanner/storage/repository.py`: add `get_all_matches(include_expired: bool, min_confidence: float)` method
- [x] T055 [US5] Write test in `tests/unit/test_match_audit.py`: populate test DB with mix of active/expired matches at various confidence levels, verify filtering and display

**Checkpoint**: Match audit shows all cached matches with confidence scores and LLM reasoning.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Quality, documentation, dry-run mode, and final validation

- [x] T056 Implement dry-run/mock mode in `src/arb_scanner/ingestion/base.py` (extend): when `--dry-run` flag is set, load test fixtures from `tests/fixtures/` instead of making network calls. Ensure deterministic, reproducible results.
- [x] T057 [P] Implement `arb-scanner migrate` command in `src/arb_scanner/cli/app.py` (extend): apply all pending SQL migrations from `src/arb_scanner/storage/migrations/`
- [x] T058 [P] Add `--help` text for all CLI commands and verify `arb-scanner --help`, `arb-scanner scan --help`, etc. all work per contracts/cli.md
- [x] T059 [P] Write README.md with setup instructions, usage examples, and architecture overview per quickstart.md content
- [x] T060 Run full quality gate suite: `uv run ruff check src/ tests/` + `uv run ruff format --check src/ tests/` + `uv run mypy src/ --strict` + `uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70`. Fix all failures.
- [x] T061 Verify `uv run arb-scanner scan --dry-run` exits 0 and produces valid JSON matching contracts/cli.md schema
- [x] T062 Verify all CLI commands produce valid output and exit with appropriate codes

**Checkpoint**: All quality gates pass. CLI is complete and documented. Project is production-ready.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundation)**: Depends on Phase 1 — BLOCKS all user stories
- **Phase 3 (US1+US3 MVP)**: Depends on Phase 2 — core pipeline
- **Phase 4 (US2 Watch)**: Depends on Phase 3 — extends orchestrator
- **Phase 5 (US4 Tickets)**: Depends on Phase 3 — extends orchestrator and repository
- **Phase 6 (US5 Audit)**: Depends on Phase 2 — only needs models and repository
- **Phase 7 (Polish)**: Depends on all above

### User Story Dependencies

- **US1 + US3 (P1)**: Co-dependent. Must build together. No dependency on other stories.
- **US2 (P2)**: Depends on US1+US3 (extends scan orchestrator with watch loop)
- **US4 (P2)**: Depends on US1+US3 (extends orchestrator with ticket persistence). Can run in parallel with US2.
- **US5 (P3)**: Only depends on Phase 2 foundation. Can start as soon as foundation is complete.

### Parallel Opportunities Within Phases

**Phase 2 — Foundation**: T007, T008, T009 (models) can run in parallel. T011, T012, T013 (utils) can run in parallel. T017, T018 (unit tests) can run in parallel.

**Phase 3 — MVP**: T023, T024 (fixtures) can run in parallel. T030 (Claude fixture) can parallel with T023/T024. T036, T037, T038 (engine tests) can run in parallel.

**Phase 4+5**: US2 (watch/alerts) and US4 (ticket persistence) can run in parallel since they extend different parts of the codebase.

---

## Parallel Execution Examples

### Phase 2 — Launch models in parallel:
```
Task: "Implement MatchResult model in src/arb_scanner/models/matching.py"         [T007]
Task: "Implement ArbOpportunity model in src/arb_scanner/models/arbitrage.py"     [T008]
Task: "Implement Settings models in src/arb_scanner/models/config.py"             [T009]
```

### Phase 3 — Launch fixtures in parallel:
```
Task: "Create Polymarket test fixtures in tests/fixtures/"                         [T023]
Task: "Create Kalshi test fixtures in tests/fixtures/"                             [T024]
Task: "Create Claude match response fixture in tests/fixtures/"                    [T030]
```

### Phase 3 — Launch engine tests in parallel:
```
Task: "Write calculator unit tests in tests/unit/test_calculator.py"               [T036]
Task: "Write fee model unit tests in tests/unit/test_fee_models.py"                [T037]
Task: "Write ticket unit tests in tests/unit/test_tickets.py"                      [T038]
```

---

## Implementation Strategy

### MVP First (Phase 1 + 2 + 3)

1. Complete Phase 1: Setup (T001-T005)
2. Complete Phase 2: Foundation (T006-T019)
3. Complete Phase 3: US1+US3 MVP (T020-T041)
4. **STOP AND VALIDATE**: Run `arb-scanner scan --dry-run` — verify correct JSON output
5. Run all quality gates — fix until green

### Incremental Delivery

1. Setup + Foundation → Infrastructure ready
2. US1+US3 → Scan + Matching MVP → Demo
3. US2 → Watch mode + alerts → Demo
4. US4 → Execution tickets → Demo
5. US5 → Match audit → Demo
6. Polish → Production-ready

### Autonomous Execution Notes

Between the design approval checkpoint (now) and implementation completion:
- Do NOT pause between tasks — execute sequentially within phases, parallel where marked [P]
- Fix quality gate failures immediately — do not ask for human input
- Make reasonable decisions and document in code comments
- Update CLAUDE.md if any architectural decisions change during implementation

---

## Notes

- [P] tasks = different files, no dependencies
- [US*] label maps task to specific user story for traceability
- Tests included per spec requirement (≥70% coverage)
- Commit after each task or logical group
- All quality gates must pass after Phase 7: ruff, mypy --strict, pytest ≥70% coverage
