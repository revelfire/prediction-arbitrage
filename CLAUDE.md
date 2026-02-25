# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cross-venue arbitrage scanner for Polymarket and Kalshi prediction markets. Poll-based, CLI-only, no auto-trading. Detects mispricings, calculates net profit after fees, and alerts via webhook. LLM-powered contract matching using Claude API for semantic equivalence and resolution risk assessment.

## Tech Stack

- Python 3.11+ with uv for dependency management
- httpx (async HTTP), pydantic v2 (data models), anthropic SDK (Claude API)
- PostgreSQL + pgvector (via asyncpg, persistence + vector similarity), structlog (logging)
- typer (CLI), pyyaml (config), pytest + pytest-asyncio (testing)
- ruff (lint + format), mypy (type checking)

## Architecture

```
src/arb_scanner/
├── models/          # Pydantic data models (Market, MatchedPair, ArbOpportunity, etc.)
├── config/          # YAML config loader, settings dataclass, fee schedules
├── ingestion/       # Async API clients: PolymarketClient, KalshiClient
├── matching/        # Pre-filter (BM25 + pgvector), embedding.py (Voyage AI client), embedding_prefilter.py (cosine rerank), Claude semantic matcher, cache layer
├── engine/          # Arb calculator, combinatorial checker (stretch)
├── storage/         # PostgreSQL + pgvector repository, migrations, analytics_repository
├── notifications/   # Webhook dispatcher (Slack/Discord), trend alert detector + dispatch, stdout reporter
├── api/             # FastAPI REST API + static dashboard (serve command)
├── cli/             # Typer app: scan, watch, report, match-audit, history, stats, alerts commands
└── utils/           # Retry logic, rate limiter, async helpers
```

## Key Design Decisions

- Poll-based (no WebSocket in v1), default 60s interval
- Claude Sonnet for matching (cost-effective for high-volume pair evaluation)
- Human-in-the-loop: system produces execution tickets, never places orders
- Match results cached in PostgreSQL with 24h TTL
- BM25 pre-filter via bm25s (method="bm25+") reduces candidate pairs before Claude API calls
- Voyage AI embedding pre-filter: after BM25 recall, cosine similarity of title embeddings drops low-quality pairs before Claude evaluation. Configured via `EmbeddingConfig` (model, api_key, cosine_threshold, dimensions). Gracefully degrades to BM25-only when disabled, missing key, or API error. Embeddings persisted to `markets.title_embedding` (pgvector) for reuse.
- Fee models differ per venue (Polymarket: % on winnings; Kalshi: per-contract flat fee)
- FastAPI dashboard served from same process (no separate frontend build). Vanilla JS + Chart.js CDN.

## API Integration Notes

### Polymarket (two APIs needed)
- **Gamma API** (`gamma-api.polymarket.com`): Market discovery with filtering (`active=true`). Offset pagination.
- **CLOB API** (`clob.polymarket.com`): Order book via `/book?token_id=`. No auth required.
- `clobTokenIds` and `outcomePrices` are JSON strings that need parsing
- Rate limits: ~900 req/10s for Gamma `/markets`, 1500/10s for CLOB `/book`

### Kalshi
- Base: `https://api.elections.kalshi.com/trade-api/v2` (serves ALL markets despite "elections" subdomain)
- Market data is PUBLIC — no auth needed for `GET /markets`, `/orderbook`, `/events`
- Use ONLY `*_dollars` (4 decimals) and `*_fp` (2 decimals) fields — integer fields deprecated March 5, 2026
- Orderbook returns ONLY bids. Compute asks: `YES_ask = 1.00 - highest_NO_bid`. Best bid = LAST element.
- Auth (if needed later): RSA-PSS signing, NOT Bearer token. Timestamp in milliseconds.
- Rate limits: 20 reads/sec (Basic tier). Cursor-based pagination, empty cursor = end.

## Commands

```bash
uv run arb-scanner scan          # Run one scan cycle
uv run arb-scanner watch         # Continuous polling loop
uv run arb-scanner report        # Generate latest arb report
uv run arb-scanner match-audit   # Dump cached contract matches for review
uv run arb-scanner history --pair POLY/KALSHI  # Spread history for a pair
uv run arb-scanner stats         # Aggregated analytics and scanner health
uv run arb-scanner alerts         # List recent trend alerts
uv run arb-scanner serve          # Start web dashboard at http://localhost:8000
uv run arb-scanner migrate       # Apply pending SQL migrations
uv run pytest                    # Run test suite (live tests excluded by default)
LIVE_TESTS=1 uv run pytest tests/live/ -v  # Run live API tests (requires network)
LIVE_TESTS=1 ANTHROPIC_API_KEY=sk-... uv run pytest tests/live/ -v  # Run all live tests including Claude
uv run mypy src/ --strict        # Type check
uv run ruff check src/ tests/    # Lint
uv run ruff format src/ tests/   # Format
```

