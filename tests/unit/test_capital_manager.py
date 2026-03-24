"""Unit tests for the CapitalManager."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from arb_scanner.execution.capital_manager import CapitalManager
from arb_scanner.models.config import ExecutionConfig


def _make_manager(
    *,
    poly_balance: Decimal = Decimal("1000"),
    kalshi_balance: Decimal = Decimal("800"),
    **overrides: object,
) -> CapitalManager:
    """Build a CapitalManager with async balance stubs."""
    config = ExecutionConfig(**overrides)  # type: ignore[arg-type]
    poly_fn = AsyncMock(return_value=poly_balance)
    kalshi_fn = AsyncMock(return_value=kalshi_balance)
    mgr = CapitalManager(config, poly_fn, kalshi_fn)
    mgr._poly_balance = poly_balance
    mgr._kalshi_balance = kalshi_balance
    return mgr


class TestSuggestSize:
    """Tests for suggest_size()."""

    def test_basic_sizing(self) -> None:
        """Uses pct_of_balance on the lower balance."""
        mgr = _make_manager(pct_of_balance=0.02)
        # 2% of min(1000, 800) = 2% of 800 = 16.00
        assert mgr.suggest_size() == Decimal("16.00")

    def test_capped_by_max_size(self) -> None:
        """Never exceeds max_size_usd."""
        mgr = _make_manager(pct_of_balance=0.50, max_size_usd=10.0)
        assert mgr.suggest_size() == Decimal("10.00")

    def test_capped_by_venue_pct(self) -> None:
        """Capped by max_pct_per_venue on the lower venue."""
        mgr = _make_manager(
            poly_balance=Decimal("1000"),
            kalshi_balance=Decimal("100"),
            pct_of_balance=0.50,
            max_pct_per_venue=0.05,
            max_size_usd=1000.0,
        )
        # min venue cap = 5% of 100 = 5.00
        assert mgr.suggest_size() == Decimal("5.00")

    def test_zero_balance(self) -> None:
        """Zero balance results in zero size."""
        mgr = _make_manager(poly_balance=Decimal("0"), kalshi_balance=Decimal("0"))
        assert mgr.suggest_size() == Decimal("0.00")


class TestCheckVenueReserve:
    """Tests for check_venue_reserve()."""

    def test_sufficient_reserve(self) -> None:
        """Trade leaves both venues above reserve."""
        mgr = _make_manager(min_reserve_usd=50.0)
        ok, msg = mgr.check_venue_reserve(Decimal("100"))
        assert ok is True
        assert "OK" in msg

    def test_poly_below_reserve(self) -> None:
        """Polymarket would drop below reserve."""
        mgr = _make_manager(
            poly_balance=Decimal("100"),
            min_reserve_usd=50.0,
        )
        ok, msg = mgr.check_venue_reserve(Decimal("80"))
        assert ok is False
        assert "Polymarket" in msg

    def test_kalshi_below_reserve(self) -> None:
        """Kalshi would drop below reserve."""
        mgr = _make_manager(
            poly_balance=Decimal("500"),
            kalshi_balance=Decimal("60"),
            min_reserve_usd=50.0,
        )
        ok, msg = mgr.check_venue_reserve(Decimal("20"))
        assert ok is False
        assert "Kalshi" in msg


class TestCheckExposure:
    """Tests for check_exposure()."""

    def test_no_positions(self) -> None:
        """No open positions means not blocked."""
        mgr = _make_manager(max_exposure_pct=0.25)
        current, remaining, blocked = mgr.check_exposure()
        assert current == Decimal("0")
        assert blocked is False

    def test_blocked_at_limit(self) -> None:
        """Blocked when at or above exposure cap."""
        mgr = _make_manager(max_exposure_pct=0.10)
        mgr.record_fill("t1", "m1", Decimal("200"))
        current, remaining, blocked = mgr.check_exposure()
        # cap = 0.10 * (1000 + 800) = 180, current = 200 > 180
        assert blocked is True


class TestCheckDailyPnl:
    """Tests for check_daily_pnl()."""

    def test_no_losses(self) -> None:
        """No losses means not blocked."""
        mgr = _make_manager(daily_loss_limit_usd=100.0)
        pnl, limit, blocked = mgr.check_daily_pnl()
        assert pnl == Decimal("0")
        assert blocked is False

    def test_blocked_on_loss(self) -> None:
        """Blocked when daily P&L exceeds loss limit."""
        mgr = _make_manager(daily_loss_limit_usd=50.0)
        mgr.record_fill("t1", "m1", Decimal("10"), pnl=Decimal("-60"))
        pnl, limit, blocked = mgr.check_daily_pnl()
        assert blocked is True
        assert pnl == Decimal("-60")


class TestCheckCooldown:
    """Tests for check_cooldown()."""

    def test_no_cooldown(self) -> None:
        """No recent loss means no cooldown."""
        mgr = _make_manager(cooldown_after_loss_seconds=300)
        active, remaining = mgr.check_cooldown()
        assert active is False
        assert remaining == 0

    def test_cooldown_after_loss(self) -> None:
        """Cooldown active after a loss fill."""
        mgr = _make_manager(cooldown_after_loss_seconds=300)
        mgr.record_fill("t1", "m1", Decimal("10"), pnl=Decimal("-5"))
        active, remaining = mgr.check_cooldown()
        assert active is True
        assert remaining > 0


class TestCheckConcentration:
    """Tests for check_concentration()."""

    def test_no_existing_exposure(self) -> None:
        """No existing exposure means not blocked."""
        mgr = _make_manager(max_per_market_pct=0.10)
        current, limit, blocked = mgr.check_concentration("m1", Decimal("50"))
        assert blocked is False

    def test_blocked_on_concentration(self) -> None:
        """Blocked when market exposure exceeds limit."""
        mgr = _make_manager(max_per_market_pct=0.05)
        mgr.record_fill("t1", "m1", Decimal("100"))
        # limit = 0.05 * 1800 = 90, current = 100, adding 10 = 110 > 90
        current, limit, blocked = mgr.check_concentration("m1", Decimal("10"))
        assert blocked is True


class TestCheckOpenPositions:
    """Tests for check_open_positions()."""

    def test_no_positions(self) -> None:
        """Not blocked with zero positions."""
        mgr = _make_manager(max_open_positions=5)
        current, max_pos, blocked = mgr.check_open_positions()
        assert current == 0
        assert blocked is False

    def test_blocked_at_max(self) -> None:
        """Blocked at max open positions."""
        mgr = _make_manager(max_open_positions=2)
        mgr.record_fill("t1", "m1", Decimal("10"))
        mgr.record_fill("t2", "m2", Decimal("10"))
        current, max_pos, blocked = mgr.check_open_positions()
        assert blocked is True
        assert current == 2


class TestRecordFill:
    """Tests for record_fill()."""

    def test_accumulates_exposure(self) -> None:
        """Multiple fills on same market accumulate."""
        mgr = _make_manager()
        mgr.record_fill("t1", "m1", Decimal("50"))
        mgr.record_fill("t2", "m1", Decimal("30"))
        assert mgr.current_exposure == Decimal("80")

    def test_tracks_pnl(self) -> None:
        """P&L is tracked in daily total."""
        mgr = _make_manager()
        mgr.record_fill("t1", "m1", Decimal("10"), pnl=Decimal("5"))
        mgr.record_fill("t2", "m2", Decimal("10"), pnl=Decimal("-3"))
        assert mgr.daily_pnl == Decimal("2")


class TestClosePosition:
    """Tests for close_position()."""

    def test_removes_market(self) -> None:
        """Closing a position removes it from tracking."""
        mgr = _make_manager()
        mgr.record_fill("t1", "m1", Decimal("50"))
        assert mgr.current_exposure == Decimal("50")
        mgr.close_position("m1")
        assert mgr.current_exposure == Decimal("0")

    def test_close_nonexistent(self) -> None:
        """Closing unknown market is a no-op."""
        mgr = _make_manager()
        mgr.close_position("nonexistent")
        assert mgr.current_exposure == Decimal("0")


class TestRefreshBalances:
    """Tests for refresh_balances()."""

    @pytest.mark.asyncio()
    async def test_updates_balances(self) -> None:
        """Balance getters are called and stored."""
        config = ExecutionConfig()
        poly_fn = AsyncMock(return_value=Decimal("500"))
        kalshi_fn = AsyncMock(return_value=Decimal("300"))
        mgr = CapitalManager(config, poly_fn, kalshi_fn)
        p, k = await mgr.refresh_balances()
        assert p == Decimal("500")
        assert k == Decimal("300")
        assert mgr.poly_balance == Decimal("500")
        assert mgr.kalshi_balance == Decimal("300")
