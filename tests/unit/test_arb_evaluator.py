"""Unit tests for the arbitrage criteria evaluator."""

from __future__ import annotations

from decimal import Decimal


from arb_scanner.execution.arb_evaluator import evaluate_arb_criteria
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models._auto_exec_config import AutoExecutionConfig


def _config(**overrides: object) -> AutoExecutionConfig:
    return AutoExecutionConfig(**overrides)  # type: ignore[arg-type]


def _breakers(tripped: bool = False) -> CircuitBreakerManager:
    b = CircuitBreakerManager(_config())
    if tripped:
        for _ in range(3):
            b.record_failure()
    return b


def _opp(**overrides: object) -> dict:
    base = {
        "arb_id": "arb-1",
        "confidence": 0.85,
        "spread_pct": 0.08,
        "category": "nba",
        "ticket_type": "arbitrage",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


class TestArbEvaluator:
    """Tests for evaluate_arb_criteria()."""

    def test_passes_when_all_criteria_met(self) -> None:
        """Happy path: all criteria pass."""
        ok, reasons = evaluate_arb_criteria(_opp(), _config(), [], Decimal("0"), _breakers())
        assert ok is True
        assert reasons == []

    def test_rejects_spread_below_min(self) -> None:
        """Spread below minimum triggers rejection."""
        ok, reasons = evaluate_arb_criteria(
            _opp(spread_pct=0.01), _config(min_spread_pct=0.03), [], Decimal("0"), _breakers()
        )
        assert ok is False
        assert any("spread" in r and "min" in r for r in reasons)

    def test_rejects_spread_above_max(self) -> None:
        """Spread above maximum triggers rejection."""
        ok, reasons = evaluate_arb_criteria(
            _opp(spread_pct=0.80), _config(max_spread_pct=0.50), [], Decimal("0"), _breakers()
        )
        assert ok is False
        assert any("spread" in r and "max" in r for r in reasons)

    def test_rejects_low_confidence(self) -> None:
        """Confidence below min triggers rejection."""
        ok, reasons = evaluate_arb_criteria(
            _opp(confidence=0.30), _config(min_confidence=0.70), [], Decimal("0"), _breakers()
        )
        assert ok is False
        assert any("confidence" in r for r in reasons)

    def test_rejects_blocked_category(self) -> None:
        """Blocked category triggers rejection."""
        ok, reasons = evaluate_arb_criteria(
            _opp(category="politics"),
            _config(blocked_categories=["politics"]),
            [],
            Decimal("0"),
            _breakers(),
        )
        assert ok is False
        assert any("blocked" in r for r in reasons)

    def test_rejects_daily_loss_exceeded(self) -> None:
        """Daily loss exceeding limit triggers rejection."""
        ok, reasons = evaluate_arb_criteria(
            _opp(), _config(daily_loss_limit_usd=100.0), [], Decimal("-150"), _breakers()
        )
        assert ok is False
        assert any("daily_pnl" in r for r in reasons)

    def test_rejects_tripped_breaker(self) -> None:
        """Tripped circuit breaker triggers rejection."""
        ok, reasons = evaluate_arb_criteria(
            _opp(), _config(), [], Decimal("0"), _breakers(tripped=True)
        )
        assert ok is False
        assert any("circuit_breaker" in r for r in reasons)

    def test_rejects_duplicate_position(self) -> None:
        """Duplicate arb_id triggers rejection."""
        positions = [{"arb_id": "arb-1"}]
        ok, reasons = evaluate_arb_criteria(
            _opp(arb_id="arb-1"), _config(), positions, Decimal("0"), _breakers()
        )
        assert ok is False
        assert any("duplicate" in r for r in reasons)


class TestArbEvaluatorVerification:
    """US5: Verify arb evaluator enforces spread bounds."""

    def test_spread_bounds_enforced(self) -> None:
        """Arb evaluator enforces both min and max spread."""
        ok_low, _ = evaluate_arb_criteria(
            _opp(spread_pct=0.01), _config(min_spread_pct=0.03), [], Decimal("0"), _breakers()
        )
        ok_high, _ = evaluate_arb_criteria(
            _opp(spread_pct=0.80), _config(max_spread_pct=0.50), [], Decimal("0"), _breakers()
        )
        assert ok_low is False
        assert ok_high is False

    def test_no_ticket_type_in_source(self) -> None:
        """The arb_evaluator source contains no ticket_type conditionals."""
        import inspect

        import arb_scanner.execution.arb_evaluator as mod

        src = inspect.getsource(mod)
        assert "ticket_type" not in src
