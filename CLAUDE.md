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
├── matching/        # Pre-filter (BM25 + pgvector), Claude semantic matcher, cache layer
├── engine/          # Arb calculator, combinatorial checker (stretch)
├── storage/         # PostgreSQL + pgvector repository, migrations
├── notifications/   # Webhook dispatcher (Slack/Discord), stdout reporter
├── cli/             # Typer app: scan, watch, report, match-audit commands
└── utils/           # Retry logic, rate limiter, async helpers
```

## Key Design Decisions

- Poll-based (no WebSocket in v1), default 60s interval
- Claude Sonnet for matching (cost-effective for high-volume pair evaluation)
- Human-in-the-loop: system produces execution tickets, never places orders
- Match results cached in PostgreSQL with 24h TTL
- BM25 pre-filter via bm25s (method="bm25+") reduces candidate pairs before Claude API calls
- Fee models differ per venue (Polymarket: % on winnings; Kalshi: per-contract flat fee)

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
uv run pytest                    # Run test suite
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

## Recent Changes
- 001-arb-scanner-core: Added Python 3.11+ + httpx (async HTTP), pydantic v2, anthropic SDK, bm25s, asyncpg, typer, structlog, pyyaml
