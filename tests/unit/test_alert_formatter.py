"""Tests for flippening alert formatting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arb_scanner.flippening.alert_formatter import (
    build_entry_discord_payload,
    build_entry_slack_payload,
    build_exit_discord_payload,
    build_exit_slack_payload,
    dispatch_flip_alert,
)
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    SpikeDirection,
)

_NOW = datetime.now(tz=UTC)


def _event() -> FlippeningEvent:
    return FlippeningEvent(
        market_id="m1",
        market_title="Lakers vs Celtics",
        baseline_yes=Decimal("0.65"),
        spike_price=Decimal("0.48"),
        spike_magnitude_pct=Decimal("0.262"),
        spike_direction=SpikeDirection.FAVORITE_DROP,
        confidence=Decimal("0.82"),
        sport="nba",
        detected_at=_NOW,
    )


def _entry() -> EntrySignal:
    return EntrySignal(
        event_id="e1",
        side="yes",
        entry_price=Decimal("0.50"),
        target_exit_price=Decimal("0.605"),
        stop_loss_price=Decimal("0.425"),
        suggested_size_usd=Decimal("80.00"),
        expected_profit_pct=Decimal("0.21"),
        max_hold_minutes=45,
        created_at=_NOW,
    )


def _exit_sig(reason: ExitReason = ExitReason.REVERSION) -> ExitSignal:
    return ExitSignal(
        event_id="e1",
        side="yes",
        exit_price=Decimal("0.61"),
        exit_reason=reason,
        realized_pnl=Decimal("0.11"),
        realized_pnl_pct=Decimal("0.22"),
        hold_minutes=Decimal("15.0"),
        created_at=_NOW + timedelta(minutes=15),
    )


class TestEntryPayloads:
    """Tests for entry alert payloads."""

    def test_slack_has_header_and_emoji(self) -> None:
        """Slack entry payload has rotating_light header."""
        payload = build_entry_slack_payload(_event(), _entry())
        header = payload["blocks"][0]["text"]["text"]
        assert "Flippening Detected" in header
        assert ":rotating_light:" in header

    def test_discord_has_orange_color(self) -> None:
        """Discord entry payload has orange color."""
        payload = build_entry_discord_payload(_event(), _entry())
        assert payload["embeds"][0]["color"] == 15105570

    def test_slack_contains_sport(self) -> None:
        """Slack entry payload includes sport field."""
        payload = build_entry_slack_payload(_event(), _entry())
        fields_text = str(payload["blocks"][1]["fields"])
        assert "NBA" in fields_text

    def test_discord_contains_confidence(self) -> None:
        """Discord entry payload includes confidence."""
        payload = build_entry_discord_payload(_event(), _entry())
        fields = payload["embeds"][0]["fields"]
        conf_field = next(f for f in fields if f["name"] == "Confidence")
        assert "82%" in conf_field["value"]


class TestExitPayloads:
    """Tests for exit alert payloads."""

    def test_slack_reversion_has_moneybag(self) -> None:
        """Slack reversion exit uses moneybag emoji."""
        payload = build_exit_slack_payload(
            _event(),
            _entry(),
            _exit_sig(ExitReason.REVERSION),
        )
        header = payload["blocks"][0]["text"]["text"]
        assert ":moneybag:" in header

    def test_slack_stop_loss_has_x(self) -> None:
        """Slack stop-loss exit uses x emoji."""
        payload = build_exit_slack_payload(
            _event(),
            _entry(),
            _exit_sig(ExitReason.STOP_LOSS),
        )
        header = payload["blocks"][0]["text"]["text"]
        assert ":x:" in header

    def test_discord_reversion_green(self) -> None:
        """Discord reversion exit has green color."""
        payload = build_exit_discord_payload(
            _event(),
            _entry(),
            _exit_sig(ExitReason.REVERSION),
        )
        assert payload["embeds"][0]["color"] == 3066993

    def test_discord_stop_loss_red(self) -> None:
        """Discord stop-loss exit has red color."""
        payload = build_exit_discord_payload(
            _event(),
            _entry(),
            _exit_sig(ExitReason.STOP_LOSS),
        )
        assert payload["embeds"][0]["color"] == 15158332

    def test_discord_timeout_gray(self) -> None:
        """Discord timeout exit has gray color."""
        payload = build_exit_discord_payload(
            _event(),
            _entry(),
            _exit_sig(ExitReason.TIMEOUT),
        )
        assert payload["embeds"][0]["color"] == 9807270


class TestDispatch:
    """Tests for dispatch_flip_alert."""

    @pytest.mark.asyncio
    async def test_dispatch_does_not_raise(self) -> None:
        """Dispatch swallows errors gracefully."""
        await dispatch_flip_alert(
            {"text": "test"},
            {"content": "test"},
            slack_url="",
            discord_url="",
        )
