# Tasks: 014 — Live Price Ticker

## Phase 1: PriceRingBuffer

- [x] T001: Create `price_ring_buffer.py` with PriceTick dataclass
- [x] T002: Implement PriceRingBuffer class (push, get_latest, get_history, market_count)
- [x] T003: Add module-level singleton accessors (get_shared_buffer, set_shared_buffer)

## Phase 2: SSE Endpoint

- [x] T004: Create `routes_price_stream.py` with SSE streaming endpoint
- [x] T005: Register price-stream router in `app.py`

## Phase 3: Orchestrator Integration

- [x] T006: Add ring buffer push in `_orch_processing.py` after process_update
- [x] T007: Create and set shared buffer in `orchestrator.py` at startup

## Phase 4: Dashboard UI

- [x] T008: Add Live Prices HTML section to `index.html` flippenings tab
- [x] T009: Add SSE connection + live price table rendering in `app.js`
- [x] T010: Add ticker-specific CSS styles in `style.css`

## Phase 5: Tests

- [x] T011: Write `test_price_ring_buffer.py` unit tests
- [x] T012: Write `test_price_stream.py` SSE endpoint tests

## Quality Gates

- [x] T013: ruff check clean
- [x] T014: ruff format clean
- [x] T015: mypy --strict clean
- [x] T016: pytest all tests pass
