# 016 — WebSocket Telemetry Monitor: Implementation Plan

## Approach

Add a "WS Health" tab to the dashboard showing real-time WebSocket connection telemetry. Three new REST API endpoints provide telemetry data (latest, history, events). The dashboard tab includes a connection status banner, throughput chart, schema match gauge, stall/reconnect log, and order book cache metrics panel.

## Components

### 1. Pydantic Models (`models/ws_telemetry.py`)
- `WsTelemetrySnapshot`: Latest telemetry data response model.
- `WsTelemetryEvent`: Stall/reconnect event model.

### 2. Repository Extensions (`storage/_ws_telemetry_queries.py`)
- New SQL queries for history (time-windowed) and events.
- New repository methods on `FlippeningRepository`.

### 3. API Routes (`api/routes_ws_telemetry.py`)
- `GET /api/flippening/ws-telemetry` — Latest snapshot.
- `GET /api/flippening/ws-telemetry/history?hours=24` — Historical.
- `GET /api/flippening/ws-telemetry/events?limit=50` — Stall/reconnect events.

### 4. Route Registration (`api/app.py`)
- Include ws_telemetry router.

### 5. Dashboard Tab (`static/index.html`, `static/app.js`, `static/style.css`)
- "WS Health" tab button.
- Connection status banner (FR-001).
- Throughput chart with rolling average overlay (FR-002).
- Schema match rate gauge (FR-003).
- Stall and reconnect log table (FR-004).
- Order book cache metrics panel (FR-005).

### 6. Tests (`tests/unit/test_ws_telemetry_dashboard.py`)
- API route tests (3 endpoints, error handling).
- Pydantic model tests.

## Risks
- ws_telemetry table already exists (migration 014); no new migration needed.
- Query for "events" must be derived from telemetry snapshots (connection_state changes), not a separate events table.
