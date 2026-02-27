"""Unit tests for execution data models."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.models.execution import (
    ExecutionOrder,
    ExecutionResult,
    LiquidityResult,
    OrderRequest,
    OrderResponse,
    PreflightCheck,
    PreflightResult,
)


class TestPreflightCheck:
    """Tests for PreflightCheck model."""

    def test_passed_check(self) -> None:
        """A passing check has passed=True."""
        c = PreflightCheck(name="test", passed=True, message="OK")
        assert c.passed is True
        assert c.value is None

    def test_failed_check_with_value(self) -> None:
        """A failed check can carry a numeric value."""
        c = PreflightCheck(
            name="exposure",
            passed=False,
            message="Too high",
            value=Decimal("500.00"),
        )
        assert c.passed is False
        assert c.value == Decimal("500.00")


class TestPreflightResult:
    """Tests for PreflightResult model."""

    def test_all_passed_true(self) -> None:
        """all_passed is True when every check passes."""
        r = PreflightResult(
            checks=[
                PreflightCheck(name="a", passed=True, message="ok"),
                PreflightCheck(name="b", passed=True, message="ok"),
            ]
        )
        assert r.all_passed is True

    def test_all_passed_false(self) -> None:
        """all_passed is False when any check fails."""
        r = PreflightResult(
            checks=[
                PreflightCheck(name="a", passed=True, message="ok"),
                PreflightCheck(name="b", passed=False, message="fail"),
            ]
        )
        assert r.all_passed is False

    def test_empty_checks_passes(self) -> None:
        """Empty checks list vacuously passes."""
        r = PreflightResult(checks=[])
        assert r.all_passed is True

    def test_default_values(self) -> None:
        """Default values are zero/none."""
        r = PreflightResult(checks=[])
        assert r.suggested_size_usd == Decimal("0")
        assert r.max_size_usd == Decimal("0")
        assert r.estimated_slippage_poly is None


class TestExecutionOrder:
    """Tests for ExecutionOrder model."""

    def test_default_status(self) -> None:
        """Default status is submitting."""
        o = ExecutionOrder(
            id="abc",
            arb_id="t1",
            venue="polymarket",
            side="buy_yes",
            requested_price=Decimal("0.55"),
            size_usd=Decimal("10"),
        )
        assert o.status == "submitting"
        assert o.fill_price is None
        assert o.venue_order_id is None


class TestExecutionResult:
    """Tests for ExecutionResult model."""

    def test_default_status(self) -> None:
        """Default status is pending."""
        r = ExecutionResult(id="abc", arb_id="t1")
        assert r.status == "pending"
        assert r.total_cost_usd is None


class TestOrderRequest:
    """Tests for OrderRequest model."""

    def test_fields(self) -> None:
        """All fields are set correctly."""
        req = OrderRequest(
            venue="kalshi",
            side="buy_no",
            price=Decimal("0.40"),
            size_usd=Decimal("50"),
            size_contracts=125,
            ticker="KXELECTION",
        )
        assert req.venue == "kalshi"
        assert req.size_contracts == 125
        assert req.ticker == "KXELECTION"
        assert req.token_id == ""


class TestOrderResponse:
    """Tests for OrderResponse model."""

    def test_defaults(self) -> None:
        """Default response is empty submitting."""
        resp = OrderResponse()
        assert resp.status == "submitting"
        assert resp.venue_order_id == ""


class TestLiquidityResult:
    """Tests for LiquidityResult model."""

    def test_defaults(self) -> None:
        """Default liquidity result fails."""
        lr = LiquidityResult()
        assert lr.passed is False
        assert lr.warnings == []
        assert lr.poly_depth_contracts == 0

    def test_passing_result(self) -> None:
        """A result with passed=True and data."""
        lr = LiquidityResult(
            poly_vwap=Decimal("0.56"),
            kalshi_vwap=Decimal("0.42"),
            poly_slippage=Decimal("0.005"),
            kalshi_slippage=Decimal("0.003"),
            poly_depth_contracts=200,
            kalshi_depth_contracts=150,
            max_absorbable_usd=Decimal("500.00"),
            passed=True,
        )
        assert lr.passed is True
        assert lr.max_absorbable_usd == Decimal("500.00")
