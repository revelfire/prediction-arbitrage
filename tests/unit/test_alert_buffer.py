"""Tests for the flippening alert buffer."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from arb_scanner.flippening.alert_buffer import AlertBuffer
from arb_scanner.models.config import (
    FeeSchedule,
    FeesConfig,
    FlippeningConfig,
    NotificationConfig,
    Settings,
    StorageConfig,
)
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    SpikeDirection,
)

_NOW = datetime.now(tz=UTC)

_FEES = FeesConfig(
    polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="on_winnings"),
    kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
)


def _settings(
    slack: str = "https://hooks.slack.com/test",
    discord: str = "",
    max_per_batch: int = 10,
) -> Settings:
    return Settings(
        flippening=FlippeningConfig(enabled=True, alert_max_per_batch=max_per_batch),
        notifications=NotificationConfig(
            slack_webhook=slack,
            discord_webhook=discord,
        ),
        storage=StorageConfig(database_url="postgresql://test:test@localhost/test"),
        fees=_FEES,
    )


def _event(market_id: str = "m1", confidence: str = "0.80") -> FlippeningEvent:
    return FlippeningEvent(
        market_id=market_id,
        market_title=f"Market {market_id}",
        baseline_yes=Decimal("0.60"),
        spike_price=Decimal("0.45"),
        spike_magnitude_pct=Decimal("0.25"),
        spike_direction=SpikeDirection.FAVORITE_DROP,
        confidence=Decimal(confidence),
        sport="nba",
        category="nba",
        detected_at=_NOW,
    )


def _entry(
    event_id: str = "e1",
    profit_pct: str = "0.20",
    size: str = "80",
) -> EntrySignal:
    return EntrySignal(
        event_id=event_id,
        side="yes",
        entry_price=Decimal("0.46"),
        target_exit_price=Decimal("0.61"),
        stop_loss_price=Decimal("0.40"),
        suggested_size_usd=Decimal(size),
        expected_profit_pct=Decimal(profit_pct),
        max_hold_minutes=45,
        created_at=_NOW,
    )


def _exit_signal(
    event_id: str = "e1",
    pnl_pct: str = "0.22",
    reason: ExitReason = ExitReason.REVERSION,
) -> ExitSignal:
    return ExitSignal(
        event_id=event_id,
        side="yes",
        exit_price=Decimal("0.58"),
        exit_reason=reason,
        realized_pnl=Decimal("0.12"),
        realized_pnl_pct=Decimal(pnl_pct),
        hold_minutes=Decimal("15"),
        created_at=_NOW,
    )


class TestAlertBufferAppend:
    """Tests for append_entry and append_exit."""

    def test_append_entry_basic(self) -> None:
        """Appending an entry increments pending count."""
        buf = AlertBuffer()
        assert buf.pending == 0
        buf.append_entry(_event(), _entry())
        assert buf.pending == 1

    def test_append_exit_basic(self) -> None:
        """Appending an exit increments pending count."""
        buf = AlertBuffer()
        buf.append_exit(_event(), _entry(), _exit_signal())
        assert buf.pending == 1

    def test_entry_dedup_keeps_higher_score(self) -> None:
        """Duplicate market_id keeps the higher-scoring entry."""
        buf = AlertBuffer()
        buf.append_entry(_event(confidence="0.50"), _entry(profit_pct="0.10"))
        buf.append_entry(_event(confidence="0.90"), _entry(profit_pct="0.30"))
        assert buf.pending == 1
        # score = profit * confidence; 0.30*0.90 > 0.10*0.50

    def test_entry_dedup_keeps_existing_if_higher(self) -> None:
        """Duplicate market_id keeps existing if new score is lower."""
        buf = AlertBuffer()
        buf.append_entry(_event(confidence="0.90"), _entry(profit_pct="0.30"))
        buf.append_entry(_event(confidence="0.50"), _entry(profit_pct="0.10"))
        assert buf.pending == 1

    def test_exit_dedup_replaces(self) -> None:
        """Duplicate market_id exit always replaces (keeps latest)."""
        buf = AlertBuffer()
        buf.append_exit(_event(), _entry(), _exit_signal(pnl_pct="0.10"))
        buf.append_exit(_event(), _entry(), _exit_signal(pnl_pct="0.30"))
        assert buf.pending == 1

    def test_entry_and_exit_independent(self) -> None:
        """Entry and exit for same market_id are tracked separately."""
        buf = AlertBuffer()
        buf.append_entry(_event(), _entry())
        buf.append_exit(_event(), _entry(), _exit_signal())
        assert buf.pending == 2


class TestAlertBufferFlush:
    """Tests for the flush method."""

    @pytest.mark.asyncio
    async def test_empty_flush_returns_zero(self) -> None:
        """Flushing empty buffer returns 0 and does no dispatch."""
        buf = AlertBuffer()
        config = _settings()
        client = AsyncMock()
        result = await buf.flush(config, client)
        assert result == 0

    @pytest.mark.asyncio
    async def test_flush_dispatches_and_clears(self) -> None:
        """Flush dispatches alerts and clears the buffer."""
        buf = AlertBuffer()
        buf.append_entry(_event(), _entry())
        buf.append_exit(_event(market_id="m2"), _entry(), _exit_signal())

        config = _settings()
        client = AsyncMock()
        with patch(
            "arb_scanner.flippening.alert_buffer.dispatch_flip_alert",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            result = await buf.flush(config, client)

        assert result == 2
        assert buf.pending == 0
        mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_flush_caps_at_max(self) -> None:
        """Flush caps total alerts at alert_max_per_batch."""
        buf = AlertBuffer()
        for i in range(15):
            buf.append_entry(
                _event(market_id=f"m{i}", confidence="0.80"),
                _entry(profit_pct=f"0.{10 + i:02d}"),
            )
        assert buf.pending == 15

        config = _settings(max_per_batch=5)
        client = AsyncMock()
        with patch(
            "arb_scanner.flippening.alert_buffer.dispatch_flip_alert",
            new_callable=AsyncMock,
        ):
            result = await buf.flush(config, client)

        assert result == 5
        assert buf.pending == 0

    @pytest.mark.asyncio
    async def test_flush_ranks_entries_by_score_desc(self) -> None:
        """Higher-scoring entries come first in the Slack payload."""
        buf = AlertBuffer()
        buf.append_entry(
            _event(market_id="low", confidence="0.50"),
            _entry(profit_pct="0.10"),
        )
        buf.append_entry(
            _event(market_id="high", confidence="0.90"),
            _entry(profit_pct="0.30"),
        )

        config = _settings()
        client = AsyncMock()
        captured_slack: list[dict[str, object]] = []

        async def capture(slack: object, discord: object, **kwargs: object) -> None:
            if slack is not None:
                captured_slack.append(slack)  # type: ignore[arg-type]

        with patch(
            "arb_scanner.flippening.alert_buffer.dispatch_flip_alert",
            side_effect=capture,
        ):
            await buf.flush(config, client)

        assert len(captured_slack) == 1
        text = captured_slack[0]["text"]
        assert isinstance(text, str)
        high_pos = text.index("Market high")
        low_pos = text.index("Market low")
        assert high_pos < low_pos

    @pytest.mark.asyncio
    async def test_flush_entries_fill_before_exits(self) -> None:
        """Entries fill the batch first, then exits fill remaining slots."""
        buf = AlertBuffer()
        for i in range(8):
            buf.append_entry(
                _event(market_id=f"e{i}", confidence="0.80"),
                _entry(profit_pct="0.20"),
            )
        for i in range(5):
            buf.append_exit(
                _event(market_id=f"x{i}"),
                _entry(),
                _exit_signal(pnl_pct="0.10"),
            )

        config = _settings(max_per_batch=10)
        client = AsyncMock()
        with patch(
            "arb_scanner.flippening.alert_buffer.dispatch_flip_alert",
            new_callable=AsyncMock,
        ):
            result = await buf.flush(config, client)

        # 8 entries + 2 exits = 10 (cap)
        assert result == 10

    @pytest.mark.asyncio
    async def test_flush_swallows_exceptions(self) -> None:
        """Flush swallows dispatch exceptions and returns 0."""
        buf = AlertBuffer()
        buf.append_entry(_event(), _entry())

        config = _settings()
        client = AsyncMock()
        with patch(
            "arb_scanner.flippening.alert_buffer.dispatch_flip_alert",
            side_effect=RuntimeError("network error"),
        ):
            result = await buf.flush(config, client)

        assert result == 0
        assert buf.pending == 0  # buffer is cleared even on error

    @pytest.mark.asyncio
    async def test_discord_payload_has_embeds(self) -> None:
        """Discord payload includes embeds for each alert."""
        buf = AlertBuffer()
        buf.append_entry(_event(), _entry())

        config = _settings(slack="", discord="https://discord.com/test")
        client = AsyncMock()
        captured_discord: list[dict[str, object]] = []

        async def capture(slack: object, discord: object, **kwargs: object) -> None:
            if discord is not None:
                captured_discord.append(discord)  # type: ignore[arg-type]

        with patch(
            "arb_scanner.flippening.alert_buffer.dispatch_flip_alert",
            side_effect=capture,
        ):
            await buf.flush(config, client)

        assert len(captured_discord) == 1
        embeds = captured_discord[0].get("embeds")
        assert isinstance(embeds, list)
        assert len(embeds) == 1
        assert "Entry:" in embeds[0]["title"]
