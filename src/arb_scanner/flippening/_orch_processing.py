"""Orchestrator processing: update handling, entry/exit pipeline."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import structlog

from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.models.config import Settings
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitSignal,
    FlippeningEvent,
    PriceUpdate,
    SpikeDirection,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.orch_processing",
)


async def process_update(
    update: PriceUpdate,
    game_mgr: GameManager,
    spike_detector: SpikeDetector,
    signal_gen: SignalGenerator,
    config: Settings,
    repo: Any,
    http_client: httpx.AsyncClient,
    dry_run: bool,
    tick_repo: Any = None,
) -> None:
    """Process a single price update through the pipeline.

    Args:
        update: Real-time price update.
        game_mgr: Game lifecycle manager.
        spike_detector: Spike detection engine.
        signal_gen: Signal generation engine.
        config: Application settings.
        repo: FlippeningRepository (None if dry_run).
        http_client: Shared HTTP client for webhooks.
        dry_run: Whether to skip persistence and alerts.
        tick_repo: TickRepository for drift persistence (None if dry_run).
    """
    event, exit_sig, drift_info = game_mgr.process(update)

    if drift_info is not None and tick_repo is not None and not dry_run:
        try:
            market_id, old_yes, new_yes, drifted_at = drift_info
            await tick_repo.insert_drift(market_id, old_yes, new_yes, drifted_at)
        except Exception:
            logger.warning("drift_persist_failed")

    state = game_mgr.get_state(update.market_id)
    if state is None:
        return

    if state.baseline is not None and state.active_signal is None and event is None:
        event = spike_detector.check_spike(update, state.baseline, state.price_history)

    if event is not None and not game_mgr.has_open_signal(update.market_id):
        await handle_entry(
            event,
            update,
            state,
            game_mgr,
            signal_gen,
            config,
            repo,
            http_client,
            dry_run,
        )

    if state.active_signal is not None and exit_sig is None:
        exit_sig = signal_gen.check_exit(update, state.active_signal)

    if exit_sig is not None:
        await handle_exit(exit_sig, state, game_mgr, config, repo, http_client, dry_run)


async def handle_entry(
    event: FlippeningEvent,
    update: PriceUpdate,
    state: Any,
    game_mgr: GameManager,
    signal_gen: SignalGenerator,
    config: Settings,
    repo: Any,
    http_client: httpx.AsyncClient,
    dry_run: bool,
) -> None:
    """Handle a new flippening entry signal.

    Args:
        event: Detected flippening event.
        update: Current price update.
        state: Game state.
        game_mgr: Game lifecycle manager.
        signal_gen: Signal generation engine.
        config: Application settings.
        repo: FlippeningRepository (None if dry_run).
        http_client: Shared HTTP client.
        dry_run: Whether to skip persistence and alerts.
    """
    from arb_scanner.flippening._orch_alerts import dispatch_entry_alert
    from arb_scanner.flippening._orch_repo import persist_entry

    current_ask = (
        update.yes_ask if state.baseline.yes_price >= state.baseline.no_price else update.no_ask
    )
    entry = signal_gen.create_entry(event, current_ask, state.baseline)
    event.market_title = state.market_title
    game_mgr.set_active_signal(update.market_id, entry)

    logger.info(
        "flip_entry",
        market_id=update.market_id,
        side=entry.side,
        entry=float(entry.entry_price),
        target=float(entry.target_exit_price),
        confidence=float(event.confidence),
        category=event.category,
    )

    if not dry_run:
        await persist_entry(repo, event, entry, state.baseline)
        await dispatch_entry_alert(event, entry, config, http_client)


async def handle_exit(
    exit_sig: ExitSignal,
    state: Any,
    game_mgr: GameManager,
    config: Settings,
    repo: Any,
    http_client: httpx.AsyncClient,
    dry_run: bool,
) -> None:
    """Handle a flippening exit signal.

    Args:
        exit_sig: Exit signal with P&L.
        state: Game state.
        game_mgr: Game lifecycle manager.
        config: Application settings.
        repo: FlippeningRepository (None if dry_run).
        http_client: Shared HTTP client.
        dry_run: Whether to skip persistence and alerts.
    """
    from arb_scanner.flippening._orch_alerts import dispatch_exit_alert
    from arb_scanner.flippening._orch_repo import persist_exit

    entry: EntrySignal | None = state.active_signal
    game_mgr.clear_active_signal(state.market_id)

    logger.info(
        "flip_exit",
        market_id=state.market_id,
        reason=exit_sig.exit_reason.value,
        pnl=float(exit_sig.realized_pnl),
        hold_min=float(exit_sig.hold_minutes),
    )

    if not dry_run and entry is not None:
        await persist_exit(repo, exit_sig)
        event = FlippeningEvent(
            id=entry.event_id,
            market_id=state.market_id,
            market_title=state.market_title,
            baseline_yes=state.baseline.yes_price if state.baseline else exit_sig.exit_price,
            spike_price=entry.entry_price,
            spike_magnitude_pct=abs(exit_sig.realized_pnl_pct),
            spike_direction=SpikeDirection.FAVORITE_DROP,
            confidence=Decimal("0"),
            sport=state.sport,
            category=getattr(state, "category", ""),
            category_type=getattr(state, "category_type", "sport"),
            detected_at=entry.created_at,
        )
        await dispatch_exit_alert(event, entry, exit_sig, config, http_client)
