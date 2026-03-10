# 024 — Adaptive Signal Tuning

## Overview

Close the feedback loop between backtesting analysis (023) and live signal generation. The system reads persisted performance metrics (category win rates, signal alignment data, optimal parameter snapshots) and automatically adjusts signal confidence, category thresholds, alert filtering, and position sizing recommendations. The operator retains full control via approval/reject workflow — the system **proposes** tuning changes, never silently applies them.

## Motivation

Feature 023 answers "how am I doing?" and "which signals work?" but the operator must manually update `config.yaml` to act on those insights. This creates a gap:

1. **Stale parameters**: `spike_threshold_pct` was tuned once via `flip-sweep` but market dynamics change. The system knows the optimal value has shifted but doesn't act on it.
2. **Uniform confidence**: All signals within a category get the same confidence score, even though backtesting shows BTC threshold markets are 3x more profitable than sports spreads.
3. **Alert fatigue**: The operator receives signals for market types where they consistently lose money. The system has the data to suppress these but doesn't.
4. **No sizing intelligence**: Tickets say "buy Yes" but not "how much." Historical win rates and average P&L per category provide the inputs for Kelly-criterion sizing, but nothing computes or presents it.

This feature transforms the scanner from a static detection tool into an **adaptive system** that learns from outcomes — while keeping the human in the loop per Constitution Principle I.

## Constitution Amendment

### Principle I: Human-in-the-Loop Execution (Amended)

**Current text (v2.0.0, post-018):**
> The system MUST produce execution tickets but MUST NEVER place orders without explicit operator confirmation.

**Proposed text (v2.1.0):**
> The system MUST produce execution tickets but MUST NEVER place orders without explicit operator confirmation. The system MAY automatically adjust signal parameters (confidence weights, thresholds, alert filters) based on historical performance data, provided: (a) all adjustments are logged with before/after values, (b) the operator can review pending adjustments before they take effect via an approval queue, and (c) a hard override exists to revert any automatic adjustment to manual config values.

**Rationale:** Adjusting signal *generation* parameters is categorically different from placing *orders*. The operator still decides whether to act on each signal. Automatic tuning improves signal quality without violating the human-in-the-loop principle for execution. The approval queue ensures transparency.

## Functional Requirements

### FR-001: Tuning Proposal Engine

The system MUST generate **tuning proposals** based on 023's persisted performance data. A proposal is a structured recommendation to change a specific parameter, with justification. Proposals are generated on a configurable schedule (default: after each portfolio recalculation or daily).

Each proposal MUST include:
- **parameter**: The config key to change (e.g., `flippening.categories.nba.spike_threshold_pct`).
- **current_value**: Current value from config.
- **proposed_value**: Recommended new value.
- **justification**: Human-readable explanation (e.g., "Win rate improved from 55% to 72% at spike_threshold_pct=0.08 over last 30 days, 47 trades").
- **confidence**: How confident the system is in the recommendation (based on sample size, statistical significance).
- **data_source**: Which metrics informed this proposal (category_performance row, optimal_params sweep, signal alignment data).

Proposal types:

1. **Parameter optimization**: Propose updating `spike_threshold_pct`, `max_hold_minutes`, `stop_loss_pct`, `reversion_target_pct` based on `optimal_params` table (from `flip-sweep --persist`).
2. **Confidence reweighting**: Propose adjusting `confidence_modifier` per category based on `category_performance.win_rate` relative to overall win rate.
3. **Category suppression**: Propose disabling categories with consistently negative `total_pnl` and `win_rate < 0.3` over a minimum sample (20+ trades).
4. **Alert threshold adjustment**: Propose raising `min_confidence` for categories where `contrary_win_rate > aligned_win_rate` (signals are inversely predictive).

### FR-002: Approval Queue

Tuning proposals MUST NOT auto-apply by default. They enter an **approval queue** visible in:

- **Dashboard**: "Tuning" section within the Backtest tab showing pending proposals with approve/reject buttons.
- **CLI**: `tuning-proposals` command listing pending proposals with `--approve <id>` and `--reject <id>` subcommands.
- **API**: `GET /api/tuning/proposals`, `POST /api/tuning/proposals/{id}/approve`, `POST /api/tuning/proposals/{id}/reject`.

When approved:
- The system applies the change to the in-memory config (takes effect immediately for live signal generation).
- The change is logged to `tuning_audit_log` with before/after values.
- The operator is notified via webhook (if configured).
- The change does NOT modify `config.yaml` on disk — it's an overlay. This means restarting the process reverts to base config unless the operator manually updates YAML.

