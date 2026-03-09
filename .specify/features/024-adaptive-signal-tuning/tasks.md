# 024 — Adaptive Signal Tuning: Tasks

**Prerequisite**: Feature 023 (Trade History & Backtesting) MUST be complete before starting.

## Phase 1: Data Models

- [ ] 1.1 Add `ProposalType`, `ProposalStatus`, `TuningProposal` models to `models/backtesting.py`
- [ ] 1.2 Add `SizingRecommendation` model to `models/backtesting.py`
- [ ] 1.3 Add `TuningConfig` to `models/config.py` with all fields (auto_apply, kelly_fraction, cooldown, etc.)
- [ ] 1.4 Wire `TuningConfig` into main config loader (optional section, disabled by default)
- [ ] 1.5 Export new models from `models/__init__.py`
- [ ] 1.6 Run quality gates (ruff, mypy)

## Phase 2: Database Migration

- [ ] 2.1 Create `storage/migrations/027_tuning.sql` with `tuning_proposals` table (status CHECK, indexes on status and category)
- [ ] 2.2 Add `tuning_audit_log` table (FK to proposals, index on applied_at)
- [ ] 2.3 Test migration against local PostgreSQL

## Phase 3: Config Overlay

- [ ] 3.1 Create `tuning/__init__.py`
- [ ] 3.2 Create `tuning/config_overlay.py` with `ConfigOverlay` class
- [ ] 3.3 Implement `get()`, `apply()`, `revert()`, `reset()`, `active_overrides()` methods
- [ ] 3.4 Add thread safety via `threading.Lock`
- [ ] 3.5 Initialize as singleton, injectable into signal generation pipeline
- [ ] 3.6 Write `tests/unit/test_config_overlay.py` — apply, revert, reset, thread safety, get-with-default
- [ ] 3.7 Run quality gates

## Phase 4: Proposal Engine

- [ ] 4.1 Create `tuning/proposal_engine.py` with `generate_proposals()` function
- [ ] 4.2 Implement parameter optimization proposals (compare optimal_params vs. current config)
- [ ] 4.3 Implement confidence reweighting proposals (category win_rate vs. global average)
- [ ] 4.4 Implement category suppression proposals (negative P&L + low win rate + sufficient trades)
- [ ] 4.5 Implement alert threshold proposals (contrary_win_rate > aligned_win_rate detection)
- [ ] 4.6 Add cooldown enforcement (skip recently rejected parameters)
- [ ] 4.7 Add min_sample_size enforcement and low_confidence flagging
- [ ] 4.8 Write `tests/unit/test_proposal_engine.py` — all 4 types, cooldown, insufficient data, edge cases
- [ ] 4.9 Run quality gates

## Phase 5: Confidence Reweighter

- [ ] 5.1 Create `tuning/confidence_reweighter.py` with `reweight_confidence()` function
- [ ] 5.2 Implement category multiplier formula: `0.5 + (cat_win_rate / global_win_rate)`, clamped [0.5, 1.5]
- [ ] 5.3 Implement signal alignment bonus (+0.1 when aligned_win_rate > 0.6)
- [ ] 5.4 Integrate into `flippening/signal_generator.py` as optional hook (no change when tuning disabled)
- [ ] 5.5 Write `tests/unit/test_confidence_reweighter.py` — boost, reduce, clamp, alignment bonus, disabled
- [ ] 5.6 Run quality gates

## Phase 6: Position Sizer

- [ ] 6.1 Create `tuning/position_sizer.py` with `compute_sizing()` function
- [ ] 6.2 Implement Kelly criterion: `f = (p * avg_win - (1-p) * avg_loss) / avg_win`
- [ ] 6.3 Apply fractional Kelly (default 0.25)
- [ ] 6.4 Apply per-position cap (`max_position_pct * total_capital`)
- [ ] 6.5 Apply category exposure cap (`max_category_pct * total_capital`)
- [ ] 6.6 Handle edge cases: negative Kelly, zero avg_win, perfect win rate with small sample
- [ ] 6.7 Generate human-readable `sizing_basis` explanation string
- [ ] 6.8 Write `tests/unit/test_position_sizer.py` — Kelly formula, negative edge, caps, small sample, fractional
- [ ] 6.9 Run quality gates

## Phase 7: Alert Filter

