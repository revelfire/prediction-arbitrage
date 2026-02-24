# Prediction Market Arbitrage Scanner

Cross-venue arbitrage scanner for Polymarket and Kalshi prediction markets. Poll-based, CLI-only, no auto-trading. Detects mispricings across venues, calculates net profit after venue-specific fees, and alerts via webhook. Uses Claude for semantic contract matching to determine whether markets on different venues resolve equivalently.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- PostgreSQL 15+ with pgvector extension
- Anthropic API key (for Claude-powered semantic matching)

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
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

# Copy and edit config
cp config.example.yaml config.yaml
# Edit config.yaml to adjust thresholds, intervals, etc.

# Run database migrations
uv run arb-scanner migrate

# Verify installation with a dry run (uses test fixtures, no network calls)
uv run arb-scanner scan --dry-run
```

## Usage

### Single Scan

Run one scan cycle: ingest markets from both venues, match contracts, calculate spreads, and output results.

```bash
# Dry run with test fixtures (no API keys required)
uv run arb-scanner scan --dry-run

# Live scan
uv run arb-scanner scan

# With minimum spread filter and table output
uv run arb-scanner scan --min-spread 0.03 --output table
```

Exit codes: 0 = success, 1 = error, 2 = partial (one venue failed).

### Continuous Monitoring

Poll both venues on a loop with webhook alerts for new opportunities.

```bash
# Default 60-second interval
uv run arb-scanner watch

# Custom interval and minimum alert spread
uv run arb-scanner watch --interval 30 --min-spread 0.05
```

Press Ctrl+C for graceful shutdown.

### Reports

Generate a report of recent opportunities with execution ticket status.

```bash
# Markdown report of last 10 opportunities (default)
uv run arb-scanner report

# JSON format, last 20
uv run arb-scanner report --last 20 --format json
```

### Match Audit

Review cached contract matches from the Claude semantic matcher.

```bash
# Active matches only
uv run arb-scanner match-audit

# Include expired entries, filter by confidence
uv run arb-scanner match-audit --include-expired --min-confidence 0.8
```

### Database Migrations

Apply pending SQL migrations to the configured database.

```bash
uv run arb-scanner migrate
```

## Architecture

```
User runs scan -> Ingest markets from Polymarket + Kalshi (async, concurrent)
               -> BM25 pre-filter reduces candidate pairs by ~80%
               -> Claude evaluates top candidates for semantic equivalence
               -> Calculate arb spreads after venue-specific fees
               -> Persist to PostgreSQL, generate execution tickets
               -> Alert via webhook if spread exceeds threshold
```

### Source Layout

```
src/arb_scanner/
  cli/             Typer app: scan, watch, report, match-audit, migrate
  config/          YAML config loader with env var interpolation
  ingestion/       Async API clients for Polymarket (Gamma + CLOB) and Kalshi
  matching/        BM25 pre-filter, Claude semantic matcher, match cache
  engine/          Arb calculator, execution ticket generator
  storage/         PostgreSQL + pgvector repository, migrations runner
  notifications/   Webhook dispatcher (Slack/Discord), stdout reporter
  models/          Pydantic data models (Market, MatchResult, ArbOpportunity)
  utils/           Retry logic, rate limiter, structured logging
```

### Key Design Decisions

- **Poll-based**: No WebSocket in v1; default 60-second scan interval.
- **Human-in-the-loop**: The system produces execution tickets but never places orders.
- **Claude Sonnet for matching**: Cost-effective for high-volume pair evaluation with resolution risk assessment.
- **Match cache**: Results cached in PostgreSQL with configurable TTL (default 24h) to avoid redundant API calls.
- **Venue-specific fees**: Polymarket uses percentage on winnings; Kalshi uses per-contract flat fee with cap.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (unless `--dry-run`) | Claude API key for semantic matching |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SLACK_WEBHOOK_URL` | No | Slack notification webhook URL |
| `DISCORD_WEBHOOK_URL` | No | Discord notification webhook URL |
| `ARB_SCANNER_CONFIG` | No | Path to config file (default: `config.yaml`) |

## Development

```bash
# Run tests
uv run pytest

# Type check
uv run mypy src/ --strict

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Coverage report
uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70
```
