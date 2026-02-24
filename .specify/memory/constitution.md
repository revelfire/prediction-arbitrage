<!--
  Sync Impact Report
  Version change: 1.0.0 → 1.1.0 (pre-filter, storage, coverage amendments)
  Modified principles: V (TF-IDF → BM25 pre-filter)
  Modified sections: Technology Constraints (SQLite → PostgreSQL+pgvector), Quality Gates (80% → 70%)
  Templates requiring updates: ✅ None
  Follow-up TODOs: None
-->

# Prediction Market Arbitrage Scanner Constitution

## Core Principles

### I. Human-in-the-Loop Execution

The system MUST produce execution tickets (structured data describing what to buy/sell) but MUST NEVER place orders or interact with trading APIs. All trade decisions remain with the human operator. This is a detection and alerting system, not a trading bot.

### II. Pydantic at Every Boundary

All data flowing across system boundaries — API responses, config files, database rows, inter-module communication — MUST use Pydantic v2 models with strict validation. No raw dicts at module interfaces. This ensures type safety, self-documenting data contracts, and fail-fast behavior on malformed input.

### III. Async-First I/O

All network I/O (venue API calls, Claude API calls, webhook dispatches) MUST use async/await with httpx. Synchronous HTTP calls are prohibited. This enables concurrent polling of multiple venues and batched Claude API calls without thread overhead.

### IV. Structured Logging Always

All modules MUST use structlog with JSON output. No print statements. No f-string logging. Every log entry MUST include at minimum: module name, operation, and relevant IDs (event_id, arb_id). This enables post-hoc analysis and debugging of scan cycles.

### V. Two-Pass Matching Pipeline

Contract matching MUST follow a two-pass architecture: (1) cheap BM25 keyword pre-filter to reduce candidate pairs, then (2) Claude API semantic evaluation of survivors. Direct all-pairs Claude API calls are prohibited — the pre-filter MUST run first to control cost and latency. pgvector embeddings MAY be used as an additional or alternative pre-filter signal alongside BM25.

### VI. Configuration Over Code

Fee schedules, API endpoints, thresholds, polling intervals, model selection, and notification targets MUST live in YAML config with environment variable interpolation. No hardcoded venue-specific values in application code. Config changes MUST NOT require code changes or redeployment.

## Technology Constraints

- **Language**: Python 3.11+ (required for modern type syntax: `X | None`, `match` statements)
- **Package Manager**: uv (not pip, not poetry, not conda)
- **HTTP Client**: httpx (async mode only)
- **Data Validation**: Pydantic v2 with strict mode
- **LLM SDK**: anthropic Python SDK (Claude Sonnet for matching)
- **CLI Framework**: typer
- **Storage**: PostgreSQL + pgvector (via asyncpg, no ORM)
- **Logging**: structlog (JSON format)
- **Config**: pyyaml with env var interpolation
- **Testing**: pytest + pytest-asyncio, httpx MockTransport for API mocking
- **Linting**: ruff (lint + format)
- **Type Checking**: mypy with --strict

## Quality Gates

Every task and every PR MUST pass all five gates before being considered complete. Failures MUST be fixed immediately without human intervention:

1. `uv run ruff check src/ tests/` — zero lint errors
2. `uv run ruff format --check src/ tests/` — formatting clean
3. `uv run mypy src/ --strict` — zero type errors
4. `uv run pytest tests/ -x --tb=short` — all tests pass
5. `uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70` — coverage ≥70%

Additional constraints:
- No function longer than 50 lines
- No module longer than 300 lines
- Every module has a corresponding test file
- All public functions have docstrings with type hints

## Governance

This constitution supersedes all ad-hoc decisions during implementation. Amendments require:

1. Documentation of the change and rationale
2. Version bump (MAJOR for principle changes, MINOR for additions, PATCH for clarifications)
3. Update to CLAUDE.md if the change affects development workflow

**Version**: 1.1.0 | **Ratified**: 2026-02-24 | **Last Amended**: 2026-02-24
