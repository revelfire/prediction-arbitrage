# Implementation Plan: WebSocket Schema Validation and Parser Hardening

**Feature**: `010-ws-schema-validation` | **Date**: 2026-02-26 | **Status**: Draft
**Spec**: `specs/010-ws-schema-validation/spec.md`

## Architecture Overview

The WebSocket client (`ws_client.py`) gains a telemetry layer, message classifier, and order book enrichment cache. The spike detector applies a confidence penalty for synthetic spreads. New migration, API endpoint, and CLI command expose telemetry.

```
WebSocket / Polling
        │
        ▼
_classify_ws_message()        ← FR-004
  ├─ heartbeat → ignore
  ├─ subscription_ack → ignore
  ├─ error → warn
  ├─ unknown → fail counter
  └─ price_update
        │
        ▼
_parse_ws_message()           (existing, enhanced)
  ├─ success → PriceUpdate(synthetic_spread=True)
  └─ failure → telemetry counter + diagnostics  ← FR-001, FR-002
        │
        ▼
OrderBookCache.enrich()       ← FR-005
  ├─ cache hit → real bid/ask (synthetic_spread=False)
  ├─ cache stale → async refresh, use stale
  └─ cache miss → keep synthetic, schedule fetch
        │
        ▼
queue → SpikeDetector
  └─ synthetic_spread=True → confidence × penalty  ← FR-007
        │
        ▼
WsTelemetry (counters)        ← FR-001
  ├─ periodic log (60s)
  ├─ schema drift check       ← FR-003
  ├─ GET /api/flippenings/ws-health  ← FR-009
  └─ ws_telemetry table (5min)  ← FR-010
```

## File Change Map

### Modified Files

| File | Changes | FRs |
|------|---------|-----|
| `src/arb_scanner/models/flippening.py` | Add `synthetic_spread`, `book_depth_bids`, `book_depth_asks` to `PriceUpdate`; add `spread` computed property | FR-006 |
| `src/arb_scanner/models/config.py` | Extend `FlippeningConfig` with 6 new fields | FR-011 |
| `src/arb_scanner/flippening/ws_client.py` | Add `WsTelemetry` class, `_classify_ws_message()`, `OrderBookCache`, enhance `_parse_ws_message()` with diagnostics, integrate telemetry into `WebSocketPriceStream` | FR-001–005 |
| `src/arb_scanner/flippening/spike_detector.py` | Apply `synthetic_spread_penalty` in `_score_confidence()` when `update.synthetic_spread` is True | FR-007 |
| `src/arb_scanner/flippening/orchestrator.py` | Pass config to `WebSocketPriceStream` for telemetry config, wire telemetry persistence | FR-001, FR-010 |
| `src/arb_scanner/storage/flippening_repository.py` | Add `insert_ws_telemetry()` and `get_ws_telemetry()` methods | FR-009, FR-010 |
| `src/arb_scanner/storage/_flippening_queries.py` | Add `INSERT_WS_TELEMETRY` and `GET_WS_TELEMETRY` SQL | FR-009, FR-010 |
| `src/arb_scanner/api/routes_flippening.py` | Add `GET /api/flippenings/ws-health` endpoint | FR-009 |
| `src/arb_scanner/cli/flippening_commands.py` | Add `flip-ws-validate` command | FR-008 |
| `config.example.yaml` | Add new FlippeningConfig fields with defaults | FR-011 |

### New Files

| File | Purpose | FRs |
|------|---------|-----|
| `src/arb_scanner/flippening/ws_telemetry.py` | `WsTelemetry` counter class and `OrderBookCache` LRU cache | FR-001–003, FR-005 |
| `src/arb_scanner/storage/migrations/014_create_ws_telemetry.sql` | Create `ws_telemetry` table | FR-010 |
| `tests/unit/test_ws_telemetry.py` | Tests for telemetry counters, schema drift, message classification | FR-001–004 |
| `tests/unit/test_orderbook_cache.py` | Tests for LRU cache, enrichment, TTL expiry, rate limiting | FR-005 |
| `tests/unit/test_ws_validate_cli.py` | Tests for `flip-ws-validate` CLI | FR-008 |

## Implementation Phases

### Phase 1: PriceUpdate Model Extension (FR-006)

Add fields to `PriceUpdate` in `models/flippening.py`:
```python
synthetic_spread: bool = False
book_depth_bids: int = 0
book_depth_asks: int = 0

@property
def spread(self) -> Decimal:
    return self.yes_ask - self.yes_bid
```

