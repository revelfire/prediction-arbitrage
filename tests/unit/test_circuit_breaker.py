"""Unit tests for the circuit breaker manager."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models._auto_exec_config import AutoExecutionConfig
from arb_scanner.models.auto_execution import CircuitBreakerType


def _make_breakers(**overrides: object) -> CircuitBreakerManager:
    """Build a CircuitBreakerManager with default config."""
    config = AutoExecutionConfig(**overrides)  # type: ignore[arg-type]
    return CircuitBreakerManager(config)


class TestInitialState:
    """Tests for initial circuit breaker state."""

    def test_none_tripped_initially(self) -> None:
        """No breakers tripped on creation."""
        mgr = _make_breakers()
        assert mgr.is_any_tripped() is False

    def test_get_state_returns_three_breakers(self) -> None:
        """State contains loss, failure, and anomaly breakers."""
        mgr = _make_breakers()
        states = mgr.get_state()
        assert len(states) == 3
        types = {s.breaker_type for s in states}
        assert types == {
            CircuitBreakerType.loss,
            CircuitBreakerType.failure,
            CircuitBreakerType.anomaly,
        }

    def test_all_breakers_not_tripped(self) -> None:
        """All initial states have tripped=False."""
        mgr = _make_breakers()
        for s in mgr.get_state():
            assert s.tripped is False


class TestLossBreaker:
    """Tests for the daily loss circuit breaker."""

    def test_trips_when_exceeds_limit(self) -> None:
        """Loss breaker trips when daily P&L exceeds limit."""
        mgr = _make_breakers(daily_loss_limit_usd=100.0)
        result = mgr.check_loss(Decimal("-150"))
        assert result is True
        assert mgr.is_any_tripped() is True

    def test_does_not_trip_within_limit(self) -> None:
        """Loss breaker stays clear when within limit."""
        mgr = _make_breakers(daily_loss_limit_usd=100.0)
        result = mgr.check_loss(Decimal("-50"))
        assert result is False
        assert mgr.is_any_tripped() is False

    def test_does_not_trip_on_positive_pnl(self) -> None:
        """Positive P&L never trips the loss breaker."""
        mgr = _make_breakers(daily_loss_limit_usd=100.0)
        result = mgr.check_loss(Decimal("500"))
        assert result is False

    def test_auto_resets_at_midnight(self) -> None:
        """Loss breaker auto-resets when date changes."""
        mgr = _make_breakers(daily_loss_limit_usd=50.0)
        mgr.check_loss(Decimal("-100"))
        assert mgr.is_any_tripped() is True

        # Simulate tripped_at was yesterday
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        mgr._loss_tripped_at = yesterday

        # Next call to is_any_tripped triggers _auto_reset_loss
        assert mgr.is_any_tripped() is False


class TestFailureBreaker:
    """Tests for the consecutive failure circuit breaker."""

    def test_trips_after_max_failures(self) -> None:
        """Failure breaker trips after max_consecutive_failures."""
        mgr = _make_breakers(max_consecutive_failures=3)
        mgr.record_failure()
        mgr.record_failure()
        assert mgr.is_any_tripped() is False

        result = mgr.record_failure()
        assert result is True
        assert mgr.is_any_tripped() is True

    def test_does_not_trip_below_threshold(self) -> None:
        """Failure breaker stays clear below threshold."""
        mgr = _make_breakers(max_consecutive_failures=5)
        for _ in range(4):
            mgr.record_failure()
        assert mgr.is_any_tripped() is False

    def test_success_resets_counter(self) -> None:
        """Successful trade resets the failure counter."""
        mgr = _make_breakers(max_consecutive_failures=3)
        mgr.record_failure()
        mgr.record_failure()
        mgr.record_success()

        # Should need 3 more failures to trip
        mgr.record_failure()
        mgr.record_failure()
        assert mgr.is_any_tripped() is False

    def test_success_clears_tripped_state(self) -> None:
        """Success after trip clears the failure breaker."""
        mgr = _make_breakers(max_consecutive_failures=2)
        mgr.record_failure()
        mgr.record_failure()
        assert mgr.is_any_tripped() is True

        mgr.record_success()
        assert mgr.is_any_tripped() is False

    def test_failure_probe_window_allows_periodic_attempt(self) -> None:
        """Failure-only trip can periodically allow one probe attempt."""
        mgr = _make_breakers(max_consecutive_failures=1, cooldown_seconds=30)
        mgr.record_failure()

        reasons = mgr.get_blocking_reasons(allow_failure_probe=True)
        assert any("circuit_breaker_failure" in r for r in reasons)
        mgr._failure_probe_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        reasons = mgr.get_blocking_reasons(allow_failure_probe=True)
        assert reasons == []

        reasons = mgr.get_blocking_reasons(allow_failure_probe=True)
        assert any("circuit_breaker_failure" in r for r in reasons)

    def test_failure_probe_metrics_track_failed_probe(self) -> None:
        """Probe attempt that fails is reflected in telemetry counters."""
        mgr = _make_breakers(max_consecutive_failures=1, cooldown_seconds=30)
        mgr.record_failure()
        mgr._failure_probe_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert mgr.get_blocking_reasons(allow_failure_probe=True) == []
        assert mgr.consume_failure_probe_attempt() is True
        mgr.record_failure()

        metrics = mgr.get_failure_probe_metrics()
        assert metrics["attempts"] == 1
        assert metrics["failures"] == 1
        assert metrics["successes"] == 0

    def test_failure_probe_metrics_track_successful_probe(self) -> None:
        """Probe attempt that succeeds updates success rate."""
        mgr = _make_breakers(max_consecutive_failures=1, cooldown_seconds=30)
        mgr.record_failure()
        mgr._failure_probe_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert mgr.get_blocking_reasons(allow_failure_probe=True) == []
        assert mgr.consume_failure_probe_attempt() is True
        mgr.record_success()

        metrics = mgr.get_failure_probe_metrics()
        assert metrics["attempts"] == 1
        assert metrics["failures"] == 0
        assert metrics["successes"] == 1
        assert metrics["success_rate"] == 1.0

    def test_failure_probe_cooldown_adapts_to_outcomes(self) -> None:
        """Probe cooldown grows on failed probes and shrinks on successful probes."""
        mgr = _make_breakers(max_consecutive_failures=1, cooldown_seconds=40)
        mgr.record_failure()
        baseline = float(mgr.get_failure_probe_metrics()["cooldown_seconds"])

        mgr._failure_probe_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert mgr.get_blocking_reasons(allow_failure_probe=True) == []
        assert mgr.consume_failure_probe_attempt() is True
        mgr.record_failure()
        grown = float(mgr.get_failure_probe_metrics()["cooldown_seconds"])
        assert grown > baseline

        mgr._failure_probe_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert mgr.get_blocking_reasons(allow_failure_probe=True) == []
        assert mgr.consume_failure_probe_attempt() is True
        mgr.record_success()
        shrunk = float(mgr.get_failure_probe_metrics()["cooldown_seconds"])
        assert shrunk < grown
        assert shrunk >= 15.0


class TestAnomalyBreaker:
    """Tests for the anomaly spread circuit breaker."""

    def test_trips_when_spread_exceeds_threshold(self) -> None:
        """Anomaly breaker trips on extreme spreads."""
        mgr = _make_breakers()
        # Default anomaly_spread_pct is 0.30 (from CriticConfig)
        result = mgr.check_anomaly(0.50)
        assert result is True
        assert mgr.is_any_tripped() is True

    def test_does_not_trip_within_threshold(self) -> None:
        """Normal spread does not trip anomaly breaker."""
        mgr = _make_breakers()
        result = mgr.check_anomaly(0.10)
        assert result is False
        assert mgr.is_any_tripped() is False

    def test_reset_anomaly_clears_breaker(self) -> None:
        """Manual reset clears the anomaly breaker."""
        mgr = _make_breakers()
        mgr.check_anomaly(0.50)
        assert mgr.is_any_tripped() is True

        mgr.reset_anomaly()
        assert mgr.is_any_tripped() is False

    def test_anomaly_requires_ack(self) -> None:
        """Anomaly breaker state has requires_ack=True."""
        mgr = _make_breakers()
        states = mgr.get_state()
        anomaly = next(s for s in states if s.breaker_type == CircuitBreakerType.anomaly)
        assert anomaly.requires_ack is True


class TestResetAll:
    """Tests for reset_all()."""

    def test_clears_all_breakers(self) -> None:
        """Reset all clears every breaker."""
        mgr = _make_breakers(daily_loss_limit_usd=10.0, max_consecutive_failures=1)
        mgr.check_loss(Decimal("-100"))
        mgr.record_failure()
        mgr.check_anomaly(0.99)
        assert mgr.is_any_tripped() is True

        mgr.reset_all()
        assert mgr.is_any_tripped() is False
        for s in mgr.get_state():
            assert s.tripped is False


class TestGetState:
    """Tests for get_state() accuracy."""

    def test_returns_correct_tripped_states(self) -> None:
        """get_state reflects which breakers are tripped."""
        mgr = _make_breakers(daily_loss_limit_usd=10.0)
        mgr.check_loss(Decimal("-50"))

        states = mgr.get_state()
        loss = next(s for s in states if s.breaker_type == CircuitBreakerType.loss)
        failure = next(s for s in states if s.breaker_type == CircuitBreakerType.failure)
        assert loss.tripped is True
        assert loss.reason != ""
        assert failure.tripped is False
