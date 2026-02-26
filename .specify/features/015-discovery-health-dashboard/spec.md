# 015 — Discovery Health Dashboard

## Overview

Add a "Discovery Health" section to the dashboard showing per-category market discovery metrics, hit rates, dropout alerts, and classification method breakdowns over time. Provides at-a-glance visibility into whether the market classification pipeline is functioning correctly.

## Motivation

The sports/category discovery pipeline silently degrades when Polymarket changes slugs, tags, or market naming conventions. Currently, operators only learn about degradation via webhook alerts after 2-3 consecutive cycles. A dashboard view provides proactive monitoring and trend visibility.

## Functional Requirements

### FR-001: Current Cycle Summary

Display the latest `DiscoveryHealthSnapshot` with:
- Total markets scanned
- Markets classified (with % hit rate)
- Per-category breakdown (bar chart)
- Per-category_type breakdown (pie chart)
- Overrides applied count
- Exclusions applied count
- Unclassified candidates count (with expandable top-10 sample)

### FR-002: Historical Hit Rate Chart

Line chart showing `hit_rate` over time (last 24 hours, 7 days, 30 days selectable). Data sourced from `flippening_discovery_health` table. Overlay the `min_hit_rate_pct` threshold as a horizontal reference line.

### FR-003: Per-Category Trend

Stacked area chart showing market count per category over time. Categories that drop to zero are highlighted in red. Shows the last 50 discovery cycles.

### FR-004: Classification Method Breakdown

Donut chart showing the distribution of classification methods across all discovered markets: `slug`, `tag`, `title`, `fuzzy`, `manual_override`. Helps identify if the pipeline is relying too heavily on fuzzy matching (a reliability risk).

### FR-005: Dropout Alert History

Table listing recent dropout alerts (from `check_degradation()`) with timestamp, alert text, and current status (active/resolved). An alert is "resolved" when the category returns > 0 results in a subsequent cycle.

### FR-006: Unclassified Market Inspector

Expandable panel showing the top 10 unclassified market titles and slugs from the latest cycle. Helps operators identify markets that should be classified and write manual overrides or add keywords.

### FR-007: REST API Endpoints

- `GET /api/flippening/discovery-health` — Latest health snapshot.
- `GET /api/flippening/discovery-health/history?hours=24` — Historical snapshots.
- `GET /api/flippening/discovery-health/alerts?limit=20` — Recent degradation alerts.

## Edge Cases

- EC-001: No discovery health data yet → Show "No data — run flip-watch to populate" placeholder.
- EC-002: Category added mid-session → Appears in next cycle's snapshot with no historical baseline.
- EC-003: Very high unclassified count (> 90%) → Show prominent warning banner.

## Success Criteria

- SC-001: Hit rate chart loads in < 1 second for 7-day view.
- SC-002: Category trend correctly shows zero-result categories in red.
- SC-003: Unclassified inspector shows actual market titles from Polymarket.
- SC-004: All quality gates pass.

## Out of Scope

- Automatic keyword suggestion from unclassified markets (would need LLM).
- Real-time SSE streaming of discovery health (polling every 60s is sufficient).
- Discovery health alerts from the dashboard (alerts are via webhooks only).
