# 024 — Adaptive Signal Tuning: Implementation Plan

## Current State Audit

Depends on 023 being complete. Assumes these exist:
- `category_performance` table with per-category win rates, P&L, alignment metrics
- `optimal_params` table with sweep results
- `backtesting_repository.py` with read methods for both tables
- `models/backtesting.py` with `CategoryPerformance`, `OptimalParamSnapshot`

Key files to modify:

| File | Lines | Change |
|------|------:|--------|
| `models/backtesting.py` | ~150 (post-023) | Add TuningProposal, SizingRecommendation models |
| `models/config.py` | 368 | Add TuningConfig dataclass |
| `flippening/signal_generator.py` | existing | Inject confidence reweighting |
| `execution/flip_evaluator.py` | existing | Add position sizing to tickets |
| `storage/backtesting_repository.py` | ~250 (post-023) | Add tuning tables queries |
| `api/routes_backtesting.py` | ~180 (post-023) | Add tuning endpoints |
| `api/static/app.js` | 2054+ | Add tuning UI section |
| `notifications/webhook.py` | existing | Add tuning notification type |

## Architecture

```
New files:
  tuning/                          # New package
    __init__.py
    proposal_engine.py             # ~120 lines: Generate tuning proposals from perf data
    confidence_reweighter.py       # ~80 lines: Category-based confidence adjustment
    position_sizer.py              # ~100 lines: Kelly criterion sizing
    alert_filter.py                # ~80 lines: Suppress negative-edge categories
    config_overlay.py              # ~100 lines: In-memory config override layer
  storage/migrations/027_tuning.sql  # tuning_proposals, tuning_audit_log tables
  cli/tuning_commands.py           # ~100 lines: tuning-proposals, tuning-status, tuning-reset

Modified files:
  models/backtesting.py            # +~60 lines: TuningProposal, SizingRecommendation
  models/config.py                 # +~30 lines: TuningConfig
  storage/backtesting_repository.py  # +~80 lines: tuning queries
  storage/_backtesting_queries.py  # +~40 lines: tuning SQL
  flippening/signal_generator.py   # +~15 lines: confidence reweighting hook
  api/routes_backtesting.py        # +~60 lines: tuning endpoints
  api/static/app.js                # +~150 lines: tuning UI
  api/static/index.html            # +~40 lines: tuning section HTML
  cli/app.py                       # +3 lines: register tuning commands
  api/deps.py                      # minor: reuse backtest repo
```

## Phase 1: Data Models (~60 lines added to models/backtesting.py)

```python
class ProposalType(str, Enum):
    PARAMETER_OPTIMIZATION = "parameter_optimization"
    CONFIDENCE_REWEIGHT = "confidence_reweight"
    CATEGORY_SUPPRESSION = "category_suppression"
    ALERT_THRESHOLD = "alert_threshold"

class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"

class TuningProposal(BaseModel):
    id: int | None = None
    proposal_type: ProposalType
    category: str
    parameter: str
    current_value: float
    proposed_value: float
    justification: str
    confidence: float          # 0.0-1.0
    data_source: str           # "category_performance", "optimal_params", "signal_alignment"
    status: ProposalStatus
    sample_size: int
    low_confidence: bool       # True if below min_sample_size
    created_at: datetime
    resolved_at: datetime | None = None
    operator_notes: str | None = None

class SizingRecommendation(BaseModel):
    suggested_size_usdc: Decimal
    kelly_fraction: float
    fractional_kelly: float    # After applying fraction (default 0.25)
    sizing_basis: str          # Human-readable explanation
    category_win_rate: float
    avg_win: Decimal
    avg_loss: Decimal
```

**TuningConfig in `models/config.py`:**
```python
class TuningConfig(BaseModel):
    enabled: bool = False
    auto_apply: bool = False
    auto_apply_categories: list[str] = []
    min_sample_size: int = 20
    min_confidence: float = 0.7
    cooldown_days: int = 7
    proposal_schedule: str = "daily"  # "daily", "on_import", "manual"
    kelly_fraction: float = 0.25
    max_position_pct: float = 0.05    # 5% of capital per position
    max_category_pct: float = 0.20    # 20% of capital per category
    suppress_negative_edge: bool = True
```

## Phase 2: Database Migration

**New file: `storage/migrations/027_tuning.sql`**

```sql
CREATE TABLE IF NOT EXISTS tuning_proposals (
    id BIGSERIAL PRIMARY KEY,
    proposal_type TEXT NOT NULL,
    category TEXT NOT NULL,
    parameter TEXT NOT NULL,
    current_value NUMERIC(10,6) NOT NULL,
    proposed_value NUMERIC(10,6) NOT NULL,
    justification TEXT NOT NULL,
    confidence NUMERIC(6,4) NOT NULL,
    data_source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    sample_size INT NOT NULL DEFAULT 0,
    low_confidence BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    operator_notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_tuning_proposals_status ON tuning_proposals(status);
CREATE INDEX IF NOT EXISTS idx_tuning_proposals_category ON tuning_proposals(category);

CREATE TABLE IF NOT EXISTS tuning_audit_log (
    id BIGSERIAL PRIMARY KEY,
    proposal_id BIGINT REFERENCES tuning_proposals(id),
    parameter TEXT NOT NULL,
    old_value NUMERIC(10,6) NOT NULL,
    new_value NUMERIC(10,6) NOT NULL,
    approval_method TEXT NOT NULL CHECK (approval_method IN ('manual', 'auto')),
    operator_notes TEXT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tuning_audit_applied ON tuning_audit_log(applied_at);
```

