"""Tests for flippening orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.flippening._orch_processing import process_update as _process_update
from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.flippening.orchestrator import run_flip_watch
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.models.config import (
    FeeSchedule,
    FeesConfig,
    FlippeningConfig,
    NotificationConfig,
    Settings,
    StorageConfig,
)
from arb_scanner.models.flippening import (
    PriceUpdate,
    SportsMarket,
)
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=UTC)

_FEES = FeesConfig(
    polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="on_winnings"),
    kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
)


def _minimal_settings(enabled: bool = True) -> Settings:
    """Build minimal Settings for testing."""
    return Settings(
        flippening=FlippeningConfig(enabled=enabled),
        notifications=NotificationConfig(),
        storage=StorageConfig(database_url="postgresql://test:test@localhost/test"),
        fees=_FEES,
    )


def _update(
    market_id: str = "m1",
    yes_bid: str = "0.48",
    yes_ask: str = "0.50",
    ts: datetime | None = None,
) -> PriceUpdate:
    return PriceUpdate(
        market_id=market_id,
        token_id="tok-1",
        yes_bid=Decimal(yes_bid),
        yes_ask=Decimal(yes_ask),
        no_bid=Decimal("0.49"),
        no_ask=Decimal("0.51"),
        timestamp=ts or _NOW,
    )


def _market() -> Market:
    return Market(
        venue=Venue.POLYMARKET,
        event_id="m1",
        title="Lakers vs Celtics",
        description="NBA game",
        resolution_criteria="Official",
        yes_bid=Decimal("0.65"),
        yes_ask=Decimal("0.67"),
        no_bid=Decimal("0.32"),
        no_ask=Decimal("0.34"),
        volume_24h=Decimal("10000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


class TestRunFlipWatch:
    """Tests for run_flip_watch entry point."""

    @pytest.mark.asyncio
    async def test_exits_when_disabled(self) -> None:
        """Exits early when flippening is not enabled."""
        config = _minimal_settings(enabled=False)
        await run_flip_watch(config, dry_run=False)

    @pytest.mark.asyncio
    async def test_dry_run_runs_even_when_disabled(self) -> None:
        """Dry run proceeds even when flippening disabled."""
        config = _minimal_settings(enabled=False)
        with (
            patch(
                "arb_scanner.flippening._orch_repo.discover_markets",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "arb_scanner.flippening.orchestrator.create_price_stream",
                new_callable=AsyncMock,
            ) as mock_stream,
        ):
            mock_ps = AsyncMock()
            mock_ps.subscribe = AsyncMock()
            mock_ps.close = AsyncMock()
            mock_ps.__aiter__ = MagicMock(return_value=_async_iter([]))
            mock_stream.return_value = mock_ps
            await run_flip_watch(config, dry_run=True)


class TestProcessUpdate:
    """Tests for _process_update pipeline."""

    @pytest.mark.asyncio
    async def test_spike_triggers_entry(self) -> None:
        """Spike detection triggers entry signal creation."""
        flip_cfg = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.10,
            min_confidence=0.0,
        )
        game_mgr = GameManager(flip_cfg)
        spike_det = SpikeDetector(flip_cfg)
        signal_gen = SignalGenerator(flip_cfg)

        sm = SportsMarket(
            market=_market(),
            sport="nba",
            category="nba",
            game_start_time=_NOW - timedelta(minutes=30),
            token_id="tok-1",
        )
        game_mgr.initialize([sm])

        baseline_upd = _update(
            yes_bid="0.64",
            yes_ask="0.66",
            ts=_NOW,
        )
        game_mgr.process(baseline_upd)

        state = game_mgr.get_state("m1")
        assert state is not None
        assert state.baseline is not None

        spike_upd = _update(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=30),
        )
        event = spike_det.check_spike(
            spike_upd,
            state.baseline,
            state.price_history,
        )
        assert event is not None
        entry = signal_gen.create_entry(
            event,
            spike_upd.yes_ask,
            state.baseline,
        )
        assert entry.side == "yes"
        assert entry.entry_price == Decimal("0.46")

    @pytest.mark.asyncio
    async def test_exit_on_reversion(self) -> None:
        """Exit signal generated when price reverts to target."""
        from arb_scanner.models.flippening import EntrySignal, ExitReason

        signal_gen = SignalGenerator(FlippeningConfig(enabled=True))
        entry = EntrySignal(
            event_id="e1",
            side="yes",
            entry_price=Decimal("0.50"),
            target_exit_price=Decimal("0.60"),
            stop_loss_price=Decimal("0.42"),
            suggested_size_usd=Decimal("80"),
            expected_profit_pct=Decimal("0.20"),
            max_hold_minutes=45,
            created_at=_NOW,
        )
        update = _update(
            yes_bid="0.61",
            ts=_NOW + timedelta(minutes=10),
        )
        exit_sig = signal_gen.check_exit(update, entry)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.REVERSION

    @pytest.mark.asyncio
    async def test_no_spike_on_unknown_market(self) -> None:
        """Unknown market_id produces no signals."""
        flip_cfg = FlippeningConfig(enabled=True)
        config = _minimal_settings()
        game_mgr = GameManager(flip_cfg)
        spike_det = SpikeDetector(flip_cfg)
        signal_gen = SignalGenerator(flip_cfg)

        import httpx

        client = httpx.AsyncClient()
        try:
            await _process_update(
                _update(market_id="unknown"),
                game_mgr,
                spike_det,
                signal_gen,
                config,
                None,
                client,
                True,
            )
        finally:
            await client.aclose()


async def _async_iter(items: list[object]) -> object:
    """Create an async iterator from a list."""
    for item in items:
        yield item