Using `bool = False` default means all existing code that constructs `PriceUpdate` (the polling stream, tests) continues to work unchanged. Only the WebSocket parser sets `synthetic_spread=True` explicitly.

### Phase 2: Message Classifier + Parse Diagnostics (FR-001, FR-002, FR-004)

Create `ws_telemetry.py` with:

1. **`WsTelemetry` dataclass**: Holds rolling and cumulative counters for received, parsed_ok, parse_failed, ignored. Methods: `record_parsed()`, `record_failed(reason)`, `record_ignored()`, `snapshot()` (returns dict), `reset_rolling()`.

2. **`_classify_ws_message(data: dict) -> str`**: Classifies raw parsed JSON:
   - Has `type` key with value `"heartbeat"` or `"ping"` → `"heartbeat"`
   - Has `type` key with value `"subscribe"` or `"subscribed"` → `"subscription_ack"`
   - Has `type` key with value `"error"` → `"error"`
   - Has `price` or `asset_id` key → `"price_update"`
   - Otherwise → `"unknown"`

3. **Enhanced `_parse_ws_message()`**: After JSON parse, call classifier. Only attempt price extraction for `"price_update"` type. On failure, log reason (`missing_market_id`, `missing_token_id`, `missing_price`, `invalid_json`, `price_out_of_range`) to telemetry. Set `synthetic_spread=True` on all WS-parsed `PriceUpdate` objects (since WS only provides a single price).

4. **Periodic logging**: `WsTelemetry` has a `should_log(interval_seconds)` method checked in the reader loop. When true, log rolling counters at info level and reset.

### Phase 3: Schema Drift Detection (FR-003)

Add to `WsTelemetry`:
- `known_schemas: set[frozenset[str]]` tracking unique top-level key combinations seen.
- On each message, compute `frozenset(data.keys())`. If new, log at info level.
- `schema_match_rate` computed as: messages with expected keys / total received over the rolling window.
- `check_drift(threshold)` returns True when match rate drops below threshold.

The orchestrator checks drift on each telemetry log interval and fires a webhook alert via `dispatch_webhook()` if triggered. Rate-limited to one alert per hour.

### Phase 4: Order Book Cache + Enrichment (FR-005)

Add `OrderBookCache` class to `ws_telemetry.py`:

```python
class OrderBookCache:
    def __init__(self, max_size: int, ttl_seconds: float, rate_limiter: RateLimiter):
        self._cache: dict[str, CacheEntry] = {}  # token_id → entry
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._limiter = rate_limiter
        self._pending: set[str] = set()  # token_ids with in-flight fetches

    async def enrich(self, update: PriceUpdate, client: httpx.AsyncClient) -> PriceUpdate:
        """Replace synthetic spread with real book depth if cached."""

    async def _fetch_book(self, token_id: str, client: httpx.AsyncClient) -> None:
        """Background fetch of order book, updates cache."""
```

Design choices:
- **LRU eviction**: When `_cache` exceeds `max_size`, evict oldest-accessed entry.
- **Non-blocking**: `enrich()` returns immediately with cached or synthetic data. If cache is stale/missing, schedules `_fetch_book()` as a fire-and-forget `asyncio.create_task()`.
- **Rate limiting**: Uses existing `RateLimiter` to cap book fetches.
- **Staleness**: Stale cached data is used (better than blocking) but `cache_age` is logged.

Integration: After `_parse_ws_message()` produces a `PriceUpdate`, call `cache.enrich(update, client)` before putting on the queue. The `enrich()` method updates `yes_bid`, `yes_ask`, `no_bid`, `no_ask`, `synthetic_spread`, `book_depth_bids`, `book_depth_asks`.

### Phase 5: Confidence Penalty for Synthetic Spreads (FR-007)

In `spike_detector.py`, modify `_score_confidence()`:

```python
# After computing raw score and applying sport_mod + late_join_penalty:
if hasattr(update, 'synthetic_spread') and update.synthetic_spread:
    raw *= self._config.synthetic_spread_penalty
```

