"""Unit tests for order book liquidity validation."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.execution.base import contracts_from_usd, estimate_vwap
from arb_scanner.execution.liquidity import validate_liquidity
from arb_scanner.models.config import ExecutionConfig


def _make_book(asks: list[tuple[float, int]]) -> dict:
    """Build a minimal order book with ask levels."""
    return {
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        "bids": [],
    }


class TestContractsFromUsd:
    """Tests for contracts_from_usd()."""

    def test_basic_conversion(self) -> None:
        """$10 at $0.50 = 20 contracts."""
        assert contracts_from_usd(Decimal("10"), Decimal("0.50")) == 20

    def test_rounds_down(self) -> None:
        """Fractional contracts round down."""
        assert contracts_from_usd(Decimal("10"), Decimal("0.33")) == 30

    def test_zero_price(self) -> None:
        """Zero price returns 0 contracts."""
        assert contracts_from_usd(Decimal("10"), Decimal("0")) == 0


class TestEstimateVwap:
    """Tests for estimate_vwap()."""

    def test_single_level(self) -> None:
        """VWAP equals the price when one level has enough depth."""
        levels = [{"price": "0.55", "size": "100"}]
        vwap, depth = estimate_vwap(levels, 50)
        assert vwap == Decimal("0.55")
        assert depth == 50  # filled contracts, not total book

    def test_walks_multiple_levels(self) -> None:
        """VWAP walks through levels when size exceeds first."""
        levels = [
            {"price": "0.55", "size": "10"},
            {"price": "0.60", "size": "30"},
        ]
        vwap, depth = estimate_vwap(levels, 20)
        # 10 @ 0.55 + 10 @ 0.60 = 5.50 + 6.00 = 11.50 / 20 = 0.575
        assert vwap == Decimal("0.575")

    def test_empty_book(self) -> None:
        """Empty book returns zero."""
        vwap, depth = estimate_vwap([], 10)
        assert vwap == Decimal("0")
        assert depth == 0

    def test_insufficient_depth(self) -> None:
        """Partial fill when book doesn't have enough depth."""
        levels = [{"price": "0.50", "size": "5"}]
        vwap, depth = estimate_vwap(levels, 20)
        assert depth == 5


class TestValidateLiquidity:
    """Tests for validate_liquidity()."""

    def test_passes_with_deep_books(self) -> None:
        """Passes when both books have sufficient depth."""
        config = ExecutionConfig(max_slippage_pct=0.05, min_book_depth_contracts=10)
        poly_book = _make_book([(0.55, 100)])
        kalshi_book = _make_book([(0.42, 100)])
        result = validate_liquidity(
            poly_book,
            kalshi_book,
            Decimal("10"),
            Decimal("0.55"),
            Decimal("0.42"),
            config,
        )
        assert result.passed is True
        assert len(result.warnings) == 0

    def test_fails_on_slippage(self) -> None:
        """Fails when slippage exceeds threshold."""
        config = ExecutionConfig(max_slippage_pct=0.01, min_book_depth_contracts=5)
        # Only 5 contracts at 0.55, then jumps to 0.70
        poly_book = _make_book([(0.55, 5), (0.70, 100)])
        kalshi_book = _make_book([(0.42, 100)])
        result = validate_liquidity(
            poly_book,
            kalshi_book,
            Decimal("50"),
            Decimal("0.55"),
            Decimal("0.42"),
            config,
        )
        assert result.passed is False
        assert any("slippage" in w.lower() for w in result.warnings)

    def test_fails_on_insufficient_depth(self) -> None:
        """Fails when book depth is below minimum."""
        config = ExecutionConfig(max_slippage_pct=0.10, min_book_depth_contracts=50)
        poly_book = _make_book([(0.55, 10)])
        kalshi_book = _make_book([(0.42, 10)])
        result = validate_liquidity(
            poly_book,
            kalshi_book,
            Decimal("5"),
            Decimal("0.55"),
            Decimal("0.42"),
            config,
        )
        assert result.passed is False

    def test_empty_books(self) -> None:
        """Empty books fail validation."""
        config = ExecutionConfig(min_book_depth_contracts=5)
        result = validate_liquidity(
            {"asks": [], "bids": []},
            {"asks": [], "bids": []},
            Decimal("10"),
            Decimal("0.50"),
            Decimal("0.50"),
            config,
        )
        assert result.passed is False

    def test_max_absorbable(self) -> None:
        """max_absorbable_usd is computed."""
        config = ExecutionConfig(max_slippage_pct=0.05, min_book_depth_contracts=5)
        poly_book = _make_book([(0.55, 500)])
        kalshi_book = _make_book([(0.42, 500)])
        result = validate_liquidity(
            poly_book,
            kalshi_book,
            Decimal("10"),
            Decimal("0.55"),
            Decimal("0.42"),
            config,
        )
        assert result.max_absorbable_usd >= Decimal("0")
