# Tasks: 013 — Event Market Reversion

## Phase 1: Model Layer

- [ ] 1.1 Add `CategoryConfig` model to `models/config.py` with validation
- [ ] 1.2 Add `categories: dict[str, CategoryConfig]` to `FlippeningConfig` with auto-migration validator
- [ ] 1.3 Rename `SportsMarket` → `CategoryMarket` in `models/flippening.py`, add `category_type` field
- [ ] 1.4 Add `category`, `category_type`, `baseline_strategy` fields to `Baseline` model
- [ ] 1.5 Add `category`, `category_type` fields to `FlippeningEvent` model
- [ ] 1.6 Write `tests/unit/test_category_config.py` — validator auto-migration, validation errors, overlay logic
- [ ] 1.7 Update all imports of `SportsMarket` → `CategoryMarket` across codebase
- [ ] 1.8 Run quality gates (ruff, mypy, pytest)

## Phase 2: Discovery Refactor

- [ ] 2.1 Create `flippening/category_keywords.py` from `sport_keywords.py` — rename functions, keep `DEFAULT_SPORT_KEYWORDS`
- [ ] 2.2 Create `flippening/market_classifier.py` from `sports_filter.py` — `classify_markets()`, `_detect_category()`, updated health snapshot
- [ ] 2.3 Update `DiscoveryHealthSnapshot`: `by_sport` → `by_category`, add `by_category_type`
- [ ] 2.4 Update `check_degradation()` for per-category dropout alerting
- [ ] 2.5 Delete `sports_filter.py` and `sport_keywords.py`
- [ ] 2.6 Update all imports: `sports_filter` → `market_classifier`, `sport_keywords` → `category_keywords`
- [ ] 2.7 Write `tests/unit/test_market_classifier.py` — slug/tag/keyword discovery, overlaps (EC-002), health, degradation
- [ ] 2.8 Rename/update `tests/unit/test_sports_filter_robust.py` → `test_market_classifier_robust.py`
- [ ] 2.9 Run quality gates

## Phase 3: Baseline Strategy

- [ ] 3.1 Create `flippening/baseline_strategy.py` with `BaselineCapture` class (`first_price`, `rolling_window`, `pre_event_snapshot`)
- [ ] 3.2 Extract drift logic from `game_manager.py` → `flippening/drift_tracker.py`
- [ ] 3.3 Add `category`, `category_type`, `baseline_strategy`, `event_window_hours` to `GameState`
- [ ] 3.4 Update `GameManager.initialize()` to accept `list[CategoryMarket]`, set strategy fields from `CategoryConfig`
- [ ] 3.5 Update `GameManager.capture_baseline()` to delegate to `BaselineCapture`
- [ ] 3.6 Add rolling window baseline refresh in `GameManager.process()` for `rolling_window` strategy
- [ ] 3.7 Update `_advance_lifecycle()` for event window completion (time-bounded and open-ended events)
- [ ] 3.8 Write `tests/unit/test_baseline_strategy.py` — all three strategies, EC-003 fallback, EC-006 insufficient data
- [ ] 3.9 Update `tests/unit/test_game_manager.py` — `CategoryMarket` fixtures, event windows, rolling baseline
- [ ] 3.10 Run quality gates

## Phase 4: Spike Detection and Signal Generation

- [ ] 4.1 Update `spike_detector.py`: `_get_threshold()` and `_get_min_confidence()` read from `CategoryConfig`
- [ ] 4.2 Update `_score_confidence()` to read `confidence_modifier` from `CategoryConfig`
- [ ] 4.3 Update `signal_generator.py`: `create_entry()` reads per-category `max_hold_minutes`, `reversion_target_pct`, `stop_loss_pct`
- [ ] 4.4 Update `tests/unit/test_spike_detector.py` — category fields, `CategoryConfig` thresholds
- [ ] 4.5 Update `tests/unit/test_signal_generator.py` — per-category overrides
- [ ] 4.6 Run quality gates

## Phase 5: Orchestrator Refactor

- [ ] 5.1 Extract `_process_update()`, `_handle_entry()`, `_handle_exit()` → `_orch_processing.py`
- [ ] 5.2 Extract alert dispatch functions → `_orch_alerts.py`
- [ ] 5.3 Extract `_check_telemetry()` → `_orch_telemetry.py`
- [ ] 5.4 Extract repo creation and persist helpers → `_orch_repo.py`
- [ ] 5.5 Update `orchestrator.py` to use `active_categories`, call `classify_markets()`, pass category info
- [ ] 5.6 Update `_periodic_discovery()` for category-based refresh
- [ ] 5.7 Verify all orchestrator modules stay under 300 lines
- [ ] 5.8 Run quality gates

## Phase 6: CLI Refactoring

- [ ] 6.1 Add `--categories` option to `flip-watch` command, make `--sports` an alias
- [ ] 6.2 Update `flip-discover` to display `category_type`, use `classify_markets()`
- [ ] 6.3 Add `--category` and `--category-type` filters to `flip-history` and `flip-stats`
- [ ] 6.4 Update `flip-replay`, `flip-evaluate`, `flip-sweep` with `--category` option (keep `--sport` alias)
- [ ] 6.5 Update `_flip_discover_helpers.py` for category-based discovery
- [ ] 6.6 Update `_ws_validate_helpers.py` for category-based token discovery
- [ ] 6.7 Split render helpers if `flippening_commands.py` exceeds 300 lines
- [ ] 6.8 Update `tests/unit/test_flippening_commands.py` — `--categories` flag
- [ ] 6.9 Update `tests/unit/test_flip_discover_cli.py` — category_type output
- [ ] 6.10 Run quality gates

## Phase 7: Database Migration

- [ ] 7.1 Create `migrations/017_add_category_columns.sql`
- [ ] 7.2 Update `_flippening_queries.py` — add category columns to INSERT/SELECT
- [ ] 7.3 Update `flippening_repository.py` — add `category` params to `get_history()`, `get_stats()`
- [ ] 7.4 Update `_tick_queries.py` and `tick_repository.py` — add `category` filter
- [ ] 7.5 Run quality gates

## Phase 8: Replay Engine

- [ ] 8.1 Rename `replay_sport()` → `replay_category()` in `replay_engine.py`
- [ ] 8.2 Handle `rolling_window` baseline from tick history during replay
- [ ] 8.3 Update `replay_evaluator.py` if needed for category grouping
- [ ] 8.4 Update `tests/unit/test_replay_engine.py` — `replay_category()`
- [ ] 8.5 Run quality gates

## Phase 9: API and Dashboard

- [ ] 9.1 Add `category`, `category_type` query params to `routes_flippening.py`
- [ ] 9.2 Add Category column to flippenings dashboard tables in `app.js`
- [ ] 9.3 Add optional `category_type` filter dropdown in dashboard
- [ ] 9.4 Run quality gates

## Phase 10: Config and Docs

- [ ] 10.1 Update `config.yaml` — replace `sports` + `sport_overrides` with `categories` map
- [ ] 10.2 Update `config.example.yaml` with categories example including non-sport categories
- [ ] 10.3 Update `CLAUDE.md` — document category system, new CLI options, 013 in Recent Changes

## Phase 11: Regression and Final Verification

- [ ] 11.1 Write `tests/unit/test_sport_regression.py` — SC-001: 6 sports identical after refactor
- [ ] 11.2 Full quality gate run with coverage check
- [ ] 11.3 Verify all modules ≤ 300 lines, all functions ≤ 50 lines
- [ ] 11.4 Manual smoke test: `flip-watch --categories nba` connects and discovers markets
