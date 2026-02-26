# 013 — Event Market Reversion

## Overview

Generalize the flippening mean reversion engine beyond live sports to support **event-driven markets** — any Polymarket market where a scheduled or real-time event triggers emotional overreaction spikes that are likely to revert. The current sports-only pipeline (sports_filter → GameManager → SpikeDetector → SignalGenerator) becomes a special case of a broader **market category** system, where each category defines its own discovery rules, baseline strategy, spike thresholds, and event windows.

## Motivation

The existing flippening engine only monitors 6 sport types. Polymarket hosts hundreds of event-driven markets with similar mean-reversion dynamics:

- **Award shows** (Oscars, Grammys, Emmys): Spike on early category announcements, revert as full ceremony plays out.
- **Political debates / press conferences**: Spike on soundbites or perceived gaffes, revert as poll consensus settles.
- **Crypto price thresholds** ("Will BTC hit $X by date?"): Spike on intraday moves toward/away from threshold, revert when momentum fades.
- **Economic data releases** (CPI, jobs report, Fed rate decisions): Spike on initial headline number, revert as market digests full report.
- **Tech/corporate events** (earnings, product launches, antitrust rulings): Spike on headline, revert as context emerges.
- **Elections / political outcomes**: Spike on early returns or exit polls, revert as more precincts report.

These markets share the sports pattern: a knowable baseline, a defined event window, emotional overreaction, and mean reversion — but each category has different discovery heuristics, baseline capture strategies, and optimal hold times.

## Functional Requirements

### FR-001: Market Category System

The system MUST support a configurable list of **market categories** that replace the current sports-only model. Each category defines:

- **category_id**: Unique slug (e.g., `"nba"`, `"oscars"`, `"btc_threshold"`, `"fed_rate"`).
- **category_type**: One of `"sport"`, `"entertainment"`, `"politics"`, `"crypto"`, `"economics"`, `"corporate"`.
- **discovery_keywords**: List of keywords/phrases for fuzzy matching (analogous to existing `sport_keywords`).
- **discovery_tags**: Tag patterns to match in Polymarket tags field.
- **discovery_slugs**: Slug prefixes to match (analogous to existing slug matching).
- **baseline_strategy**: How to capture the baseline — `"first_price"` (current sports behavior), `"rolling_window"` (rolling average over N minutes), `"pre_event_snapshot"` (snapshot taken at a configured offset before event start).
- **baseline_window_minutes**: Window size for `"rolling_window"` strategy (default: 30).
- **spike_threshold_pct**: Override for default spike threshold.
- **max_hold_minutes**: Override for maximum hold time.
- **confidence_modifier**: Multiplier for confidence scoring (default: 1.0).
- **event_window_hours**: How long to monitor after event start (default: 4).
- **enabled**: Boolean to enable/disable per category.

All existing sports (`nba`, `nhl`, `nfl`, `mlb`, `epl`, `ufc`) MUST continue to work as before with `category_type: "sport"` and `baseline_strategy: "first_price"`.

### FR-002: Generalized Market Discovery

The market discovery pipeline MUST be generalized to classify markets into any configured category, not just sports. The multi-pass classification (slug → tag → title → fuzzy) MUST work for all category types.

- The existing `classify_sports_markets()` function MUST be refactored to `classify_markets()` that accepts a list of category definitions rather than a flat list of sport names.
- Discovery health reporting (`DiscoveryHealthSnapshot`) MUST report by category instead of by sport.
- Per-category 3-cycle dropout alerting MUST work for all categories.

### FR-003: Baseline Strategy Selection

The system MUST support three baseline capture strategies:

1. **`first_price`** (existing behavior): Baseline is the first price update after the game/event goes live. Used for sports where pre-game odds are stable.
2. **`rolling_window`**: Baseline is a rolling average of the last N minutes of prices. Used for markets without a sharp event start (e.g., crypto thresholds, ongoing political situations).
3. **`pre_event_snapshot`**: Baseline is captured at a configured offset before event start (e.g., 15 minutes before Oscars ceremony begins). Requires `game_start_time` in market data.

The `GameManager` and `Baseline` model MUST be updated to support strategy selection per market based on its category configuration.

### FR-004: Category-Specific Confidence Tuning

Each category MUST support its own set of overrides for confidence scoring:

- `spike_threshold_pct`: Minimum deviation to trigger a spike.
- `confidence_modifier`: Multiplier applied to the final confidence score.
- `min_confidence`: Minimum confidence to generate an entry signal.
- `reversion_target_pct`: What percentage of the spike to target for reversion.
- `stop_loss_pct`: Stop-loss distance.
- `max_hold_minutes`: Maximum time to hold before timeout exit.
- `late_join_penalty`: Penalty when joining mid-event.

These MUST overlay the global defaults, identical to how `sport_overrides` works today but generalized to all categories.

### FR-005: Event Window Management

Markets MUST be monitored only during their active event window:

- For sports: From `game_start_time` (or late join) through game resolution (existing behavior).
- For time-bounded events (award shows, debates): From `event_window_start` to `event_window_start + event_window_hours`.
- For open-ended events (crypto thresholds): Continuously while the market is active (no end time), using `rolling_window` baseline.