## Phase 3: Config Overlay (~100 lines)

**New file: `tuning/config_overlay.py`**

In-memory override layer that sits between base config and signal generation:

- `ConfigOverlay` class: holds `dict[str, float]` of parameter overrides.
- `get(parameter, default)` — return override if exists, else default.
- `apply(proposal: TuningProposal)` — add override, log change.
- `revert(parameter)` — remove single override.
- `reset()` — clear all overrides.
- `active_overrides() -> dict` — list current overrides for status display.
- Thread-safe: use `threading.Lock` since config reads happen from async signal generation.

The overlay is a singleton, initialized at process startup, and injected into the signal generator and evaluator.

## Phase 4: Proposal Engine (~120 lines)

**New file: `tuning/proposal_engine.py`**

- `generate_proposals(category_perf, optimal_params, config, overlay) -> list[TuningProposal]`

Four proposal generators:

1. **Parameter optimization**: For each `optimal_params` row, if `optimal_value != current_config_value` and `win_rate_at_optimal > current_win_rate + 0.05`, generate proposal.
2. **Confidence reweighting**: Compute global average win rate. For each category with `trade_count >= min_sample_size`, propose `confidence_modifier = 0.5 + (category_win_rate / global_win_rate)` clamped to [0.5, 1.5].
3. **Category suppression**: If `total_pnl < 0` AND `win_rate < 0.35` AND `trade_count >= min_sample_size`, propose setting `enabled: false`.
4. **Alert threshold**: If `contrary_win_rate > aligned_win_rate + 0.10` with sufficient data, propose raising `min_confidence` for category.

Cooldown enforcement: skip proposals for parameters rejected within `cooldown_days`.

## Phase 5: Confidence Reweighter (~80 lines)

**New file: `tuning/confidence_reweighter.py`**

- `reweight_confidence(base_confidence, category, overlay, category_perf) -> float`
- Called from signal generator when producing `EntrySignal`.
- Reads `confidence_modifier` from overlay (if tuning approved) or base config.
- Applies signal alignment bonus if `aligned_win_rate > 0.6`.
- Returns adjusted confidence, clamped to [0.0, 1.0].

**Modification to `flippening/signal_generator.py`:**
- Add optional `confidence_reweighter` parameter to `SignalGenerator.__init__`.
- If present, call `reweight_confidence()` after base confidence calculation.
- If absent (tuning disabled), behavior is identical to pre-024.

## Phase 6: Position Sizer (~100 lines)

**New file: `tuning/position_sizer.py`**

- `compute_sizing(category, category_perf, capital, config) -> SizingRecommendation`
- Kelly formula: `f = (p * avg_win - (1-p) * avg_loss) / avg_win` where `p = win_rate`.
- Apply `kelly_fraction` (default 0.25 = quarter Kelly).
- Cap at `max_position_pct * total_capital`.
- Check category exposure against `max_category_pct`.
- Return `SizingRecommendation` with all intermediate values for transparency.

Edge cases:
- Negative Kelly → suggest $0, flag "no edge."
- Zero avg_win → suggest minimum size.
- win_rate = 1.0 with < 15 trades → flag `low_confidence`, suggest conservative size.

## Phase 7: Alert Filter (~80 lines)

**New file: `tuning/alert_filter.py`**

- `should_suppress(category, category_perf, config) -> tuple[bool, str]`
- Returns `(suppress, reason)`.
- Check negative-edge suppression: `total_pnl < 0 AND win_rate < 0.35 AND trade_count >= 20`.
- Check inverse-signal detection: `contrary_win_rate > aligned_win_rate + 0.10`.
- Check losing streak: last 10 trades all losses (query from imported_trades).
- Used by webhook dispatcher and dashboard alert rendering.

**Modification to signal generation path:**
- Before dispatching webhook notification, check `should_suppress()`.
- If suppressed, log at DEBUG level, skip webhook, but still write signal to DB (for audit).

## Phase 8: Storage Layer (~120 lines added across existing files)

**`storage/_backtesting_queries.py` (+~40 lines):**
- INSERT/SELECT/UPDATE tuning_proposals, INSERT tuning_audit_log, SELECT audit_log.

