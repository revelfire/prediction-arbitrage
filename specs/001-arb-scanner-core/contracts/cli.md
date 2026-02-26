# CLI Contract: arb-scanner

## Commands

### `arb-scanner scan`

Run a single scan cycle: ingest → match → calculate → output.

```
arb-scanner scan [--dry-run] [--min-spread PCT] [--output FORMAT]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dry-run` | bool | false | Use test fixtures, no network calls |
| `--min-spread` | float | 0.02 | Minimum net spread % to report |
| `--output` | string | "json" | Output format: "json" or "table" |

**Exit codes:** 0 = success, 1 = error, 2 = partial (one venue failed)

**stdout (json):**
```json
{
  "scan_id": "uuid",
  "timestamp": "ISO8601",
  "venues_polled": ["polymarket", "kalshi"],
  "markets_ingested": {"polymarket": 500, "kalshi": 300},
  "candidate_pairs": 180,
  "opportunities": [
    {
      "id": "uuid",
      "poly_title": "...",
      "kalshi_title": "...",
      "match_confidence": 0.95,
      "buy_venue": "polymarket",
      "buy_side": "YES",
      "buy_price": 0.62,
      "sell_venue": "kalshi",
      "sell_side": "NO",
      "sell_price": 0.35,
      "cost": 0.97,
      "gross_profit": 0.03,
      "net_profit": 0.018,
      "net_spread_pct": 0.0186,
      "max_size_usd": 150.0,
      "annualized_return": 0.34,
      "depth_risk": false
    }
  ],
  "summary": {"total_opportunities": 1, "best_spread_pct": 0.0186}
}
```

### `arb-scanner watch`

Continuous polling loop with webhook alerts.

```
arb-scanner watch [--interval SECS] [--min-spread PCT]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--interval` | int | 60 | Seconds between scan cycles |
| `--min-spread` | float | 0.02 | Minimum spread to trigger alert |

**Behavior:** Runs indefinitely. Ctrl+C for graceful shutdown. Logs to stderr. Alerts via configured webhooks.

### `arb-scanner report`

Generate a Markdown report of latest opportunities.

```
arb-scanner report [--last N] [--format FORMAT]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--last` | int | 10 | Number of recent opportunities |
| `--format` | string | "markdown" | "markdown" or "json" |

**stdout:** Markdown table of execution tickets sorted by net spread descending.

### `arb-scanner match-audit`

Dump all cached contract matches.

```
arb-scanner match-audit [--include-expired] [--min-confidence FLOAT]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--include-expired` | bool | false | Show expired cache entries |
| `--min-confidence` | float | 0.0 | Filter by minimum confidence |

**stdout:** Tabular output with columns: poly_id, kalshi_id, confidence, equivalent, safe, reasoning (truncated), expires.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (unless --dry-run) | Claude API key |
| `KALSHI_API_KEY` | No (market data is public) | For future trading features |
| `KALSHI_PRIVATE_KEY_PATH` | No | RSA private key for Kalshi auth |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ARBITRAGE_SLACK_WEBHOOK_URL` | No | Slack notification webhook |
| `DISCORD_WEBHOOK_URL` | No | Discord notification webhook |

## Config File

Default: `config.yaml` in working directory. Override with `ARB_SCANNER_CONFIG` env var.
