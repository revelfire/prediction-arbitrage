"""Tests for per-market exposure cap correctness."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.execution.arb_pipeline import (
    _compute_market_exposure as arb_compute,
)
from arb_scanner.execution.flip_pipeline import (
    _compute_market_exposure as flip_compute,
)


class TestPerMarketExposure:
    """Verify per-market exposure calculation."""

    def test_single_position_exposure(self) -> None:
        """Single position calculates correctly."""
        positions = [
            {
                "market_id": "mkt-1",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
            }
        ]
        result = flip_compute(positions, "mkt-1")
        assert result == Decimal("50")

    def test_multiple_positions_same_market(self) -> None:
        """Multiple positions for same market are summed."""
        positions = [
            {
                "market_id": "mkt-1",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
            },
            {
                "market_id": "mkt-1",
                "entry_price": Decimal("0.60"),
                "size_contracts": 50,
            },
        ]
        result = flip_compute(positions, "mkt-1")
        assert result == Decimal("80")

    def test_ignores_other_markets(self) -> None:
        """Exposure for other markets is not included."""
        positions = [
            {
                "market_id": "mkt-1",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
            },
            {
                "market_id": "mkt-2",
                "entry_price": Decimal("0.80"),
                "size_contracts": 200,
            },
        ]
        result = flip_compute(positions, "mkt-1")
        assert result == Decimal("50")

    def test_empty_positions_returns_zero(self) -> None:
        """No positions returns zero exposure."""
        result = flip_compute([], "mkt-1")
        assert result == Decimal("0")

    def test_no_matching_market_returns_zero(self) -> None:
        """No positions for target market returns zero."""
        positions = [
            {
                "market_id": "mkt-2",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
            }
        ]
        result = flip_compute(positions, "mkt-1")
        assert result == Decimal("0")

    def test_arb_pipeline_same_logic(self) -> None:
        """Arb pipeline uses same computation logic."""
        positions = [
            {
                "market_id": "arb-1",
                "entry_price": Decimal("0.40"),
                "size_contracts": 50,
            }
        ]
        result = arb_compute(positions, "arb-1")
        assert result == Decimal("20")
