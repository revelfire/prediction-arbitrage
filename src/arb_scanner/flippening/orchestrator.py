"""Flippening engine orchestrator — main entry point for flip-watch."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from arb_scanner.flippening._orch_alerts import handle_discovery_health
from arb_scanner.flippening._orch_processing import process_update
from arb_scanner.flippening.price_ring_buffer import (
    PriceRingBuffer,
    set_shared_buffer,
)
from arb_scanner.flippening._orch_repo import (
    create_repo,
    create_tick_repo,
    discover_markets,
)
from arb_scanner.flippening._orch_telemetry import check_telemetry
from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.flippening.market_classifier import (
    DiscoveryHealthSnapshot,
    classify_markets,
)
from arb_scanner.flippening.orderbook_cache import OrderBookCache
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.flippening.tick_buffer import TickBuffer
from arb_scanner.flippening.ws_client import create_price_stream
from arb_scanner.flippening.ws_telemetry import WsTelemetry
from arb_scanner.models.config import CategoryConfig, Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.orchestrator",
)

_DISCOVERY_INTERVAL_SECONDS = 300  # 5 minutes


async def run_flip_watch(
    config: Settings,
    *,
    dry_run: bool = False,
    sport_filter: list[str] | None = None,
    category_filter: list[str] | None = None,
) -> None:
    """Run the flippening watch loop.

    Discovers markets by category, subscribes to price streams,
    detects spikes, and generates entry/exit signals.

    Args:
        config: Application settings.
        dry_run: Skip persistence and alerts when True.
        sport_filter: Legacy sport filter (mapped to category_filter).
        category_filter: Optional list of categories to monitor.
    """
    flip_cfg = config.flippening
    if not flip_cfg.enabled and not dry_run:
        logger.warning("flippening_disabled")
        return

    effective_filter = category_filter or sport_filter
    active_categories = _resolve_categories(flip_cfg.categories, effective_filter)

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
        repo = await create_repo(config)
        tick_repo = await create_tick_repo(config)
    tick_buffer = TickBuffer(tick_repo, flip_cfg)

    price_buffer = PriceRingBuffer()
    set_shared_buffer(price_buffer)

    http_client = httpx.AsyncClient(base_url="https://clob.polymarket.com", timeout=10.0)
    try:
        await _run_main_loop(
            config,
            active_categories,
            game_mgr,
            spike_detector,
            signal_gen,
            telemetry,
            book_cache,
            repo,
            tick_repo,
            tick_buffer,
            http_client,
            dry_run,
        )
    finally:
        await http_client.aclose()


async def _run_main_loop(
    config: Settings,
    active_categories: dict[str, CategoryConfig],
    game_mgr: GameManager,
    spike_detector: SpikeDetector,
    signal_gen: SignalGenerator,
    telemetry: WsTelemetry,
    book_cache: OrderBookCache,
    repo: Any,
    tick_repo: Any,
    tick_buffer: TickBuffer,
    http_client: httpx.AsyncClient,
    dry_run: bool,
) -> None:
    """Inner loop: discover, subscribe, process updates."""
    markets = await discover_markets(config)
    if not markets:
        logger.warning("no_markets_found")

    category_markets, health = classify_markets(markets, active_categories, config.flippening)
    prev_health: DiscoveryHealthSnapshot | None = None
    await handle_discovery_health(
        health,
        prev_health,
        config,
        active_categories,
        repo,
        http_client,
        dry_run,
    )
    prev_health = health
    game_mgr.initialize(category_markets)

    token_ids = [sm.token_id for sm in category_markets]
    stream = await create_price_stream(config.flippening, telemetry=telemetry)
    if token_ids:
        await stream.subscribe(token_ids)

    logger.info(
        "flip_watch_started",
        games=game_mgr.active_game_count,
        tokens=len(token_ids),
        dry_run=dry_run,
    )

    timers = _LoopTimers()
    try:
        async for update in stream:
            enriched = await book_cache.enrich(update, http_client)
            if tick_buffer.append(enriched):
                await tick_buffer.flush()
            await process_update(
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
            if now - timers.last_tick_flush > config.flippening.tick_flush_interval_seconds:
                await tick_buffer.flush()
                timers.last_tick_flush = now
            if now - timers.last_discovery > _DISCOVERY_INTERVAL_SECONDS:
                prev_health = await _periodic_discovery(
                    config,
                    active_categories,
                    http_client,
                    game_mgr,
                    stream,
                    prev_health,
                    repo,
                    dry_run,
                )
                timers.last_discovery = now
            (
                timers.stall_count,
                timers.last_stall_received,
                timers.last_drift_alert,
            ) = await check_telemetry(
                telemetry,
                book_cache,
                config,
                repo,
                http_client,
                dry_run,
                now,
                timers.last_persist,
                timers.stall_count,
                timers.last_stall_received,
                timers.last_drift_alert,
                stream,
            )
            if now - timers.last_persist > config.flippening.ws_telemetry_persist_interval_seconds:
                timers.last_persist = now
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("flip_watch_shutting_down")
    finally:
        await tick_buffer.flush()
        await stream.close()


class _LoopTimers:
    """Mutable timer state for the main loop."""

    def __init__(self) -> None:
        """Initialise timer values."""
        _now = asyncio.get_event_loop().time()
        self.last_discovery: float = _now
        self.last_persist: float = _now
        self.last_tick_flush: float = _now
        self.stall_count: int = 0
        self.last_stall_received: int = 0
        self.last_drift_alert: float = 0.0


async def _periodic_discovery(
    config: Settings,
    categories: dict[str, CategoryConfig],
    client: httpx.AsyncClient,
    game_mgr: GameManager,
    stream: Any,
    prev_health: DiscoveryHealthSnapshot | None,
    repo: Any,
    dry_run: bool,
) -> DiscoveryHealthSnapshot | None:
    """Refresh market discovery and update game manager.

    Args:
        config: Application settings.
        categories: Active category configs.
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
        markets = await discover_markets(config)
        category_markets, health = classify_markets(markets, categories, config.flippening)
        await handle_discovery_health(
            health,
            prev_health,
            config,
            categories,
            repo,
            client,
            dry_run,
        )
        game_mgr.initialize(category_markets)
        new_tokens = [
            sm.token_id
            for sm in category_markets
            if game_mgr.get_state(sm.market.event_id) is not None
        ]
        if new_tokens:
            await stream.subscribe(new_tokens)
        logger.info("periodic_discovery_complete", new_markets=len(category_markets))
        return health
    except Exception:
        logger.exception("periodic_discovery_failed")
        return prev_health


def _resolve_categories(
    all_categories: dict[str, CategoryConfig],
    filter_list: list[str] | None,
) -> dict[str, CategoryConfig]:
    """Resolve active categories from config and optional filter.

    Args:
        all_categories: All configured categories.
        filter_list: Optional category/sport filter list.

    Returns:
        Dict of category_id -> CategoryConfig for active categories.
    """
    if filter_list:
        return {k: v for k, v in all_categories.items() if k in filter_list and v.enabled}
    return {k: v for k, v in all_categories.items() if v.enabled}
