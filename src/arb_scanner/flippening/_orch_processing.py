"""Orchestrator processing: update handling, entry/exit pipeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import structlog

from arb_scanner.execution.flip_position_math import compute_realized_pnl
from arb_scanner.execution.activity_feed import push_activity
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
from arb_scanner.models.execution import OrderRequest, OrderResponse, OrderSide, OrderStatus
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
    game_mgr.set_active_signal(update.market_id, entry, event=event)

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


async def retry_active_signals(
    game_mgr: GameManager,
    config: Settings,
) -> int:
    """Re-feed active flippening signals that lack open auto-exec positions.

    Signals are only fed to the auto-execution pipeline once at entry
    time.  If rejected (max positions, confidence, circuit breaker),
    they sit on the dashboard but never get retried even when
    conditions change.  This function periodically retries them.

    Args:
        game_mgr: Game lifecycle manager with active signals.
        config: Application settings (carries _flip_pipeline sidecar).

    Returns:
        Number of signals re-fed to the pipeline.
    """
    pipeline = getattr(config, "_flip_pipeline", None)
    if pipeline is None or getattr(pipeline, "_mode", "off") != "auto":
        return 0
    pos_repo = getattr(pipeline, "_position_repo", None)
    if pos_repo is None:
        return 0

    active = game_mgr.iter_active_signals()
    if not active:
        return 0

    # Get markets that already have open auto-exec positions
    try:
        open_positions = await pos_repo.get_open_positions()
    except Exception:
        return 0
    open_market_ids = {str(p.get("market_id", "")) for p in open_positions}

    fed = 0
    for _market_id, state in active:
        entry = state.active_signal
        event = state.active_event
        if entry is None or event is None:
            continue
        if state.market_id in open_market_ids:
            continue  # Already has an open position
        slug = getattr(state, "market_slug", "")
        try:
            await _feed_auto_pipeline(event, entry, config, market_slug=slug)
            fed += 1
        except Exception:
            logger.warning("retry_signal_feed_failed", market_id=state.market_id)
    if fed:
        logger.info("retry_active_signals_fed", fed=fed, checked=len(active))
    return fed


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
        if str(p.get("status", "open")) not in {"open", "exit_failed"}:
            continue
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


async def reconcile_pending_db_positions(config: Settings) -> int:
    """Reconcile flip positions in exit_pending with venue order status.

    Args:
        config: Application settings (pipeline stored on _flip_pipeline).

    Returns:
        Number of positions transitioned out of exit_pending.
    """
    pipeline = getattr(config, "_flip_pipeline", None)
    if pipeline is None:
        return 0
    pos_repo = getattr(pipeline, "_position_repo", None)
    exec_repo = getattr(pipeline, "_exec_repo", None)
    poly = getattr(pipeline, "_poly", None)
    if pos_repo is None or exec_repo is None or poly is None:
        return 0
    retry_policy = _build_pending_retry_policy(pipeline)
    watchdog_metrics = getattr(pipeline, "_exit_watchdog_metrics", None)
    try:
        positions: list[dict[str, Any]] = await pos_repo.get_exit_pending_positions()
    except Exception:
        logger.warning("pending_exit_query_failed")
        return 0

    resolved = 0
    for position in positions:
        market_id = str(position.get("market_id", ""))
        if not market_id:
            continue
        try:
            transitioned = await _reconcile_pending_position(
                position,
                pos_repo=pos_repo,
                exec_repo=exec_repo,
                poly=poly,
                retry_policy=retry_policy,
                watchdog_metrics=watchdog_metrics,
            )
            if transitioned:
                resolved += 1
        except Exception:
            logger.exception("pending_exit_reconcile_failed", market_id=market_id)
    return resolved


async def _reconcile_pending_position(
    position: dict[str, Any],
    *,
    pos_repo: Any,
    exec_repo: Any,
    poly: Any,
    retry_policy: dict[str, Decimal | int],
    watchdog_metrics: Any = None,
) -> bool:
    """Reconcile a single exit_pending position against execution/venue state."""
    market_id = str(position.get("market_id", ""))
    exit_order_id = str(position.get("exit_order_id", "") or "")
    if not exit_order_id:
        logger.warning("pending_exit_missing_order_id", market_id=market_id)
        return False

    order = await exec_repo.get_order(exit_order_id)
    if order is None:
        logger.warning(
            "pending_exit_order_missing",
            market_id=market_id,
            exit_order_id=exit_order_id,
        )
        return False

    now = datetime.now(UTC)
    status = await _resolve_pending_order_status(order, poly)
    if status is None:
        if _is_pending_order_stale(order, now, int(retry_policy["stale_seconds"])):
            _metric_inc(watchdog_metrics, "stale_detected")
            _watchdog_activity(
                "stale_detected",
                market_id,
                exit_order_id=exit_order_id,
            )
            return await _retry_stale_pending_exit(
                position,
                order,
                pos_repo=pos_repo,
                exec_repo=exec_repo,
                poly=poly,
                retry_policy=retry_policy,
                watchdog_metrics=watchdog_metrics,
            )
        logger.info(
            "pending_exit_waiting_for_venue_order_id",
            market_id=market_id,
            exit_order_id=exit_order_id,
        )
        return False

    await exec_repo.update_order_status(
        exit_order_id,
        status.status,
        fill_price=status.fill_price,
        venue_order_id=status.venue_order_id or None,
        error_message=status.error_message,
    )

    if status.status in ("failed", "cancelled"):
        await pos_repo.mark_exit_failed(market_id)
        logger.warning(
            "pending_exit_marked_failed",
            market_id=market_id,
            exit_order_id=exit_order_id,
            status=status.status,
        )
        return True

    if status.status == "filled" or (
        status.status == "submitted" and status.fill_price is not None
    ):
        return await _close_pending_position(
            position,
            order=order,
            fill_price=status.fill_price,
            pos_repo=pos_repo,
            exit_order_id=exit_order_id,
        )

    if _is_pending_status(status.status) and _is_pending_order_stale(
        order,
        now,
        int(retry_policy["stale_seconds"]),
    ):
        _metric_inc(watchdog_metrics, "stale_detected")
        _watchdog_activity(
            "stale_detected",
            market_id,
            exit_order_id=exit_order_id,
        )
        return await _retry_stale_pending_exit(
            position,
            order,
            pos_repo=pos_repo,
            exec_repo=exec_repo,
            poly=poly,
            retry_policy=retry_policy,
            watchdog_metrics=watchdog_metrics,
        )

    return False


async def _close_pending_position(
    position: dict[str, Any],
    *,
    order: dict[str, Any],
    fill_price: Decimal | None,
    pos_repo: Any,
    exit_order_id: str,
) -> bool:
    """Close a pending position using the best available fill price."""
    market_id = str(position.get("market_id", ""))
    resolved_fill = fill_price
    if resolved_fill is None:
        resolved_fill = _to_decimal(order.get("fill_price"))
    if resolved_fill is None:
        resolved_fill = _to_decimal(order.get("requested_price"))
    if resolved_fill is None:
        resolved_fill = _to_decimal(position.get("exit_price"))
    if resolved_fill is None:
        logger.warning(
            "pending_exit_missing_fill_price",
            market_id=market_id,
            exit_order_id=exit_order_id,
        )
        return False
    entry_price = _to_decimal(position.get("entry_price"))
    contracts = _to_int(position.get("size_contracts"))
    if entry_price is None or contracts is None:
        logger.warning(
            "pending_exit_missing_position_fields",
            market_id=market_id,
            exit_order_id=exit_order_id,
        )
        return False
    pnl = compute_realized_pnl(entry_price, resolved_fill, contracts)
    await pos_repo.close_position(
        market_id,
        exit_order_id=exit_order_id,
        exit_price=resolved_fill,
        realized_pnl=pnl,
        exit_reason=str(position.get("exit_reason", "") or "pending_reconciled"),
    )
    logger.info(
        "pending_exit_closed",
        market_id=market_id,
        exit_order_id=exit_order_id,
        fill_price=float(resolved_fill),
        pnl=float(pnl),
    )
    return True


async def _retry_stale_pending_exit(
    position: dict[str, Any],
    order: dict[str, Any],
    *,
    pos_repo: Any,
    exec_repo: Any,
    poly: Any,
    retry_policy: dict[str, Decimal | int],
    watchdog_metrics: Any = None,
) -> bool:
    """Cancel stale pending order and place a more aggressive replacement."""
    market_id = str(position.get("market_id", ""))
    exit_order_id = str(order.get("id", "") or position.get("exit_order_id", ""))
    venue_order_id = str(order.get("venue_order_id", "") or "")
    attempts = await _count_exit_attempts(position, exec_repo)
    max_attempts = int(retry_policy["max_attempts"])

    if venue_order_id:
        cancelled = await poly.cancel_order(venue_order_id)
        if not cancelled:
            _metric_inc(watchdog_metrics, "cancel_failed")
            _watchdog_activity(
                "cancel_failed",
                market_id,
                exit_order_id=exit_order_id,
                venue_order_id=venue_order_id,
            )
            logger.warning(
                "pending_exit_cancel_failed",
                market_id=market_id,
                exit_order_id=exit_order_id,
                venue_order_id=venue_order_id,
            )
            return False
        await exec_repo.update_order_status(
            exit_order_id,
            "cancelled",
            venue_order_id=venue_order_id,
            error_message="stale_retry_cancelled",
        )
    else:
        await exec_repo.update_order_status(
            exit_order_id,
            "failed",
            error_message="stale_missing_venue_order_id",
        )

    if attempts >= max_attempts:
        _metric_inc(watchdog_metrics, "retry_exhausted")
        _watchdog_activity(
            "retry_exhausted",
            market_id,
            attempts=attempts,
            max_attempts=max_attempts,
        )
        await pos_repo.mark_exit_failed(market_id)
        logger.warning(
            "pending_exit_retry_exhausted",
            market_id=market_id,
            attempts=attempts,
            max_attempts=max_attempts,
        )
        return True

    req = _build_retry_sell_request(position, order, retry_policy)
    if req is None:
        await pos_repo.mark_exit_failed(market_id)
        logger.warning("pending_exit_retry_invalid_position", market_id=market_id)
        return True

    new_order_id = str(uuid.uuid4())
    _metric_inc(watchdog_metrics, "retries_placed")
    _watchdog_activity(
        "retry_placed",
        market_id,
        old_order_id=exit_order_id,
        new_order_id=new_order_id,
        attempts=attempts + 1,
    )
    await exec_repo.insert_order(
        order_id=new_order_id,
        arb_id=str(position.get("arb_id", "")),
        venue="polymarket",
        venue_order_id=None,
        side=req.side,
        requested_price=req.price,
        fill_price=None,
        size_usd=Decimal("0"),
        size_contracts=req.size_contracts,
        status="submitting",
        error_message=None,
    )
    try:
        resp = await poly.place_order(req)
    except Exception as exc:
        _metric_inc(watchdog_metrics, "retry_failed")
        _watchdog_activity(
            "retry_failed",
            market_id,
            new_order_id=new_order_id,
            error=str(exc),
        )
        await exec_repo.update_order_status(new_order_id, "failed", error_message=str(exc))
        await pos_repo.mark_exit_failed(market_id)
        logger.error(
            "pending_exit_retry_place_failed",
            market_id=market_id,
            error=str(exc),
        )
        return True

    await exec_repo.update_order_status(
        new_order_id,
        resp.status,
        fill_price=resp.fill_price,
        venue_order_id=resp.venue_order_id,
        error_message=resp.error_message,
    )
    if resp.status in ("failed", "cancelled"):
        _metric_inc(watchdog_metrics, "retry_failed")
        _watchdog_activity(
            "retry_failed",
            market_id,
            new_order_id=new_order_id,
            status=resp.status,
        )
        await pos_repo.mark_exit_failed(market_id)
        logger.warning(
            "pending_exit_retry_failed",
            market_id=market_id,
            status=resp.status,
        )
        return True
    if resp.status == "filled" or (resp.status == "submitted" and resp.fill_price is not None):
        _metric_inc(watchdog_metrics, "retry_closed")
        _watchdog_activity(
            "retry_closed",
            market_id,
            new_order_id=new_order_id,
            status=resp.status,
        )
        resolved_fill = resp.fill_price
        if resp.status == "filled" and resolved_fill is None:
            resolved_fill = req.price
        return await _close_pending_position(
            position,
            order=order,
            fill_price=resolved_fill,
            pos_repo=pos_repo,
            exit_order_id=new_order_id,
        )
    await pos_repo.mark_exit_pending(
        market_id,
        exit_order_id=new_order_id,
        exit_price=req.price,
        exit_reason=str(position.get("exit_reason", "") or "stale_retry"),
    )
    logger.info(
        "pending_exit_retried",
        market_id=market_id,
        old_order_id=exit_order_id,
        new_order_id=new_order_id,
        price=float(req.price),
        attempts=attempts + 1,
    )
    return False


def _build_retry_sell_request(
    position: dict[str, Any],
    order: dict[str, Any],
    retry_policy: dict[str, Decimal | int],
) -> OrderRequest | None:
    """Build repriced retry request for a stale pending exit."""
    side_str = str(position.get("side", "")).lower()
    token_id = str(position.get("token_id", ""))
    contracts = _to_int(position.get("size_contracts"))
    if side_str not in {"yes", "no"} or not token_id or contracts is None or contracts <= 0:
        return None
    step_pct = Decimal(str(retry_policy["reprice_step_pct"]))
    min_price = Decimal(str(retry_policy["min_price"]))
    current_price = _to_decimal(order.get("requested_price")) or _to_decimal(
        position.get("exit_price")
    )
    if current_price is None:
        current_price = min_price
    next_price = (current_price * (Decimal("1") - step_pct)).quantize(Decimal("0.0001"))
    if next_price < min_price:
        next_price = min_price
    sell_side: OrderSide = f"sell_{side_str}"  # type: ignore[assignment]
    return OrderRequest(
        venue="polymarket",
        side=sell_side,
        price=next_price,
        size_usd=Decimal("0"),
        size_contracts=contracts,
        token_id=token_id,
    )


async def _count_exit_attempts(position: dict[str, Any], exec_repo: Any) -> int:
    """Count historical sell attempts for this position."""
    arb_id = str(position.get("arb_id", ""))
    side = str(position.get("side", "")).lower()
    if not arb_id or side not in {"yes", "no"}:
        return 1
    target_side = f"sell_{side}"
    try:
        orders = await exec_repo.get_orders_for_ticket(arb_id)
    except Exception:
        return 1
    attempts = sum(1 for order in orders if str(order.get("side", "")).lower() == target_side)
    return max(attempts, 1)


def _is_pending_order_stale(order: dict[str, Any], now: datetime, stale_seconds: int) -> bool:
    """Return True when order age exceeds stale threshold."""
    ts = order.get("updated_at") or order.get("created_at")
    if not isinstance(ts, datetime):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds() >= stale_seconds


def _is_pending_status(status: str) -> bool:
    """Return True for in-flight order statuses."""
    return status in {"submitting", "submitted", "partially_filled"}


def _build_pending_retry_policy(pipeline: Any) -> dict[str, Decimal | int]:
    """Build retry policy from pipeline config with safe defaults."""
    ac = getattr(pipeline, "_ac", None)
    stale_seconds = int(getattr(ac, "exit_pending_stale_seconds", 30))
    max_attempts = int(getattr(ac, "exit_retry_max_attempts", 4))
    reprice_step_pct = Decimal(str(getattr(ac, "exit_retry_reprice_pct", 0.02)))
    min_price = Decimal(str(getattr(ac, "exit_retry_min_price", 0.01)))
    if stale_seconds < 5:
        stale_seconds = 5
    if max_attempts < 1:
        max_attempts = 1
    if reprice_step_pct < Decimal("0"):
        reprice_step_pct = Decimal("0")
    if reprice_step_pct > Decimal("0.50"):
        reprice_step_pct = Decimal("0.50")
    if min_price < Decimal("0.01"):
        min_price = Decimal("0.01")
    return {
        "stale_seconds": stale_seconds,
        "max_attempts": max_attempts,
        "reprice_step_pct": reprice_step_pct,
        "min_price": min_price,
    }


def _metric_inc(metrics: Any, key: str) -> None:
    """Increment watchdog metric counter if metrics sidecar is available."""
    if metrics is None:
        return
    try:
        metrics.incr(key)
    except Exception:
        return


def _watchdog_activity(event_type: str, market_id: str, **fields: object) -> None:
    """Push watchdog event to activity feed, swallowing failures."""
    try:
        push_activity(
            event_type,
            market_id,
            pipeline="flip",
            **fields,
        )
    except Exception:
        return


async def _resolve_pending_order_status(order: dict[str, Any], poly: Any) -> OrderResponse | None:
    """Return best-known normalized status for a pending exit order."""
    local_status = _normalize_order_status(order.get("status"))
    local_fill = _to_decimal(order.get("fill_price"))
    local_venue_order_id = str(order.get("venue_order_id", "") or "")

    if local_status in ("filled", "partially_filled", "failed", "cancelled"):
        return OrderResponse(
            venue_order_id=local_venue_order_id,
            status=local_status,
            fill_price=local_fill,
            error_message=order.get("error_message"),
        )

    if not local_venue_order_id:
        return None
    return await poly.get_order_status(local_venue_order_id)


def _to_decimal(value: object) -> Decimal | None:
    """Safely parse a Decimal from an arbitrary value."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _normalize_order_status(value: object) -> OrderStatus:
    """Normalize status text into OrderStatus-compatible values."""
    status = str(value or "").strip().lower()
    if status in {"submitting", "submitted", "filled", "partially_filled", "failed", "cancelled"}:
        return status
    if status in {"canceled", "expired"}:
        return "cancelled"
    if status in {"partial", "partiallyfilled", "partially-filled"}:
        return "partially_filled"
    return "submitted"