This is a one-line change. The `hasattr` check provides backward compatibility with any test code that constructs `PriceUpdate` without the field (though the default is `False` so it's technically unnecessary — belt and suspenders).

Actually, since `synthetic_spread` defaults to `False`, the simpler approach:
```python
# In check_spike(), pass update to _score_confidence (already done)
# In _score_confidence(), after late_join penalty:
if update.synthetic_spread:
    raw *= self._config.synthetic_spread_penalty
```

This requires passing `update` to `_score_confidence()` — it already receives it (line 67-71 in current code).

### Phase 6: CLI Validation Command (FR-008)

Add `flip-ws-validate` to `flippening_commands.py`:

1. Connects to Polymarket CLOB WebSocket.
2. Optionally auto-discovers token IDs from sports markets (or uses `--tokens` flag).
3. Captures N messages (default 100) with timeout (default 60s).
4. For each message: classify type, record key sets, attempt parse.
5. Reports: message type distribution, top-level key frequencies, schema match rate, sample message per type.
6. `--save FILE` writes raw messages to JSONL for offline analysis.
7. No database required — pure diagnostic tool.

### Phase 7: Persistence + API (FR-009, FR-010)

1. Migration `014_create_ws_telemetry.sql`:
   ```sql
   CREATE TABLE ws_telemetry (
       id BIGSERIAL PRIMARY KEY,
       snapshot_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       messages_received INT NOT NULL,
       messages_parsed INT NOT NULL,
       messages_failed INT NOT NULL,
       messages_ignored INT NOT NULL,
       schema_match_rate DOUBLE PRECISION NOT NULL,
       book_cache_hit_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
       connection_state TEXT NOT NULL DEFAULT 'unknown'
   );
   CREATE INDEX idx_ws_telemetry_ts ON ws_telemetry (snapshot_time DESC);
   ```

2. Repository methods: `insert_ws_telemetry(snapshot)` and `get_ws_telemetry(limit)`.

3. API endpoint: `GET /api/flippenings/ws-health?limit=20` returns recent snapshots plus live counters from the in-memory `WsTelemetry` instance. The live counters are exposed via a module-level reference set by the orchestrator.

### Phase 8: Orchestrator Integration

Wire everything into the flip-watch loop:
- Create `WsTelemetry` and `OrderBookCache` instances on startup.
- Pass to `WebSocketPriceStream` (or wrap the stream's output).
- On each telemetry interval: log, check drift, persist snapshot if persistence interval reached.
- Pass `httpx.AsyncClient` to `OrderBookCache` for book fetches (reuse existing client).

## Edge Case Handling

| Edge Case | Handling | Phase |
|-----------|----------|-------|
| EC-001: Complete schema change | Schema drift detection fires within 60s; `flip-ws-validate` gives raw samples | Phase 3 + 6 |
| EC-002: REST rate limit on book fetch | Graceful degradation to synthetic; `RateLimiter` prevents cascade | Phase 4 |
| EC-003: Stale cache in fast market | Use stale data (non-blocking), log `cache_age` | Phase 4 |
| EC-004: Non-JSON WS messages | `json.loads` catches; classify as `heartbeat` or `unknown`, don't count as `parse_failed` | Phase 2 |
| EC-005: Zero messages for 120s | Stall counter in `WsTelemetry`; force reconnect after 3 stalls | Phase 2 |
| EC-006: Mixed real/synthetic per token | `synthetic_spread` is per-`PriceUpdate`, handled per-token by cache | Phase 4 |

## Module Size Compliance

All new/modified files stay under the 300-line constraint:
- `ws_telemetry.py`: ~150 lines (`WsTelemetry` ~80 + `OrderBookCache` ~70)
- `ws_client.py`: Currently 385 lines — will need to extract `OrderBookCache` to `ws_telemetry.py` and move `_classify_ws_message` there too, keeping `ws_client.py` focused on stream protocol.
- `sports_filter.py`: Currently 153 lines, will grow to ~250 with fuzzy pass.

## Testing Strategy

- **WsTelemetry**: Counter increment/reset, rolling window, schema drift detection with synthetic message sequences.
- **Message classifier**: Test each message type with sample payloads.
- **OrderBookCache**: Cache hit/miss/stale, LRU eviction, rate limiting, enrichment of PriceUpdate fields.
- **Confidence penalty**: Unit test that synthetic_spread=True produces lower confidence than False for same price data.
- **CLI**: Mocked WebSocket connection, verify output format and message capture.
- **API**: Mocked repository, verify endpoint returns telemetry snapshots.

## Quality Gates

All must pass after each phase:
1. `ruff check` — zero errors
2. `ruff format --check` — clean
3. `mypy src/ --strict` — zero errors
4. `pytest tests/ -x` — all pass
5. `pytest --cov --cov-fail-under=70` — coverage maintained
