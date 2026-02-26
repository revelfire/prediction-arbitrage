# Plan: 013 — Event Market Reversion

## Current State Audit

| File | Lines | Status |
|------|-------|--------|
| `orchestrator.py` | 729 | OVER 300 limit (needs split) |
| `game_manager.py` | 389 | OVER 300 limit (needs split) |
| `flippening_commands.py` | 310 | OVER 300 limit (needs split) |
| `sports_filter.py` | 300 | At limit |
| `spike_detector.py` | 272 | OK |
| `signal_generator.py` | 253 | OK |
| `sport_keywords.py` | 240 | OK |
| `config.py` | 222 | OK |
| `flippening.py` | 175 | OK |

## Phase 1: Model Layer — Category Config and Baseline Strategy

### 1A. New models in `models/config.py`

Add `CategoryConfig` model with fields: `category_type`, `enabled`, `baseline_strategy`, `baseline_window_minutes`, `spike_threshold_pct`, `confidence_modifier`, `min_confidence`, `reversion_target_pct`, `stop_loss_pct`, `max_hold_minutes`, `late_join_penalty`, `event_window_hours`, `discovery_keywords`, `discovery_tags`, `discovery_slugs`. Validate `category_type` against `{"sport","entertainment","politics","crypto","economics","corporate"}` and `baseline_strategy` against `{"first_price","rolling_window","pre_event_snapshot"}`.

Modify `FlippeningConfig`:
- Add `categories: dict[str, CategoryConfig] = {}`
- Add `model_validator(mode="after")` that auto-converts `sports` + `sport_overrides` + `sport_keywords` into `categories` entries when `categories` is empty (EC-004). Log deprecation warning.
- After validator runs, all downstream code reads only `categories`.

### 1B. Modify `models/flippening.py`

- Rename `SportsMarket` → `CategoryMarket`. Fields: `market`, `category`, `category_type`, `game_start_time`, `token_id`, `classification_method`.
- `Baseline`: Add `category`, `category_type`, `baseline_strategy` fields. Keep `sport` for DB compat (set = category for non-sports).
- `FlippeningEvent`: Add `category`, `category_type` fields alongside `sport`.

## Phase 2: Discovery Refactor — Category-Based Classification

### 2A. Replace `sports_filter.py` with `market_classifier.py`

- `classify_sports_markets()` → `classify_markets(markets, categories: dict[str, CategoryConfig], config)` returning `list[CategoryMarket]`.
- `DiscoveryHealthSnapshot`: `by_sport` → `by_category`, add `by_category_type`.
- `_detect_sport()` → `_detect_category()`: iterate `categories.items()`, check each category's `discovery_slugs`, `discovery_tags`, `discovery_keywords`.
- `check_degradation()`: per-category 3-cycle dropout (EC-005).
- Module state: `_sport_zero_count` → `_category_zero_count`.

### 2B. Replace `sport_keywords.py` with `category_keywords.py`

- Keep `DEFAULT_SPORT_KEYWORDS` dict as fallback for auto-generated sport categories.
- `get_sport_keywords()` → `get_category_keywords(category: CategoryConfig, category_id: str)`.
- `fuzzy_match_sport()` → `fuzzy_match_category()`.

## Phase 3: Baseline Strategy Implementation

### 3A. New file: `flippening/baseline_strategy.py` (~120 lines)

```python
class BaselineCapture:
    @staticmethod
    def capture_first_price(state, update, late_join) -> Baseline: ...
    @staticmethod
    def capture_rolling_window(state, update, window_minutes) -> Baseline | None: ...
    @staticmethod
    def capture_pre_event_snapshot(state, update, offset_minutes) -> Baseline | None: ...
```

- `rolling_window`: Time-windowed average of YES midpoints from `state.price_history`. Returns `None` if < 3 data points (EC-006).
- `pre_event_snapshot`: Captures at `game_start_time - offset`. Falls back to `first_price` if no `game_start_time` (EC-003).

### 3B. Modify `game_manager.py` — extract drift, add strategy dispatch

- Extract `_update_drift()`, `DriftInfo`, constants → new `drift_tracker.py` (~80 lines).
- `GameState`: add `category`, `category_type`, `baseline_strategy`, `event_window_hours`.
- `initialize()`: accept `list[CategoryMarket]`. Set strategy fields from `CategoryConfig`.
- `capture_baseline()`: delegate to `BaselineCapture` based on `state.baseline_strategy`.
- `_advance_lifecycle()`: time-bounded events complete at `game_start_time + event_window_hours`. Open-ended events stay LIVE.
- Rolling window: re-compute baseline on every price update before spike detection.

Post-split target: ~250 lines.

## Phase 4: Spike Detection and Signal Generation

### 4A. Modify `spike_detector.py`

- `_get_threshold(sport)` → `_get_threshold(category)`: read from `CategoryConfig`.
- `_get_min_confidence(sport)` → `_get_min_confidence(category)`.
- `_score_confidence()`: read `confidence_modifier` from `CategoryConfig`.

### 4B. Modify `signal_generator.py`

- `create_entry()`: read `max_hold_minutes`, `reversion_target_pct`, `stop_loss_pct` from `CategoryConfig`, fall back to global defaults.

## Phase 5: Orchestrator Refactor

### 5A. Split `orchestrator.py` (729 lines → 5 modules)

| New File | Contents | Est. Lines |
|----------|----------|-----------|
| `orchestrator.py` | `run_flip_watch()`, periodic discovery | ~200 |
| `_orch_processing.py` | `_process_update()`, `_handle_entry()`, `_handle_exit()` | ~150 |
| `_orch_alerts.py` | Alert dispatch, discovery health handling | ~120 |
| `_orch_telemetry.py` | `_check_telemetry()` | ~100 |
| `_orch_repo.py` | `_create_repo()`, `_create_tick_repo()`, persist helpers | ~80 |