When rejected:
- The proposal is marked rejected with optional operator notes.
- The system MUST NOT re-propose the same change for the same parameter within a cooldown period (default: 7 days).

### FR-003: Auto-Apply Mode (Opt-In)

For operators who trust the system, an **auto-apply** mode MAY be enabled per category or globally:

```yaml
tuning:
  auto_apply: false           # Global default: proposals require approval
  auto_apply_categories:      # Per-category override
    - nba                     # Auto-apply tuning for NBA (high confidence from data)
  min_sample_size: 20         # Minimum trades before proposing changes
  min_confidence: 0.7         # Minimum proposal confidence to auto-apply
  cooldown_days: 7            # Don't re-propose rejected changes for N days
  proposal_schedule: daily    # When to generate proposals: "daily", "on_import", "manual"
```

Auto-applied changes MUST still be logged and visible in the audit trail.

### FR-004: Position Sizing Recommendations

The system MUST compute and display **suggested position sizes** on execution tickets and dashboard signals, based on:

- **Kelly criterion** (simplified): `f* = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win` where values come from `category_performance` for the signal's category.
- **Fractional Kelly**: Apply a configurable fraction (default: 0.25 = quarter-Kelly) to reduce variance.
- **Capital constraint**: Cap at a configurable percentage of total deposited capital (default: 5% per position).
- **Category exposure limit**: Cap total exposure per category (default: 20% of capital).

Position sizing is a **recommendation only** — displayed alongside the signal, not enforced. This preserves Principle I.

Output on each signal/ticket:
- `suggested_size_usdc: Decimal` — Recommended USDC amount.
- `kelly_fraction: float` — Raw Kelly fraction.
- `sizing_basis: str` — Explanation (e.g., "Quarter-Kelly on 65% win rate, 47 trades, capped at 5% of $1000 capital").

### FR-005: Confidence Reweighting

The system MUST adjust signal confidence scores based on historical category performance:

- **Base confidence**: Computed by existing spike detector (magnitude, strength, speed formula).
- **Category multiplier**: `confidence_modifier` adjusted based on `category_performance.win_rate` vs. global average.
  - Categories with win_rate > global average: boost multiplier (up to 1.5x).
  - Categories with win_rate < global average: reduce multiplier (down to 0.5x).
  - Formula: `modifier = 0.5 + (category_win_rate / global_win_rate)`, clamped to [0.5, 1.5].
- **Signal alignment bonus**: If `aligned_win_rate > 0.6` for the category, add a +0.1 confidence bonus to signals that match historical winning patterns.

Reweighting is applied in real-time during signal generation, not retroactively.

### FR-006: Adaptive Alert Filtering

The system MUST suppress low-value alerts based on performance data:

