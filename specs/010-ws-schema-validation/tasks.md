# Tasks: WebSocket Schema Validation and Parser Hardening

**Feature**: `010-ws-schema-validation` | **Date**: 2026-02-26

## Phase 1: PriceUpdate Model Extension [FR-006]

- [ ] P1-T01: Add `synthetic_spread: bool = False` field to `PriceUpdate` in `models/flippening.py`.
- [ ] P1-T02: Add `book_depth_bids: int = 0` and `book_depth_asks: int = 0` fields to `PriceUpdate`.
- [ ] P1-T03: Add `spread` computed property to `PriceUpdate`: `@property def spread(self) -> Decimal: return self.yes_ask - self.yes_bid`.
- [ ] P1-T04: Run quality gates. Verify all existing tests pass (default values ensure backward compat).

## Phase 2: Config Extensions [FR-011]

- [ ] P2-T01: Add 6 new fields to `FlippeningConfig` in `models/config.py`: `ws_telemetry_interval_seconds: int = 60`, `ws_schema_match_pct: float = 0.50`, `orderbook_cache_ttl_seconds: float = 10.0`, `orderbook_cache_max_size: int = 200`, `synthetic_spread_penalty: float = 0.85`, `ws_telemetry_persist_interval_seconds: int = 300`.
- [ ] P2-T02: Add new config fields (commented examples) to `config.example.yaml` under `flippening:`.
- [ ] P2-T03: Run quality gates.

## Phase 3: WsTelemetry Class [FR-001, FR-002]

- [ ] P3-T01: Create `src/arb_scanner/flippening/ws_telemetry.py`. Define `WsTelemetry` class with rolling counters: `received: int`, `parsed_ok: int`, `parse_failed: int`, `ignored: int`. Also cumulative versions of each. Add `_last_log_time: datetime` and `_failure_reasons: dict[str, int]` for per-reason tracking.
- [ ] P3-T02: Implement `record_parsed()`, `record_failed(reason: str)`, `record_ignored()` methods that increment both rolling and cumulative counters.
- [ ] P3-T03: Implement `snapshot() -> dict[str, Any]` returning all rolling and cumulative counters plus `parse_success_rate` percentage.
- [ ] P3-T04: Implement `should_log(interval_seconds: int) -> bool` checking elapsed time since `_last_log_time`. When True, log rolling counters + failure reason breakdown at info level via structlog, then call `reset_rolling()`.
- [ ] P3-T05: Implement `reset_rolling()` that zeros rolling counters and `_failure_reasons` dict.
- [ ] P3-T06: Write unit tests in `tests/unit/test_ws_telemetry.py`: counter increments, snapshot values, rolling reset, should_log timing, failure reason tracking.
- [ ] P3-T07: Run quality gates.

## Phase 4: Message Classifier [FR-004]

- [ ] P4-T01: Implement `classify_ws_message(data: dict[str, object]) -> str` in `ws_telemetry.py`. Return one of: `"heartbeat"` (type=heartbeat/ping), `"subscription_ack"` (type=subscribe/subscribed), `"error"` (type=error), `"price_update"` (has price or asset_id key), `"unknown"` (none of the above).
- [ ] P4-T02: Write unit tests for each message type classification with sample payloads: heartbeat, subscription ack, error, price update, unknown.
- [ ] P4-T03: Run quality gates.

## Phase 5: Enhanced WS Parser [FR-001, FR-002, FR-004]

- [ ] P5-T01: Refactor `_parse_ws_message()` in `ws_client.py` to accept an optional `WsTelemetry` parameter. Call `classify_ws_message()` after JSON parse. For `heartbeat` and `subscription_ack`, call `telemetry.record_ignored()` and return None. For `error`, log at warning and call `telemetry.record_ignored()`.
- [ ] P5-T02: For `price_update` type, attempt price extraction. On success, set `synthetic_spread=True` on the returned `PriceUpdate` (WS only provides single price). Call `telemetry.record_parsed()`.
- [ ] P5-T03: On parse failure, determine specific reason (`missing_market_id`, `missing_token_id`, `missing_price`, `invalid_json`, `price_out_of_range`) and call `telemetry.record_failed(reason)`. Log raw message (truncated to 500 chars) at debug level.
- [ ] P5-T04: Handle non-JSON messages (EC-004): catch `json.JSONDecodeError`, try UTF-8 decode if bytes, classify as `heartbeat` or `unknown` — do NOT count as `parse_failed`.
- [ ] P5-T05: Integrate telemetry logging into `WebSocketPriceStream._reader_loop()`: create `WsTelemetry` instance, pass to `_parse_ws_message()`, call `telemetry.should_log()` after each message.
- [ ] P5-T06: Update existing `_parse_ws_message()` tests to verify `synthetic_spread=True` on returned PriceUpdate. Add tests for each failure reason.
- [ ] P5-T07: Run quality gates.

