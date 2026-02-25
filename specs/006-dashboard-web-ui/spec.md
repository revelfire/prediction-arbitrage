# Feature Specification: Dashboard Web UI

**Feature**: `006-dashboard-web-ui` | **Date**: 2026-02-24 | **Status**: Draft
**Depends on**: `005-trend-alerting` (complete)

## Problem Statement

All scanner data is only accessible via CLI commands or raw database queries. The operator has no visual overview of active opportunities, spread trends, scanner health, or alert history. Reviewing arbitrage positions requires running multiple commands (`report`, `stats`, `history`, `alerts`) and mentally assembling the picture. There is no way to approve or expire execution tickets without direct DB access.

## Solution

Add a FastAPI-based REST API and a lightweight static HTML/JS dashboard served from the same process. The API wraps existing repository methods as JSON endpoints. The dashboard provides a single-page overview with tabs for opportunities, spread charts, scanner health, trend alerts, and ticket management. No build step or npm — the frontend uses vanilla JS with a lightweight charting library loaded from CDN.

## User Stories

### US1: Opportunity Overview (P1)
**As a** market operator, **I want** a dashboard showing all active arbitrage opportunities with spreads, sizes, and confidence scores, **so that** I can assess positions at a glance.

### US2: Spread Time-Series Chart (P1)
**As a** market operator, **I want** to see a time-series chart of spread history for any pair, **so that** I can visually identify convergence/divergence trends.

### US3: Scanner Health Dashboard (P1)
**As a** system operator, **I want** a health panel showing scan success rates, durations, LLM call counts, and error rates, **so that** I can monitor system reliability.

### US4: Trend Alert Feed (P1)
**As a** market operator, **I want** a live feed of trend alerts with type filtering, **so that** I can see convergence, divergence, and health alerts in one place.

### US5: Ticket Management (P2)
**As a** market operator, **I want** to approve or expire execution tickets from the dashboard, **so that** I don't need direct DB access for ticket workflow.

### US6: On-Demand Scan (P2)
**As a** market operator, **I want** a "Run Scan" button that triggers an immediate scan cycle, **so that** I can refresh data without waiting for the next watch interval.

## Functional Requirements

### FR-001: FastAPI Application
The system MUST add a FastAPI application in `src/arb_scanner/api/` with async endpoints wrapping existing repository methods. The app MUST share the same database connection logic and Pydantic models as the CLI.

### FR-002: API Endpoints
The API MUST expose these endpoints:

**Data (GET):**
- `GET /api/opportunities?limit=N&since=ISO` — Recent arb opportunities
- `GET /api/pairs/summaries?hours=N&top=N` — Pair-level aggregated stats
- `GET /api/pairs/{poly_id}/{kalshi_id}/history?hours=N` — Spread history for one pair
- `GET /api/matches?include_expired=bool&min_confidence=float` — Cached match results
- `GET /api/health?hours=N` — Scanner health metrics (hourly buckets)
- `GET /api/health/scans?limit=N` — Recent scan logs
- `GET /api/alerts?limit=N&type=TYPE` — Recent trend alerts
- `GET /api/tickets?status=pending` — Execution tickets

**Actions (POST):**
- `POST /api/scan` — Trigger an immediate scan cycle (returns scan result JSON)
- `POST /api/tickets/{arb_id}/approve` — Set ticket status to "approved"
- `POST /api/tickets/{arb_id}/expire` — Set ticket status to "expired"

### FR-003: Static Dashboard
The system MUST serve a single-page HTML dashboard at `GET /` with these views:
- **Opportunities** — Table of active arbs with sortable columns
- **Pair Detail** — Spread time-series chart (selected from opportunities table)
- **Health** — Scanner metrics panel with scan count, avg duration, error rate
- **Alerts** — Scrollable feed of trend alerts with type filter dropdown
- **Tickets** — Pending tickets with Approve/Expire action buttons

### FR-004: Chart Library
The dashboard MUST use Chart.js (loaded from CDN) for time-series charts. No npm, no build step.

### FR-005: Auto-Refresh
The dashboard MUST auto-refresh data every 30 seconds via JavaScript fetch polling. A manual refresh button MUST also be available.

### FR-006: CLI Integration
The system MUST add a `serve` CLI command: `arb-scanner serve --host 0.0.0.0 --port 8000`. This starts the FastAPI app with uvicorn.

### FR-007: Dashboard Config
The system MUST add `DashboardConfig` to settings with fields: `enabled` (bool, default true), `host` (str, default "0.0.0.0"), `port` (int, default 8000).

### FR-008: API Error Handling
All API endpoints MUST return structured JSON errors with appropriate HTTP status codes. Database connection failures MUST return 503 Service Unavailable.

## Success Criteria

- SC-001: `arb-scanner serve` starts and serves the dashboard at http://localhost:8000
- SC-002: `/api/opportunities` returns valid JSON matching the ArbOpportunity schema
- SC-003: Spread history chart renders time-series data for a selected pair
- SC-004: Ticket approve/expire buttons update ticket status via API
- SC-005: Dashboard auto-refreshes every 30 seconds
- SC-006: All existing 403 mocked tests still pass
- SC-007: All quality gates pass (ruff, mypy --strict, 70% coverage)

## Out of Scope

- Authentication/authorization (no login, internal tool only)
- WebSocket real-time streaming (polling is sufficient for v1)
- React/Vue/Angular — vanilla JS only, no build toolchain
- Mobile-responsive design (desktop-first internal tool)
- Deployment configuration (Docker, systemd, etc.)
