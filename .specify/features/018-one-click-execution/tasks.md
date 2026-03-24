# 018 — One-Click Execution: Tasks

## Phase 1: Configuration + Models

### Task 1: ExecutionConfig model
- [ ] Add `PolyExecConfig(chain_id, clob_api_url, usdc_contract)` to `models/config.py`
- [ ] Add `KalshiExecConfig(api_base_url)` to `models/config.py`
- [ ] Add `ExecutionConfig` with all fields: enabled, max_size_usd, max_slippage_pct, price_staleness_seconds, pct_of_balance, max_pct_per_venue, max_exposure_pct, min_reserve_usd, daily_loss_limit_usd, max_open_positions, max_per_market_pct, cooldown_after_loss_seconds, min_book_depth_contracts, polymarket (PolyExecConfig), kalshi (KalshiExecConfig)
- [ ] Add `execution: ExecutionConfig | None = None` to `Settings`
- [ ] All defaults match spec config section

### Task 2: Execution data models
- [ ] Create `src/arb_scanner/models/execution.py`
- [ ] `OrderSide` literal type: "buy_yes", "buy_no", "sell_yes", "sell_no"
- [ ] `OrderStatus` literal type: "submitting", "submitted", "filled", "partially_filled", "failed", "cancelled"
- [ ] `ExecutionOrder` model: id, arb_id, venue, venue_order_id, side, requested_price, fill_price, size_usd, size_contracts, status, error_message, created_at, updated_at
- [ ] `ResultStatus` literal type: "pending", "complete", "partial", "failed"
- [ ] `ExecutionResult` model: id, arb_id, total_cost_usd, actual_spread, slippage_from_ticket, poly_order_id, kalshi_order_id, status, created_at
- [ ] `PreflightCheck` model: name, passed (bool), message, value (optional numeric)
- [ ] `PreflightResult` model: checks (list[PreflightCheck]), suggested_size_usd, max_size_usd, estimated_slippage_poly, estimated_slippage_kalshi, poly_balance, kalshi_balance, all_passed (computed property)
- [ ] `OrderRequest` model: venue, side, price, size_usd, size_contracts, token_id (poly) or ticker (kalshi)
- [ ] `OrderResponse` model: venue_order_id, status, fill_price, error_message

### Task 3: Migration 020_execution_orders.sql
- [ ] Create `src/arb_scanner/storage/migrations/020_execution_orders.sql`
- [ ] `execution_orders` table: id UUID PK, arb_id TEXT NOT NULL, venue TEXT NOT NULL, venue_order_id TEXT, side TEXT NOT NULL, requested_price NUMERIC NOT NULL, fill_price NUMERIC, size_usd NUMERIC NOT NULL, size_contracts INTEGER, status TEXT NOT NULL DEFAULT 'submitting', error_message TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
- [ ] `execution_results` table: id UUID PK, arb_id TEXT NOT NULL UNIQUE, total_cost_usd NUMERIC, actual_spread NUMERIC, slippage_from_ticket NUMERIC, poly_order_id UUID, kalshi_order_id UUID, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW()
- [ ] Indexes: execution_orders(arb_id), execution_orders(status), execution_orders(created_at), execution_results(arb_id)

## Phase 2: Venue Executors

### Task 4: Executor protocol + base utilities
- [ ] Create `src/arb_scanner/execution/__init__.py` with re-exports
- [ ] Create `src/arb_scanner/execution/base.py`
- [ ] `VenueExecutor` Protocol class: `async place_order(req: OrderRequest) -> OrderResponse`, `async cancel_order(venue_order_id: str) -> bool`, `async get_balance() -> Decimal`, `async get_book_depth(token_or_ticker: str) -> dict`
- [ ] `estimate_vwap(bids_or_asks: list[dict], size_contracts: int) -> tuple[Decimal, int]` — walks the book, returns (volume-weighted avg price, contracts available)
- [ ] `contracts_from_usd(size_usd: Decimal, price: Decimal) -> int` — converts USD to contract count

