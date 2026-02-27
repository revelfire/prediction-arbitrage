"""Capital-aware position sizing and exposure management."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import structlog

from arb_scanner.models.config import ExecutionConfig

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.capital",
)

_ZERO = Decimal("0")


class CapitalManager:
    """Tracks balances, exposure, and enforces capital preservation limits.

    All pre-execution validation flows through this gatekeeper.
    """

    def __init__(
        self,
        config: ExecutionConfig,
        poly_get_balance: object,
        kalshi_get_balance: object,
    ) -> None:
        """Initialize capital manager.

        Args:
            config: Execution configuration with capital limits.
            poly_get_balance: Async callable returning Polymarket balance.
            kalshi_get_balance: Async callable returning Kalshi balance.
        """
        self._config = config
        self._poly_get_balance = poly_get_balance
        self._kalshi_get_balance = kalshi_get_balance
        self._poly_balance: Decimal = _ZERO
        self._kalshi_balance: Decimal = _ZERO
        self._daily_pnl: Decimal = _ZERO
        self._daily_pnl_date: str = ""
        self._last_loss_at: datetime | None = None
        self._open_positions: dict[str, Decimal] = {}

    @property
    def poly_balance(self) -> Decimal:
        """Current cached Polymarket balance."""
        return self._poly_balance

    @property
    def kalshi_balance(self) -> Decimal:
        """Current cached Kalshi balance."""
        return self._kalshi_balance

    @property
    def total_balance(self) -> Decimal:
        """Sum of both venue balances."""
        return self._poly_balance + self._kalshi_balance

    @property
    def current_exposure(self) -> Decimal:
        """Total USD deployed across open positions."""
        return sum(self._open_positions.values(), _ZERO)

    @property
    def daily_pnl(self) -> Decimal:
        """Today's realized P&L (resets at UTC midnight)."""
        self._maybe_reset_daily()
        return self._daily_pnl

    async def refresh_balances(self) -> tuple[Decimal, Decimal]:
        """Fetch live balances from both venues.

        Returns:
            Tuple of (poly_balance, kalshi_balance).
        """
        from typing import Any

        poly_fn: Any = self._poly_get_balance
        kalshi_fn: Any = self._kalshi_get_balance
        self._poly_balance = await poly_fn()
        self._kalshi_balance = await kalshi_fn()
        logger.info(
            "balances_refreshed",
            poly=str(self._poly_balance),
            kalshi=str(self._kalshi_balance),
        )
        return self._poly_balance, self._kalshi_balance

    def suggest_size(self) -> Decimal:
        """Compute suggested trade size based on percentage of balance.

        Returns:
            Suggested size in USD, respecting all caps.
        """
        min_bal = min(self._poly_balance, self._kalshi_balance)
        pct_size = min_bal * Decimal(str(self._config.pct_of_balance))
        poly_cap = self._poly_balance * Decimal(str(self._config.max_pct_per_venue))
        kalshi_cap = self._kalshi_balance * Decimal(str(self._config.max_pct_per_venue))
        venue_cap = min(poly_cap, kalshi_cap)
        hard_cap = Decimal(str(self._config.max_size_usd))
        result = min(pct_size, venue_cap, hard_cap)
        return max(result.quantize(Decimal("0.01")), _ZERO)

    def check_venue_reserve(self, size_usd: Decimal) -> tuple[bool, str]:
        """Check that the trade won't drop either venue below min reserve.

        Args:
            size_usd: Proposed trade size in USD.

        Returns:
            Tuple of (passed, message).
        """
        reserve = Decimal(str(self._config.min_reserve_usd))
        poly_after = self._poly_balance - size_usd
        kalshi_after = self._kalshi_balance - size_usd
        if poly_after < reserve:
            return False, (
                f"Polymarket balance would drop to ${poly_after:.2f} (reserve: ${reserve:.2f})"
            )
        if kalshi_after < reserve:
            return False, (
                f"Kalshi balance would drop to ${kalshi_after:.2f} (reserve: ${reserve:.2f})"
            )
        return True, "Venue reserves OK"

    def check_exposure(self) -> tuple[Decimal, Decimal, bool]:
        """Check total portfolio exposure against cap.

        Returns:
            Tuple of (current_exposure, remaining_capacity, blocked).
        """
        total = self.total_balance
        if total <= _ZERO:
            return _ZERO, _ZERO, True
        cap = total * Decimal(str(self._config.max_exposure_pct))
        current = self.current_exposure
        remaining = max(cap - current, _ZERO)
        blocked = current >= cap
        return current, remaining, blocked

    def check_daily_pnl(self) -> tuple[Decimal, Decimal, bool]:
        """Check daily P&L against loss limit.

        Returns:
            Tuple of (daily_pnl, limit, blocked).
        """
        self._maybe_reset_daily()
        limit = Decimal(str(self._config.daily_loss_limit_usd))
        blocked = self._daily_pnl <= -limit
        return self._daily_pnl, limit, blocked

    def check_cooldown(self) -> tuple[bool, int]:
        """Check if post-loss cooldown is active.

        Returns:
            Tuple of (active, remaining_seconds).
        """
        if self._last_loss_at is None:
            return False, 0
        elapsed = (datetime.now(tz=UTC) - self._last_loss_at).total_seconds()
        cooldown = self._config.cooldown_after_loss_seconds
        if elapsed >= cooldown:
            return False, 0
        return True, int(cooldown - elapsed)

    def check_concentration(
        self,
        market_id: str,
        size_usd: Decimal,
    ) -> tuple[Decimal, Decimal, bool]:
        """Check per-market concentration limit.

        Args:
            market_id: The market/event identifier.
            size_usd: Proposed additional size.

        Returns:
            Tuple of (current_exposure, limit, blocked).
        """
        total = self.total_balance
        if total <= _ZERO:
            return _ZERO, _ZERO, True
        limit = total * Decimal(str(self._config.max_per_market_pct))
        current = self._open_positions.get(market_id, _ZERO)
        blocked = (current + size_usd) > limit
        return current, limit, blocked

    def check_open_positions(self) -> tuple[int, int, bool]:
        """Check open position count against limit.

        Returns:
            Tuple of (current_count, max_count, blocked).
        """
        current = len(self._open_positions)
        max_pos = self._config.max_open_positions
        return current, max_pos, current >= max_pos

    def record_fill(
        self,
        arb_id: str,
        market_id: str,
        size_usd: Decimal,
        pnl: Decimal | None = None,
    ) -> None:
        """Record a fill and update in-memory state.

        Args:
            arb_id: The ticket ID.
            market_id: The market identifier for concentration tracking.
            size_usd: The executed size.
            pnl: Realized P&L (None if still open).
        """
        self._open_positions[market_id] = self._open_positions.get(market_id, _ZERO) + size_usd
        if pnl is not None:
            self._maybe_reset_daily()
            self._daily_pnl += pnl
            if pnl < _ZERO:
                self._last_loss_at = datetime.now(tz=UTC)
            logger.info(
                "capital_fill_recorded",
                arb_id=arb_id,
                pnl=str(pnl),
                daily_pnl=str(self._daily_pnl),
            )

    def close_position(self, market_id: str) -> None:
        """Remove a market from open positions.

        Args:
            market_id: The market identifier to close.
        """
        self._open_positions.pop(market_id, None)

    def _maybe_reset_daily(self) -> None:
        """Reset daily P&L at UTC midnight."""
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        if self._daily_pnl_date != today:
            self._daily_pnl = _ZERO
            self._daily_pnl_date = today
