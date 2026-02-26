# Feature Specification: Robust Sports Market Discovery

**Feature**: `009-robust-sports-discovery` | **Date**: 2026-02-26 | **Status**: Draft
**Depends on**: `008-flippening-engine` (complete)

## Problem Statement

The `classify_sports_markets()` function in `sports_filter.py` identifies sports markets by inspecting Polymarket Gamma API metadata fields (`groupSlug`, `tags`, `groupItemTitle`). This classification is entirely dependent on Polymarket's tagging conventions. If Polymarket changes slug formats, renames tags, or adds new sports categories, the filter silently misses markets with zero indication that coverage has degraded.

Specific fragilities in the current implementation:

1. **Slug matching is prefix-only**: `_detect_sport()` checks `slug.startswith(f"{sport}-")`, so a slug like `basketball-nba-finals` matches `basketball` but not `nba`. A slug format change from `nba-lakers-vs-celtics` to `basketball/nba/lakers-vs-celtics` would silently drop all NBA markets.
2. **Tag matching is substring-based**: `if sport in tag_lower` produces false positives (e.g., `"mlb"` matches `"tumblr"`) and false negatives when tags change format.
3. **No manual override**: When the classifier misses a known sports market, the operator has no way to manually include it without modifying code.
4. **No health observability**: The only log is a single `sports_classification_complete` info message. There is no metric tracking hit rate over time, no alert when classification drops to zero results, and no way to detect gradual degradation (e.g., 50 markets yesterday, 12 today).
5. **No fallback strategy**: If all three heuristics (slug, tags, title) fail, the market is silently dropped.

## Solution

Harden the sports discovery pipeline with manual overrides, classification health metrics, fallback heuristics, and observability. The `classify_sports_markets()` function remains the single entry point, but gains resilience layers that ensure markets are not silently lost.

## Functional Requirements

### FR-001: Manual Market Override List
The system MUST support a `manual_market_ids` list in `FlippeningConfig` (default empty). Markets whose `event_id` or `conditionId` appears in this list MUST be included in the sports market results regardless of whether automated classification succeeds. Each override entry MUST specify: `market_id` (str) and `sport` (str). Overrides MUST be logged at info level when applied.

### FR-002: Manual Market Exclusion List
The system MUST support an `excluded_market_ids` list in `FlippeningConfig` (default empty). Markets whose `event_id` or `conditionId` appears in this list MUST be excluded from results even if automated classification matches them. Exclusions take priority over both automated classification and manual overrides. This prevents false-positive markets from generating noise.

### FR-003: Classification Health Metrics
The system MUST track and expose classification health metrics via structlog and the REST API:
- `total_markets_scanned`: Total Polymarket markets evaluated per discovery cycle.
- `sports_markets_found`: Markets that passed classification.
- `classification_hit_rate`: `sports_markets_found / total_markets_scanned`.
- `markets_by_sport`: Count per sport category.
- `manual_overrides_applied`: Count of markets included via FR-001.
- `exclusions_applied`: Count of markets excluded via FR-002.
- `unclassified_candidate_count`: Markets that matched partial heuristics but fell below threshold (see FR-005).

These metrics MUST be logged at info level on every discovery cycle and persisted to the `flippening_discovery_health` table for trend analysis.

### FR-004: Classification Degradation Alert
The system MUST emit a webhook alert (via existing `dispatch_webhook()`) when:
- `classification_hit_rate` drops below `min_hit_rate_pct` (configurable, default 0.01 -- i.e., fewer than 1% of markets are sports) for two consecutive discovery cycles.
- `sports_markets_found` drops to zero when the previous cycle found > 0.
- A specific sport in `allowed_sports` returns zero markets for three consecutive cycles when it previously had results.

Alerts MUST be rate-limited to one per sport per hour to prevent spam.

### FR-005: Fuzzy Classification Fallback
When the primary heuristics (slug prefix, tag match, title match) all fail, the system MUST apply a secondary fuzzy pass on the `groupItemTitle` and `question` fields using keyword sets per sport. For example, the `nba` keyword set includes: `["lakers", "celtics", "warriors", "bucks", "76ers", "heat", "knicks", "bulls", "nets", "nuggets", "suns", "mavericks", "clippers", "spurs", "rockets", "grizzlies", "timberwolves", "pelicans", "thunder", "pacers", "hawks", "hornets", "wizards", "pistons", "cavaliers", "magic", "raptors", "kings", "trail blazers", "jazz"]`. Keyword sets MUST be configurable via `sport_keywords` in `FlippeningConfig` (dict[str, list[str]]). Fuzzy-matched markets MUST be flagged with `classification_method="fuzzy"` to distinguish from primary matches (`classification_method="primary"`).

