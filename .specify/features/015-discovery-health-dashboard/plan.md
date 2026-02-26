# 015 - Discovery Health Dashboard

## Summary

Add a Discovery tab to the flippening dashboard that visualizes market discovery health metrics: per-category bar chart, hit rate line chart, classification method donut chart, degradation alerts table, unclassified market inspector, and summary cards. Includes new API endpoints for history and alerts, a database migration for alert persistence, and alert persistence in the orchestrator.

## Components

### Backend
1. **SQL Queries** (`_flippening_queries.py`): Add `SELECT_DISCOVERY_HEALTH_HISTORY`, `INSERT_DISCOVERY_ALERT`, `SELECT_DISCOVERY_ALERTS`, `RESOLVE_DISCOVERY_ALERT`.
2. **Repository** (`flippening_repository.py`): Add `get_discovery_health_history(since)` and `get_discovery_alerts(limit)`, `insert_discovery_alert(text, category)`, `resolve_discovery_alerts(category)`.
3. **API Endpoints** (`routes_flippening.py`): Add `GET /api/flippenings/discovery-health/history` and `GET /api/flippenings/discovery-health/alerts`.
4. **Migration** (`018_discovery_alerts_table.sql`): Create `flippening_discovery_alerts` table with id, alert_text, category, resolved, created_at, resolved_at.
5. **Alert Persistence** (`_orch_alerts.py`): Persist degradation alerts to DB and resolve when categories recover.

### Frontend
6. **HTML** (`index.html`): Discovery tab button and content section with chart containers, tables, summary cards.
7. **JavaScript** (`app.js`): `refreshDiscovery()` function with Chart.js bar/line/donut charts, alerts table, unclassified panel, summary cards.

### Tests
8. **Unit tests** (`test_discovery_health_dashboard.py`): API endpoints, repository method mocks, alert persistence flow.

## Risks
- `app.js` already exceeds 300 lines (471). The discovery tab adds ~120 more lines. This is acceptable for a JS dashboard file that is not subject to the same strict module limits as Python source.
