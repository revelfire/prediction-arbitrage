# Implementation Plan: Cross-Venue Arbitrage Scanner

**Branch**: `001-arb-scanner-core` | **Date**: 2026-02-24 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-arb-scanner-core/spec.md`

## Summary

Build a Python CLI application that detects cross-venue arbitrage opportunities between Polymarket and Kalshi prediction markets. Uses BM25 pre-filtering + Claude semantic matching to pair equivalent contracts, calculates net profit after venue-specific fees, and alerts via webhook. Human-in-the-loop: produces execution tickets, never trades.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: httpx (async HTTP), pydantic v2, anthropic SDK, bm25s, asyncpg, typer, structlog, pyyaml
**Storage**: PostgreSQL 15+ with pgvector extension (via asyncpg)
**Testing**: pytest + pytest-asyncio, httpx MockTransport, pytest-postgresql
**Target Platform**: macOS/Linux (CLI)
**Project Type**: CLI application
**Performance Goals**: Complete scan cycle < 2 minutes for 1000 markets/venue
**Constraints**: Poll-based (no WebSocket), no auto-trading, 24h match cache TTL
**Scale/Scope**: ~1000 active markets per venue, ~200 candidate pairs per scan, single user

## Constitution Check

*GATE: All principles verified. No violations.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Human-in-the-Loop | PASS | ExecutionTicket model has status field, no trading endpoints, CLI produces tickets only |
| II. Pydantic at Every Boundary | PASS | Market, MatchResult, ArbOpportunity, ExecutionTicket, Config all Pydantic v2 models |
| III. Async-First I/O | PASS | httpx.AsyncClient for all venue/Claude API calls, asyncpg for DB |
| IV. Structured Logging | PASS | structlog with JSON output, module+operation+ID context in all log entries |
| V. Two-Pass Matching | PASS | BM25 pre-filter (bm25s) → Claude semantic evaluation. Direct all-pairs LLM calls prohibited |
| VI. Configuration Over Code | PASS | Fee schedules, endpoints, thresholds, model selection all in config.yaml with env var interpolation |

## Project Structure

### Documentation (this feature)

```text
specs/001-arb-scanner-core/
├── plan.md              # This file
├── research.md          # API research findings
├── data-model.md        # Entity definitions
├── quickstart.md        # Setup and usage guide
├── contracts/
│   ├── cli.md           # CLI command interface
│   └── notifications.md # Webhook payload formats
└── tasks.md             # Task breakdown (created by /speckit.tasks)
```

### Source Code (repository root)

```text
src/arb_scanner/
├── __init__.py
├── __main__.py          # Entry point
├── models/
│   ├── __init__.py
│   ├── market.py        # Market, Venue enum
│   ├── matching.py      # MatchResult
│   ├── arbitrage.py     # ArbOpportunity, ExecutionTicket
│   └── config.py        # Settings, FeeSchedule, VenueConfig
├── config/
│   ├── __init__.py
│   └── loader.py        # YAML parser, env var interpolation
├── ingestion/
│   ├── __init__.py
│   ├── base.py          # Abstract venue client
│   ├── polymarket.py    # PolymarketClient (Gamma API + CLOB)
│   └── kalshi.py        # KalshiClient (public endpoints)
├── matching/
│   ├── __init__.py
│   ├── prefilter.py     # BM25 pre-filter (bm25s)
│   ├── semantic.py      # Claude semantic matcher
│   └── cache.py         # PostgreSQL-backed match cache with TTL
├── engine/
│   ├── __init__.py
│   ├── calculator.py    # Arb spread/fee calculation
│   └── tickets.py       # Execution ticket generator
├── storage/
│   ├── __init__.py
│   ├── db.py            # asyncpg connection pool management
│   ├── repository.py    # CRUD for all entities
│   └── migrations/      # Numbered SQL migration files
├── notifications/
│   ├── __init__.py
│   ├── webhook.py       # Slack/Discord webhook dispatcher
│   └── reporter.py      # Markdown report formatter
├── cli/
│   ├── __init__.py
│   └── app.py           # Typer app: scan, watch, report, match-audit
└── utils/
    ├── __init__.py
    ├── retry.py          # Exponential backoff with jitter
    └── rate_limiter.py   # asyncio.Semaphore-based per-venue rate limiter

tests/
├── conftest.py           # Shared fixtures, mock transports, test DB
├── fixtures/             # JSON fixtures for mocked API responses
│   ├── polymarket_markets.json
│   ├── kalshi_markets.json
│   ├── kalshi_orderbook.json
│   └── claude_match_response.json
├── unit/
│   ├── test_models.py
│   ├── test_config_loader.py
│   ├── test_prefilter.py
│   ├── test_calculator.py
│   ├── test_fee_models.py
│   └── test_tickets.py
├── integration/
│   ├── test_polymarket_client.py
│   ├── test_kalshi_client.py
│   ├── test_semantic_matcher.py
│   ├── test_match_cache.py
│   ├── test_repository.py
│   └── test_webhook.py
└── e2e/
    └── test_scan_pipeline.py

config.example.yaml       # Example configuration
pyproject.toml            # Project metadata and dependencies
```

**Structure Decision**: Single-project CLI layout. `src/arb_scanner/` package with module-per-concern. Tests mirror source structure with unit/integration/e2e separation.

## Key Technical Decisions from Research

### Polymarket: Dual-API Strategy

Must use **two** Polymarket APIs:
- **Gamma API** (`gamma-api.polymarket.com`): Market discovery with `active=true&closed=false` filtering. Returns rich metadata including `outcomePrices`, `clobTokenIds`, `bestBid`/`bestAsk`. Offset-based pagination.
- **CLOB API** (`clob.polymarket.com`): Order book depth via `/book?token_id=`. Used for liquidity assessment (max executable size).

The CLOB `/markets` endpoint has NO filtering (returns all ~494k markets including archived). Gamma API is the correct entry point for discovery.

### Kalshi: Public Endpoints, No Auth Needed

Market data endpoints (`GET /markets`, `GET /markets/{ticker}/orderbook`) are **public** — no authentication required for v1 scanner.

**Critical:** Use ONLY `*_dollars` string fields for prices (4 decimal places) and `*_fp` fields for volumes. Integer cent fields deprecated March 5, 2026.

**Orderbook quirk:** Only bids returned. Compute asks: `YES_ask = 1.00 - highest_NO_bid`. Best bid is LAST array element (ascending sort).

### BM25 Pre-Filter: bm25s with BM25+

Use `bm25s` library (v0.3.0) with `method="bm25+"` for short-text recall. Built-in tokenizer with stopword removal. Rebuild full index per scan cycle (~milliseconds for 1000 titles). No incremental indexing needed at this scale.

### Fee Calculation: Two Distinct Models

| Venue | Model | Calculation |
|-------|-------|------------|
| Polymarket | % on net winnings | `fee = taker_fee_pct × (payout - cost)` where payout = $1.00 |
| Kalshi | Per-contract flat | `fee = min(taker_fee, fee_cap)` per contract |

Both models must be applied to the same opportunity to calculate true net profit.

## Complexity Tracking

> No constitution violations. No complexity justifications needed.
