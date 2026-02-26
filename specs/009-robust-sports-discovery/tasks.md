# Tasks: Robust Sports Market Discovery

**Feature**: `009-robust-sports-discovery` | **Date**: 2026-02-26

## Phase 1: Config + Model Extensions [FR-006, FR-010]

- [ ] P1-T01: Add `ManualOverride` pydantic model to `models/config.py` with fields `market_id: str` and `sport: str`.
- [ ] P1-T02: Add 5 new fields to `FlippeningConfig`: `manual_market_ids: list[ManualOverride] = []`, `excluded_market_ids: list[str] = []`, `sport_keywords: dict[str, list[str]] = {}`, `min_hit_rate_pct: float = 0.01`, `discovery_alert_cooldown_minutes: int = 60`.
- [ ] P1-T03: Add `classification_method: str = "primary"` field to `SportsMarket` model in `models/flippening.py`.
- [ ] P1-T04: Add `manual_market_ids`, `excluded_market_ids`, `sport_keywords` examples (commented out) to `config.example.yaml` under the `flippening:` section.
- [ ] P1-T05: Run quality gates (ruff, mypy, pytest). Verify all existing tests pass with new default fields.

## Phase 2: Fuzzy Keyword Module [FR-005]

- [ ] P2-T01: Create `src/arb_scanner/flippening/sport_keywords.py` with `DEFAULT_SPORT_KEYWORDS: dict[str, list[str]]` containing ~30 team/league names per sport for nba, nhl, nfl, mlb, epl, ufc.
- [ ] P2-T02: Implement `get_sport_keywords(config_keywords: dict[str, list[str]], sport: str) -> list[str]` that returns config override keywords if present, else built-in defaults for that sport.
- [ ] P2-T03: Implement `fuzzy_match_sport(title: str, question: str, allowed: set[str], keywords: dict[str, list[str]]) -> str | None` that checks title and question fields against keyword sets. Return first sport match in `allowed` iteration order (EC-004).
- [ ] P2-T04: Write unit tests for `get_sport_keywords()` (config override, fallback) and `fuzzy_match_sport()` (match, no-match, keyword overlap between sports).
- [ ] P2-T05: Run quality gates.

## Phase 3: Sports Filter Refactor [FR-001, FR-002, FR-003, FR-005, FR-006]

- [ ] P3-T01: Refactor `_detect_sport()` to return `tuple[str, str] | None` where second element is classification method (`"slug"`, `"tag"`, `"title"`). Update all callers within `sports_filter.py`.
- [ ] P3-T02: Add override pass at the start of `classify_sports_markets()`: iterate `config.manual_market_ids`, match by `event_id` or `conditionId` in `raw_data`, create `SportsMarket` with `classification_method="manual_override"`. Log info per override applied. Skip markets already matched by automated pass (EC-001). Log warning for override market_ids not found in API response (EC-002).
- [ ] P3-T03: Add fuzzy pass after the automated loop: for markets not yet classified, call `fuzzy_match_sport()` on `groupItemTitle` and `question` fields. Set `classification_method="fuzzy"` on matches.
- [ ] P3-T04: Add exclusion filter after all classification passes: remove markets whose `event_id` or `raw_data.get("conditionId")` appears in `config.excluded_market_ids`. Track count for health metrics.
- [ ] P3-T05: Update `classify_sports_markets()` signature to accept `config: FlippeningConfig` parameter (in addition to existing `markets` and `allowed_sports`). Extract `manual_market_ids`, `excluded_market_ids`, `sport_keywords` from config.
- [ ] P3-T06: Add `DiscoveryHealthSnapshot` dataclass (or dict) computation at end of `classify_sports_markets()`: `total_scanned`, `sports_found`, `hit_rate`, `by_sport` (dict of sport->count), `overrides_applied`, `exclusions_applied`, `unclassified_candidates`. Return as second value from function (tuple return).
- [ ] P3-T07: Update the structlog `sports_classification_complete` message to include all health metrics from FR-003.
- [ ] P3-T08: Write unit tests in `tests/unit/test_sports_filter_robust.py`: manual override inclusion, override for missing market (warning logged), exclusion filtering, fuzzy keyword match, classification_method tracking, health metric computation, automated match takes priority over override (EC-001).
- [ ] P3-T09: Update existing `tests/unit/test_sports_filter.py` tests to pass the new `config` parameter (use default `FlippeningConfig()`).
- [ ] P3-T10: Run quality gates.

## Phase 4: Degradation Alerting [FR-004]

