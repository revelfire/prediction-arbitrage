# Quickstart: Prediction Market Arbitrage Scanner

## Prerequisites

- Python 3.11+
- uv (package manager)
- PostgreSQL 15+ with pgvector extension
- Anthropic API key (for Claude-powered matching)

## Setup

```bash
# Clone and enter project
git clone git@github.com:revelfire/prediction-arbitrage.git
cd prediction-arbitrage/prediction-market-arb-scanner

# Install dependencies
uv sync

# Set up PostgreSQL database
createdb arb_scanner
psql arb_scanner -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Configure environment
export DATABASE_URL="postgresql://localhost/arb_scanner"
export ANTHROPIC_API_KEY="sk-ant-..."

# Optional: notification webhooks
export ARBITRAGE_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

# Copy and edit config
cp config.example.yaml config.yaml
# Edit config.yaml to adjust thresholds, intervals, etc.

# Run database migrations
uv run arb-scanner migrate

# Verify installation
uv run arb-scanner scan --dry-run
```

## Usage

```bash
# Single scan (dry run with test fixtures)
uv run arb-scanner scan --dry-run

# Single scan (live data)
uv run arb-scanner scan

# Continuous monitoring
uv run arb-scanner watch --interval 60

# View recent opportunities
uv run arb-scanner report

# Audit contract matches
uv run arb-scanner match-audit
```

## Development

```bash
# Run tests
uv run pytest

# Type check
uv run mypy src/ --strict

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## Architecture Overview

```
User runs scan → Ingest markets from Polymarket + Kalshi (async, concurrent)
                → BM25 pre-filter reduces candidate pairs by ~80%
                → Claude evaluates top candidates for semantic equivalence
                → Calculate arb spreads after venue-specific fees
                → Persist to PostgreSQL, generate execution tickets
                → Alert via webhook if spread exceeds threshold
```