**`storage/backtesting_repository.py` (+~80 lines):**
- `create_proposal(proposal) -> int` — Insert, return ID.
- `get_pending_proposals(category?) -> list[TuningProposal]`
- `approve_proposal(id, notes?) -> TuningProposal` — Update status, create audit log entry.
- `reject_proposal(id, notes?) -> TuningProposal` — Update status.
- `get_audit_log(limit?, since?) -> list[dict]`
- `check_cooldown(category, parameter) -> bool` — Has this been rejected within cooldown?

## Phase 9: CLI Commands (~100 lines)

**New file: `cli/tuning_commands.py`**

- `tuning-proposals` — List pending proposals (table format: ID, category, parameter, current → proposed, confidence).
- `tuning-proposals --approve <id>` — Approve proposal, apply to overlay, log.
- `tuning-proposals --reject <id> --reason "..."` — Reject with notes.
- `tuning-status` — Show active overrides vs. base config (table: parameter, base value, override value, applied_at).
- `tuning-reset` — Clear all overrides, confirm with user.

Register in `cli/app.py`.

## Phase 10: API Routes (~60 lines added)

**Add to `api/routes_backtesting.py`:**

- `GET /api/tuning/proposals` — Pending proposals (with optional `?status=` filter).
- `POST /api/tuning/proposals/{id}/approve` — Approve and apply.
- `POST /api/tuning/proposals/{id}/reject` — Reject with optional body `{notes: "..."}`.
- `GET /api/tuning/status` — Active overrides.
- `POST /api/tuning/reset` — Clear all overrides.
- `GET /api/tuning/audit-log` — Audit trail.

## Phase 11: Dashboard Integration (~190 lines across HTML + JS)

**Add to Backtest tab in `index.html` (+~40 lines):**
- "Tuning Proposals" section: table with approve/reject buttons.
- "Active Overrides" section: current vs. base config.
- Category health heatmap (color-coded table).

**Add to `app.js` (+~150 lines):**
- `loadTuningSection()` — Fetch proposals, status, render.
- Approve/reject handlers → POST to API → refresh.
- Category heatmap: win_rate → green/yellow/red coloring.
- Position sizing display on active signal cards (if sizing data available).

## Phase 12: Tests (~400 lines)

**New test files:**
- `tests/unit/test_proposal_engine.py` — All 4 proposal types, cooldown, min sample size, edge cases.
- `tests/unit/test_confidence_reweighter.py` — Boost/reduce/clamp, alignment bonus, disabled state.
- `tests/unit/test_position_sizer.py` — Kelly formula, negative edge, caps, fractional Kelly.
- `tests/unit/test_alert_filter.py` — Suppression logic, inverse signal, losing streak.
- `tests/unit/test_config_overlay.py` — Apply, revert, reset, thread safety.
- `tests/unit/test_tuning_commands.py` — CLI approve/reject/status/reset.
- `tests/unit/test_tuning_routes.py` — API endpoints with mock data.

## Implementation Order

| Phase | Depends On | Complexity | Est. New Lines |
|-------|-----------|------------|----------------|
| 1. Data Models | 023 complete | Low | ~90 |
| 2. Migration | None | Low | ~30 |
| 3. Config Overlay | Phase 1 | Medium | ~100 |
| 4. Proposal Engine | Phase 1, 3 | High | ~120 |
| 5. Confidence Reweighter | Phase 3 | Medium | ~80 |
| 6. Position Sizer | Phase 1 | Medium | ~100 |
| 7. Alert Filter | Phase 1 | Low | ~80 |
| 8. Storage Layer | Phase 1, 2 | Medium | ~120 |
| 9. CLI Commands | Phase 3, 4, 8 | Medium | ~100 |
| 10. API Routes | Phase 3, 4, 8 | Medium | ~60 |
| 11. Dashboard | Phase 10 | Medium | ~190 |
| 12. Tests | All above | Medium | ~400 |
| **Total** | | | **~1,470** |

## Key Design Decisions

1. **In-memory overlay, not YAML mutation**: Tuning changes are volatile — restarting the process reverts to base config. This is safer than auto-editing config files (no merge conflicts, no accidental overwrites, easy rollback). If an operator likes a tuning change, they manually update YAML.

2. **Proposal queue, not auto-apply default**: Constitution Principle I demands human oversight. Auto-apply is opt-in per category, requiring explicit trust from the operator.

3. **Quarter Kelly, not full Kelly**: Full Kelly sizing is theoretically optimal but assumes perfect edge estimation and leads to extreme volatility. Quarter Kelly is industry standard for markets with noisy edge estimates.

4. **Separate `tuning/` package**: Signal tuning is conceptually distinct from both backtesting (023) and execution (022). It sits between analysis and action — reading from 023's data, writing to the signal generation pipeline.

5. **Suppression logs to DB, skips webhook only**: Suppressed signals are still recorded for audit. This means the operator can review what was suppressed and override if needed. No data loss from filtering.

6. **No config.yaml persistence by design**: Auto-writing config files is brittle (formatting loss, comment stripping, env var interpolation issues). The overlay pattern is cleaner and the operator can always run `tuning-status` to see what's active and manually port changes to YAML.
