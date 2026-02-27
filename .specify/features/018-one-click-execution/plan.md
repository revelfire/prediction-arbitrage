# 018 — One-Click Execution: Implementation Plan

## Context

The system detects arbitrage opportunities and generates execution tickets, but operators must manually navigate to each venue and place orders by hand. This 30-90 second friction window often exceeds the opportunity's lifespan. This feature adds one-click execution from the dashboard with capital preservation guardrails: percentage-of-balance sizing, liquidity validation, exposure limits, and daily loss caps.

## Design Decisions

1. **New `execution/` subpackage** — Venue executors, capital manager, and order tracking live in `src/arb_scanner/execution/`. This is a new domain (order placement) distinct from detection (`engine/`) and data ingestion (`ingestion/`).

2. **Venue executors as protocol classes** — `PolymarketExecutor` and `KalshiExecutor` implement a shared `VenueExecutor` protocol (async `place_order`, `cancel_order`, `get_balance`, `get_book_depth`). This keeps venue-specific signing/SDK details isolated.

3. **`CapitalManager` as a stateful singleton** — Tracks balances, open positions, daily P&L, and cooldown state in-memory with DB persistence. All pre-execution validation flows through this single gatekeeper. Initialized at app startup with a balance refresh.

4. **Walk-the-book VWAP** — Liquidity validation doesn't just check top-of-book; it walks the full order book to compute volume-weighted average price for the requested size. This gives operators an honest slippage estimate before they click.

5. **Preflight panel in existing ticket detail modal** — Rather than a separate execution page, the preflight check renders inside the existing ticket detail modal. This keeps the workflow tight: review ticket → see preflight → click execute.

6. **`py-clob-client` for Polymarket** — The official SDK handles EIP-712 signing, order creation, and CLOB interaction. We wrap it in our executor rather than reimplementing signing.

7. **RSA-PSS signing for Kalshi** — Direct HTTP with `cryptography` library. Kalshi's auth is straightforward: sign `timestamp + method + path` with RSA-PSS, send in headers.

8. **Execution config as new top-level section** — `ExecutionConfig` model in `models/config.py` with all capital preservation parameters. Master kill switch defaults to `enabled: false`.

## Task Breakdown

### Phase 1: Configuration + Models

**Task 1: `ExecutionConfig` model**
- Add `ExecutionConfig` to `models/config.py` with all fields from spec config section
- Nested `PolyExecConfig` and `KalshiExecConfig` for venue-specific settings
- Add `execution: ExecutionConfig` to `Settings` (optional, defaults to disabled)
- File: `src/arb_scanner/models/config.py`

**Task 2: Execution data models**
- `ExecutionOrder` model (per-leg order record)
- `ExecutionResult` model (aggregate result for both legs)
- `PreflightResult` model (validation results for UI)
- `PreflightCheck` enum-like for individual check statuses
- File: `src/arb_scanner/models/execution.py` (NEW, ~120 lines)

**Task 3: Migration `020_execution_orders.sql`**
- `execution_orders` table (id, arb_id, venue, venue_order_id, side, requested_price, fill_price, size_usd, size_contracts, status, error_message, timestamps)
- `execution_results` table (id, arb_id, total_cost, actual_spread, slippage, poly_order_id, kalshi_order_id, status, timestamps)
- Indexes on arb_id, status, created_at
- File: `src/arb_scanner/storage/migrations/020_execution_orders.sql`

### Phase 2: Venue Executors

**Task 4: Executor protocol + base utilities**
- `VenueExecutor` protocol: `place_order()`, `cancel_order()`, `get_balance()`, `get_book_depth()`
- `OrderRequest` / `OrderResponse` dataclasses
- VWAP book-walking helper: `estimate_vwap(book, size_contracts) -> (vwap, depth_available)`
- File: `src/arb_scanner/execution/__init__.py` (re-exports), `src/arb_scanner/execution/base.py` (NEW, ~80 lines)

**Task 5: Polymarket executor**
- Wraps `py-clob-client` SDK (`ClobClient`)
- `place_order()`: Create GTC limit order via `create_and_post_order()`
- `cancel_order()`: Cancel via order ID
- `get_balance()`: USDC balance on Polygon
- `get_book_depth()`: Full order book via CLOB `/book` endpoint
- Credential loading from `POLY_PRIVATE_KEY` env var
- File: `src/arb_scanner/execution/polymarket_executor.py` (NEW, ~150 lines)

