# 016 — WebSocket Telemetry Monitor: Tasks

## Tasks

- [x] T016-01: Create Pydantic models (`models/ws_telemetry.py`) for WsTelemetrySnapshot and WsTelemetryEvent.
- [x] T016-02: Add SQL queries (`storage/_ws_telemetry_queries.py`) for history and events.
- [x] T016-03: Add repository methods to FlippeningRepository for ws-telemetry history and events.
- [x] T016-04: Create API routes (`api/routes_ws_telemetry.py`) with 3 endpoints.
- [x] T016-05: Register ws_telemetry router in `api/app.py`.
- [x] T016-06: Add "WS Health" tab to `index.html`.
- [x] T016-07: Add WS Health tab JS logic to `app.js` (FR-001 through FR-005).
- [x] T016-08: Add WS Health CSS styles to `style.css`.
- [x] T016-09: Write unit tests for API routes, models, and query wiring.
- [x] T016-10: Run quality gates (ruff check, format, mypy, pytest, coverage).