### 5B. Category support in orchestrator

- `allowed_sports` → `active_categories: dict[str, CategoryConfig]`.
- `classify_markets()` call, `game_mgr.initialize(category_markets)`.
- Log entries include `category` and `category_type` (NFR-003).

## Phase 6: CLI Refactoring

- `flip-watch`: add `--categories` option. `--sports` becomes alias filtering `category_type == "sport"`.
- `flip-discover`: display `category_type` column. Use `classify_markets()`.
- `flip-history`, `flip-stats`: add `--category`, `--category-type` filters.
- `flip-replay`, `flip-evaluate`, `flip-sweep`: `--sport` → `--category` (keep `--sport` alias).
- Split render helpers from `flippening_commands.py` if over 300 lines.

## Phase 7: Database Migration

### `017_add_category_columns.sql`

- `flippening_baselines`: add `category`, `category_type`, `baseline_strategy`. Backfill `category = sport`.
- `flippening_events`: add `category`, `category_type`. Backfill `category = sport`.
- `flippening_discovery_health`: add `by_category JSONB`. Backfill from `by_sport`.
- Add indexes on `category`.

### Query/repository updates

- `_flippening_queries.py`: add category columns to INSERT/SELECT.
- `flippening_repository.py`: add `category` params to `get_history()`, `get_stats()`.
- `_tick_queries.py`, `tick_repository.py`: add `category` filter.

## Phase 8: Replay Engine Updates

- `replay_sport()` → `replay_category()`.
- Handle `rolling_window` baseline from tick history during replay.

## Phase 9: API and Dashboard

- `routes_flippening.py`: add `category`, `category_type` query params.
- `app.js`: add Category column to tables, optional `category_type` filter dropdown.

## Phase 10: Config File Update

Replace `flippening.sports` + `sport_overrides` with `flippening.categories` map. Each sport gets `category_type: sport`, `baseline_strategy: first_price`.

## Phase 11: Testing and Verification

### New test files

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `test_category_config.py` | Config model validation, auto-migration | ~150 |
| `test_market_classifier.py` | Replaces `test_sports_filter.py` | ~200 |
| `test_baseline_strategy.py` | Three baseline strategies, edge cases | ~120 |
| `test_sport_regression.py` | SC-001: 6 sports identical after refactor | ~80 |

### Modified test files

- `test_game_manager.py`: `CategoryMarket`, event windows, rolling baseline.
- `test_spike_detector.py`: category fields, `CategoryConfig` thresholds.
- `test_signal_generator.py`: per-category overrides.
- `test_replay_engine.py`: `replay_category()`.
- `test_flippening_commands.py`: `--categories` flag.
- `test_flip_discover_cli.py`: category_type output.
- `test_flippening_models.py`: `CategoryMarket`, new Baseline fields.
- `test_sports_filter_robust.py`: rename/update for category-based degradation.

## Implementation Order

| Phase | Depends On | Complexity |
|-------|-----------|-----------|
| 1. Model Layer | None | Medium |
| 2. Discovery Refactor | Phase 1 | Medium-High |
| 3. Baseline Strategy | Phase 1 | Medium |
| 4. Spike/Signal Updates | Phase 1 | Low |
| 5. Orchestrator Refactor | Phases 1-4 | High |
| 6. CLI Refactoring | Phases 1-5 | Medium |
| 7. Database Migration | Phase 1 | Low |
| 8. Replay Engine | Phases 1, 4, 7 | Low |
| 9. API/Dashboard | Phase 7 | Low |
| 10. Config File | Phase 1 | Low |
| 11. Testing | All | High (volume) |

## File Change Summary

**New files (14):** `market_classifier.py`, `category_keywords.py`, `baseline_strategy.py`, `drift_tracker.py`, `_orch_processing.py`, `_orch_alerts.py`, `_orch_telemetry.py`, `_orch_repo.py`, `017_add_category_columns.sql`, `test_category_config.py`, `test_market_classifier.py`, `test_baseline_strategy.py`, `test_sport_regression.py`, `_flip_render_helpers.py`.

**Deleted files (2):** `sports_filter.py` (→ `market_classifier.py`), `sport_keywords.py` (→ `category_keywords.py`).

**Modified files (~25):** `config.py`, `flippening.py`, `game_manager.py`, `spike_detector.py`, `signal_generator.py`, `orchestrator.py`, `replay_engine.py`, `alert_formatter.py`, `flippening_commands.py`, `replay_commands.py`, `_flip_discover_helpers.py`, `_ws_validate_helpers.py`, `flippening_repository.py`, `_flippening_queries.py`, `_tick_queries.py`, `tick_repository.py`, `routes_flippening.py`, `app.js`, `config.yaml`, `CLAUDE.md`, plus ~10 test files.

## Key Design Decisions

1. **`sport` field kept in DB/models** — `category` is the primary field; `sport` set = `category` for non-sports so existing queries work.
2. **Config migration in Pydantic validator** — auto-converts `sports` → `categories` at load time. All downstream code reads `categories` only.
3. **Orchestrator split uses `_orch_*.py` private modules** — internal to flippening package.
4. **`BaselineCapture` uses `@staticmethod` methods** — three strategies are simple, no class hierarchy needed.
5. **Rolling window uses existing `price_history` deque** — no new data structure.
6. **Alphabetical tiebreak for overlapping keywords** — deterministic, matches existing behavior.
