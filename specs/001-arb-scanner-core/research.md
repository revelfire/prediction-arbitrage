# Research: Cross-Venue Arbitrage Scanner

## Decision 1: Polymarket API Strategy

**Decision:** Use the CLOB `/markets` endpoint with cursor-based pagination, filtering client-side for `active=true` and `accepting_orders=true`. Use the Gamma API (`gamma-api.polymarket.com`) for richer market metadata (description, category, resolution criteria).

**Rationale:**
- The CLOB `/markets` endpoint returns ~494k total markets but does not support server-side filtering. It uses base64-encoded offset cursors (`next_cursor`) with 1000 items per page.
- End-of-data is signaled by `next_cursor = "LTE="` (base64 for `-1`) and `count: 0`.
- Most markets are closed/archived. Active markets with order books are a small subset (~1000-2000).
- The CLOB market object contains: `condition_id`, `question`, `description`, `tokens[].price`, `maker_base_fee`, `taker_base_fee`, `end_date_iso`, `active`, `closed`, `accepting_orders`.
- The Gamma API (`/markets/slug/{slug}`) provides richer metadata: `bestBid`, `bestAsk`, `liquidity`, `volume`, `outcomes`, `outcomePrices`, `category`, `tags`.
- No authentication required for any read endpoints.
- Rate limits: ~10 req/s (undocumented, empirical). Implement exponential backoff.

**Alternatives considered:**
- `/sampling-markets` and `/simplified-markets` endpoints exist but return the same pagination structure with slightly different field sets. The main `/markets` endpoint has the richest data.

## Decision 2: Kalshi API Strategy

**Decision:** Use public (unauthenticated) endpoints for market data. RSA-PSS signing only needed if we later add portfolio/trading features.

**Rationale:**
- Market data endpoints are PUBLIC — no auth required: `GET /markets`, `GET /markets/{ticker}`, `GET /markets/{ticker}/orderbook`, `GET /events`.
- The original spec assumed Bearer token auth. Research reveals Kalshi uses RSA-PSS signature auth (3 custom headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP` in milliseconds, `KALSHI-ACCESS-SIGNATURE`).
- Since we only read market data and never trade, we can skip auth entirely.
- Rate limits: 20 reads/sec (Basic tier). More generous than initially assumed.
- Cursor-based pagination with empty string cursor indicating end of data.

**Critical implementation notes:**
- Integer price fields (`yes_bid`, `no_ask`) deprecated March 5, 2026. Use ONLY `*_dollars` string fields (up to 4 decimal places, e.g. `"0.5500"`).
- Volume/count fields: use `*_fp` string fields (2 decimal places, e.g. `"150.00"`).
- Orderbook returns ONLY bids (no asks). Compute asks via complement: `YES_ask = 1.00 - highest_NO_bid`.
- Orderbook arrays sorted ascending — best bid is LAST element.
- `GET /events` excludes multivariate events since Dec 2025.
- `rules_primary` and `rules_secondary` fields contain resolution criteria text.

**Alternatives considered:**
- Implementing RSA-PSS auth for future trading support. Deferred — YAGNI for v1 scanner.

## Decision 3: BM25 Library

**Decision:** Use `bm25s` (v0.3.0) with `method="bm25+"` for the pre-filter.

**Rationale:**
- Actively maintained (last release Feb 17, 2026), MIT license, Python 3.8+.
- Built-in tokenizer with stopword removal: `bm25s.tokenize(corpus, stopwords="en")`.
- BM25+ variant ensures matched terms always contribute positive scores — critical for short text recall.
- Pre-computes scoring matrix at index time; sub-millisecond queries for ~1000 docs.
- No incremental indexing, but full re-index of ~1000 titles takes single-digit milliseconds.

**Alternatives considered:**
- `rank-bm25`: Unmaintained since Feb 2022. No built-in tokenizer. Still works but risky for long-term.
- pgvector embeddings: Better for semantic matching but adds latency (embedding API call per query), cost, and false positive risk on similar-but-different events (e.g., "BTC $100k 2025" vs "BTC $100k 2026"). Better suited as a second-pass signal, not primary pre-filter.
- Elasticsearch/OpenSearch: Massively overkill for ~1000 titles.

## Decision 4: PostgreSQL + pgvector Schema

**Decision:** Use asyncpg for async PostgreSQL access. pgvector for optional embedding storage. Raw SQL migrations (no ORM, no Alembic).

**Rationale:**
- Constitution mandates no ORM. asyncpg is the fastest async PostgreSQL driver for Python.
- pgvector extension enables future embedding-based similarity if BM25 proves insufficient.
- Simple migration system: numbered SQL files in `migrations/` directory, applied in order.
- Tables: `markets`, `match_results`, `arb_opportunities`, `execution_tickets`, `scan_logs`.

**Alternatives considered:**
- psycopg3 (async): Viable but asyncpg has better performance benchmarks for pure async workloads.
- SQLAlchemy async: Adds ORM complexity we explicitly rejected.

## Decision 5: Fee Calculation Models

**Decision:** Implement two distinct fee calculation strategies, configurable per venue.

**Rationale:**
- Polymarket: fees are % on net winnings (not on cost). If you buy YES at $0.62 and win, fee = 2% × ($1.00 - $0.62) = $0.0076. This is subtle — the fee base is profit, not principal.
- Kalshi: flat fee per contract, capped at $0.07. Contract notional is $1.00. Fee = min(taker_fee, fee_cap) per contract.
- These models produce very different net profit calculations for the same gross spread.

## Decision 6: Claude API for Semantic Matching

**Decision:** Use `claude-sonnet-4-20250514` via the `anthropic` Python SDK with structured JSON output. Batch 5 candidate pairs per API call.

**Rationale:**
- Sonnet is cost-effective for high-volume evaluations (~200 pairs per scan = ~40 API calls).
- Structured output via system prompt enforcing JSON schema. Parse with Pydantic for validation.
- Include both market titles AND resolution criteria (Polymarket: `description`; Kalshi: `rules_primary` + `rules_secondary`) in the prompt for accurate equivalence assessment.
- Cache results in PostgreSQL with 24h TTL keyed on `(poly_event_id, kalshi_ticker)`.

## Decision 7: Async Architecture

**Decision:** Single async event loop with `asyncio.gather()` for concurrent venue polling. `httpx.AsyncClient` with connection pooling per venue.

**Rationale:**
- Two venue clients can poll concurrently.
- Rate limiting implemented per-client using `asyncio.Semaphore` (10/s for Polymarket, 20/s for Kalshi).
- Claude API calls batched and dispatched concurrently within rate limits.
- Webhook notifications fire-and-forget with retry on failure.