### Task 5: Polymarket executor
- [ ] Create `src/arb_scanner/execution/polymarket_executor.py`
- [ ] `PolymarketExecutor(config: PolyExecConfig)`
- [ ] Load private key from `POLY_PRIVATE_KEY` env var
- [ ] Initialize `ClobClient` from `py-clob-client` with key + chain_id
- [ ] `place_order()`: map OrderRequest to ClobClient.create_and_post_order(), GTC limit
- [ ] `cancel_order()`: ClobClient cancel
- [ ] `get_balance()`: ClobClient balance query (USDC on Polygon)
- [ ] `get_book_depth()`: fetch full book via CLOB `/book` endpoint
- [ ] `is_configured() -> bool` — returns True if private key is set
- [ ] All credential values redacted from logs (structlog filter)

### Task 6: Kalshi executor
- [ ] Create `src/arb_scanner/execution/kalshi_executor.py`
- [ ] `KalshiExecutor(config: KalshiExecConfig)`
- [ ] Load RSA key from `KALSHI_RSA_PRIVATE_KEY_PATH`, key ID from `KALSHI_API_KEY_ID`
- [ ] `_sign_request(method, path, body_str) -> dict[str, str]` — RSA-PSS over timestamp_ms + method + path, return auth headers
- [ ] `place_order()`: POST `/portfolio/orders` with signed request, limit order
- [ ] `cancel_order()`: DELETE `/portfolio/orders/{order_id}` with signed request
- [ ] `get_balance()`: GET `/portfolio/balance` → parse available_balance
- [ ] `get_book_depth()`: GET `/orderbook` with full depth params
- [ ] `is_configured() -> bool` — returns True if key ID and key path are set
- [ ] All credential values redacted from logs

## Phase 3: Capital Manager

### Task 7: Capital manager
- [ ] Create `src/arb_scanner/execution/capital_manager.py`
- [ ] `CapitalManager(config: ExecutionConfig, poly: PolymarketExecutor, kalshi: KalshiExecutor)`
- [ ] `_poly_balance: Decimal`, `_kalshi_balance: Decimal` — cached, refreshed on demand
- [ ] `_daily_pnl: Decimal` — reset at UTC midnight
- [ ] `_last_loss_at: datetime | None` — tracks cooldown
- [ ] `_open_positions: dict[str, Decimal]` — market_id → deployed_usd
- [ ] `async refresh_balances() -> tuple[Decimal, Decimal]`
- [ ] `suggest_size(ticket) -> Decimal` — min(poly_bal, kalshi_bal) × pct_of_balance, capped by max_pct_per_venue of each, capped by max_size_usd
- [ ] `check_venue_reserve(size_usd) -> tuple[bool, str]` — would trade drop either venue below min_reserve?
- [ ] `check_exposure() -> tuple[Decimal, Decimal, bool]` — current, remaining, blocked
- [ ] `check_daily_pnl() -> tuple[Decimal, Decimal, bool]` — daily_pnl, limit, blocked
- [ ] `check_cooldown() -> tuple[bool, int]` — active, remaining_seconds
- [ ] `check_concentration(market_id, size_usd) -> tuple[Decimal, Decimal, bool]` — current, limit, blocked
- [ ] `record_fill(arb_id, market_id, size_usd, pnl) -> None` — update open positions + daily P&L + cooldown
- [ ] All methods are async (balance refresh may hit APIs)

### Task 8: Liquidity validator
- [ ] Create `src/arb_scanner/execution/liquidity.py`
- [ ] `LiquidityResult` model: poly_vwap, kalshi_vwap, poly_slippage, kalshi_slippage, poly_depth_contracts, kalshi_depth_contracts, max_absorbable_usd, passed (bool), warnings (list[str])
- [ ] `validate_liquidity(poly_book, kalshi_book, size_usd, price_poly, price_kalshi, config) -> LiquidityResult`
- [ ] Walk both books using `estimate_vwap()` from base.py
- [ ] Compute slippage: `vwap - top_of_book` for each leg
- [ ] If slippage > max_slippage_pct on either leg: passed=False
- [ ] If depth < min_book_depth_contracts on either leg: passed=False
- [ ] Compute max_absorbable_usd: largest size where both legs stay within slippage tolerance

## Phase 4: Execution Orchestrator + Storage