**Task 6: Kalshi executor**
- Direct httpx with RSA-PSS signing
- `_sign_request()`: timestamp_ms + method + path → RSA-PSS signature
- `place_order()`: POST `/portfolio/orders` with signed request
- `cancel_order()`: DELETE `/portfolio/orders/{order_id}`
- `get_balance()`: GET `/portfolio/balance`
- `get_book_depth()`: GET `/orderbook` (full depth)
- Credential loading from `KALSHI_API_KEY_ID` + `KALSHI_RSA_PRIVATE_KEY_PATH` env vars
- File: `src/arb_scanner/execution/kalshi_executor.py` (NEW, ~180 lines)

### Phase 3: Capital Manager

**Task 7: Capital manager**
- `CapitalManager(config, poly_executor, kalshi_executor)`
- `refresh_balances()` — fetch live balances from both venues
- `suggest_size(ticket) -> Decimal` — pct_of_balance of min(poly_bal, kalshi_bal), capped
- `check_exposure() -> (current_usd, remaining_usd, blocked: bool)`
- `check_daily_pnl() -> (daily_pnl, limit, blocked: bool)`
- `check_cooldown() -> (active: bool, remaining_seconds: int)`
- `check_concentration(market_id) -> (current_usd, limit_usd, blocked: bool)`
- `record_fill(order) -> None` — update in-memory state after execution
- `record_loss(pnl) -> None` — update daily P&L and trigger cooldown if negative
- File: `src/arb_scanner/execution/capital_manager.py` (NEW, ~200 lines)

**Task 8: Liquidity validator**
- `validate_liquidity(poly_book, kalshi_book, size, config) -> LiquidityResult`
- Walk both order books to compute VWAP for requested size
- Return: estimated slippage per leg, depth available, max absorbable size, pass/fail
- File: `src/arb_scanner/execution/liquidity.py` (NEW, ~100 lines)

### Phase 4: Execution Orchestrator + Storage

**Task 9: Execution repository**
- `ExecutionRepository(pool)` following existing pattern
- `insert_order()`, `update_order_status()`, `get_orders_for_ticket()`, `get_open_orders()`, `insert_result()`, `get_result()`, `get_daily_pnl()`, `count_open_positions()`
- Query constants in `_execution_queries.py`
- Files: `src/arb_scanner/storage/execution_repository.py` (NEW, ~150 lines), `src/arb_scanner/storage/_execution_queries.py` (NEW, ~120 lines)

**Task 10: Execution orchestrator**
- `ExecutionOrchestrator(config, capital_mgr, poly_exec, kalshi_exec, repo)`
- `preflight(ticket) -> PreflightResult` — run all validation checks, return structured result for UI
- `execute(ticket, size_usd) -> ExecutionResult` — place both legs concurrently with `asyncio.gather`, record results
- `cancel_order(order_id) -> bool` — cancel a single pending order on its venue
- Concurrent leg placement with individual error handling (one can fail while other succeeds)
- File: `src/arb_scanner/execution/orchestrator.py` (NEW, ~200 lines)

### Phase 5: API Routes

**Task 11: Execution API routes**
- `GET /api/execution/status` — credential status, balances, exposure, daily P&L, cooldown
- `POST /api/execution/preflight/{arb_id}` — run preflight, return validation results + suggested size
- `POST /api/execution/execute/{arb_id}` — place both legs, body: `{ "size_usd": 50.0 }`
- `GET /api/execution/orders/{arb_id}` — execution result for a ticket
- `DELETE /api/execution/orders/{order_id}` — cancel pending order
- Dependency: `get_execution_orchestrator()` in deps.py
- Files: `src/arb_scanner/api/routes_execution.py` (NEW, ~200 lines), `src/arb_scanner/api/deps.py` (MODIFY)

**Task 12: Register execution router in app.py**
- Import and include `routes_execution.router`
- Initialize executors + capital manager in lifespan (only when `execution.enabled`)
- Store on `app.state` for dependency injection
- File: `src/arb_scanner/api/app.py` (MODIFY)

### Phase 6: Dashboard UI

**Task 13: Preflight panel in ticket detail modal**
- After existing ticket detail content, add "Execute Trade" section (only when execution enabled)
- "Run Preflight" button → calls `POST /api/execution/preflight/{arb_id}` → renders check results
- Validation checks shown as green/red rows: credentials, balances, price freshness, spread, slippage, liquidity depth, exposure, daily P&L, cooldown, concentration
- Suggested size (editable), max size, balance info, estimated slippage per leg
- "Execute" button (disabled until all checks pass)
- File: `src/arb_scanner/api/static/index.html` (MODIFY)

**Task 14: Execution progress + result display**
- After clicking Execute, show progress: "Submitting Leg 1..." → "Submitting Leg 2..." → "Complete" or "Partial"
- Result panel: order IDs, fill prices, actual cost, actual spread, slippage
- Partial execution warning banner (red) with manual cancel option
- File: `src/arb_scanner/api/static/app.js` (MODIFY, +250 lines)

