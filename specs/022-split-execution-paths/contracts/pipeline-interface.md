# Pipeline Interface Contract

Both `ArbAutoExecutionPipeline` and `FlipAutoExecutionPipeline` expose a shared control interface for the dashboard and CLI.

## Control Methods

```
set_mode(mode: "off" | "manual" | "auto") -> None
    Sets pipeline operation mode.

kill() -> None
    Emergency stop. Prevents all new trades until mode is reset.

mode -> str (property)
    Returns current mode string.
```

## Processing Methods

```
# Arb pipeline
process_opportunity(opportunity: dict, source: str) -> AutoExecLogEntry | None
    Entry point for arb opportunities from scan watch loop.

# Flip pipeline
process_opportunity(opportunity: dict, source: str) -> AutoExecLogEntry | None
    Entry point for flippening opportunities from _orch_processing.

process_exit(exit_sig: ExitSignal, entry_sig: EntrySignal, event: FlippeningEvent) -> None
    Entry point for flippening exit signals. Flip pipeline only.
```

## Dashboard API Contracts

### GET /api/auto-execution/status

Response includes per-pipeline status:

```json
{
  "mode": "auto",
  "killed": false,
  "arb_breaker": {"name": "failure", "tripped": false, "count": 0},
  "flip_breaker": {"name": "failure", "tripped": false, "count": 0},
  "loss_breaker": {"name": "loss", "tripped": false},
  "anomaly_breaker_arb": {"name": "anomaly", "tripped": false},
  "anomaly_breaker_flip": {"name": "anomaly", "tripped": false}
}
```

### GET /api/auto-execution/positions

Each position includes explicit `pipeline_type`:

```json
[
  {
    "arb_id": "uuid",
    "pipeline_type": "arb",
    "poly_market_id": "...",
    "kalshi_ticker": "...",
    "entry_spread": 0.045,
    "entry_cost_usd": 25.0,
    "status": "open",
    "opened_at": "2026-03-04T15:00:00Z"
  },
  {
    "arb_id": "uuid",
    "pipeline_type": "flip",
    "market_id": "...",
    "side": "yes",
    "size_contracts": 100,
    "entry_price": 0.37,
    "max_hold_minutes": 45,
    "status": "open",
    "opened_at": "2026-03-04T15:10:00Z"
  }
]
```

### Activity Feed Events

```json
{
  "type": "trade_executed",
  "arb_id": "uuid",
  "pipeline": "flip",
  "ts": "2026-03-04T15:10:00Z",
  "title": "...",
  "status": "complete"
}
```