## Phase 6: Schema Drift Detection [FR-003]

- [ ] P6-T01: Add `known_schemas: set[frozenset[str]]` and `_schema_match_count: int` / `_schema_total_count: int` rolling counters to `WsTelemetry`.
- [ ] P6-T02: Implement `record_schema(keys: frozenset[str])` method: add to `known_schemas`, check if keys contain expected fields (`market`/`condition_id` + `asset_id` + `price`), increment match/total counters.
- [ ] P6-T03: Implement `schema_match_rate` property: `_schema_match_count / _schema_total_count` (or 1.0 if no messages).
- [ ] P6-T04: Implement `check_drift(threshold: float) -> bool` returning True when `schema_match_rate` drops below threshold.
- [ ] P6-T05: Log new schema variants at info level when first seen (log the key set).
- [ ] P6-T06: Call `record_schema()` in `_parse_ws_message()` for every successfully JSON-parsed message (before classification).
- [ ] P6-T07: Write unit tests: new schema logged, match rate computation, drift detection with mixed schemas, reset clears schema counters.
- [ ] P6-T08: Run quality gates.

## Phase 7: Order Book Cache [FR-005]

- [ ] P7-T01: Define `CacheEntry` dataclass in `ws_telemetry.py`: `yes_bid: Decimal`, `yes_ask: Decimal`, `no_bid: Decimal`, `no_ask: Decimal`, `depth_bids: int`, `depth_asks: int`, `fetched_at: datetime`.
- [ ] P7-T02: Implement `OrderBookCache.__init__(max_size, ttl_seconds, rate_limiter)` with `_cache: dict[str, CacheEntry]`, `_pending: set[str]`, `_access_order: list[str]` for LRU tracking. Add `hits: int` and `misses: int` counters.
- [ ] P7-T03: Implement `enrich(update: PriceUpdate, client: httpx.AsyncClient) -> PriceUpdate`. If cache hit and not stale: copy real bid/ask into update, set `synthetic_spread=False`, set depth fields. If stale: use stale data, schedule background refresh. If miss: keep synthetic, schedule fetch.
- [ ] P7-T04: Implement `_fetch_book(token_id, client)` as async method: `GET /book?token_id=X` via client, parse bids/asks (reuse logic from existing `_parse_orderbook()`), update cache entry. Rate-limit via `self._limiter.acquire()`.
- [ ] P7-T05: Implement LRU eviction in `_update_cache()`: when `len(_cache) > max_size`, evict least-recently-accessed entry.
- [ ] P7-T06: Implement `cache_hit_rate` property: `hits / (hits + misses)` (or 0.0 if no lookups).
- [ ] P7-T07: Write unit tests in `tests/unit/test_orderbook_cache.py`: cache hit returns real spread, cache miss keeps synthetic, TTL expiry triggers refetch, LRU eviction, rate limiter prevents flood, enrich sets correct PriceUpdate fields.
- [ ] P7-T08: Run quality gates.

## Phase 8: Confidence Penalty [FR-007]

- [ ] P8-T01: Add `synthetic_spread_penalty` config read in `SpikeDetector.__init__()`.
- [ ] P8-T02: In `_score_confidence()`, after late_join penalty block, add: `if update.synthetic_spread: raw *= self._config.synthetic_spread_penalty`. The `update` param is already passed to this method.
- [ ] P8-T03: Write unit tests: same price data with `synthetic_spread=True` produces lower confidence than `synthetic_spread=False`. Verify penalty multiplier matches config value.
- [ ] P8-T04: Run quality gates.

