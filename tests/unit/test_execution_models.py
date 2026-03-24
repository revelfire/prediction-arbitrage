"""Unit tests for execution data models."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.models.execution import (
    BalancesResponse,
    ConstraintStatus,
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
        assert r.poly_depth_contracts is None
        assert r.kalshi_depth_contracts is None


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


class TestConstraintStatus:
    """Tests for ConstraintStatus model."""

    def test_ok_constraint(self) -> None:
        """An OK constraint has ok=True."""
        c = ConstraintStatus(name="Exposure Cap", ok=True, detail="Within limits")
        assert c.ok is True
        assert c.name == "Exposure Cap"

    def test_blocked_constraint(self) -> None:
        """A blocked constraint has ok=False."""
        c = ConstraintStatus(name="Daily P&L Limit", ok=False, detail="Limit hit")
        assert c.ok is False


class TestBalancesResponse:
    """Tests for BalancesResponse model."""

    def test_serialization(self) -> None:
        """BalancesResponse serializes to JSON with all fields."""
        resp = BalancesResponse(
            poly_balance=Decimal("0"),
            kalshi_balance=Decimal("150.00"),
            total_balance=Decimal("150.00"),
            suggested_size_usd=Decimal("7.50"),
            current_exposure=Decimal("0"),
            remaining_capacity=Decimal("75.00"),
            daily_pnl=Decimal("0"),
            open_positions=0,
            constraints=[
                ConstraintStatus(name="Reserve", ok=True, detail="OK"),
            ],
        )
        data = resp.model_dump(mode="json")
        assert data["kalshi_balance"] == "150.00"
        assert data["open_positions"] == 0
        assert len(data["constraints"]) == 1
        assert data["constraints"][0]["ok"] is True

    def test_negative_pnl(self) -> None:
        """BalancesResponse handles negative P&L."""
        resp = BalancesResponse(
            poly_balance=Decimal("100"),
            kalshi_balance=Decimal("100"),
            total_balance=Decimal("200"),
            suggested_size_usd=Decimal("10"),
            current_exposure=Decimal("50"),
            remaining_capacity=Decimal("50"),
            daily_pnl=Decimal("-25.50"),
            open_positions=2,
            constraints=[],
        )
        data = resp.model_dump(mode="json")
        assert data["daily_pnl"] == "-25.50"
        assert data["open_positions"] == 2
