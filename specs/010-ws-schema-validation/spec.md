# Feature Specification: WebSocket Schema Validation and Parser Hardening

**Feature**: `010-ws-schema-validation` | **Date**: 2026-02-26 | **Status**: Draft
**Depends on**: `008-flippening-engine` (complete)

## Problem Statement

The `_parse_ws_message()` function in `ws_client.py` (lines 291-324) was written against documented Polymarket CLOB WebSocket schema but has never been validated against live traffic. Three concrete problems exist:

1. **Unvalidated field assumptions**: The parser looks for `market`/`condition_id` and `asset_id`/`price` keys based on documentation. If the actual WebSocket messages use different field names, nested structures, or message envelope formats, the parser silently returns `None` and drops every message. There are zero counters or logs when this happens -- the system would appear to be running normally while receiving no data.

2. **Synthetic bid/ask spread**: Lines 314-320 create bid/ask as `price +/- 0.01` rather than using real order book depth. This means the `yes_bid`, `yes_ask`, `no_bid`, and `no_ask` fields in `PriceUpdate` are fabricated from a single midpoint. The spike detector and signal generator both use these prices for entry/exit decisions, making position sizing and P&L calculations unreliable. The 1-cent spread assumption is arbitrary and may not reflect actual market liquidity.

3. **Silent failure on parse errors**: The `except (json.JSONDecodeError, KeyError, TypeError): return None` clause swallows all errors. There is no telemetry for how many messages arrive, how many parse successfully, how many fail, or what the failure modes are. Schema drift would be invisible.

## Solution

Add live schema validation, real order book depth integration, parse failure telemetry, and a schema drift detection system to the WebSocket message parser. The parser becomes observable and self-diagnosing rather than silently dropping data.

## Functional Requirements

### FR-001: Parse Telemetry Counters
The WebSocket client MUST track and expose the following counters, logged via structlog at 60-second intervals:
- `ws_messages_received`: Total messages received from the WebSocket.
- `ws_messages_parsed_ok`: Messages successfully parsed into `PriceUpdate`.
- `ws_messages_parse_failed`: Messages that failed parsing.
- `ws_messages_ignored`: Messages intentionally skipped (e.g., heartbeats, subscription confirmations, non-price messages).
- `ws_parse_success_rate`: `parsed_ok / (parsed_ok + parse_failed)` as a percentage.
These counters MUST reset every reporting interval. Cumulative totals MUST also be tracked.

### FR-002: Parse Failure Diagnostics
When a message fails to parse, the system MUST log at debug level:
- The raw message (truncated to 500 chars).
- The specific failure reason: `missing_market_id`, `missing_token_id`, `missing_price`, `invalid_json`, `unexpected_type`, `price_out_of_range`.
- The top-level keys present in the message (for schema drift detection).
At warning level (throttled to once per 60 seconds), the system MUST log an aggregate failure summary: `{"total_failures": N, "by_reason": {"missing_market_id": X, ...}}`.

### FR-003: Schema Drift Detection
The system MUST maintain a `known_message_schemas` set tracking the unique combinations of top-level keys seen in WebSocket messages. When a new key combination is observed for the first time, the system MUST log it at info level. If the fraction of messages matching the expected schema (containing `market` or `condition_id`, plus `asset_id`, plus `price`) drops below `ws_schema_match_pct` (configurable, default 0.50) over a 60-second window, the system MUST fire a `schema_drift` webhook alert via `dispatch_webhook()`.

### FR-004: Message Type Classification
The parser MUST classify incoming messages into types before attempting price extraction:
- `price_update`: Contains price data for a market (the parseable type).
- `heartbeat`: Connection keepalive message (skip silently, increment `ws_messages_ignored`).
- `subscription_ack`: Confirmation of subscription request (log at debug, increment `ws_messages_ignored`).
- `error`: Server error message (log at warning).
- `unknown`: Unrecognized message format (log at debug, increment `ws_messages_parse_failed`).
Classification MUST use a `_classify_ws_message()` function that returns the message type string.

### FR-005: Real Order Book Depth from REST Enrichment
When a `price_update` WebSocket message contains only a single `price` field (no explicit bid/ask/depth data), the system MUST enrich the `PriceUpdate` with real order book depth from the CLOB REST API. The enrichment strategy:
- Maintain an in-memory LRU cache of order books per `token_id`, keyed by token ID, with a configurable TTL of `orderbook_cache_ttl_seconds` (default 10).
- On each price update, if the cached order book is stale or missing, fire an async background fetch to `GET /book?token_id=X`.
- Use cached bids/asks for `yes_bid`, `yes_ask` (and derived `no_bid`, `no_ask`) instead of the synthetic `price +/- 0.01`.
- If the cache is empty and no fetch has completed, fall back to the existing synthetic spread with a `synthetic_spread=true` flag on the `PriceUpdate`.
The order book fetch MUST be rate-limited via the existing `RateLimiter` (max 10 requests/second across all tokens).

### FR-006: PriceUpdate Model Extension
The `PriceUpdate` model MUST gain:
- `synthetic_spread: bool = False` -- True when bid/ask is derived from a single price point rather than real order book data.
- `book_depth_bids: int = 0` -- Number of bid levels available.
- `book_depth_asks: int = 0` -- Number of ask levels available.
- `spread: Decimal` -- Computed property: `yes_ask - yes_bid`.
These fields enable downstream components (spike detector, signal generator) to factor in data quality when making decisions.

### FR-007: Confidence Penalty for Synthetic Spreads
The `SpikeDetector` MUST apply a confidence penalty when a `PriceUpdate` has `synthetic_spread=True`. The penalty MUST be configurable via `synthetic_spread_penalty` in `FlippeningConfig` (default 0.85 -- multiply confidence by 0.85). This discourages entry signals based on unreliable price data.