## Phase 9: CLI Validation Command [FR-008]

- [ ] P9-T01: Add `flip-ws-validate` command to `flippening_commands.py` via `register()`. Options: `--tokens` (comma-separated token IDs), `--count N` (default 100), `--timeout S` (default 60), `--format table|json`, `--save FILE`.
- [ ] P9-T02: Implement core logic: connect to Polymarket CLOB WebSocket, subscribe to tokens (auto-discover from sports markets if `--tokens` not provided), capture messages up to `--count` or `--timeout`.
- [ ] P9-T03: For each captured message: JSON parse, classify type, record top-level keys, attempt price parse. Collect results.
- [ ] P9-T04: Render report: message type distribution (count + pct per type), top-level key frequency table, schema match rate, one sample message per type (truncated).
- [ ] P9-T05: Implement `--save FILE` output: write each raw message as a JSONL line for offline analysis.
- [ ] P9-T06: Implement `--format json` path with `json.dumps()`.
- [ ] P9-T07: Write unit tests in `tests/unit/test_ws_validate_cli.py`: mock WebSocket connection, verify table output, verify json output, verify save writes JSONL.
- [ ] P9-T08: Run quality gates.

## Phase 10: Persistence + API [FR-009, FR-010]

- [ ] P10-T01: Create `src/arb_scanner/storage/migrations/014_create_ws_telemetry.sql` with `ws_telemetry` table: `id BIGSERIAL PRIMARY KEY`, `snapshot_time TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `messages_received INT NOT NULL`, `messages_parsed INT NOT NULL`, `messages_failed INT NOT NULL`, `messages_ignored INT NOT NULL`, `schema_match_rate DOUBLE PRECISION NOT NULL`, `book_cache_hit_rate DOUBLE PRECISION NOT NULL DEFAULT 0`, `connection_state TEXT NOT NULL DEFAULT 'unknown'`. Add index on `snapshot_time DESC`.
- [ ] P10-T02: Add `INSERT_WS_TELEMETRY` and `GET_WS_TELEMETRY` SQL constants to `_flippening_queries.py`.
- [ ] P10-T03: Add `insert_ws_telemetry(snapshot: dict)` and `get_ws_telemetry(limit: int = 20) -> list[dict]` methods to `FlippeningRepository`.
- [ ] P10-T04: Add `GET /api/flippenings/ws-health` endpoint to `routes_flippening.py` with `limit` query param (default 20). Returns list of telemetry snapshot dicts. 503 on DB error.
- [ ] P10-T05: Add API route test: mock `get_ws_telemetry`, verify 200 with data and empty list.
- [ ] P10-T06: Run quality gates.

## Phase 11: Orchestrator Integration

- [ ] P11-T01: Create `WsTelemetry` instance in `run_flip_watch()` on startup. Pass to `WebSocketPriceStream` (add `telemetry` parameter to constructor).
- [ ] P11-T02: Create `OrderBookCache` instance with config values (`orderbook_cache_max_size`, `orderbook_cache_ttl_seconds`). Pass shared `httpx.AsyncClient` for book fetches.
- [ ] P11-T03: After stream produces each `PriceUpdate`, call `cache.enrich(update, client)` before passing to `_process_update()`.
- [ ] P11-T04: Add telemetry persistence loop: track time since last persist, call `repo.insert_ws_telemetry(telemetry.snapshot())` every `ws_telemetry_persist_interval_seconds` (skip if dry_run or repo is None).
- [ ] P11-T05: Add schema drift check on each telemetry log interval: call `telemetry.check_drift(config.ws_schema_match_pct)`, dispatch webhook alert if True. Rate-limit to one per hour.
- [ ] P11-T06: Add stall detection (EC-005): if telemetry shows 0 messages received for 2 consecutive log intervals (120s), log warning. After 3 consecutive stalls, force stream reconnect.
- [ ] P11-T07: Update existing orchestrator tests to account for new `WsTelemetry` and `OrderBookCache` params.
- [ ] P11-T08: Run full quality gates. Verify all tests pass, coverage >= 70%.
