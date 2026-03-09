"""Circuit breaker manager for auto-execution safety."""

from __future__ import annotations

from datetime import datetime, timezone
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
        self._failure_count += 1
        if self._failure_count >= self._config.max_consecutive_failures:
            if not self._failure_tripped:
                self._failure_tripped = True
                self._failure_tripped_at = datetime.now(timezone.utc)
                self._failure_reason = f"{self._failure_count} consecutive failures"
                logger.warning(
                    "circuit_breaker_tripped",
                    breaker="failure",
                    count=self._failure_count,
                )
            return True
        return False

    def record_success(self) -> None:
        """Record a successful trade, resetting failure counter."""
        self._failure_count = 0
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
        self._failure_reason = ""
        self.reset_anomaly()
        logger.info("circuit_breakers_reset_all")

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