- **Negative-edge suppression**: If a category has `total_pnl < 0` AND `trade_count >= 20` AND `win_rate < 0.35`, the system SHOULD suppress alerts for that category (log at DEBUG, don't fire webhook). The operator can override via config (`suppress_negative_edge: false`).
- **Inverse-signal detection**: If `contrary_win_rate > aligned_win_rate` by more than 10 percentage points with 20+ trades, log a warning: "Signals for {category} are inversely predictive — consider reversing signal direction or disabling." Generate a tuning proposal to disable or invert.
- **Diminishing-returns filtering**: If last 10 trades in a category are all losses, temporarily suppress alerts until next portfolio recalculation (circuit-breaker pattern for signal quality).

### FR-007: Performance Dashboard Integration

Extend the Backtest tab (from 023) with a "Tuning" section:

- **Pending proposals table**: Parameter, current value, proposed value, justification, confidence, approve/reject buttons.
- **Audit log**: Recent approved/rejected changes with before/after values and timestamps.
- **Position sizing display**: On active signals/tickets, show suggested size alongside existing fields.
- **Category health heatmap**: Color-coded grid showing win rate, P&L trend, and signal alignment per category. Green = edge, yellow = marginal, red = negative edge.

### FR-008: CLI Commands

- `tuning-proposals` — List pending tuning proposals. Options: `--format table|json`.
- `tuning-proposals --approve <id>` — Approve and apply a proposal.
- `tuning-proposals --reject <id> --reason "..."` — Reject with notes.
- `tuning-status` — Show current active tuning overrides vs. base config.
- `tuning-reset` — Revert all tuning overrides to base config values.

## Non-Functional Requirements

### NFR-001: Statistical Significance

The system MUST NOT generate tuning proposals based on insufficient data. Minimum thresholds:
- Parameter optimization: 30+ replay signals in the sweep.
- Confidence reweighting: 20+ actual trades in the category.
- Category suppression: 20+ trades AND negative P&L over at least 14 days.
- Position sizing: 15+ trades for Kelly calculation.

If data is insufficient, proposals MUST include a `low_confidence` flag and MUST NOT auto-apply even in auto-apply mode.

### NFR-002: Auditability

Every tuning change MUST be logged to `tuning_audit_log` with:
- Timestamp, parameter, old value, new value, proposal ID, approval method (manual/auto), operator notes.
- The audit log MUST be queryable via API and CLI.
- Retention: indefinite (tuning history is valuable for long-term analysis).

### NFR-003: Reversibility

All tuning changes MUST be reversible:
- `tuning-reset` reverts to base config.
- Individual overrides can be reverted via dashboard or CLI.
- Restarting the process clears all tuning overrides (they're in-memory overlays, not persisted to `config.yaml`).

### NFR-004: Performance

Proposal generation MUST complete in under 2 seconds. Confidence reweighting during signal generation MUST add less than 10ms latency. Position sizing calculation MUST complete in under 50ms per signal.

### NFR-005: Backward Compatibility

If tuning is disabled (`tuning.enabled: false` in config), all signal generation, alerting, and execution MUST behave identically to pre-024 behavior. Tuning is purely additive.

## Edge Cases

### EC-001: No Performance Data

If `category_performance` is empty (no trades imported yet), the system MUST NOT generate any proposals. All confidence modifiers remain at their config.yaml values.

### EC-002: Category Not in Config

If `category_performance` contains a category that doesn't exist in `flippening.categories` config (e.g., user traded a market type the scanner doesn't monitor), the system MUST skip it for tuning but still display it in portfolio analysis.

### EC-003: Conflicting Proposals

If two proposals affect the same parameter (e.g., one from sweep data, one from win rate analysis), the system MUST keep the one with higher confidence and discard the other with a log entry.

### EC-004: Rapid Config Changes

If an operator approves multiple proposals in quick succession, each MUST be applied atomically and independently. If proposal B depends on the value set by proposal A, proposal B MUST use the post-A value.

### EC-005: Auto-Apply With Negative Outcome

If an auto-applied change leads to worse performance (detected at next recalculation), the system MUST generate a "revert" proposal with high priority. It MUST NOT auto-revert (could cause oscillation) — the revert proposal goes through normal approval flow unless the operator has configured `auto_revert: true`.

### EC-006: Kelly Criterion Edge Cases

- If `avg_win = 0` (no winning trades): Kelly fraction = 0, suggest minimum position size.
- If Kelly fraction is negative (negative edge): suggest $0 position size and flag the category as "no edge detected."
- If win_rate = 1.0 (all wins, small sample): Cap Kelly at fractional maximum and flag `low_confidence`.

## Success Criteria

- SC-001: After importing trade history and running `portfolio`, the system generates at least one tuning proposal based on category performance data.
- SC-002: Approving a proposal immediately changes signal confidence for the affected category in live `flip-watch`.
- SC-003: `tuning-reset` reverts all overrides and signal behavior returns to base config.
- SC-004: Position sizing recommendations appear on execution tickets with correct Kelly-based values.
- SC-005: Categories with negative edge (win_rate < 0.35, 20+ trades) trigger suppression proposals.
- SC-006: Audit log captures all approved/rejected proposals with before/after values.
- SC-007: Auto-apply mode works for configured categories without manual intervention.
- SC-008: All quality gates pass (ruff, mypy, pytest, coverage >= 70%).

## Dependencies

- **023-trade-history-backtesting**: `category_performance` and `optimal_params` tables, portfolio recalculation pipeline.
- Existing flippening config system (`FlippeningConfig`, `CategoryConfig`).
- Existing signal generation pipeline (`SpikeDetector`, `SignalGenerator`).
- Existing execution ticket system.
- Existing webhook notification system.

## Out of Scope

- **Reinforcement learning or ML-based tuning**: This feature uses statistical heuristics (win rate, Kelly criterion), not machine learning. An ML approach could be a future feature.
- **Cross-category correlation**: The system tunes each category independently. Portfolio-level optimization (e.g., "reduce BTC exposure when sports are performing well") is out of scope.
- **Automatic `config.yaml` modification**: Tuning changes are in-memory overlays. Persisting to disk would require file write permissions and conflict resolution — deferred to a future feature.
- **Multi-venue tuning**: This feature tunes Polymarket flippening signals only. Cross-venue arb signal tuning requires arb-specific performance data not yet captured.
- **Real-time parameter optimization**: Tuning proposals are generated on schedule (daily or on-import), not in real-time as each trade resolves. Real-time would require streaming P&L updates.