## Quality Gates (all must pass before any task is complete)

1. `uv run ruff check src/ tests/` — zero lint errors
2. `uv run ruff format --check src/ tests/` — formatting clean
3. `uv run mypy src/ --strict` — zero type errors
4. `uv run pytest tests/ -x --tb=short` — all tests pass
5. `uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70` — coverage ≥70%

## Code Constraints

- No function longer than 50 lines; no module longer than 300 lines
- Pydantic models at all data boundaries (API responses, config, DB rows)
- All network I/O must be async (httpx, no requests/urllib)
- structlog JSON logging only, no print statements
- Fee schedules and thresholds in config.yaml, never hardcoded
- All public functions require docstrings with type hints

## SDD Artifacts

This project uses Spec-Driven Development (GitHub Spec-Kit):
- `.specify/memory/constitution.md` — project principles and constraints
- `.specify/features/*/spec.md` — feature requirements
- `.specify/features/*/plan.md` — implementation plans
- `.specify/features/*/tasks.md` — task breakdowns

## Active Technologies
- Python 3.11+ + httpx (async HTTP), pydantic v2, anthropic SDK, bm25s, asyncpg, typer, structlog, pyyaml (001-arb-scanner-core)
- PostgreSQL 15+ with pgvector extension (via asyncpg) (001-arb-scanner-core)
- Voyage AI (voyage-3-lite) + numpy + pgvector Python package for embedding pre-filter (003-pgvector-embedding-prefilter)

## Live Test Gating

Live API tests (`tests/live/`) are excluded from default `pytest` runs via `-m "not live"` in `pyproject.toml` addopts. To run them, set `LIVE_TESTS=1`. Claude semantic matching tests additionally require `ANTHROPIC_API_KEY`. Live tests hit real Polymarket Gamma/CLOB, Kalshi, and Anthropic APIs -- they need network access and may incur API costs.

## Recent Changes
- 006-dashboard-web-ui: Added FastAPI REST API with 11 endpoints wrapping existing repository methods. Vanilla JS dashboard with dark-theme UI, Chart.js spread/health charts, tab-based layout (Opportunities, Health, Alerts, Tickets). DashboardConfig for host/port. `serve` CLI command starts uvicorn. Ticket approve/expire from dashboard. Auto-refresh every 30s.
- 005-trend-alerting: Added TrendDetector engine with rolling-window convergence/divergence/new-high/disappeared/health detection. Alert webhooks dispatch via existing Slack/Discord infrastructure with distinct emoji/color per alert type. TrendAlertConfig with configurable thresholds, window size, and cooldown. Alert persistence to trend_alerts table (migration 010). New `alerts` CLI command.
- 004-live-api-testing: Added live API test suite (`tests/live/`) for Polymarket, Kalshi, and Claude semantic matching. Fixed Kalshi volume field bug (`volume_fp` -> `volume_dollars_24h_fp` with fallback). Added `live` pytest marker gated by `LIVE_TESTS=1` env var, excluded from default runs via addopts. Added `requires_live` and `requires_anthropic` skip markers in live conftest.
- 003-pgvector-embedding-prefilter: Added Voyage AI embedding client (`matching/embedding.py`), cosine-similarity reranker (`matching/embedding_prefilter.py`), `EmbeddingConfig` model, pgvector type registration in `db.py`, `UPDATE_MARKET_EMBEDDING` query + `update_market_embedding()` repository method, fire-and-forget embedding persistence in orchestrator, and integration tests for the full embedding pipeline
- 002-arb-history-analytics: Added `history` and `stats` CLI commands, analytics models (SpreadSnapshot, PairStats, ScannerHealth), analytics_repository with time-windowed queries, date-range filtering on `report`/`match-audit`, and V002 migration for spread_snapshots + scan_log tables
- 001-arb-scanner-core: Added Python 3.11+ + httpx (async HTTP), pydantic v2, anthropic SDK, bm25s, asyncpg, typer, structlog, pyyaml
