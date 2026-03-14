"""Circuit breaker manager for auto-execution safety."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog

from arb_scanner.models._auto_exec_config import AutoExecutionConfig
from arb_scanner.models.auto_execution import CircuitBreakerState, CircuitBreakerType

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.circuit_breaker",
)


class CircuitBreakerManager:
    """Manages three independent circuit breakers: loss, failure, anomaly.

    Args:
        config: Auto-execution configuration.
    """

    def __init__(self, config: AutoExecutionConfig) -> None:
        """Initialize circuit breakers.

        Args:
            config: Auto-execution configuration with limits.
        """
        self._config = config
        self._loss_tripped = False
        self._loss_tripped_at: datetime | None = None
        self._loss_reason = ""
        self._failure_count = 0
        self._failure_tripped = False
        self._failure_tripped_at: datetime | None = None
        self._failure_reason = ""
        self._failure_probe_cooldown_min_seconds = max(
            float(config.failure_probe_cooldown_min_seconds),
            1.0,
        )
        self._failure_probe_cooldown_max_seconds = max(
            float(config.failure_probe_cooldown_max_seconds),
            self._failure_probe_cooldown_min_seconds,
        )
        self._failure_probe_cooldown_seconds = max(
            float(config.cooldown_seconds),
            self._failure_probe_cooldown_min_seconds,
        )
        self._failure_probe_backoff_multiplier = max(
            float(config.failure_probe_backoff_multiplier),
            1.01,
        )
        self._failure_probe_recovery_multiplier = min(
            max(float(config.failure_probe_recovery_multiplier), 0.10),
            0.99,
        )
        self._failure_probe_after: datetime | None = None
        self._failure_probe_token_active = False
        self._failure_probe_attempt_active = False
        self._failure_probe_attempts = 0
        self._failure_probe_successes = 0
        self._failure_probe_failures = 0
        self._anomaly_tripped = False
        self._anomaly_tripped_at: datetime | None = None
        self._anomaly_reason = ""

    def is_any_tripped(self) -> bool:
        """Check if any circuit breaker is currently tripped.

        Returns:
            True if any breaker is tripped.
        """
        self._auto_reset_loss()
        self._auto_reset_failure()
        return self._loss_tripped or self._failure_tripped or self._anomaly_tripped

    def check_loss(self, daily_pnl: Decimal) -> bool:
        """Check and potentially trip the loss breaker.

        Args:
            daily_pnl: Today's cumulative P&L.

        Returns:
            True if breaker is tripped.
        """
        self._auto_reset_loss()
        limit = Decimal(str(self._config.daily_loss_limit_usd))
        if daily_pnl < -limit:
            if not self._loss_tripped:
                self._loss_tripped = True
                self._loss_tripped_at = datetime.now(timezone.utc)
                self._loss_reason = (
                    f"Daily loss ${float(-daily_pnl):.2f} exceeds limit ${float(limit):.2f}"
                )
                logger.warning(
                    "circuit_breaker_tripped",
                    breaker="loss",
                    pnl=float(daily_pnl),
                    limit=float(limit),
                )
            return True
        return False

    def record_failure(self) -> bool:
        """Record a trade failure and check breaker threshold.

        Returns:
            True if breaker is now tripped.
        """
        if self._failure_probe_attempt_active:
            self._failure_probe_failures += 1
            self._failure_probe_attempt_active = False
            self._failure_probe_cooldown_seconds = min(
                self._failure_probe_cooldown_seconds * self._failure_probe_backoff_multiplier,
                self._failure_probe_cooldown_max_seconds,
            )
            self._failure_probe_token_active = False

        self._failure_count += 1
        if self._failure_count >= self._config.max_consecutive_failures:
            if not self._failure_tripped:
                self._failure_tripped = True
                self._failure_tripped_at = datetime.now(timezone.utc)
                self._failure_probe_after = self._failure_tripped_at + timedelta(
                    seconds=self._failure_probe_cooldown_seconds
                )
                self._failure_reason = f"{self._failure_count} consecutive failures"
                logger.warning(
                    "circuit_breaker_tripped",
                    breaker="failure",
                    count=self._failure_count,
                )
            else:
                self._failure_probe_after = datetime.now(timezone.utc) + timedelta(
                    seconds=self._failure_probe_cooldown_seconds
                )
            return True
        return False

    def record_success(self) -> None:
        """Record a successful trade, resetting failure counter."""
        if self._failure_probe_attempt_active:
            self._failure_probe_successes += 1
            self._failure_probe_attempt_active = False
            self._failure_probe_cooldown_seconds = max(
                self._failure_probe_cooldown_seconds * self._failure_probe_recovery_multiplier,
                self._failure_probe_cooldown_min_seconds,
            )
        self._failure_probe_token_active = False
        self._failure_count = 0
        self._failure_probe_after = None
        if self._failure_tripped:
            self._failure_tripped = False
            self._failure_tripped_at = None
            self._failure_reason = ""
            logger.info("circuit_breaker_reset", breaker="failure")

    def check_anomaly(self, spread_pct: float) -> bool:
        """Check and potentially trip the anomaly breaker.

        Args:
            spread_pct: Current spread percentage.

        Returns:
            True if breaker is tripped.
        """
        threshold = self._config.critic.anomaly_spread_pct
        if spread_pct > threshold:
            if not self._anomaly_tripped:
                self._anomaly_tripped = True
                self._anomaly_tripped_at = datetime.now(timezone.utc)
                self._anomaly_reason = (
                    f"Spread {spread_pct:.2%} exceeds anomaly threshold {threshold:.2%}"
                )
                logger.warning(
                    "circuit_breaker_tripped",
                    breaker="anomaly",
                    spread=spread_pct,
                    threshold=threshold,
                )
            return True
        return False

    def reset_loss(self) -> None:
        """Manually reset the loss breaker."""
        self._loss_tripped = False
        self._loss_tripped_at = None
        self._loss_reason = ""
        logger.info("circuit_breaker_reset", breaker="loss")

    def reset_anomaly(self) -> None:
        """Manually acknowledge and reset the anomaly breaker."""
        self._anomaly_tripped = False
        self._anomaly_tripped_at = None
        self._anomaly_reason = ""
        logger.info("circuit_breaker_reset", breaker="anomaly")

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        self._loss_tripped = False
        self._loss_tripped_at = None
        self._loss_reason = ""
        self._failure_count = 0
        self._failure_tripped = False
        self._failure_tripped_at = None
        self._failure_probe_after = None
        self._failure_probe_token_active = False
        self._failure_probe_attempt_active = False
        self._failure_reason = ""
        self.reset_anomaly()
        logger.info("circuit_breakers_reset_all")

    def get_blocking_reasons(self, *, allow_failure_probe: bool = False) -> list[str]:
        """Return breaker reasons that should block execution.

        Args:
            allow_failure_probe: If True, allows one periodic probe trade while
                only the failure breaker is tripped.
        """
        tripped = [s for s in self.get_state() if s.tripped]
        if not tripped:
            return []
        if allow_failure_probe and self._consume_failure_probe_if_eligible(tripped):
            return []
        return [f"circuit_breaker_{s.breaker_type.value}: {s.reason}" for s in tripped]

    def consume_failure_probe_attempt(self) -> bool:
        """Mark that a probe-allowed execution is actually attempting an order.

        Returns:
            True when this execution consumes an active probe token.
        """
        if not self._failure_probe_token_active:
            return False
        self._failure_probe_token_active = False
        self._failure_probe_attempt_active = True
        self._failure_probe_attempts += 1
        return True

    def get_failure_probe_metrics(self) -> dict[str, object]:
        """Return failure-probe telemetry counters."""
        attempts = self._failure_probe_attempts
        successes = self._failure_probe_successes
        success_rate = float(successes / attempts) if attempts > 0 else 0.0
        next_probe_after = (
            self._failure_probe_after.isoformat()
            if self._failure_probe_after is not None
            else None
        )
        return {
            "cooldown_seconds": self._failure_probe_cooldown_seconds,
            "token_active": self._failure_probe_token_active,
            "attempt_active": self._failure_probe_attempt_active,
            "attempts": attempts,
            "successes": successes,
            "failures": self._failure_probe_failures,
            "success_rate": success_rate,
            "next_probe_after": next_probe_after,
        }

    def get_state(self) -> list[CircuitBreakerState]:
        """Get current state of all circuit breakers.

        Returns:
            List of breaker states.
        """
        self._auto_reset_loss()
        self._auto_reset_failure()
        return [
            CircuitBreakerState(
                breaker_type=CircuitBreakerType.loss,
                tripped=self._loss_tripped,
                tripped_at=self._loss_tripped_at,
                reason=self._loss_reason,
            ),
            CircuitBreakerState(
                breaker_type=CircuitBreakerType.failure,
                tripped=self._failure_tripped,
                tripped_at=self._failure_tripped_at,
                reason=self._failure_reason,
            ),
            CircuitBreakerState(
                breaker_type=CircuitBreakerType.anomaly,
                tripped=self._anomaly_tripped,
                tripped_at=self._anomaly_tripped_at,
                reason=self._anomaly_reason,
                requires_ack=True,
            ),
        ]

    def reset_failure(self) -> None:
        """Manually reset the failure breaker."""
        self._failure_count = 0
        self._failure_tripped = False
        self._failure_tripped_at = None
        self._failure_probe_after = None
        self._failure_probe_token_active = False
        self._failure_probe_attempt_active = False
        self._failure_reason = ""
        logger.info("circuit_breaker_reset", breaker="failure")

    def _auto_reset_failure(self) -> None:
        """Reset failure breaker after 15 minutes."""
        if not self._failure_tripped or self._failure_tripped_at is None:
            return
        now = datetime.now(timezone.utc)
        elapsed = (now - self._failure_tripped_at).total_seconds()
        if elapsed >= 900:  # 15 minutes
            self._failure_count = 0
            self._failure_tripped = False
            self._failure_tripped_at = None
            self._failure_probe_after = None
            self._failure_probe_token_active = False
            self._failure_probe_attempt_active = False
            self._failure_reason = ""
            logger.info("circuit_breaker_auto_reset", breaker="failure", elapsed_s=elapsed)

    def _auto_reset_loss(self) -> None:
        """Reset loss breaker at UTC midnight."""
        if not self._loss_tripped or self._loss_tripped_at is None:
            return
        now = datetime.now(timezone.utc)
        if now.date() > self._loss_tripped_at.date():
            self._loss_tripped = False
            self._loss_tripped_at = None
            self._loss_reason = ""
            logger.info("circuit_breaker_auto_reset", breaker="loss")

    def _consume_failure_probe_if_eligible(self, tripped: list[CircuitBreakerState]) -> bool:
        """Return True if we should allow a limited probe while failure-tripped."""
        if len(tripped) != 1 or tripped[0].breaker_type is not CircuitBreakerType.failure:
            return False
        if not self._failure_tripped:
            return False
        now = datetime.now(timezone.utc)
        ready_at = self._failure_probe_after
        if ready_at is None:
            ready_at = now + timedelta(seconds=self._failure_probe_cooldown_seconds)
            self._failure_probe_after = ready_at
        if now < ready_at:
            return False
        self._failure_probe_after = now + timedelta(seconds=self._failure_probe_cooldown_seconds)
        self._failure_probe_token_active = True
        logger.info(
            "circuit_breaker_failure_probe_open",
            cooldown_seconds=self._failure_probe_cooldown_seconds,
        )
        return True
