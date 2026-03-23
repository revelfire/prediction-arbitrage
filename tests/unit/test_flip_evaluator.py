"""Unit tests for the flippening criteria evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal


from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.execution.flip_evaluator import evaluate_flip_criteria
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
        "arb_id": "flip-1",
        "confidence": 0.85,
        "spread_pct": 0.15,
        "category": "nba",
        "ticket_type": "flippening",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


class TestFlipEvaluator:
    """Tests for evaluate_flip_criteria()."""

    def test_passes_when_all_criteria_met(self) -> None:
        """Happy path: all criteria pass."""
        ok, reasons = evaluate_flip_criteria(_opp(), _config(), [], Decimal("0"), _breakers())
        assert ok is True
        assert reasons == []

    def test_rejects_low_confidence(self) -> None:
        """Confidence below min triggers rejection."""
        ok, reasons = evaluate_flip_criteria(
            _opp(confidence=0.30), _config(min_confidence=0.70), [], Decimal("0"), _breakers()
        )
        assert ok is False
        assert any("confidence" in r for r in reasons)

    def test_dynamic_confidence_relaxes_for_strong_spread(self) -> None:
        """High spread in healthy regime can pass slightly below base min confidence."""
        ok, reasons = evaluate_flip_criteria(
            _opp(confidence=0.62, spread_pct=0.30),
            _config(min_confidence=0.65, min_spread_pct=0.03),
            [],
            Decimal("0"),
            _breakers(),
        )
        assert ok is True
        assert reasons == []

    def test_dynamic_confidence_tightens_under_drawdown_and_load(self) -> None:
        """Drawdown and position load increase required confidence."""
        positions = [{"arb_id": f"pos-{i}"} for i in range(8)]
        ok, reasons = evaluate_flip_criteria(
            _opp(confidence=0.62, spread_pct=0.30),
            _config(min_confidence=0.65, min_spread_pct=0.03, max_open_positions=10),
            positions,
            Decimal("-180"),
            _breakers(),
        )
        assert ok is False
        assert any("drawdown_penalty" in r for r in reasons)

    def test_rejects_blocked_category(self) -> None:
        """Blocked category triggers rejection."""
        ok, reasons = evaluate_flip_criteria(
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
        ok, reasons = evaluate_flip_criteria(
            _opp(), _config(daily_loss_limit_usd=100.0), [], Decimal("-150"), _breakers()
        )
        assert ok is False
        assert any("daily_pnl" in r for r in reasons)

    def test_rejects_max_open_positions(self) -> None:
        """Max open positions triggers rejection."""
        positions = [{"arb_id": f"pos-{i}", "status": "open"} for i in range(10)]
        ok, reasons = evaluate_flip_criteria(
            _opp(), _config(max_open_positions=10), positions, Decimal("0"), _breakers()
        )
        assert ok is False
        assert any("open_positions" in r for r in reasons)

    def test_does_not_reject_large_spread(self) -> None:
        """Large spread does NOT cause rejection — deviation IS the signal."""
        ok, reasons = evaluate_flip_criteria(
            _opp(spread_pct=0.90), _config(max_spread_pct=0.50), [], Decimal("0"), _breakers()
        )
        assert ok is True
        assert reasons == []

    def test_rejects_duplicate_position(self) -> None:
        """Duplicate arb_id with status=open triggers rejection."""
        positions = [{"arb_id": "flip-1", "status": "open"}]
        ok, reasons = evaluate_flip_criteria(
            _opp(arb_id="flip-1"), _config(), positions, Decimal("0"), _breakers()
        )
        assert ok is False
        assert any("duplicate" in r for r in reasons)

    def test_allows_duplicate_when_exiting(self) -> None:
        """Duplicate arb_id with exit_pending/exit_failed does NOT reject."""
        for status in ("exit_pending", "exit_failed"):
            positions = [{"arb_id": "flip-1", "status": status}]
            ok, reasons = evaluate_flip_criteria(
                _opp(arb_id="flip-1"), _config(), positions, Decimal("0"), _breakers()
            )
            assert ok is True, f"Should allow re-entry when status={status}"
            assert not any("duplicate" in r for r in reasons)

    def test_rejects_tripped_breaker(self) -> None:
        """Tripped circuit breaker triggers rejection."""
        ok, reasons = evaluate_flip_criteria(
            _opp(), _config(), [], Decimal("0"), _breakers(tripped=True)
        )
        assert ok is False
        assert any("circuit_breaker" in r for r in reasons)

    def test_failure_probe_allows_attempt_after_cooldown(self) -> None:
        """Flip evaluator allows periodic probes when only failure breaker is tripped."""
        breakers = _breakers(tripped=True)
        breakers._failure_probe_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        ok, reasons = evaluate_flip_criteria(_opp(), _config(), [], Decimal("0"), breakers)
        assert ok is True
        assert reasons == []


class TestFlipEvaluatorVerification:
    """US5: Verify flip evaluator has no arb-specific logic."""

    def test_extreme_spread_not_rejected(self) -> None:
        """Spread=0.90 (far outside arb bounds) does NOT reject."""
        ok, reasons = evaluate_flip_criteria(
            _opp(spread_pct=0.90),
            _config(min_spread_pct=0.03, max_spread_pct=0.50),
            [],
            Decimal("0"),
            _breakers(),
        )
        assert ok is True

    def test_rejects_disallowed_ticket_type(self) -> None:
        """Disallowed ticket_type triggers rejection."""
        ok, reasons = evaluate_flip_criteria(
            _opp(ticket_type="unknown"),
            _config(allowed_ticket_types=["arbitrage", "flippening"]),
            [],
            Decimal("0"),
            _breakers(),
        )
        assert ok is False
        assert any("ticket_type" in r for r in reasons)

    def test_allows_valid_ticket_type(self) -> None:
        """Valid ticket_type passes the check."""
        ok, _ = evaluate_flip_criteria(
            _opp(ticket_type="flippening"),
            _config(allowed_ticket_types=["arbitrage", "flippening"]),
            [],
            Decimal("0"),
            _breakers(),
        )
        assert ok is True
