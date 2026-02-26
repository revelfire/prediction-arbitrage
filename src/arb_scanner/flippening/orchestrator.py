"""Flippening engine orchestrator — main entry point for flip-watch."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import structlog

from arb_scanner.flippening.alert_formatter import (
    build_entry_discord_payload,
    build_entry_slack_payload,
    build_exit_discord_payload,
    build_exit_slack_payload,
    dispatch_flip_alert,
)
from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.flippening.orderbook_cache import OrderBookCache
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.flippening.tick_buffer import TickBuffer
from arb_scanner.flippening.sports_filter import (
    DiscoveryHealthSnapshot,
    check_degradation,
    classify_sports_markets,
)
from arb_scanner.flippening.ws_client import create_price_stream
from arb_scanner.flippening.ws_telemetry import WsTelemetry
from arb_scanner.models.config import Settings
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitSignal,
    FlippeningEvent,
    PriceUpdate,
    SpikeDirection,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.orchestrator",
)

_DISCOVERY_INTERVAL_SECONDS = 300  # 5 minutes


async def run_flip_watch(
    config: Settings,
    *,
    dry_run: bool = False,
    sport_filter: list[str] | None = None,
) -> None:
    """Run the flippening watch loop.

    Discovers sports markets, subscribes to price streams,
    detects spikes, and generates entry/exit signals.

    Args:
        config: Application settings.
        dry_run: Skip persistence and alerts when True.
        sport_filter: Optional list of sports to monitor.
    """
    flip_cfg = config.flippening
    if not flip_cfg.enabled and not dry_run:
        logger.warning("flippening_disabled")
        return

    allowed_sports = sport_filter or flip_cfg.sports
    spike_detector = SpikeDetector(flip_cfg)
    signal_gen = SignalGenerator(flip_cfg)
    game_mgr = GameManager(flip_cfg)
    telemetry = WsTelemetry()
    book_cache = OrderBookCache(
        max_size=flip_cfg.orderbook_cache_max_size,
        ttl_seconds=flip_cfg.orderbook_cache_ttl_seconds,
    )

    repo: Any = None
    tick_repo: Any = None
    if not dry_run:
        repo = await _create_repo(config)
        tick_repo = await _create_tick_repo(config)
    tick_buffer = TickBuffer(tick_repo, flip_cfg)

    http_client = httpx.AsyncClient(
        base_url="https://clob.polymarket.com",
        timeout=10.0,
    )
    try:
        markets = await _discover_markets(
            config,
            allowed_sports,
            http_client,
        )
        if not markets:
            logger.warning("no_sports_markets_found")

        sports_markets, health = classify_sports_markets(markets, allowed_sports, config.flippening)
        prev_health: DiscoveryHealthSnapshot | None = None
        await _handle_discovery_health(
            health,
            prev_health,
            config,
            allowed_sports,
            repo,
            http_client,
            dry_run,
        )
        prev_health = health
        game_mgr.initialize(sports_markets)

        token_ids = [sm.token_id for sm in sports_markets]
        stream = await create_price_stream(flip_cfg, telemetry=telemetry)
        if token_ids:
            await stream.subscribe(token_ids)

        logger.info(
            "flip_watch_started",
            games=game_mgr.active_game_count,
            tokens=len(token_ids),
            dry_run=dry_run,
        )

        last_discovery = asyncio.get_event_loop().time()
        last_persist = asyncio.get_event_loop().time()
        last_tick_flush = asyncio.get_event_loop().time()
        stall_count = 0
        last_stall_received = telemetry.cum_received
        last_drift_alert = 0.0
        try:
            async for update in stream:
                enriched = await book_cache.enrich(update, http_client)
                needs_flush = tick_buffer.append(enriched)
                if needs_flush:
                    await tick_buffer.flush()
                await _process_update(
                    enriched,
                    game_mgr,
                    spike_detector,
                    signal_gen,
                    config,
                    repo,
                    http_client,
                    dry_run,
                    tick_repo=tick_repo,
                )
                now = asyncio.get_event_loop().time()
                if now - last_tick_flush > flip_cfg.tick_flush_interval_seconds:
                    await tick_buffer.flush()
                    last_tick_flush = now
                if now - last_discovery > _DISCOVERY_INTERVAL_SECONDS:
                    prev_health = await _periodic_discovery(
                        config,
                        allowed_sports,
                        http_client,
                        game_mgr,
                        stream,
                        prev_health,
                        repo,
                        dry_run,
                    )
                    last_discovery = now
                stall_count, last_stall_received, last_drift_alert = await _check_telemetry(
                    telemetry,
                    book_cache,
                    config,
                    repo,
                    http_client,
                    dry_run,
                    now,
                    last_persist,
                    stall_count,
                    last_stall_received,
                    last_drift_alert,
                    stream,
                )
                if now - last_persist > flip_cfg.ws_telemetry_persist_interval_seconds:
                    last_persist = now
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("flip_watch_shutting_down")
        finally:
            await tick_buffer.flush()
            await stream.close()
    finally:
        await http_client.aclose()


async def _process_update(
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
        event = spike_detector.check_spike(
            update,
            state.baseline,
            state.price_history,
        )

    if event is not None and not game_mgr.has_open_signal(update.market_id):
        await _handle_entry(
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
        await _handle_exit(
            exit_sig,
            state,
            game_mgr,
            config,
            repo,
            http_client,
            dry_run,
        )


async def _handle_entry(
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
    )

    if not dry_run:
        await _persist_entry(repo, event, entry, state.baseline)
        await _dispatch_entry_alert(event, entry, config, http_client)


async def _handle_exit(
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
    entry = state.active_signal
    game_mgr.clear_active_signal(state.market_id)

    logger.info(
        "flip_exit",
        market_id=state.market_id,
        reason=exit_sig.exit_reason.value,
        pnl=float(exit_sig.realized_pnl),
        hold_min=float(exit_sig.hold_minutes),
    )

    if not dry_run and entry is not None:
        await _persist_exit(repo, exit_sig)
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
            detected_at=entry.created_at,
        )
        await _dispatch_exit_alert(
            event,
            entry,
            exit_sig,
            config,
            http_client,
        )


async def _persist_entry(
    repo: Any,
    event: FlippeningEvent,
    entry: EntrySignal,
    baseline: Any,
) -> None:
    """Persist entry data to the database.

    Args:
        repo: FlippeningRepository.
        event: Detected flippening event.
        entry: Entry signal.
        baseline: Baseline data.
    """
    try:
        await repo.insert_baseline(baseline)
        await repo.insert_event(event)
        await repo.insert_signal(entry)
        await repo.insert_flip_ticket(event, entry)
    except Exception:
        logger.exception("persist_entry_failed")


async def _persist_exit(repo: Any, exit_sig: ExitSignal) -> None:
    """Persist exit signal to the database.

    Args:
        repo: FlippeningRepository.
        exit_sig: Exit signal.
    """
    try:
        await repo.insert_signal(exit_sig)
    except Exception:
        logger.exception("persist_exit_failed")


async def _dispatch_entry_alert(
    event: FlippeningEvent,
    entry: EntrySignal,
    config: Settings,
    client: httpx.AsyncClient,
) -> None:
    """Dispatch entry alert webhooks.

    Args:
        event: Detected flippening event.
        entry: Entry signal.
        config: Application settings.
        client: Shared HTTP client.
    """
    notif = config.notifications
    await dispatch_flip_alert(
        build_entry_slack_payload(event, entry) if notif.slack_webhook else None,
        build_entry_discord_payload(event, entry) if notif.discord_webhook else None,
        slack_url=notif.slack_webhook,
        discord_url=notif.discord_webhook,
        client=client,
    )


async def _dispatch_exit_alert(
    event: FlippeningEvent,
    entry: EntrySignal,
    exit_sig: ExitSignal,
    config: Settings,
    client: httpx.AsyncClient,
) -> None:
    """Dispatch exit alert webhooks.

    Args:
        event: Original flippening event.
        entry: Entry signal.
        exit_sig: Exit signal.
        config: Application settings.
        client: Shared HTTP client.
    """
    notif = config.notifications
    await dispatch_flip_alert(
        build_exit_slack_payload(event, entry, exit_sig) if notif.slack_webhook else None,
        build_exit_discord_payload(event, entry, exit_sig) if notif.discord_webhook else None,
        slack_url=notif.slack_webhook,
        discord_url=notif.discord_webhook,
        client=client,
    )


async def _handle_discovery_health(
    health: DiscoveryHealthSnapshot,
    prev_health: DiscoveryHealthSnapshot | None,
    config: Settings,
    allowed_sports: list[str],
    repo: Any,
    client: httpx.AsyncClient,
    dry_run: bool,
) -> None:
    """Persist health snapshot and dispatch degradation alerts.

    Args:
        health: Current discovery health snapshot.
        prev_health: Previous cycle's health snapshot.
        config: Application settings.
        allowed_sports: Sports being monitored.
        repo: FlippeningRepository (None if dry_run).
        client: Shared HTTP client for webhooks.
        dry_run: Whether to skip persistence and alerts.
    """
    if not dry_run and repo is not None:
        try:
            import dataclasses

            await repo.insert_discovery_health(dataclasses.asdict(health))
        except Exception:
            logger.exception("discovery_health_persist_failed")

    alerts = check_degradation(
        health,
        prev_health,
        config.flippening,
        allowed_sports,
    )
    if alerts and not dry_run:
        for msg in alerts:
            logger.warning("discovery_degradation", alert=msg)
        notif = config.notifications
        if notif.slack_webhook or notif.discord_webhook:
            slack_payload = (
                {"text": f":warning: Sports Discovery Alert\n{chr(10).join(alerts)}"}
                if notif.slack_webhook
                else None
            )
            discord_payload = (
                {"content": f"**Sports Discovery Alert**\n{chr(10).join(alerts)}"}
                if notif.discord_webhook
                else None
            )
            await dispatch_flip_alert(
                slack_payload,
                discord_payload,
                slack_url=notif.slack_webhook,
                discord_url=notif.discord_webhook,
                client=client,
            )


async def _discover_markets(
    config: Settings,
    allowed_sports: list[str],
    client: httpx.AsyncClient,
) -> list[Any]:
    """Discover markets from Polymarket.

    Args:
        config: Application settings.
        allowed_sports: Sports to filter for.
        client: Shared HTTP client.

    Returns:
        List of Market objects.
    """
    try:
        from arb_scanner.ingestion.polymarket import PolymarketClient

        async with PolymarketClient(config.venues.polymarket) as poly:
            return await poly.fetch_markets()
    except Exception:
        logger.exception("market_discovery_failed")
        return []


async def _create_repo(config: Settings) -> Any:
    """Create FlippeningRepository from config.

    Args:
        config: Application settings with storage config.

    Returns:
        FlippeningRepository instance or None.
    """
    try:
        from arb_scanner.storage.db import Database
        from arb_scanner.storage.flippening_repository import (
            FlippeningRepository,
        )

        db = Database(config.storage.database_url)
        await db.connect()
        return FlippeningRepository(db.pool)
    except Exception:
        logger.exception("repo_creation_failed")
        return None


async def _create_tick_repo(config: Settings) -> Any:
    """Create TickRepository from config.

    Args:
        config: Application settings with storage config.

    Returns:
        TickRepository instance or None.
    """
    try:
        from arb_scanner.storage.db import Database
        from arb_scanner.storage.tick_repository import TickRepository

        db = Database(config.storage.database_url)
        await db.connect()
        return TickRepository(db.pool)
    except Exception:
        logger.exception("tick_repo_creation_failed")
        return None


async def _periodic_discovery(
    config: Settings,
    allowed_sports: list[str],
    client: httpx.AsyncClient,
    game_mgr: GameManager,
    stream: Any,
    prev_health: DiscoveryHealthSnapshot | None,
    repo: Any,
    dry_run: bool,
) -> DiscoveryHealthSnapshot | None:
    """Refresh sports market discovery and update game manager.

    Args:
        config: Application settings.
        allowed_sports: Sports to filter for.
        client: Shared HTTP client.
        game_mgr: Game lifecycle manager.
        stream: Price stream for subscribing new tokens.
        prev_health: Previous discovery health snapshot.
        repo: FlippeningRepository (None if dry_run).
        dry_run: Whether to skip persistence and alerts.

    Returns:
        Updated health snapshot, or prev_health on failure.
    """
    try:
        markets = await _discover_markets(
            config,
            allowed_sports,
            client,
        )
        sports_markets, health = classify_sports_markets(markets, allowed_sports, config.flippening)
        await _handle_discovery_health(
            health,
            prev_health,
            config,
            allowed_sports,
            repo,
            client,
            dry_run,
        )
        game_mgr.initialize(sports_markets)
        new_tokens = [
            sm.token_id
            for sm in sports_markets
            if game_mgr.get_state(sm.market.event_id) is not None
        ]
        if new_tokens:
            await stream.subscribe(new_tokens)
        logger.info(
            "periodic_discovery_complete",
            new_markets=len(sports_markets),
        )
        return health
    except Exception:
        logger.exception("periodic_discovery_failed")
        return prev_health


_STALL_THRESHOLD = 3
_DRIFT_ALERT_COOLDOWN = 3600.0  # 1 hour


async def _check_telemetry(
    telemetry: WsTelemetry,
    book_cache: OrderBookCache,
    config: Settings,
    repo: Any,
    client: httpx.AsyncClient,
    dry_run: bool,
    now: float,
    last_persist: float,
    stall_count: int,
    last_stall_received: int,
    last_drift_alert: float,
    stream: Any,
) -> tuple[int, int, float]:
    """Check telemetry health: persist, drift alerts, stall detection.

    Args:
        telemetry: WS telemetry tracker.
        book_cache: Order book cache.
        config: Application settings.
        repo: FlippeningRepository (None if dry_run).
        client: HTTP client for webhooks.
        dry_run: Whether to skip persistence.
        now: Current loop time.
        last_persist: Last persist timestamp.
        stall_count: Consecutive stall intervals.
        last_stall_received: cum_received at last stall check.
        last_drift_alert: Timestamp of last drift alert.
        stream: Price stream (for forced reconnect).

    Returns:
        Updated (stall_count, last_stall_received, last_drift_alert).
    """
    flip_cfg = config.flippening
    logged = telemetry.should_log(flip_cfg.ws_telemetry_interval_seconds)

    if logged:
        # Stall detection
        if telemetry.cum_received == last_stall_received:
            stall_count += 1
            if stall_count >= 2:
                logger.warning("ws_stall_detected", stalls=stall_count)
            if stall_count >= _STALL_THRESHOLD:
                logger.error("ws_stall_reconnect", stalls=stall_count)
                await stream.close()
                stall_count = 0
        else:
            stall_count = 0
        last_stall_received = telemetry.cum_received

        # Schema drift check
        if telemetry.check_drift(flip_cfg.ws_schema_match_pct):
            if now - last_drift_alert > _DRIFT_ALERT_COOLDOWN:
                logger.warning(
                    "ws_schema_drift",
                    match_rate=telemetry.schema_match_rate,
                )
                if not dry_run:
                    await _dispatch_drift_alert(config, client)
                last_drift_alert = now

    # Telemetry persistence
    persist_interval = flip_cfg.ws_telemetry_persist_interval_seconds
    if not dry_run and repo is not None and now - last_persist > persist_interval:
        snap = telemetry.snapshot()
        snap["book_cache_hit_rate"] = book_cache.cache_hit_rate
        try:
            await repo.insert_ws_telemetry(snap)
        except Exception:
            logger.exception("ws_telemetry_persist_failed")

    return stall_count, last_stall_received, last_drift_alert


async def _dispatch_drift_alert(
    config: Settings,
    client: httpx.AsyncClient,
) -> None:
    """Dispatch a schema drift alert via webhooks.

    Args:
        config: Application settings.
        client: HTTP client.
    """
    notif = config.notifications
    msg = "WebSocket schema drift detected — parser match rate below threshold."
    slack_payload = {"text": f":warning: {msg}"} if notif.slack_webhook else None
    discord_payload = {"content": f"**{msg}**"} if notif.discord_webhook else None
    await dispatch_flip_alert(
        slack_payload,
        discord_payload,
        slack_url=notif.slack_webhook,
        discord_url=notif.discord_webhook,
        client=client,
    )