def _to_int(value: object) -> int | None:
    """Safely parse an int from an arbitrary value."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


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


async def reconcile_open_positions_with_exchange(config: Settings) -> int:
    """Close DB positions whose tokens are no longer held on Polymarket.

    Queries each open/exit_failed position's actual conditional-token
    balance on the exchange.  Positions with zero balance are closed
    with reason ``startup_reconciled_no_balance``.

    Args:
        config: Application settings (carries _flip_pipeline sidecar).

    Returns:
        Number of positions closed by reconciliation.
    """
    pipeline = getattr(config, "_flip_pipeline", None)
    if pipeline is None:
        return 0
    pos_repo = getattr(pipeline, "_position_repo", None)
    poly = getattr(pipeline, "_poly", None)
    if pos_repo is None or poly is None:
        return 0
    try:
        positions: list[dict[str, Any]] = await pos_repo.get_open_positions()
    except Exception:
        logger.warning("reconcile_open_query_failed")
        return 0
    closed = 0
    for p in positions:
        token_id = str(p.get("token_id", ""))
        market_id = str(p.get("market_id", ""))
        if not token_id or not market_id:
            continue
        balance = await poly.get_token_balance(token_id)
        if balance < 0:
            # Could not check — skip (network error, etc.)
            continue
        if balance > 0:
            continue
        # Token balance is 0 → position no longer held on exchange
        entry_price = _to_decimal(p.get("entry_price")) or Decimal("0")
        contracts = _to_int(p.get("size_contracts")) or 0
        # Cannot determine actual exit price; use 0 realized PnL
        try:
            await pos_repo.close_position(
                market_id,
                exit_order_id="",
                exit_price=entry_price,
                realized_pnl=Decimal("0"),
                exit_reason="reconciled_no_balance",
            )
            closed += 1
            logger.warning(
                "reconcile_position_closed",
                market_id=market_id,
                token_id=token_id[:16],
                status=str(p.get("status", "")),
                contracts=contracts,
            )
            push_activity(
                "position_reconciled",
                market_id,
                pipeline="flip",
                reason="no_token_balance",
            )
        except Exception:
            logger.exception("reconcile_close_failed", market_id=market_id)
    if closed:
        logger.info("reconcile_open_complete", closed=closed, checked=len(positions))
    return closed


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
