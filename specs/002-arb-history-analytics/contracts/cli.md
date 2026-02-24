# CLI Contract: Analytics Commands

## New Commands

### `arb-scanner history`

Show spread history for a specific market pair.

```
arb-scanner history --pair <POLY_ID>/<KALSHI_ID> [--hours N] [--format table|json]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--pair` | `str` | (required) | Pair ID in format `POLY_EVENT_ID/KALSHI_EVENT_ID` |
| `--hours` | `int` | 24 | Time window in hours |
| `--format` | `str` | `table` | Output format: `table` or `json` |

**Table output:**
```
Spread History: abc123 / KALSHI-XYZ  (last 24h)
DETECTED_AT          NET_SPREAD  ANNUALIZED  DEPTH_RISK  MAX_SIZE
2026-02-24 15:30:00  3.20%       41.6%       No          $500
2026-02-24 14:30:00  2.80%       36.4%       No          $500
2026-02-24 13:30:00  1.50%       19.5%       Yes         $200
(3 data points)
```

**JSON output:** Array of `SpreadSnapshot` objects.

**Exit codes:** 0 = success, 1 = error (no DB, invalid pair format)

### `arb-scanner stats`

Show aggregated statistics and scanner health.

```
arb-scanner stats [--hours N] [--top N] [--format table|json]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--hours` | `int` | 24 | Time window in hours |
| `--top` | `int` | 10 | Number of top pairs to show |
| `--format` | `str` | `table` | Output format: `table` or `json` |

**Table output:**
```
Top Pairs by Peak Spread (last 24h)
POLY_ID      KALSHI_ID    PEAK    AVG     DETECTIONS  FIRST_SEEN           LAST_SEEN
abc123       KALSHI-XYZ   3.20%   2.50%   15          2026-02-24 08:00     2026-02-24 15:30
def456       KALSHI-ABC   2.10%   1.80%   8           2026-02-24 10:00     2026-02-24 14:00

Scanner Health (last 24h)
HOUR                 SCANS  AVG_DURATION  LLM_CALLS  OPPS_FOUND  ERRORS
2026-02-24 15:00     60     12.3s         45         3           0
2026-02-24 14:00     58     14.1s         52         5           1
```

**Exit codes:** 0 = success, 1 = error

## Extended Commands

### `arb-scanner report` (extended)

New optional flags:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--since` | `str` | None | ISO 8601 date/datetime filter (inclusive) |
| `--until` | `str` | None | ISO 8601 date/datetime filter (exclusive) |

`--since`/`--until` and `--last` are mutually exclusive. If both provided, `--since`/`--until` takes precedence.

### `arb-scanner match-audit` (extended)

New optional flag:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--since` | `str` | None | ISO 8601 date/datetime filter on `matched_at` |