The `GameManager` lifecycle (UPCOMING → LIVE → COMPLETED) MUST handle all three patterns.

### FR-006: Category Configuration in YAML

Categories MUST be configurable in `config.yaml` under `flippening.categories`. The existing `flippening.sports` list is replaced by the categories system. Example:

```yaml
flippening:
  enabled: true
  categories:
    nba:
      category_type: sport
      baseline_strategy: first_price
      spike_threshold_pct: 0.15
      confidence_modifier: 1.0
    oscars:
      category_type: entertainment
      baseline_strategy: pre_event_snapshot
      baseline_window_minutes: 15
      spike_threshold_pct: 0.12
      max_hold_minutes: 60
      event_window_hours: 5
      discovery_keywords: ["oscar", "academy award", "best picture", "best actor", "best actress"]
      discovery_slugs: ["oscars-", "academy-awards-"]
    btc_threshold:
      category_type: crypto
      baseline_strategy: rolling_window
      baseline_window_minutes: 30
      spike_threshold_pct: 0.10
      max_hold_minutes: 120
      discovery_keywords: ["bitcoin", "btc", "will btc", "bitcoin price"]
      discovery_slugs: ["btc-", "bitcoin-"]
    fed_rate:
      category_type: economics
      baseline_strategy: pre_event_snapshot
      baseline_window_minutes: 30
      spike_threshold_pct: 0.08
      max_hold_minutes: 90
      event_window_hours: 6
      discovery_keywords: ["fed rate", "fomc", "federal reserve", "interest rate", "rate cut", "rate hike"]
      discovery_slugs: ["fed-", "fomc-"]
```

### FR-007: CLI Category Support

- `flip-watch` MUST accept `--categories nba,oscars,btc_threshold` in addition to the existing `--sports` flag (which becomes an alias filtering to `category_type: sport` categories only).
- `flip-discover` MUST display category type alongside sport in output.
- `flip-history` and `flip-stats` MUST support filtering by category and category_type.
- `flip-replay` MUST work with event markets stored in the tick database.

### FR-008: Dashboard Category Visibility

The flippening dashboard tab MUST display the category for each monitored market. Category_type grouping SHOULD be available as a filter. No new dashboard pages are required in this feature.

## Non-Functional Requirements

### NFR-001: Backward Compatibility

All existing sports markets MUST continue to work identically. The migration from `flippening.sports` to `flippening.categories` MUST be seamless — if only `sports` is present in config, the system auto-generates category entries with `category_type: "sport"` and `baseline_strategy: "first_price"`.

### NFR-002: Performance

Discovery classification MUST complete in < 500ms for 500 markets across all categories. Adding categories MUST NOT increase WebSocket subscription latency.

### NFR-003: Observability

All category-specific behavior MUST be logged via structlog with `category` and `category_type` fields. Discovery health snapshots MUST include per-category breakdowns.

## Edge Cases

### EC-001: Unknown Category in Config

If a category in config references an invalid `category_type`, the system MUST log a warning and skip that category. The system MUST NOT crash.

### EC-002: Overlapping Discovery Keywords

When a market matches keywords from multiple categories, the first match in alphabetical order wins (consistent with existing fuzzy match behavior). Log a debug entry noting the overlap.

### EC-003: Missing Event Start Time with `pre_event_snapshot` Strategy

If a market uses `pre_event_snapshot` but has no `game_start_time` in the API data, fall back to `first_price` strategy and log a warning.

### EC-004: Legacy `sports` Config Key

If config contains `flippening.sports` but no `flippening.categories`, auto-generate categories from the sports list using default sport settings. If both exist, `categories` takes precedence and `sports` is ignored (with a deprecation warning logged).

### EC-005: Category With Zero Markets for Extended Period

Apply the existing 3-cycle dropout alerting per category. Non-sport categories that have zero markets for 3+ cycles trigger the same alerting path.

### EC-006: Rolling Window Baseline Insufficient Data

For `rolling_window` strategy, if fewer than 3 price updates exist within the window, do not compute a baseline. Wait for sufficient data before spike detection begins.

## Success Criteria

- SC-001: All 6 existing sports continue to function identically after refactor (regression test).
- SC-002: At least 3 non-sport categories can be configured and discover markets from live Polymarket data.
- SC-003: `rolling_window` baseline correctly averages prices over the configured window and updates with each new price.
- SC-004: `pre_event_snapshot` baseline captures at the correct offset before event start.
- SC-005: `flip-watch --categories nba,btc_threshold` monitors only those two categories.
- SC-006: Category-specific thresholds override global defaults correctly.
- SC-007: All quality gates pass (ruff, mypy, pytest, coverage >= 70%).

## Out of Scope

- Multi-venue event markets (Kalshi). This feature is Polymarket-only, matching existing flippening scope.
- LLM-assisted market classification. Discovery is keyword/slug/tag based only.
- Custom confidence models per category_type. All categories use the same magnitude/strength/speed formula with per-category weight overrides.
- Historical event calendar integration (e.g., knowing when the Oscars ceremony starts from an external API).
