"""Unit tests for auto-execution position sizing."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.execution.auto_sizing import compute_auto_size
from arb_scanner.models._auto_exec_config import AutoExecutionConfig


def _make_config(**overrides: object) -> AutoExecutionConfig:
    """Build an AutoExecutionConfig with defaults."""
    return AutoExecutionConfig(**overrides)  # type: ignore[arg-type]


class TestComputeAutoSize:
    """Tests for compute_auto_size()."""

    def test_base_case(self) -> None:
        """Simple computation: base_size * (spread / min_spread)."""
        config = _make_config(base_size_usd=25.0, max_size_usd=50.0, min_size_usd=5.0)
        result = compute_auto_size(
            spread_pct=0.06,
            min_spread_pct=0.03,
            config=config,
            market_exposure=Decimal("0"),
            available_balance=Decimal("1000"),
        )
        # 25 * (0.06/0.03) = 50.0, capped at max_size=50
        assert result == Decimal("50.00")

    def test_larger_spread_gives_larger_size(self) -> None:
        """Size scales linearly with spread ratio."""
        config = _make_config(base_size_usd=10.0, max_size_usd=100.0, min_size_usd=5.0)
        small = compute_auto_size(0.04, 0.03, config, Decimal("0"), Decimal("1000"))
        large = compute_auto_size(0.08, 0.03, config, Decimal("0"), Decimal("1000"))
        assert small is not None
        assert large is not None
        assert large > small

    def test_max_size_cap(self) -> None:
        """Size is capped at max_size_usd."""
        config = _make_config(base_size_usd=50.0, max_size_usd=30.0, min_size_usd=5.0)
        result = compute_auto_size(0.10, 0.03, config, Decimal("0"), Decimal("1000"))
        assert result == Decimal("30.00")

    def test_per_market_cap(self) -> None:
        """Size respects per-market exposure cap."""
        config = _make_config(
            base_size_usd=50.0,
            max_size_usd=100.0,
            min_size_usd=5.0,
            max_per_market_usd=20.0,
        )
        result = compute_auto_size(0.06, 0.03, config, Decimal("10"), Decimal("1000"))
        # per_market cap: 20 - 10 = 10 remaining
        assert result is not None
        assert result <= Decimal("10.00")

    def test_balance_cap(self) -> None:
        """Size capped at 50% of available balance."""
        config = _make_config(base_size_usd=50.0, max_size_usd=100.0, min_size_usd=5.0)
        result = compute_auto_size(0.06, 0.03, config, Decimal("0"), Decimal("20"))
        # 50% of 20 = 10
        assert result is not None
        assert result <= Decimal("10.00")

    def test_returns_none_below_min_size(self) -> None:
        """Returns None when computed size is below min_size_usd."""
        config = _make_config(
            base_size_usd=2.0,
            max_size_usd=50.0,
            min_size_usd=5.0,
        )
        result = compute_auto_size(0.04, 0.03, config, Decimal("0"), Decimal("1000"))
        # 2.0 * (0.04/0.03) = 2.66, below min 5.0
        assert result is None

    def test_returns_none_when_min_spread_zero(self) -> None:
        """Returns None when min_spread_pct is zero to avoid division by zero."""
        config = _make_config()
        result = compute_auto_size(0.05, 0.0, config, Decimal("0"), Decimal("1000"))
        assert result is None

    def test_returns_none_when_spread_zero(self) -> None:
        """Returns None when spread_pct is zero."""
        config = _make_config()
        result = compute_auto_size(0.0, 0.03, config, Decimal("0"), Decimal("1000"))
        assert result is None

    def test_quantization_to_cent(self) -> None:
        """Result is quantized to $0.01."""
        config = _make_config(base_size_usd=10.0, max_size_usd=100.0, min_size_usd=5.0)
        result = compute_auto_size(0.07, 0.03, config, Decimal("0"), Decimal("1000"))
        # 10 * (0.07/0.03) = 23.333...
        assert result is not None
        assert result == result.quantize(Decimal("0.01"))

    def test_returns_none_when_market_fully_exposed(self) -> None:
        """Returns None when market exposure is at cap."""
        config = _make_config(
            base_size_usd=25.0,
            max_size_usd=50.0,
            min_size_usd=5.0,
            max_per_market_usd=100.0,
        )
        result = compute_auto_size(
            0.06,
            0.03,
            config,
            Decimal("100"),
            Decimal("1000"),
        )
        assert result is None
