"""Unit tests for auto-execution criteria evaluator."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.execution.auto_evaluator import evaluate_criteria
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models._auto_exec_config import AutoExecutionConfig


def _make_config(**overrides: object) -> AutoExecutionConfig:
    """Build an AutoExecutionConfig with defaults."""
    return AutoExecutionConfig(**overrides)  # type: ignore[arg-type]


def _make_breakers(config: AutoExecutionConfig | None = None) -> CircuitBreakerManager:
    """Build a clean CircuitBreakerManager."""
    return CircuitBreakerManager(config or _make_config())


def _base_opportunity(**overrides: object) -> dict:
    """Build a base opportunity that passes all checks."""
    opp: dict = {
        "arb_id": "arb-001",
        "spread_pct": 0.05,
        "confidence": 0.85,
        "category": "nba",
        "ticket_type": "arbitrage",
    }
    opp.update(overrides)
    return opp


class TestEvaluateCriteria:
    """Tests for evaluate_criteria()."""

    def test_happy_path_all_pass(self) -> None:
        """All criteria pass with valid opportunity."""
        config = _make_config()
        breakers = _make_breakers(config)
        eligible, reasons = evaluate_criteria(
            _base_opportunity(),
            config,
            [],
            Decimal("0"),
            breakers,
        )
        assert eligible is True
        assert reasons == []

    def test_rejection_spread_too_low(self) -> None:
        """Rejects when spread is below minimum."""
        config = _make_config(min_spread_pct=0.05)
        breakers = _make_breakers(config)
        eligible, reasons = evaluate_criteria(
            _base_opportunity(spread_pct=0.01),
            config,
            [],
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("spread" in r and "min" in r for r in reasons)

    def test_rejection_spread_too_high(self) -> None:
        """Rejects when spread exceeds maximum."""
        config = _make_config(max_spread_pct=0.20)
        breakers = _make_breakers(config)
        eligible, reasons = evaluate_criteria(
            _base_opportunity(spread_pct=0.30),
            config,
            [],
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("spread" in r and "max" in r for r in reasons)

    def test_rejection_confidence_too_low(self) -> None:
        """Rejects when confidence is below threshold."""
        config = _make_config(min_confidence=0.80)
        breakers = _make_breakers(config)
        eligible, reasons = evaluate_criteria(
            _base_opportunity(confidence=0.50),
            config,
            [],
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("confidence" in r for r in reasons)

    def test_rejection_blocked_category(self) -> None:
        """Rejects when category is in blocked list."""
        config = _make_config(blocked_categories=["politics"])
        breakers = _make_breakers(config)
        eligible, reasons = evaluate_criteria(
            _base_opportunity(category="politics"),
            config,
            [],
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("blocked" in r for r in reasons)

    def test_rejection_category_not_in_allowed(self) -> None:
        """Rejects when category is not in allowed list."""
        config = _make_config(allowed_categories=["nba", "nfl"])
        breakers = _make_breakers(config)
        eligible, reasons = evaluate_criteria(
            _base_opportunity(category="politics"),
            config,
            [],
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("not in allowed" in r for r in reasons)

    def test_rejection_ticket_type_not_allowed(self) -> None:
        """Rejects when ticket type is not in allowed list."""
        config = _make_config(allowed_ticket_types=["arbitrage"])
        breakers = _make_breakers(config)
        eligible, reasons = evaluate_criteria(
            _base_opportunity(ticket_type="unknown"),
            config,
            [],
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("ticket_type" in r for r in reasons)

    def test_rejection_daily_loss_exceeded(self) -> None:
        """Rejects when daily loss exceeds limit."""
        config = _make_config(daily_loss_limit_usd=100.0)
        breakers = _make_breakers(config)
        eligible, reasons = evaluate_criteria(
            _base_opportunity(),
            config,
            [],
            Decimal("-150"),
            breakers,
        )
        assert eligible is False
        assert any("daily_pnl" in r for r in reasons)

    def test_rejection_too_many_open_positions(self) -> None:
        """Rejects when open positions are at max."""
        config = _make_config(max_daily_trades=2)
        breakers = _make_breakers(config)
        positions = [{"arb_id": "a"}, {"arb_id": "b"}]
        eligible, reasons = evaluate_criteria(
            _base_opportunity(),
            config,
            positions,
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("open_positions" in r for r in reasons)

    def test_rejection_duplicate_arb_id(self) -> None:
        """Rejects when arb_id already has an open position."""
        config = _make_config()
        breakers = _make_breakers(config)
        positions = [{"arb_id": "arb-001"}]
        eligible, reasons = evaluate_criteria(
            _base_opportunity(arb_id="arb-001"),
            config,
            positions,
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("duplicate" in r for r in reasons)

    def test_rejection_circuit_breaker_tripped(self) -> None:
        """Rejects when any circuit breaker is tripped."""
        config = _make_config(daily_loss_limit_usd=10.0)
        breakers = _make_breakers(config)
        breakers.check_loss(Decimal("-50"))
        assert breakers.is_any_tripped() is True

        eligible, reasons = evaluate_criteria(
            _base_opportunity(),
            config,
            [],
            Decimal("0"),
            breakers,
        )
        assert eligible is False
        assert any("circuit_breaker" in r for r in reasons)
