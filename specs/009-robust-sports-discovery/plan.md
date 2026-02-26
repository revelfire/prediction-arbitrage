# Implementation Plan: Robust Sports Market Discovery

**Feature**: `009-robust-sports-discovery` | **Date**: 2026-02-26 | **Status**: Draft
**Spec**: `specs/009-robust-sports-discovery/spec.md`

## Architecture Overview

All changes stay within the existing flippening subpackage. The sports filter module gains resilience layers; a new migration adds a health tracking table; new API and CLI endpoints expose observability. No new dependencies required.

```
FlippeningConfig (config.py)
  + manual_market_ids, excluded_market_ids, sport_keywords, min_hit_rate_pct, discovery_alert_cooldown_minutes
          │
          ▼
classify_sports_markets() (sports_filter.py)
  ┌───────────────────────────┐
  │ 1. Manual override pass   │  ← FR-001
  │ 2. Exclusion filter       │  ← FR-002
  │ 3. Primary heuristics     │  (existing: slug, tag, title)
  │ 4. Fuzzy keyword fallback │  ← FR-005
  │ 5. Health metrics capture │  ← FR-003
  │ 6. Degradation check      │  ← FR-004
  └───────────────────────────┘
          │
          ▼
SportsMarket.classification_method  ← FR-006
          │
          ├──▶ structlog (every cycle)
          ├──▶ flippening_discovery_health table  ← FR-007
          ├──▶ GET /api/flippenings/discovery-health  ← FR-008
          └──▶ flip-discover CLI  ← FR-009
```

## File Change Map

### Modified Files

| File | Changes | FRs |
|------|---------|-----|
| `src/arb_scanner/models/config.py` | Add `ManualOverride` model, extend `FlippeningConfig` with 5 new fields | FR-010 |
| `src/arb_scanner/models/flippening.py` | Add `classification_method: str = "primary"` to `SportsMarket` | FR-006 |
| `src/arb_scanner/flippening/sports_filter.py` | Refactor `classify_sports_markets()` with override/exclusion/fuzzy passes, return classification method, compute health metrics | FR-001–006 |
| `src/arb_scanner/flippening/orchestrator.py` | Pass config to `classify_sports_markets()`, call health persistence + degradation alerting after discovery | FR-003, FR-004 |
| `src/arb_scanner/storage/flippening_repository.py` | Add `insert_discovery_health()` and `get_discovery_health()` methods | FR-007, FR-008 |
| `src/arb_scanner/storage/_flippening_queries.py` | Add `INSERT_DISCOVERY_HEALTH` and `GET_DISCOVERY_HEALTH` SQL | FR-007, FR-008 |
| `src/arb_scanner/api/routes_flippening.py` | Add `GET /api/flippenings/discovery-health` endpoint | FR-008 |
| `src/arb_scanner/cli/flippening_commands.py` | Add `flip-discover` command registration and implementation | FR-009 |
| `config.example.yaml` | Add `manual_market_ids`, `excluded_market_ids`, `sport_keywords` examples | FR-010 |

### New Files

| File | Purpose | FRs |
|------|---------|-----|
| `src/arb_scanner/storage/migrations/013_create_discovery_health.sql` | Create `flippening_discovery_health` table | FR-007 |
| `src/arb_scanner/flippening/sport_keywords.py` | Built-in keyword sets per sport (NBA teams, NFL teams, etc.) | FR-005 |
| `tests/unit/test_sports_filter_robust.py` | Tests for overrides, exclusions, fuzzy matching, health metrics | All |
| `tests/unit/test_flip_discover_cli.py` | Tests for `flip-discover` CLI command | FR-009 |

## Implementation Phases

### Phase 1: Config + Model Extensions (FR-006, FR-010)

1. Add `ManualOverride` pydantic model to `config.py`:
   ```python
   class ManualOverride(BaseModel):
       market_id: str
       sport: str
   ```
2. Extend `FlippeningConfig` with 5 new fields (all with sensible defaults).
3. Add `classification_method: str = "primary"` to `SportsMarket` model. Using `str` (not enum) for extensibility — values: `"slug"`, `"tag"`, `"title"`, `"fuzzy"`, `"manual_override"`.

### Phase 2: Fuzzy Keyword Module (FR-005)

1. Create `sport_keywords.py` with `DEFAULT_SPORT_KEYWORDS: dict[str, list[str]]` containing team/league names for nba, nhl, nfl, mlb, epl, ufc.
2. The function `get_sport_keywords(config_keywords, sport)` returns config overrides if present, else built-in defaults.
3. Keep keyword lists focused — ~30 entries per sport max. These are fallback heuristics, not an exhaustive database.

