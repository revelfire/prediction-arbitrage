# Tasks: Dashboard Web UI

**Input**: `/specs/006-dashboard-web-ui/spec.md`, `/specs/006-dashboard-web-ui/plan.md`
**Depends on**: `005-trend-alerting` (complete)

## Autonomous Execution Notes

- Fix bugs as you find them
- Greenfield pre-1.0 — modify existing code directly
- All 403 existing mocked tests MUST continue to pass
- New dependencies: fastapi, uvicorn[standard] — add to pyproject.toml
- mypy --strict must pass with FastAPI typing

---

## Phase 1: Dependencies + Config + App Factory

- [x] T001 Add `fastapi` and `uvicorn[standard]` to `pyproject.toml` dependencies. Run `uv sync` to install.
- [x] T002 [P] Add `DashboardConfig` to `src/arb_scanner/models/config.py`: fields `enabled` (bool, default True), `host` (str, default "0.0.0.0"), `port` (int, default 8000). Add `dashboard: DashboardConfig` to `Settings` with default.
- [x] T003 [P] Extend `config.example.yaml` with `dashboard` section: enabled, host, port.
- [x] T004 Create `src/arb_scanner/api/__init__.py` (empty) and `src/arb_scanner/api/app.py` with `create_app(config: Settings) -> FastAPI`:
  - Use `@asynccontextmanager` lifespan to create/destroy Database pool
  - Store `config` and `db` on `app.state`
  - Mount `/static` directory from `pathlib.Path(__file__).parent / "static"`
  - Serve `index.html` at `GET /`
  - Include all route modules (empty routers for now)
  - Add structlog logger
- [x] T005 [P] Create `src/arb_scanner/api/deps.py` with dependency injection helpers:
  - `get_repo(request: Request) -> Repository`
  - `get_analytics_repo(request: Request) -> AnalyticsRepository`
  - `get_config(request: Request) -> Settings`
  - All read from `request.app.state`
- [x] T006 Create placeholder `src/arb_scanner/api/static/index.html` with minimal HTML shell (title, Chart.js CDN script tag, links to app.js and style.css). Create empty `src/arb_scanner/api/static/app.js` and `src/arb_scanner/api/static/style.css`.

**Quality gate**: All 5 gates. Existing tests must pass. `uv sync` succeeds.

---

## Phase 2: API Routes

- [x] T007 Create `src/arb_scanner/api/routes_opportunities.py` with APIRouter (prefix `/api`):
  - `GET /api/opportunities` — query params: `limit` (int, default 50), `since` (str|None). Returns list of opportunity dicts from repo.
  - `GET /api/pairs/summaries` — query params: `hours` (int, default 24), `top` (int, default 10). Returns list of PairSummary dicts.
  - `GET /api/pairs/{poly_id}/{kalshi_id}/history` — query param: `hours` (int, default 24). Returns list of SpreadSnapshot dicts.
  - All with try/except returning HTTPException(503) on DB errors.
- [x] T008 [P] Create `src/arb_scanner/api/routes_health.py` with APIRouter:
  - `GET /api/health` — query param: `hours` (int, default 24). Returns list of ScanHealthSummary dicts.
  - `GET /api/health/scans` — query param: `limit` (int, default 20). Returns list of scan log dicts.
- [x] T009 [P] Create `src/arb_scanner/api/routes_alerts.py` with APIRouter:
  - `GET /api/alerts` — query params: `limit` (int, default 20), `type` (str|None). Returns list of TrendAlert dicts.
- [x] T010 [P] Create `src/arb_scanner/api/routes_matches.py` with APIRouter:
  - `GET /api/matches` — query params: `include_expired` (bool, default False), `min_confidence` (float, default 0.0). Returns list of match dicts.
- [x] T011 Create `src/arb_scanner/api/routes_tickets.py` with APIRouter:
  - `GET /api/tickets` — query param: `status` (str, default "pending"). Returns list of ticket dicts.
  - `POST /api/tickets/{arb_id}/approve` — calls repo.update_ticket_status(arb_id, "approved"). Returns `{"status": "approved"}`.
  - `POST /api/tickets/{arb_id}/expire` — calls repo.update_ticket_status(arb_id, "expired"). Returns `{"status": "expired"}`.
- [x] T012 Create `src/arb_scanner/api/routes_scan.py` with APIRouter:
  - `POST /api/scan` — calls `run_scan(config, dry_run=False)`. Returns scan result dict (excluding `_raw_opps`). Catches exceptions, returns 500 with error message.
- [x] T013 Wire all routers into `create_app()` in `api/app.py`: import and `app.include_router()` for each route module.

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 3: CLI Command + API Tests

- [x] T014 Add `serve` command to `src/arb_scanner/cli/app.py`:
  - Options: `--host` (default "0.0.0.0"), `--port` (default 8000)
  - Loads config, calls `create_app(config)`, runs `uvicorn.run(api_app, host=host, port=port)`
