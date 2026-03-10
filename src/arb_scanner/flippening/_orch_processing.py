"""Orchestrator processing: update handling, entry/exit pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog

from arb_scanner.flippening._orch_exit import _feed_exit_pipeline
from arb_scanner.flippening.alert_buffer import AlertBuffer
from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.flippening.price_ring_buffer import (
    PriceTick,
    get_shared_buffer,
)
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.models.config import Settings
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitReason,
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
    alert_buffer: AlertBuffer | None = None,
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
        alert_buffer: AlertBuffer for batched dispatch (None in dry_run).
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

    _push_price_tick(update, state)

    if state.baseline is not None and state.active_signal is None and event is None:
        event = spike_detector.check_spike(update, state.baseline, state.price_history)

    if event is not None:
        event.no_token_id = getattr(state, "no_token_id", "")
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
            alert_buffer=alert_buffer,
        )

    if state.active_signal is not None and exit_sig is None:
        exit_sig = signal_gen.check_exit(update, state.active_signal)

    if exit_sig is not None:
        await handle_exit(
            exit_sig,
            state,
            game_mgr,
            config,
            repo,
            http_client,
            dry_run,
            alert_buffer=alert_buffer,
        )


def _push_price_tick(update: PriceUpdate, state: Any) -> None:
    """Push a PriceTick to the shared ring buffer if available.

    Args:
        update: Current price update.
        state: GameState for the market.
    """
    buf = get_shared_buffer()
    if buf is None:
        return
    yes_mid = (update.yes_bid + update.yes_ask) / 2
    baseline_yes = state.baseline.yes_price if state.baseline else None
    deviation = 0.0
    if baseline_yes and baseline_yes > 0:
        deviation = float((yes_mid - baseline_yes) / baseline_yes * 100)
        deviation = max(-999.99, min(999.99, deviation))
    tick = PriceTick(
        market_id=update.market_id,
        market_title=state.market_title,
        category=getattr(state, "category", ""),
        category_type=getattr(state, "category_type", "sport"),
        yes_mid=yes_mid,
        baseline_yes=baseline_yes,
        deviation_pct=deviation,
        spread=update.spread,
        timestamp=update.timestamp,
        book_depth_bids=update.book_depth_bids,
        book_depth_asks=update.book_depth_asks,
    )
    buf.push(tick)


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
    *,
    alert_buffer: AlertBuffer | None = None,
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
        alert_buffer: AlertBuffer for batched dispatch (None in dry_run).
    """
    from arb_scanner.flippening._orch_repo import persist_entry

    current_ask = (
        update.yes_ask if state.baseline.yes_price >= state.baseline.no_price else update.no_ask
    )
    entry = signal_gen.create_entry(event, current_ask, state.baseline)
    if entry is None:
        return
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
        from decimal import Decimal as _Decimal

        min_profit = _Decimal(str(config.flippening.min_expected_profit_usd))
        slug = getattr(state, "market_slug", "")
        await persist_entry(
            repo,
            event,
            entry,
            state.baseline,
            min_expected_profit_usd=min_profit,
            market_slug=slug,
        )
        if alert_buffer is not None:
            has_open = await _has_open_position(config, event.market_id)
            alert_buffer.append_entry(event, entry, has_open_position=has_open)
        await _feed_auto_pipeline(event, entry, config, market_slug=slug)