### FR-006: Classification Method Tracking
The `SportsMarket` model MUST gain a `classification_method` field (str, one of: `"slug"`, `"tag"`, `"title"`, `"fuzzy"`, `"manual_override"`). The `_detect_sport()` function MUST return both the sport and the method that matched. This enables analysis of which classification paths are actually working.

### FR-007: Discovery Health Persistence
The system MUST persist discovery health snapshots to a `flippening_discovery_health` table with fields: `id`, `cycle_timestamp`, `total_scanned`, `sports_found`, `hit_rate`, `by_sport` (JSONB), `overrides_applied`, `exclusions_applied`, `unclassified_candidates`. Migration MUST be numbered sequentially after existing migrations.

### FR-008: Discovery Health API Endpoint
The system MUST add `GET /api/flippenings/discovery-health?limit=N` returning the last N discovery health snapshots (default 20). This enables the dashboard to show classification health trends.

### FR-009: CLI Discovery Diagnostics
The system MUST add a `flip-discover` CLI command that runs one discovery cycle and outputs:
- Total markets scanned.
- Sports markets found (with classification method breakdown).
- Manual overrides applied.
- Exclusions applied.
- Top 10 unclassified markets that partially matched (for operator review).
Options: `--sports` (filter), `--verbose` (show all matched markets with metadata), `--format (table|json)`.

### FR-010: Configuration Extensions
`FlippeningConfig` MUST gain these fields:
- `manual_market_ids`: list[ManualOverride] (default []), where ManualOverride has `market_id: str` and `sport: str`.
- `excluded_market_ids`: list[str] (default []).
- `sport_keywords`: dict[str, list[str]] (default {} -- empty means use built-in defaults).
- `min_hit_rate_pct`: float (default 0.01).
- `discovery_alert_cooldown_minutes`: int (default 60).

## Success Criteria

- SC-001: A market added to `manual_market_ids` appears in `classify_sports_markets()` output regardless of metadata content.
- SC-002: A market added to `excluded_market_ids` is excluded even when automated classification matches it.
- SC-003: `flip-discover` outputs classification method breakdown showing slug/tag/title/fuzzy/override counts.
- SC-004: When `classification_hit_rate` drops below threshold, a degradation alert fires via webhook within one discovery cycle.
- SC-005: Fuzzy keyword fallback correctly identifies sports markets that lack proper slug/tag metadata in synthetic test data.
- SC-006: `GET /api/flippenings/discovery-health` returns health snapshots with per-sport breakdowns.
- SC-007: `SportsMarket.classification_method` is populated for all classified markets.
- SC-008: All existing tests still pass (no regressions).
- SC-009: All quality gates pass (ruff, mypy --strict, 70% coverage).

## Edge Cases

### EC-001: Duplicate Override and Automated Match
A market matches both automated classification and appears in `manual_market_ids`. The system MUST use the automated classification result and NOT double-count the market. `classification_method` MUST be the automated method, not `"manual_override"`.

### EC-002: Override Market Not Found
A `manual_market_ids` entry references a market ID not present in the current Polymarket API response. The system MUST log a warning and skip the override (the market may have resolved or been delisted).

### EC-003: Fuzzy False Positive
A fuzzy keyword match (e.g., "Celtics" matching a political market about Boston) produces a false positive. The operator MUST be able to add it to `excluded_market_ids` to suppress. The `flip-discover --verbose` output MUST show enough context (full title, slug) for the operator to make this judgment.

### EC-004: Sport Keyword Overlap
Multiple sports share a keyword (e.g., a team name used in both NBA and college basketball). The system MUST match to the FIRST sport in the `allowed_sports` list that contains the keyword.

### EC-005: Zero Markets from API
The Polymarket API returns zero markets entirely (not just zero sports markets). The system MUST NOT fire a classification degradation alert in this case -- it MUST fire a separate `api_empty_response` warning, since the issue is upstream, not in classification.

## Dependencies

- `008-flippening-engine` (complete): Sports filter, config, models, orchestrator.
- Polymarket Gamma API: Market metadata fields.

## Out of Scope

- ML-based market classification (topic modeling, NLP entity extraction).
- Automatic keyword set generation from historical market data.
- Cross-venue sports discovery (Kalshi sports markets).
- Modifying the Polymarket API response format or requesting upstream fixes.
- Backfilling classification health history for past discovery cycles.
