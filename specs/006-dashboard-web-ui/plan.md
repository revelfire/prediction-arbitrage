# Implementation Plan: Dashboard Web UI

**Branch**: `006-dashboard-web-ui` | **Date**: 2026-02-24 | **Spec**: [spec.md](spec.md)

## Summary

Add a FastAPI REST API and a vanilla JS single-page dashboard served from the same process. The API is a thin async wrapper around existing Repository and AnalyticsRepository methods. The dashboard renders opportunity tables, spread time-series charts, health metrics, trend alerts, and ticket management — all data already accessible via CLI commands.

## Technical Context

**New Dependencies**: `fastapi`, `uvicorn[standard]`
**New Package**: `src/arb_scanner/api/` (FastAPI app, routes, dependencies)
**New Static Files**: `src/arb_scanner/api/static/` (index.html, app.js, style.css)
**Chart Library**: Chart.js 4.x via CDN (no npm)
**New CLI Command**: `arb-scanner serve --host --port`

## Constitution Check

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Human-in-the-Loop | PASS | Dashboard is read-heavy; ticket approve/expire are explicit user actions |
| II. Pydantic at Every Boundary | PASS | FastAPI uses same Pydantic models for response serialization |
| III. Async-First I/O | PASS | FastAPI async endpoints, asyncpg pool shared via lifespan |
| IV. Structured Logging | PASS | structlog for API request logging |
| V. Two-Pass Matching | PASS | Unchanged — API reads results, doesn't modify matching pipeline |
| VI. Configuration Over Code | PASS | DashboardConfig for host/port |

## Project Structure (new/modified files)

```text
src/arb_scanner/
├── api/
│   ├── __init__.py              # NEW: empty
│   ├── app.py                   # NEW: FastAPI app factory with lifespan
│   ├── deps.py                  # NEW: dependency injection (DB pool, repos)
│   ├── routes_opportunities.py  # NEW: /api/opportunities, /api/pairs/*
│   ├── routes_health.py         # NEW: /api/health/*
│   ├── routes_alerts.py         # NEW: /api/alerts
│   ├── routes_matches.py        # NEW: /api/matches
│   ├── routes_tickets.py        # NEW: /api/tickets/*
│   ├── routes_scan.py           # NEW: /api/scan (POST trigger)
│   └── static/
│       ├── index.html           # NEW: single-page dashboard
│       ├── app.js               # NEW: fetch, render, Chart.js integration
│       └── style.css            # NEW: minimal dashboard styling
├── models/
│   └── config.py                # MODIFY: add DashboardConfig to Settings
├── cli/
│   └── app.py                   # MODIFY: add `serve` command
config.example.yaml              # MODIFY: add dashboard section

tests/
├── unit/
│   └── test_api_routes.py       # NEW: ~20 tests (endpoint response shapes)
├── integration/
│   └── test_api_integration.py  # NEW: ~8 tests (full request/response cycle)
```

## Key Technical Decisions

### 1. FastAPI with Lifespan for DB Pool

Use FastAPI's `lifespan` context manager to create/destroy the asyncpg pool once at startup. Inject pool via `request.app.state.db`. No per-request connection overhead.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(app.state.config.storage.database_url)
    await db.connect()
    app.state.db = db
    yield
    await db.disconnect()
```

### 2. Route Modules by Domain

Split routes into 6 small files (<100 lines each) rather than one monolith. Each creates an `APIRouter` with a prefix:

| Module | Prefix | Endpoints |
|--------|--------|-----------|
| routes_opportunities | /api/opportunities | GET list, GET pair summaries, GET pair history |
| routes_health | /api/health | GET metrics, GET scan logs |
| routes_alerts | /api/alerts | GET recent alerts |
| routes_matches | /api/matches | GET all matches |
| routes_tickets | /api/tickets | GET pending, POST approve, POST expire |
| routes_scan | /api/scan | POST trigger scan |

### 3. Dependency Injection via `deps.py`

Single module providing `get_repo()` and `get_analytics_repo()` as FastAPI dependencies. They read `request.app.state.db.pool` and construct Repository/AnalyticsRepository instances.

```python
async def get_repo(request: Request) -> Repository:
    return Repository(request.app.state.db.pool)

async def get_analytics_repo(request: Request) -> AnalyticsRepository:
    return AnalyticsRepository(request.app.state.db.pool)
```

### 4. Vanilla JS Dashboard (No Build)

A single `index.html` with embedded `<script>` and `<style>` references to `app.js` and `style.css` in the same `/static/` directory. Chart.js loaded from CDN:

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
```

The JS fetches each API endpoint, renders tables via DOM manipulation, and creates Chart.js instances for time-series data. Auto-refresh via `setInterval(refreshAll, 30000)`.

### 5. Dashboard Layout

Single-page with a tab bar:

```
[Opportunities] [Health] [Alerts] [Tickets]
─────────────────────────────────────────────
  Tab content area
  - Tables, charts, action buttons
─────────────────────────────────────────────
  Footer: Last refreshed: HH:MM:SS | [Refresh Now]
```

- **Opportunities tab**: Table of recent opps + click row to expand pair detail with Chart.js spread chart
- **Health tab**: Metrics cards (total scans, avg duration, error rate) + hourly bar chart
- **Alerts tab**: Scrollable alert list with type dropdown filter
- **Tickets tab**: Pending tickets table with Approve/Expire buttons

### 6. `serve` CLI Command

```python
@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Start the dashboard web server."""
    import uvicorn
    from arb_scanner.api.app import create_app
    config = load_config()
    api_app = create_app(config)
    uvicorn.run(api_app, host=host, port=port)
```

### 7. Error Handling

All endpoints wrapped in try/except. DB errors return 503:

```python
@router.get("/api/opportunities")
async def list_opportunities(repo: Repository = Depends(get_repo)):
    try:
        return await repo.get_recent_opportunities(limit=50)
    except Exception:
        raise HTTPException(503, "Database unavailable")
```

### 8. Static File Serving

FastAPI mounts static files and serves `index.html` at root:

```python
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def root():
    return FileResponse(static_dir / "index.html")
```

## API Response Schemas

All responses use existing Pydantic models serialized via `model_dump()` or raw dicts from repository methods. Key response shapes:

```
GET /api/opportunities?limit=20
→ [{ id, poly_event_id, kalshi_event_id, buy_venue, sell_venue,
     net_spread_pct, max_size, annualized_return, depth_risk, detected_at }]

GET /api/pairs/summaries?hours=24&top=10
→ [{ poly_event_id, kalshi_event_id, peak_spread, avg_spread,
     total_detections, first_seen, last_seen }]

GET /api/pairs/{poly_id}/{kalshi_id}/history?hours=24
→ [{ detected_at, net_spread_pct, annualized_return, depth_risk, max_size }]

GET /api/health?hours=24
→ [{ hour, scan_count, avg_duration_s, total_llm_calls, total_opps, total_errors }]

GET /api/alerts?limit=20&type=convergence
→ [{ alert_type, poly_event_id, kalshi_event_id, spread_before,
     spread_after, message, dispatched_at }]

POST /api/scan
→ { scan_id, timestamp, markets_scanned, candidate_pairs, opportunities }

POST /api/tickets/{arb_id}/approve
→ { status: "approved" }
```

## Config YAML Addition

```yaml
dashboard:
  enabled: true
  host: "0.0.0.0"
  port: 8000
```