- [ ] P4-T01: Add `_check_degradation(current: DiscoveryHealthSnapshot, previous: DiscoveryHealthSnapshot | None, config: FlippeningConfig) -> list[str]` function in `sports_filter.py`. Check three conditions: hit rate below `min_hit_rate_pct` for 2 consecutive cycles, sports_found drops to zero from >0, any sport in `allowed_sports` returns zero for 3 consecutive cycles.
- [ ] P4-T02: Add rate limiting for degradation alerts: module-level `_last_alert_time: dict[str, datetime]` tracking last alert per sport/category. Respect `discovery_alert_cooldown_minutes`.
- [ ] P4-T03: Add EC-005 handling: if `total_scanned == 0`, emit `api_empty_response` warning instead of classification degradation alert.
- [ ] P4-T04: Write unit tests for `_check_degradation()`: hit rate drop triggers alert, zero results triggers alert, sport dropout triggers alert after 3 cycles, cooldown prevents duplicate alerts, zero API markets skips degradation check (EC-005).
- [ ] P4-T05: Run quality gates.

## Phase 5: Persistence + API [FR-007, FR-008]

- [ ] P5-T01: Create `src/arb_scanner/storage/migrations/013_create_discovery_health.sql` with `flippening_discovery_health` table: `id BIGSERIAL PRIMARY KEY`, `cycle_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `total_scanned INT NOT NULL`, `sports_found INT NOT NULL`, `hit_rate DOUBLE PRECISION NOT NULL`, `by_sport JSONB NOT NULL DEFAULT '{}'`, `overrides_applied INT NOT NULL DEFAULT 0`, `exclusions_applied INT NOT NULL DEFAULT 0`, `unclassified_candidates INT NOT NULL DEFAULT 0`. Add index on `cycle_timestamp DESC`.
- [ ] P5-T02: Add `INSERT_DISCOVERY_HEALTH` and `GET_DISCOVERY_HEALTH` SQL constants to `_flippening_queries.py`.
- [ ] P5-T03: Add `insert_discovery_health(snapshot: dict)` and `get_discovery_health(limit: int = 20) -> list[dict]` methods to `FlippeningRepository`.
- [ ] P5-T04: Add `GET /api/flippenings/discovery-health` endpoint to `routes_flippening.py` with `limit` query param (default 20, ge=1, le=200). Returns list of health snapshot dicts. 503 on DB error.
- [ ] P5-T05: Add API route test in `tests/unit/test_api_routes.py`: mock `get_discovery_health`, verify 200 with data and empty list.
- [ ] P5-T06: Run quality gates.

## Phase 6: CLI Command [FR-009]

- [ ] P6-T01: Add `flip-discover` command to `flippening_commands.py` via `register()`. Options: `--sports` (comma-separated filter), `--verbose` (show all matched markets with metadata), `--format table|json`.
- [ ] P6-T02: Implement `flip_discover()`: load config, fetch markets from Polymarket (reuse `PolymarketClient`), run `classify_sports_markets()`, print classification summary (total scanned, sports found, hit rate, per-sport counts, method breakdown).
- [ ] P6-T03: In verbose mode, print each matched market with: event_id (truncated), title, sport, classification_method, token_id.
- [ ] P6-T04: Print top 10 unclassified candidate markets (those with partial fuzzy matches or sports-related title keywords that fell below threshold). Include enough context (full title, slug) for operator to decide on manual override or exclusion (EC-003).
- [ ] P6-T05: Add `--format json` output path using `json.dumps()` with `default=str`.
- [ ] P6-T06: Write unit tests in `tests/unit/test_flip_discover_cli.py`: mock PolymarketClient, verify table output format, verify json output format, verify verbose includes market details.
- [ ] P6-T07: Run quality gates.

## Phase 7: Orchestrator Integration [FR-003, FR-004]

- [ ] P7-T01: Update `_discover_markets()` in `orchestrator.py` to pass `FlippeningConfig` to `classify_sports_markets()`.
- [ ] P7-T02: After `classify_sports_markets()`, extract `DiscoveryHealthSnapshot` and persist via `repo.insert_discovery_health()` (skip if dry_run or repo is None).
- [ ] P7-T03: Track previous cycle's health snapshot in orchestrator state. Pass to `_check_degradation()` on each discovery cycle.
- [ ] P7-T04: Dispatch degradation alerts via existing `dispatch_webhook()` / `dispatch_flip_alert()` pattern. Format alert messages with sport, metric, and threshold info.
- [ ] P7-T05: Update `_periodic_discovery()` with the same health tracking + alerting logic.
- [ ] P7-T06: Update existing orchestrator tests to account for new `classify_sports_markets()` signature and return value.
- [ ] P7-T07: Run full quality gates. Verify all 573+ tests pass, coverage >= 70%.
