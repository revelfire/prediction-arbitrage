# Plan: 014 — Live Price Ticker

## Current State Audit

| File | Lines | Status |
|------|-------|--------|
| `orchestrator.py` | 288 | OK |
| `_orch_processing.py` | 188 | OK — will add ring buffer push |
| `routes_flippening.py` | 156 | OK |
| `app.py` | 87 | OK — will register new router |
| `index.html` | 234 | OK — will add Live Prices section |
| `app.js` | 472 | OK — will add SSE/ticker logic |
| `style.css` | 224 | OK — will add ticker styles |

## Phase 1: PriceRingBuffer (`price_ring_buffer.py`)

New module `src/arb_scanner/flippening/price_ring_buffer.py`:
- `PriceTick` dataclass with market_id, market_title, category, category_type, yes_mid, baseline_yes, deviation_pct, spread, timestamp, book_depth_bids, book_depth_asks
- `PriceRingBuffer` class using `dict[str, deque[PriceTick]]` with threading.Lock for thread safety
- Methods: `push()`, `get_latest()`, `get_history()`, `market_count()`
- Module-level singleton `_shared_buffer` with `get_shared_buffer()` and `set_shared_buffer()` accessors

## Phase 2: SSE Endpoint (`routes_price_stream.py`)

New module `src/arb_scanner/api/routes_price_stream.py`:
- FastAPI `APIRouter` with `GET /api/flippenings/price-stream`
- Uses `StreamingResponse` with `media_type="text/event-stream"`
- Reads from the shared ring buffer singleton
- Sends `event: status` with `data: {"status":"idle"}` when buffer is empty/None
- Sends `event: snapshot` with all-market JSON when data is available
- Polls buffer every 1s, sending only changed data

## Phase 3: Orchestrator Integration

- In `_orch_processing.py`, after `game_mgr.process()`, push a `PriceTick` to the shared ring buffer
- Build tick from PriceUpdate + GameState (for market_title, baseline, category)
- Import shared buffer via `get_shared_buffer()`

## Phase 4: Dashboard UI

- In `index.html`, add a "Live Prices" section above "Active Flippenings" in the flippenings tab
- In `app.js`, add SSE connection logic (`EventSource`) that renders a live price table
- In `style.css`, add styles for sparkline canvases, deviation color classes, status banner

## Phase 5: Tests

- `tests/unit/test_price_ring_buffer.py`: push/get, maxlen eviction, empty buffer, thread safety
- `tests/unit/test_price_stream.py`: SSE idle response, SSE with data

## Constraint Compliance

- All new modules under 300 lines
- All functions under 50 lines
- Type hints + docstrings on all public functions
- mypy --strict clean
- ruff clean