- [ ] 7.1 Create `tuning/alert_filter.py` with `should_suppress()` function
- [ ] 7.2 Implement negative-edge suppression (total_pnl < 0, win_rate < 0.35, 20+ trades)
- [ ] 7.3 Implement inverse-signal detection (contrary_win_rate > aligned_win_rate + 0.10)
- [ ] 7.4 Implement losing-streak circuit breaker (last 10 trades all losses)
- [ ] 7.5 Integrate into webhook dispatch path (suppress webhook, still write signal to DB)
- [ ] 7.6 Write `tests/unit/test_alert_filter.py` — all suppression types, override config, edge cases
- [ ] 7.7 Run quality gates

## Phase 8: Storage Layer

- [ ] 8.1 Add tuning SQL constants to `storage/_backtesting_queries.py` (INSERT/SELECT/UPDATE proposals, INSERT audit_log)
- [ ] 8.2 Add `create_proposal()` to `backtesting_repository.py`
- [ ] 8.3 Add `get_pending_proposals()` with optional category filter
- [ ] 8.4 Add `approve_proposal()` — update status, insert audit log, return updated proposal
- [ ] 8.5 Add `reject_proposal()` — update status with operator notes
- [ ] 8.6 Add `get_audit_log()` with limit and since params
- [ ] 8.7 Add `check_cooldown()` — check if parameter was rejected within cooldown_days
- [ ] 8.8 Run quality gates

## Phase 9: CLI Commands

- [ ] 9.1 Create `cli/tuning_commands.py` with `tuning_proposals`, `tuning_status`, `tuning_reset` commands
- [ ] 9.2 Implement `tuning-proposals` — list pending proposals as table
- [ ] 9.3 Implement `--approve <id>` — approve proposal, apply to overlay, log
- [ ] 9.4 Implement `--reject <id> --reason "..."` — reject with notes
- [ ] 9.5 Implement `tuning-status` — show active overrides vs. base config
- [ ] 9.6 Implement `tuning-reset` — clear all overrides with confirmation
- [ ] 9.7 Register commands in `cli/app.py`
- [ ] 9.8 Write `tests/unit/test_tuning_commands.py` — command invocation with mock data
- [ ] 9.9 Run quality gates

## Phase 10: API Routes

- [ ] 10.1 Add `GET /api/tuning/proposals` endpoint (with `?status=` filter)
- [ ] 10.2 Add `POST /api/tuning/proposals/{id}/approve` endpoint
- [ ] 10.3 Add `POST /api/tuning/proposals/{id}/reject` endpoint (with optional notes body)
- [ ] 10.4 Add `GET /api/tuning/status` endpoint — active overrides
- [ ] 10.5 Add `POST /api/tuning/reset` endpoint
- [ ] 10.6 Add `GET /api/tuning/audit-log` endpoint
- [ ] 10.7 Wire routes into `api/app.py`
- [ ] 10.8 Write `tests/unit/test_tuning_routes.py` — endpoint tests with mock repo
- [ ] 10.9 Run quality gates

## Phase 11: Dashboard Integration

- [ ] 11.1 Add "Tuning Proposals" section to Backtest tab in `index.html` (pending proposals table, approve/reject buttons)
- [ ] 11.2 Add "Active Overrides" section (parameter, base value, override value, applied_at)
- [ ] 11.3 Add category health heatmap (color-coded table: green/yellow/red by win rate)
- [ ] 11.4 Add `loadTuningSection()` in `app.js` — fetch proposals, status, render tables
- [ ] 11.5 Implement approve/reject button handlers → POST to API → refresh
- [ ] 11.6 Add category heatmap rendering with conditional coloring
- [ ] 11.7 Add position sizing display on active signal cards (if sizing data available)
- [ ] 11.8 Manual test: generate proposals, approve/reject from dashboard, verify overlay applied

## Phase 12: Integration & Quality

- [ ] 12.1 Run full test suite: `uv run pytest tests/ -x --tb=short`
- [ ] 12.2 Verify coverage: `uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70`
- [ ] 12.3 Run `uv run ruff check src/ tests/` — zero errors
- [ ] 12.4 Run `uv run ruff format --check src/ tests/` — clean
- [ ] 12.5 Run `uv run mypy src/ --strict` — zero errors
- [ ] 12.6 End-to-end test: import trades → run portfolio → generate proposals → approve → verify confidence reweighting in flip-watch
- [ ] 12.7 End-to-end test: verify category suppression stops webhook for negative-edge category
- [ ] 12.8 End-to-end test: verify position sizing appears on execution tickets
- [ ] 12.9 End-to-end test: verify `tuning-reset` reverts all overrides and signal behavior returns to base config
- [ ] 12.10 Update CLAUDE.md with new CLI commands and tuning config section