**Task 15: Execution status indicator**
- Header or footer shows: "Execution: Enabled ✓ | Poly: $X,XXX | Kalshi: $X,XXX | Exposure: X/Y"
- Or "Execution: Disabled" when config says disabled
- Refreshes with global auto-refresh
- File: `src/arb_scanner/api/static/app.js` (within Task 14 scope)

**Task 16: Execution styles**
- Preflight check row styles (pass/fail/warn)
- Progress indicator animation
- Partial execution warning banner
- File: `src/arb_scanner/api/static/style.css` (MODIFY)

### Phase 7: Tests

**Task 17: Unit tests — models**
- `ExecutionConfig` defaults and validation
- `PreflightResult` construction
- `ExecutionOrder` / `ExecutionResult` models
- File: `tests/unit/test_execution_models.py` (NEW)

**Task 18: Unit tests — capital manager**
- `suggest_size()` respects pct_of_balance, per-venue cap, hard cap
- `check_exposure()` blocks when over limit
- `check_daily_pnl()` blocks after loss limit
- `check_cooldown()` active after losing trade
- `check_concentration()` blocks per-market overexposure
- File: `tests/unit/test_capital_manager.py` (NEW)

**Task 19: Unit tests — liquidity validator**
- VWAP calculation on mock order book
- Slippage estimation
- Min depth rejection
- Suggested size reduction when book is thin
- File: `tests/unit/test_liquidity.py` (NEW)

**Task 20: Unit tests — orchestrator**
- Preflight passes/fails correctly
- Execute places both legs concurrently
- Partial execution detection (one leg fails)
- Cancel delegates to correct venue executor
- File: `tests/unit/test_execution_orchestrator.py` (NEW)

**Task 21: Unit tests — API routes**
- GET /status returns credential flags + balances
- POST /preflight returns validation results
- POST /execute records results
- Execution disabled → 403 on execute
- File: `tests/unit/test_execution_routes.py` (NEW)

**Task 22: Unit tests — execution repository**
- Mocked pool, query delegation
- File: `tests/unit/test_execution_repository.py` (NEW)

**Task 23: Quality gates**
- ruff check, ruff format, mypy --strict, pytest, coverage ≥70%

## Key Files

| File | Action | Est. Lines |
|------|--------|------------|
| `models/config.py` | MODIFY | +50 |
| `models/execution.py` | CREATE | ~120 |
| `storage/migrations/020_execution_orders.sql` | CREATE | ~35 |
| `execution/__init__.py` | CREATE | ~10 |
| `execution/base.py` | CREATE | ~80 |
| `execution/polymarket_executor.py` | CREATE | ~150 |
| `execution/kalshi_executor.py` | CREATE | ~180 |
| `execution/capital_manager.py` | CREATE | ~200 |
| `execution/liquidity.py` | CREATE | ~100 |
| `execution/orchestrator.py` | CREATE | ~200 |
| `storage/_execution_queries.py` | CREATE | ~120 |
| `storage/execution_repository.py` | CREATE | ~150 |
| `api/routes_execution.py` | CREATE | ~200 |
| `api/deps.py` | MODIFY | +15 |
| `api/app.py` | MODIFY | +20 |
| `api/static/index.html` | MODIFY | +60 |
| `api/static/app.js` | MODIFY | +250 |
| `api/static/style.css` | MODIFY | +30 |
| `tests/unit/test_execution_models.py` | CREATE | ~80 |
| `tests/unit/test_capital_manager.py` | CREATE | ~150 |
| `tests/unit/test_liquidity.py` | CREATE | ~100 |
| `tests/unit/test_execution_orchestrator.py` | CREATE | ~150 |
| `tests/unit/test_execution_routes.py` | CREATE | ~120 |
| `tests/unit/test_execution_repository.py` | CREATE | ~80 |

## Dependencies

New packages to add to `pyproject.toml`:
- `py-clob-client` — Polymarket CLOB SDK (order creation, signing, balance)
- `cryptography` — RSA key loading + PSS signing for Kalshi

Both `py-clob-client` and `cryptography` are pip-installable. `py-clob-client` bundles `eth-account`/`web3` for EIP-712 signing.

## Verification

1. `uv run pytest tests/ -x --tb=short` — all tests pass
2. `uv run ruff check src/ tests/` — zero lint errors
3. `uv run ruff format --check src/ tests/` — clean
4. `uv run mypy src/ --strict` — zero type errors
5. Coverage ≥70%
6. With `execution.enabled: false` (default): no execution endpoints active, dashboard shows "Execution: Disabled"
7. With `execution.enabled: true` + credentials: preflight panel renders, validation checks run, execute button works
8. Capital guardrails: size respects pct_of_balance, exposure cap blocks excess, daily loss limit stops trading, cooldown enforces pause
