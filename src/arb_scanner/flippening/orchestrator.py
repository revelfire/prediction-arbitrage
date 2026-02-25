"""Flippening engine orchestrator — main entry point for flip-watch."""

from __future__ import annotations

import asyncio
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
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.flippening.sports_filter import classify_sports_markets
from arb_scanner.flippening.ws_client import create_price_stream
from arb_scanner.models.config import Settings
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitSignal,
    FlippeningEvent,
    PriceUpdate,
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

    repo: Any = None
    if not dry_run:
        repo = await _create_repo(config)

    http_client = httpx.AsyncClient()
    try:
        markets = await _discover_markets(
            config,
            allowed_sports,
            http_client,
        )
        if not markets:
            logger.warning("no_sports_markets_found")

        sports_markets = classify_sports_markets(markets, allowed_sports)
        game_mgr.initialize(sports_markets)

        token_ids = [sm.token_id for sm in sports_markets]
        stream = await create_price_stream(flip_cfg)
        if token_ids:
            await stream.subscribe(token_ids)

        logger.info(
            "flip_watch_started",
            games=game_mgr.active_game_count,
            tokens=len(token_ids),
            dry_run=dry_run,
        )

        last_discovery = asyncio.get_event_loop().time()
        try:
            async for update in stream:
                await _process_update(
                    update,
                    game_mgr,
                    spike_detector,
                    signal_gen,
                    config,
                    repo,
                    http_client,
                    dry_run,
                )
                now = asyncio.get_event_loop().time()
                if now - last_discovery > _DISCOVERY_INTERVAL_SECONDS:
                    await _periodic_discovery(
                        config,
                        allowed_sports,
                        http_client,
                        game_mgr,
                        stream,
                    )
                    last_discovery = now
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("flip_watch_shutting_down")
        finally:
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
    """
    event, exit_sig = game_mgr.process(update)

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
            spike_direction=__import__(
                "arb_scanner.models.flippening",
                fromlist=["SpikeDirection"],
            ).SpikeDirection.FAVORITE_DROP,
            confidence=__import__("decimal").Decimal("0"),
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


async def _periodic_discovery(
    config: Settings,
    allowed_sports: list[str],
    client: httpx.AsyncClient,
    game_mgr: GameManager,
    stream: Any,
) -> None:
    """Refresh sports market discovery and update game manager.

    Args:
        config: Application settings.
        allowed_sports: Sports to filter for.
        client: Shared HTTP client.
        game_mgr: Game lifecycle manager.
        stream: Price stream for subscribing new tokens.
    """
    try:
        markets = await _discover_markets(
            config,
            allowed_sports,
            client,
        )
        sports_markets = classify_sports_markets(markets, allowed_sports)
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
    except Exception:
        logger.exception("periodic_discovery_failed")
