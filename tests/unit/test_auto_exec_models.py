"""Unit tests for auto-execution data models."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from arb_scanner.models.auto_execution import (
    AutoExecLogEntry,
    AutoExecMode,
    AutoExecPosition,
    AutoExecStats,
    CircuitBreakerState,
    CircuitBreakerType,
    CriticVerdict,
)


class TestAutoExecMode:
    """Tests for AutoExecMode literal values."""

    def test_off_is_valid(self) -> None:
        """Literal 'off' is a valid mode."""
        mode: AutoExecMode = "off"
        assert mode == "off"

    def test_manual_is_valid(self) -> None:
        """Literal 'manual' is a valid mode."""
        mode: AutoExecMode = "manual"
        assert mode == "manual"

    def test_auto_is_valid(self) -> None:
        """Literal 'auto' is a valid mode."""
        mode: AutoExecMode = "auto"
        assert mode == "auto"


class TestCriticVerdict:
    """Tests for CriticVerdict model."""

    def test_defaults(self) -> None:
        """Default verdict is approved with no flags."""
        v = CriticVerdict()
        assert v.approved is True
        assert v.risk_flags == []
        assert v.reasoning == ""
        assert v.confidence == 1.0
        assert v.skipped is False
        assert v.error is None

    def test_with_flags(self) -> None:
        """Verdict with risk flags and reasoning."""
        v = CriticVerdict(
            approved=False,
            risk_flags=["stale_data", "low_depth"],
            reasoning="Data too old",
            confidence=0.8,
        )
        assert v.approved is False
        assert len(v.risk_flags) == 2
        assert v.confidence == 0.8

    def test_with_error(self) -> None:
        """Verdict can carry an error string."""
        v = CriticVerdict(approved=True, error="timeout")
        assert v.error == "timeout"


class TestCircuitBreakerState:
    """Tests for CircuitBreakerState model."""

    def test_defaults(self) -> None:
        """Default state is not tripped."""
        s = CircuitBreakerState(breaker_type=CircuitBreakerType.loss)
        assert s.tripped is False
        assert s.tripped_at is None
        assert s.reason == ""
        assert s.reset_at is None
        assert s.requires_ack is False

    def test_tripped_state(self) -> None:
        """Tripped state with timestamp and reason."""
        now = datetime.now(timezone.utc)
        s = CircuitBreakerState(
            breaker_type=CircuitBreakerType.failure,
            tripped=True,
            tripped_at=now,
            reason="3 failures",
        )
        assert s.tripped is True
        assert s.tripped_at == now


class TestAutoExecLogEntry:
    """Tests for AutoExecLogEntry model."""

    def test_defaults(self) -> None:
        """Default log entry has zero values."""
        entry = AutoExecLogEntry(arb_id="t1")
        assert entry.id == ""
        assert entry.arb_id == "t1"
        assert entry.trigger_spread_pct == Decimal("0")
        assert entry.trigger_confidence == Decimal("0")
        assert entry.criteria_snapshot == {}
        assert entry.size_usd == Decimal("0")
        assert entry.critic_verdict is None
        assert entry.status == "pending"

    def test_with_values(self) -> None:
        """Log entry with populated fields."""
        verdict = CriticVerdict(approved=True, skipped=True)
        entry = AutoExecLogEntry(
            id="log-1",
            arb_id="t1",
            trigger_spread_pct=Decimal("0.05"),
            trigger_confidence=Decimal("0.90"),
            size_usd=Decimal("25.00"),
            critic_verdict=verdict,
            status="executed",
            source="arb_watch",
        )
        assert entry.id == "log-1"
        assert entry.size_usd == Decimal("25.00")
        assert entry.critic_verdict is not None
        assert entry.critic_verdict.skipped is True


class TestAutoExecPosition:
    """Tests for AutoExecPosition model."""

    def test_default_status_is_open(self) -> None:
        """Default position status is 'open'."""
        pos = AutoExecPosition(arb_id="t1")
        assert pos.status == "open"
        assert pos.entry_spread == Decimal("0")
        assert pos.closed_at is None

    def test_closed_status(self) -> None:
        """Position can have 'closed' status."""
        pos = AutoExecPosition(arb_id="t1", status="closed")
        assert pos.status == "closed"


class TestAutoExecStats:
    """Tests for AutoExecStats model."""

    def test_defaults(self) -> None:
        """Default stats are all zeros."""
        stats = AutoExecStats()
        assert stats.date == ""
        assert stats.total_trades == 0
        assert stats.wins == 0
        assert stats.losses == 0
        assert stats.total_pnl == Decimal("0")
        assert stats.avg_spread == Decimal("0")
        assert stats.critic_rejections == 0
        assert stats.breaker_trips == 0
