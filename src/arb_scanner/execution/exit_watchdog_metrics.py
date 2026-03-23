"""In-memory telemetry for flip exit watchdog behavior."""

from __future__ import annotations

from typing import Final

_DEFAULT_KEYS: Final[tuple[str, ...]] = (
    "stale_detected",
    "cancel_failed",
    "retries_placed",
    "retry_exhausted",
    "retry_failed",
    "retry_closed",
)


class ExitWatchdogMetrics:
    """Tracks counters for stale pending-exit recovery actions."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {k: 0 for k in _DEFAULT_KEYS}

    def incr(self, key: str, amount: int = 1) -> None:
        """Increment a metric counter."""
        self._counts[key] = self._counts.get(key, 0) + max(amount, 0)

    def snapshot(self) -> dict[str, int]:
        """Return a copy of current metric counters."""
        return dict(self._counts)