### Task 9: Execution repository
- [ ] Create `src/arb_scanner/storage/_execution_queries.py` with query constants
- [ ] `INSERT_ORDER`, `UPDATE_ORDER_STATUS`, `GET_ORDERS_FOR_TICKET`, `GET_OPEN_ORDERS`, `COUNT_OPEN_POSITIONS`
- [ ] `INSERT_RESULT`, `GET_RESULT`, `GET_DAILY_PNL` (SUM of fill results since UTC midnight)
- [ ] `GET_MARKET_EXPOSURE` (SUM of size_usd for open orders on a given market)
- [ ] Create `src/arb_scanner/storage/execution_repository.py`
- [ ] `ExecutionRepository(pool)` with methods matching each query

### Task 10: Execution orchestrator
- [ ] Create `src/arb_scanner/execution/orchestrator.py`
- [ ] `ExecutionOrchestrator(config, capital_mgr, poly_exec, kalshi_exec, exec_repo, ticket_repo)`
- [ ] `async preflight(arb_id) -> PreflightResult`:
  - Fetch ticket from ticket_repo
  - Check execution enabled
  - Check credentials configured (both venues)
  - Refresh balances
  - Check balance ≥ required (per-venue + reserve)
  - Re-fetch live prices (both venues), check staleness
  - Re-check spread still exceeds threshold
  - Run liquidity validation (walk both books)
  - Check exposure cap
  - Check daily P&L limit
  - Check cooldown
  - Check per-market concentration
  - Compute suggested size
  - Return PreflightResult with all checks + suggested size
- [ ] `async execute(arb_id, size_usd) -> ExecutionResult`:
  - Run preflight (abort if any check fails)
  - Map ticket legs to OrderRequests
  - Insert order records (status=submitting)
  - Place both legs concurrently via asyncio.gather(return_exceptions=True)
  - Update order records with results
  - Detect partial execution (one success, one failure)
  - Insert execution_result record
  - Record fill in capital manager
  - Update ticket status to "executed" (or leave "approved" if partial)
  - Return ExecutionResult
- [ ] `async cancel_order(order_id) -> bool`:
  - Look up order, determine venue
  - Call venue executor cancel
  - Update order status

## Phase 5: API Routes

### Task 11: Execution API routes
- [ ] Create `src/arb_scanner/api/routes_execution.py`
- [ ] `GET /api/execution/status` — returns: enabled, poly_configured, kalshi_configured, poly_balance, kalshi_balance, current_exposure, daily_pnl, cooldown_active, cooldown_remaining, open_positions_count
- [ ] `POST /api/execution/preflight/{arb_id}` — returns PreflightResult JSON
- [ ] `POST /api/execution/execute/{arb_id}` — body: `{"size_usd": 50.0}`, returns ExecutionResult JSON
- [ ] `GET /api/execution/orders/{arb_id}` — returns list of execution orders + result for ticket
- [ ] `DELETE /api/execution/orders/{order_id}` — cancel a pending order
- [ ] All endpoints return 403 if `execution.enabled` is false
- [ ] All endpoints return 503 if database unavailable
- [ ] Add `get_execution_orchestrator()` to `deps.py`

### Task 12: Register execution router in app.py
- [ ] Import and include `routes_execution.router`
- [ ] In lifespan: if `config.execution` is not None and `config.execution.enabled`:
  - Initialize PolymarketExecutor + KalshiExecutor
  - Initialize CapitalManager
  - Initialize ExecutionOrchestrator
  - Store on `app.state.execution_orchestrator`
- [ ] Otherwise: `app.state.execution_orchestrator = None`

## Phase 6: Dashboard UI

### Task 13: Preflight panel in ticket detail modal
- [ ] Add "Execute Trade" section to ticket detail modal (after action log)
- [ ] Only visible when execution is enabled (check via GET /api/execution/status on modal open)
- [ ] "Run Preflight" button → POST /api/execution/preflight/{arb_id}
- [ ] Render preflight checks as rows: icon (✓/✗/⚠), check name, message, value
- [ ] Show: suggested size (editable input), max size, venue balances, estimated slippage per leg, depth per leg
- [ ] "Execute" button: disabled until all checks pass, click → POST /api/execution/execute/{arb_id}