- [x] T015 [P] Create `tests/unit/test_api_routes.py` (~20 tests) using FastAPI `TestClient`:
  - Test `GET /` returns 200 with HTML content
  - Test `GET /api/opportunities` with mocked repo returns JSON list
  - Test `GET /api/pairs/summaries` with mocked analytics repo
  - Test `GET /api/pairs/{poly_id}/{kalshi_id}/history` with mocked repo
  - Test `GET /api/health` with mocked analytics repo
  - Test `GET /api/health/scans` with mocked analytics repo
  - Test `GET /api/alerts` with mocked analytics repo
  - Test `GET /api/alerts?type=convergence` filters by type
  - Test `GET /api/matches` with mocked repo
  - Test `GET /api/tickets` with mocked repo
  - Test `POST /api/tickets/{arb_id}/approve` with mocked repo
  - Test `POST /api/tickets/{arb_id}/expire` with mocked repo
  - Test `POST /api/scan` with mocked run_scan
  - Test DB error returns 503 on opportunity endpoint
  - Test DB error returns 503 on health endpoint
  - Test query param `limit` is respected
  - Test query param `hours` is respected
  - Test query param `since` is parsed correctly
  - Test unknown route returns 404
  - Test serve command registers in CLI help
  Note: Use `unittest.mock.AsyncMock` to mock repository methods. Override FastAPI dependencies with `app.dependency_overrides`.

**Quality gate**: All 5 gates.

---

## Phase 4: Dashboard Frontend

- [x] T016 Build `src/arb_scanner/api/static/style.css`:
  - CSS variables for colors (dark theme: #1a1a2e background, #16213e panels, #0f3460 accent, #e94560 alert)
  - Tab bar styling (horizontal tabs, active state)
  - Table styles (striped rows, hover highlight, sortable headers)
  - Card/panel styles for health metrics
  - Alert feed styling (colored left border per alert type)
  - Chart container responsive sizing
  - Footer bar with refresh status
  - Button styles for Approve/Expire actions (green/red)
- [x] T017 Build `src/arb_scanner/api/static/app.js` — core framework:
  - Tab switching logic (show/hide tab content divs)
  - `fetchJSON(url)` helper that calls fetch, handles errors, shows status
  - `refreshAll()` function that fetches all active tab data
  - `setInterval(refreshAll, 30000)` for auto-refresh
  - Manual refresh button handler
  - `formatPercent(val)`, `formatUSD(val)`, `formatTime(iso)` helpers
  - Last-refreshed timestamp display in footer
- [x] T018 Build `src/arb_scanner/api/static/app.js` — Opportunities tab:
  - `renderOpportunities(data)` — builds HTML table from `/api/opportunities` response
  - Columns: Contract, Buy, Sell, Spread %, Size, Confidence, Annualized, Time
  - Row click handler to load pair detail (spread chart)
  - `renderPairChart(polyId, kalshiId)` — fetches `/api/pairs/{polyId}/{kalshiId}/history`, creates Chart.js line chart (x=time, y=spread %)
  - Pair summaries section below the table
- [x] T019 Build `src/arb_scanner/api/static/app.js` — Health tab:
  - `renderHealth(data, scans)` — metric cards (total scans, avg duration, total LLM calls, total errors) + hourly bar chart of scan counts
  - Recent scans table (started_at, duration, markets fetched, opps found, errors)
- [x] T020 Build `src/arb_scanner/api/static/app.js` — Alerts tab:
  - `renderAlerts(data)` — scrollable list of alerts
  - Each alert shows: type badge (colored), pair, spread before → after, message, timestamp
  - Type filter dropdown that re-fetches with `?type=` param
- [x] T021 Build `src/arb_scanner/api/static/app.js` — Tickets tab:
  - `renderTickets(data)` — table of pending tickets
  - Columns: Arb ID, Expected Cost, Expected Profit, Status, Created, Actions
  - Approve button → POST `/api/tickets/{arb_id}/approve`, refresh
  - Expire button → POST `/api/tickets/{arb_id}/expire`, refresh
- [x] T022 Build `src/arb_scanner/api/static/index.html` — full page structure:
  - Tab bar with Opportunities, Health, Alerts, Tickets tabs
  - Content area divs for each tab
  - Chart.js CDN script tag
  - Links to style.css and app.js
  - Footer with last-refreshed time and Refresh Now button
  - Run Scan button in header area

**Quality gate**: All 5 gates. Manually verify dashboard renders at localhost:8000 if possible.

---

## Phase 5: Integration Tests + Polish

- [x] T023 [P] Create `tests/integration/test_api_integration.py` (~8 tests):
  - Test full request cycle: create_app with mocked DB, fetch opportunities, verify JSON shape
  - Test pair history endpoint returns SpreadSnapshot-compatible dicts
  - Test alert type filter works end-to-end
  - Test ticket approve updates status
  - Test scan trigger returns scan result shape
  - Test static file serving (GET / returns HTML)
  - Test 503 on database connection failure
  - Test CORS headers if needed (or verify no CORS needed for same-origin)
- [x] T024 Run full quality gate suite. Fix any failures. Verify coverage >=70%.
- [x] T025 Update CLAUDE.md: add dashboard section, note `serve` command, document API endpoints, mention DashboardConfig.

**Quality gate**: All 5 gates green. Final verification.

---

## Total: 25 tasks across 5 phases
