# Data Model: Cross-Venue Arbitrage Scanner

## Entities

### Market

Normalized representation of a binary prediction market from any venue.

| Field | Type | Description |
|-------|------|-------------|
| venue | Venue (enum) | "polymarket" or "kalshi" |
| event_id | string | Venue-specific event/condition identifier |
| title | string | Market question/title |
| description | string | Full description or resolution criteria |
| resolution_criteria | string | How the market resolves (rules text) |
| yes_bid | Decimal | Best YES bid price (0.00-1.00) |
| yes_ask | Decimal | Best YES ask price (0.00-1.00) |
| no_bid | Decimal | Best NO bid price (0.00-1.00) |
| no_ask | Decimal | Best NO ask price (0.00-1.00) |
| volume_24h | Decimal | 24-hour trading volume in dollars |
| expiry | datetime or None | Market close/expiry time |
| fees_pct | Decimal | Venue-specific fee rate |
| fee_model | string | "on_winnings" or "per_contract" |
| last_updated | datetime | When this data was fetched |
| raw_data | dict | Original API response preserved |

**Validation rules:**
- All prices must be in range [0.0, 1.0]
- `yes_bid <= yes_ask` and `no_bid <= no_ask`
- `event_id` must be non-empty
- `title` must be non-empty

**Venue-specific mapping:**
- Polymarket: `event_id` = `condition_id`, prices from `tokens[].price` or Gamma API `bestBid`/`bestAsk`, description from `description` field
- Kalshi: `event_id` = `ticker`, prices from `*_dollars` fields (parse string to Decimal), resolution from `rules_primary` + `rules_secondary`, expiry from `close_time`

### MatchResult

Outcome of LLM evaluation of two cross-venue contracts.

| Field | Type | Description |
|-------|------|-------------|
| poly_event_id | string | Polymarket condition_id |
| kalshi_event_id | string | Kalshi market ticker |
| match_confidence | float | 0.0-1.0 confidence score |
| resolution_equivalent | bool | Whether contracts resolve identically |
| resolution_risks | list[string] | Specific risks that could cause divergent resolution |
| safe_to_arb | bool | False if ANY plausible scenario of different resolution |
| reasoning | string | LLM's explanation |
| matched_at | datetime | When the match was evaluated |
| ttl_expires | datetime | When this cache entry expires |

**Validation rules:**
- `match_confidence` in [0.0, 1.0]
- `safe_to_arb` must be False if `resolution_equivalent` is False
- Cache key: `(poly_event_id, kalshi_event_id)`

### ArbOpportunity

A detected cross-venue mispricing with calculated economics.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Unique identifier |
| match | MatchResult | The underlying contract match |
| poly_market | Market | Polymarket side |
| kalshi_market | Market | Kalshi side |
| buy_venue | Venue | Where to buy YES |
| sell_venue | Venue | Where to buy NO (equivalent to selling YES) |
| cost_per_contract | Decimal | YES_ask + NO_ask |
| gross_profit | Decimal | 1.00 - cost_per_contract |
| net_profit | Decimal | After fees on both sides |
| net_spread_pct | Decimal | net_profit / cost_per_contract |
| max_size | Decimal | Min liquidity at quoted prices (dollars) |
| annualized_return | Decimal or None | If expiry known: net_spread_pct × (365 / days_to_expiry) |
| depth_risk | bool | True if max_size < thin_liquidity_threshold |
| detected_at | datetime | When opportunity was found |

**Validation rules:**
- `cost_per_contract` must be < 1.00 for a valid arb
- `gross_profit` = 1.00 - cost_per_contract
- `net_profit` must be > 0 after both venue fees applied
- `buy_venue` != `sell_venue`

### ExecutionTicket

Human-readable trade instruction. Never auto-executed.

| Field | Type | Description |
|-------|------|-------------|
| arb_id | string | References ArbOpportunity.id |
| leg_1 | dict | {"venue", "side", "price", "size"} |
| leg_2 | dict | {"venue", "side", "price", "size"} |
| expected_cost | Decimal | Total cost of both legs |
| expected_profit | Decimal | Net profit after fees |
| status | string | "pending", "approved", or "expired" |

### ScanLog

Record of each scan cycle for diagnostics.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Unique identifier |
| started_at | datetime | Scan start time |
| completed_at | datetime | Scan end time |
| poly_markets_fetched | int | Count from Polymarket |
| kalshi_markets_fetched | int | Count from Kalshi |
| candidate_pairs | int | After pre-filter |
| llm_evaluations | int | Pairs sent to Claude |
| opportunities_found | int | Qualified arbs |
| errors | list[string] | Any errors during scan |

## Relationships

```
Market (many) ←→ MatchResult (many-to-many via poly_event_id + kalshi_event_id)
MatchResult (1) → ArbOpportunity (0..1)
ArbOpportunity (1) → ExecutionTicket (1)
ScanLog (1) → ArbOpportunity (many, via detected_at within scan window)
```

## State Transitions

### MatchResult Lifecycle
```
[new pair detected] → cached (ttl_expires set)
[ttl_expires reached] → expired → re-evaluated → cached
[description changes] → invalidated → re-evaluated → cached
```

### ExecutionTicket Lifecycle
```
[arb detected] → pending
[user approves] → approved (manual, outside system)
[prices converge or expiry] → expired
```