### Task 14: Execution progress + result display
- [ ] After Execute click: show progress indicator per leg ("Submitting Leg 1...", "Submitting Leg 2...")
- [ ] On completion: show result panel (order IDs, fill prices, actual cost, actual spread, slippage)
- [ ] On partial: show red warning banner "PARTIAL EXECUTION — Leg X failed" with cancel button for successful leg
- [ ] On full failure: show error details

### Task 15: Execution status indicator
- [ ] In dashboard header/footer area: "Execution: Enabled ✓ | Poly: $X | Kalshi: $X | Exposure: X% | Daily P&L: $X"
- [ ] Or "Execution: Disabled" when not enabled
- [ ] Refresh on global auto-refresh cycle
- [ ] Green/red coloring based on guardrail status

### Task 16: Execution styles
- [ ] Preflight check row styles: `.preflight-pass` (green), `.preflight-fail` (red), `.preflight-warn` (amber)
- [ ] Progress spinner animation
- [ ] Partial execution warning banner (red background, white text)
- [ ] Execution result panel styles
- [ ] Status indicator bar styles

## Phase 7: Tests

### Task 17: Unit tests — models
- [ ] `ExecutionConfig` defaults match spec
- [ ] `ExecutionConfig` validates pct ranges (0-1)
- [ ] `PreflightResult.all_passed` computed correctly
- [ ] `ExecutionOrder` / `ExecutionResult` round-trip
- [ ] File: `tests/unit/test_execution_models.py`

### Task 18: Unit tests — capital manager
- [ ] `suggest_size()` returns pct_of_balance × min balance
- [ ] `suggest_size()` respects max_pct_per_venue cap
- [ ] `suggest_size()` respects max_size_usd hard cap
- [ ] `check_exposure()` returns blocked=True when over limit
- [ ] `check_daily_pnl()` returns blocked=True after loss limit breached
- [ ] `check_cooldown()` returns active=True for configured seconds after losing trade
- [ ] `check_concentration()` blocks when market exposure exceeds limit
- [ ] `check_venue_reserve()` blocks when trade would drop below reserve
- [ ] `record_fill()` updates state correctly
- [ ] File: `tests/unit/test_capital_manager.py`

### Task 19: Unit tests — liquidity validator
- [ ] VWAP on empty book returns zero depth
- [ ] VWAP on deep book returns top-of-book price (no slippage)
- [ ] VWAP on thin book returns higher price (slippage detected)
- [ ] Slippage exceeding max rejects
- [ ] Depth below min rejects
- [ ] max_absorbable_usd computed correctly
- [ ] File: `tests/unit/test_liquidity.py`

### Task 20: Unit tests — orchestrator
- [ ] Preflight passes when all checks pass
- [ ] Preflight fails on each individual check (credentials, balance, staleness, spread, liquidity, exposure, daily P&L, cooldown, concentration)
- [ ] Execute places both legs concurrently
- [ ] Execute detects partial execution
- [ ] Execute records results in repository
- [ ] Cancel delegates to correct venue
- [ ] File: `tests/unit/test_execution_orchestrator.py`

### Task 21: Unit tests — API routes
- [ ] GET /status returns correct flags when enabled
- [ ] GET /status returns disabled when execution off
- [ ] POST /preflight returns validation results
- [ ] POST /execute returns execution result
- [ ] POST /execute returns 403 when disabled
- [ ] DELETE /orders cancels and returns success
- [ ] File: `tests/unit/test_execution_routes.py`

### Task 22: Unit tests — execution repository
- [ ] insert_order delegates correctly
- [ ] update_order_status delegates correctly
- [ ] get_orders_for_ticket returns list
- [ ] count_open_positions returns count
- [ ] get_daily_pnl returns sum
- [ ] File: `tests/unit/test_execution_repository.py`

### Task 23: Quality gates
- [ ] `uv run ruff check src/ tests/` — zero errors
- [ ] `uv run ruff format --check src/ tests/` — clean
- [ ] `uv run mypy src/ --strict` — zero errors
- [ ] `uv run pytest tests/ -x --tb=short` — all pass
- [ ] `uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70` — ≥70%