### FR-008: Live Schema Validation Command
The system MUST add a `flip-ws-validate` CLI command that:
- Connects to the Polymarket CLOB WebSocket.
- Subscribes to a configurable list of token IDs (or auto-discovers from sports markets).
- Captures the first N messages (default 100, configurable via `--count`).
- Reports: message type distribution, top-level key frequencies, schema match rate, sample messages per type.
- Exits after N messages or `--timeout` seconds (default 60).
Options: `--tokens` (comma-separated token IDs), `--count N`, `--timeout S`, `--format (table|json)`, `--save FILE` (write raw messages to JSONL file for offline analysis).

### FR-009: Parse Telemetry API Endpoint
The system MUST add `GET /api/flippenings/ws-health` returning current WebSocket telemetry:
- Connection state (connected/disconnected/reconnecting).
- Uptime since last connect.
- Cumulative and rolling (last 60s) message counters from FR-001.
- Current schema match rate from FR-003.
- Order book cache stats: hit rate, stale rate, size.

### FR-010: Parse Telemetry Persistence
The system MUST persist WebSocket telemetry snapshots to a `ws_telemetry` table every 5 minutes with fields: `id`, `snapshot_time`, `messages_received`, `messages_parsed`, `messages_failed`, `messages_ignored`, `schema_match_rate`, `book_cache_hit_rate`, `connection_state`. Migration MUST be numbered sequentially after existing migrations.

### FR-011: Configuration Extensions
`FlippeningConfig` MUST gain these fields:
- `ws_telemetry_interval_seconds`: int (default 60) -- how often to log telemetry.
- `ws_schema_match_pct`: float (default 0.50) -- threshold for schema drift alert.
- `orderbook_cache_ttl_seconds`: float (default 10.0) -- LRU cache TTL for REST-fetched order books.
- `orderbook_cache_max_size`: int (default 200) -- max entries in the order book cache.
- `synthetic_spread_penalty`: float (default 0.85) -- confidence multiplier for synthetic spreads.
- `ws_telemetry_persist_interval_seconds`: int (default 300) -- persistence interval.

## Success Criteria

- SC-001: `flip-ws-validate` connects to the live WebSocket, captures messages, and reports schema match rate and message type distribution.
- SC-002: Parse telemetry counters are logged every 60 seconds during `flip-watch`, showing received/parsed/failed/ignored breakdowns.
- SC-003: When a WebSocket message has unknown fields, it is logged as a new schema variant at info level.
- SC-004: When schema match rate drops below threshold in synthetic tests (mocked WebSocket with schema changes), a `schema_drift` alert fires.
- SC-005: `PriceUpdate` objects produced from REST-enriched order books have `synthetic_spread=False` and accurate bid/ask prices from real depth.
- SC-006: `PriceUpdate` objects produced from single-price WS messages without cache have `synthetic_spread=True`.
- SC-007: `SpikeDetector` applies the confidence penalty when processing synthetic-spread updates in unit tests.
- SC-008: `GET /api/flippenings/ws-health` returns current telemetry counters and connection state.
- SC-009: All existing tests still pass (no regressions).
- SC-010: All quality gates pass (ruff, mypy --strict, 70% coverage).

## Edge Cases

### EC-001: WebSocket Message Format Change
The WebSocket starts sending messages in a completely different format (e.g., nested `data.price` instead of top-level `price`). The system MUST detect this via schema drift (FR-003) within 60 seconds and fire an alert. The `flip-ws-validate` command (FR-008) gives the operator raw messages to diagnose the new format.

### EC-002: Order Book REST Endpoint Rate Limit
The REST enrichment (FR-005) hits Polymarket's rate limit. The system MUST gracefully degrade to synthetic spreads for affected tokens and log a rate-limit warning. The `RateLimiter` prevents cascading failures.

### EC-003: Stale Order Book Cache During Fast Market
During a rapid flippening, the 10-second order book cache may be stale. The system MUST use the stale data with a `cache_age` field logged for observability, rather than blocking on a fresh fetch. Spike detection on stale data is preferable to delayed detection.

### EC-004: WebSocket Sends Non-JSON Messages
Some WebSocket implementations send binary ping/pong frames or text heartbeats that are not valid JSON. The parser MUST handle `bytes` input by decoding to UTF-8, and classify non-JSON text as `heartbeat` or `unknown` without incrementing `parse_failed`.

### EC-005: Zero Messages Received
If the WebSocket connection is alive but zero messages arrive for 120 seconds, the system MUST log a `ws_no_messages` warning and increment a stall counter. After 3 consecutive stall intervals, the system MUST force a reconnect.

### EC-006: Mixed Real and Synthetic Spreads
During a single monitoring session, some tokens have cached order books (real spreads) and others do not (synthetic spreads). The system MUST handle this per-token, not globally. The `synthetic_spread` flag is per-`PriceUpdate`, not per-connection.

## Dependencies

- `008-flippening-engine` (complete): WebSocket client, price stream protocol, spike detector, signal generator.
- Polymarket CLOB WebSocket: Live message format (validated by this feature).
- Polymarket CLOB REST API: `/book` endpoint for order book enrichment.

## Out of Scope

- Automatic parser adaptation to new schemas (the system detects drift; a human updates the parser).
- Full Level 2 order book streaming (depth-of-book WebSocket feeds).
- Historical WebSocket message replay or backtesting.
- WebSocket authentication or private channel subscription.
- Modifying the Polymarket WebSocket server or requesting upstream format changes.