### Phase 3: Sports Filter Refactor (FR-001–005)

Refactor `classify_sports_markets()` to a multi-pass pipeline:

1. **Override pass**: Check each market against `manual_market_ids`. Matched markets get `classification_method="manual_override"` and skip automated classification.
2. **Automated pass**: Run existing `_detect_sport()` but refactor it to return `(sport, method)` tuple instead of just `sport`. Methods: `"slug"`, `"tag"`, `"title"`.
3. **Fuzzy pass**: Markets that didn't match in steps 1-2 get a fuzzy keyword check against `sport_keywords`. Method: `"fuzzy"`.
4. **Exclusion filter**: Remove any market whose `event_id` or `conditionId` is in `excluded_market_ids`. Applied last so it overrides everything.
5. **Health metrics**: Compute `DiscoveryHealthSnapshot` dataclass with all FR-003 metrics. Log via structlog.

Key design: `_detect_sport()` signature changes from `-> str | None` to `-> tuple[str, str] | None` where the second element is the method. This is an internal function so no backward compatibility concern.

### Phase 4: Degradation Alerting (FR-004)

Add `_check_degradation()` function in `sports_filter.py`:
- Takes current health metrics + previous cycle's metrics (passed in from orchestrator).
- Checks three conditions from FR-004 (hit rate below threshold, zero results, sport dropout).
- Returns a list of alert messages.
- Orchestrator dispatches via existing `dispatch_webhook()`.
- Rate limiting: Track last alert time per sport in a module-level dict. Respect `discovery_alert_cooldown_minutes`.

### Phase 5: Persistence + API (FR-007, FR-008)

1. Migration `013_create_discovery_health.sql`:
   ```sql
   CREATE TABLE flippening_discovery_health (
       id BIGSERIAL PRIMARY KEY,
       cycle_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       total_scanned INT NOT NULL,
       sports_found INT NOT NULL,
       hit_rate DOUBLE PRECISION NOT NULL,
       by_sport JSONB NOT NULL DEFAULT '{}',
       overrides_applied INT NOT NULL DEFAULT 0,
       exclusions_applied INT NOT NULL DEFAULT 0,
       unclassified_candidates INT NOT NULL DEFAULT 0
   );
   CREATE INDEX idx_discovery_health_ts ON flippening_discovery_health (cycle_timestamp DESC);
   ```
2. Add `insert_discovery_health()` and `get_discovery_health(limit)` to `FlippeningRepository`.
3. Add `GET /api/flippenings/discovery-health?limit=20` to `routes_flippening.py`.

### Phase 6: CLI Command (FR-009)

Add `flip-discover` command to `flippening_commands.py`:
- One-shot: loads config, fetches markets from Polymarket, runs `classify_sports_markets()`, prints diagnostics.
- `--sports` filter, `--verbose` flag (show all matched markets), `--format table|json`.
- Shows unclassified candidates (markets that partially matched fuzzy but below threshold) — up to 10.
- No database required (pure classification diagnostic).

### Phase 7: Orchestrator Integration (FR-003, FR-004)

Wire the new capabilities into the orchestrator's discovery loop:
- After `classify_sports_markets()`, call persistence + degradation check.
- Pass `FlippeningConfig` to `classify_sports_markets()` (currently only receives `allowed_sports`).
- Track previous cycle's health metrics for degradation comparison.

## Edge Case Handling

| Edge Case | Handling | Phase |
|-----------|----------|-------|
| EC-001: Duplicate override + automated | Automated classification takes priority; `manual_override` only applied when automated misses | Phase 3 |
| EC-002: Override market not found | Log warning with market_id, skip (no error) | Phase 3 |
| EC-003: Fuzzy false positive | Operator adds to `excluded_market_ids`; `flip-discover --verbose` shows context | Phase 3 + 6 |
| EC-004: Sport keyword overlap | First match in `allowed_sports` list order wins | Phase 2 |
| EC-005: Zero markets from API | Separate `api_empty_response` warning, no degradation alert | Phase 4 |

## Testing Strategy

- **Unit tests**: Override inclusion, exclusion filtering, fuzzy matching, method tracking, health metric computation, degradation alert triggering.
- **CLI test**: `flip-discover` output format with mocked Polymarket data.
- **API test**: `GET /api/flippenings/discovery-health` with mocked repository.
- No integration tests needed (no new DB-specific behavior beyond standard insert/fetch).

## Quality Gates

All must pass after each phase:
1. `ruff check` — zero errors
2. `ruff format --check` — clean
3. `mypy src/ --strict` — zero errors
4. `pytest tests/ -x` — all pass
5. `pytest --cov --cov-fail-under=70` — coverage maintained