async def handle_exit(
    exit_sig: ExitSignal,
    state: Any,
    game_mgr: GameManager,
    config: Settings,
    repo: Any,
    http_client: httpx.AsyncClient,
    dry_run: bool,
    *,
    alert_buffer: AlertBuffer | None = None,
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
        alert_buffer: AlertBuffer for batched dispatch (None in dry_run).
    """
    from arb_scanner.flippening._orch_repo import persist_exit

    entry: EntrySignal | None = state.active_signal
    game_mgr.clear_active_signal(state.market_id)

    # Recapture baseline at current price to prevent the spike detector
    # from immediately re-entering on the same price deviation.
    if state.price_history:
        game_mgr.capture_baseline(state, state.price_history[-1], late_join=True)

    logger.info(
        "flip_exit",
        market_id=state.market_id,
        reason=exit_sig.exit_reason.value,
        pnl=float(exit_sig.realized_pnl),
        hold_min=float(exit_sig.hold_minutes),
    )

    if dry_run:
        return

    if entry is None:
        logger.warning("flip_exit_no_entry", market_id=state.market_id)

    try:
        await persist_exit(repo, exit_sig)
    except Exception:
        logger.exception("persist_exit_failed_in_handle_exit")

    if entry is None:
        return

    event = FlippeningEvent(
        id=entry.event_id,
        market_id=state.market_id,
        token_id=state.token_id,
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
    if alert_buffer is not None:
        alert_buffer.append_exit(event, entry, exit_sig)
    try:
        await _feed_exit_pipeline(event, entry, exit_sig, config)
    except Exception:
        logger.exception("feed_exit_pipeline_failed_in_handle_exit")


async def _feed_auto_pipeline(
    event: FlippeningEvent,
    entry: EntrySignal,
    config: Settings,
    *,
    market_slug: str = "",
) -> None:
    """Feed a flippening entry to the auto-execution pipeline if available.

    Args:
        event: Detected flippening event.
        entry: Entry signal.
        config: Application settings.
        market_slug: Polymarket slug for building market URLs.
    """
    try:
        from arb_scanner.execution.flip_pipeline import FlipAutoExecutionPipeline

        pipeline: FlipAutoExecutionPipeline | None = getattr(config, "_flip_pipeline", None)
        if pipeline is None or pipeline.mode != "auto":
            return
        opp: dict[str, object] = {
            "arb_id": event.id,
            "spread_pct": float(event.spike_magnitude_pct),
            "confidence": float(event.confidence),
            "category": event.category,
            "title": event.market_title,
            "ticket_type": "flippening",
            "market_id": event.market_id,
            "token_id": event.token_for_side(entry.side),
            "side": entry.side,
            "entry_price": float(entry.entry_price),
            "max_hold_minutes": entry.max_hold_minutes,
            "market_slug": market_slug,
        }
        await pipeline.process_opportunity(opp, source="flippening")
    except Exception:
        logger.warning("flip_pipeline_feed_failed")


async def sweep_overtime_signals(
    game_mgr: GameManager,
    config: Settings,
    repo: Any,
    http_client: httpx.AsyncClient,
    dry_run: bool,
    alert_buffer: AlertBuffer | None = None,
) -> int:
    """Force-exit any active signals past their max_hold_minutes.

    Uses wall-clock time to detect overtime positions independently
    of the WebSocket price stream.  Called periodically from the
    orchestrator loop so that quiet markets cannot hold positions
    indefinitely.

    Args:
        game_mgr: Game lifecycle manager.
        config: Application settings.
        repo: FlippeningRepository (None if dry_run).
        http_client: Shared HTTP client.
        dry_run: Whether to skip persistence and alerts.
        alert_buffer: AlertBuffer for batched dispatch.

    Returns:
        Number of signals force-exited.
    """
    now = datetime.now(UTC)
    count = 0
    for _market_id, state in game_mgr.iter_active_signals():
        entry = state.active_signal
        if entry is None:
            continue
        wall_min = (now - entry.created_at).total_seconds() / 60.0
        if wall_min < entry.max_hold_minutes:
            continue
        last_bid = _last_known_bid(state, entry)
        pnl = last_bid - entry.entry_price
        pnl_pct = pnl / entry.entry_price if entry.entry_price else Decimal("0")
        exit_sig = ExitSignal(
            event_id=entry.event_id,
            side=entry.side,
            exit_price=last_bid,
            exit_reason=ExitReason.TIMEOUT,
            realized_pnl=pnl,
            realized_pnl_pct=pnl_pct,
            hold_minutes=Decimal(str(round(wall_min, 2))),
            created_at=now,
        )
        logger.warning(
            "overtime_forced_exit",
            market_id=_market_id,
            hold_min=round(wall_min, 1),
            max_hold=entry.max_hold_minutes,
        )
        await handle_exit(
            exit_sig,
            state,
            game_mgr,
            config,
            repo,
            http_client,
            dry_run,
            alert_buffer=alert_buffer,
        )
        count += 1
    return count


def _last_known_bid(state: Any, entry: EntrySignal) -> Decimal:
    """Get last known bid price from game state's price history."""
    if state.price_history:
        last: PriceUpdate = state.price_history[-1]
        return last.yes_bid if entry.side == "yes" else last.no_bid
    return entry.entry_price


async def sweep_overtime_db_positions(config: Settings) -> int:
    """Close overtime positions via the exit pipeline (DB-level check).

    Catches positions past max_hold_minutes that have no in-memory
    signal — e.g. carried over from a prior session.  Attempts a real
    sell on Polymarket before marking the position closed.

    Args:
        config: Application settings (pipeline stored on _flip_pipeline).

    Returns:
        Number of positions for which exit was triggered.
    """
    from arb_scanner.flippening._orch_exit import _feed_exit_pipeline

    pipeline = getattr(config, "_flip_pipeline", None)
    if pipeline is None:
        return 0
    pos_repo = getattr(pipeline, "_position_repo", None)
    if pos_repo is None:
        return 0
    now = datetime.now(UTC)
    try:
        positions: list[dict[str, Any]] = await pos_repo.get_open_positions()
    except Exception:
        logger.warning("db_sweep_query_failed")
        return 0
    count = 0
    for p in positions:
        max_hold = p.get("max_hold_minutes")
        if max_hold is None:
            continue
        elapsed_min = (now - p["opened_at"]).total_seconds() / 60.0
        if elapsed_min < max_hold:
            continue
        try:
            entry_sig, exit_sig, event = _build_exit_from_position(p, elapsed_min, now)
            await _feed_exit_pipeline(event, entry_sig, exit_sig, config)
            count += 1
            logger.warning(
                "db_overtime_exit_triggered",
                market_id=p["market_id"],
                held_min=round(elapsed_min, 1),
                max_hold=max_hold,
            )
        except Exception:
            logger.exception("db_overtime_exit_failed", market_id=p["market_id"])
    return count


def _build_exit_from_position(
    p: dict[str, Any],
    elapsed_min: float,
    now: datetime,
) -> tuple[EntrySignal, ExitSignal, FlippeningEvent]:
    """Construct minimal signal objects from a DB position record."""
    entry_price = Decimal(str(p["entry_price"]))
    entry_sig = EntrySignal(
        event_id=p["arb_id"],
        side=p["side"],
        entry_price=entry_price,
        target_exit_price=entry_price,
        stop_loss_price=Decimal("0"),
        suggested_size_usd=entry_price * p["size_contracts"],
        expected_profit_pct=Decimal("0"),
        max_hold_minutes=p["max_hold_minutes"],
        created_at=p["opened_at"],
    )
    exit_sig = ExitSignal(
        event_id=p["arb_id"],
        side=p["side"],
        exit_price=entry_price,
        exit_reason=ExitReason.TIMEOUT,
        realized_pnl=Decimal("0"),
        realized_pnl_pct=Decimal("0"),
        hold_minutes=Decimal(str(round(elapsed_min, 2))),
        created_at=now,
    )
    event = FlippeningEvent(
        id=p["arb_id"],
        market_id=p["market_id"],
        token_id=p["token_id"],
        market_title=p.get("market_title", ""),
        baseline_yes=entry_price,
        spike_price=entry_price,
        spike_magnitude_pct=Decimal("0"),
        spike_direction=SpikeDirection.FAVORITE_DROP,
        confidence=Decimal("0"),
        sport="",
        detected_at=p["opened_at"],
    )
    return entry_sig, exit_sig, event


async def _has_open_position(config: Settings, market_id: str) -> bool:
    """Return True if an auto-exec position is currently open for market_id.

    Args:
        config: Application settings (carries _flip_pipeline sidecar).
        market_id: Polymarket market identifier.

    Returns:
        True when a live position exists, False otherwise or on error.
    """
    try:
        pipeline = getattr(config, "_flip_pipeline", None)
        if pipeline is None:
            return False
        pos_repo = getattr(pipeline, "_position_repo", None)
        if pos_repo is None:
            return False
        return await pos_repo.get_open_position(market_id) is not None
    except Exception:
        return False
