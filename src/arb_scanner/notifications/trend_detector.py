"""Trend detection engine for spread movement alerting.

Analyses a rolling window of scan results to detect convergence, divergence,
new highs, disappeared opportunities, and scanner health issues.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.models.analytics import AlertType, TrendAlert
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import TrendAlertConfig

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="notifications.trend_detector",
)


class TrendDetector:
    """Stateful detector that converts scan results into trend alerts.

    Maintains a rolling window of per-pair spread snapshots and fires alerts
    when spreads converge, diverge, hit new highs, disappear, or when the
    scanner itself appears unhealthy.
    """

    def __init__(self, config: TrendAlertConfig) -> None:
        self._config = config
        self._window: deque[dict[str, Decimal]] = deque(maxlen=config.window_size)
        self._cooldowns: dict[tuple[str, str], datetime] = {}
        self._consecutive_failures: int = 0
        self._consecutive_zero_opps: int = 0

    def ingest(self, scan_result: dict[str, Any]) -> list[TrendAlert]:
        """Process a scan result and return any triggered trend alerts.

        Args:
            scan_result: Dict from ``run_scan`` with ``_raw_opps`` key.

        Returns:
            A (possibly empty) list of :class:`TrendAlert` objects.
        """
        opps: list[ArbOpportunity] = scan_result.get("_raw_opps", [])
        self._update_window(opps)

        if len(self._window) < 2:
            return self._apply_cooldown(self._detect_health(scan_result))

        alerts: list[TrendAlert] = []
        alerts.extend(self._detect_convergence())
        alerts.extend(self._detect_divergence())
        alerts.extend(self._detect_new_highs())
        alerts.extend(self._detect_disappeared())
        alerts.extend(self._detect_health(scan_result))
        return self._apply_cooldown(alerts)

    @staticmethod
    def _pair_key(opp: ArbOpportunity) -> str:
        """Return ``"<poly_event_id>/<kalshi_event_id>"`` for *opp*."""
        return f"{opp.poly_market.event_id}/{opp.kalshi_market.event_id}"

    def _update_window(self, opps: list[ArbOpportunity]) -> None:
        """Build a spread snapshot and append it to the rolling window."""
        self._window.append({self._pair_key(o): o.net_spread_pct for o in opps})

    def _rolling_avg(self, pair_key: str) -> Decimal | None:
        """Mean spread for *pair_key* across all window entries, or ``None``."""
        values = [e[pair_key] for e in self._window if pair_key in e]
        if not values:
            return None
        total: Decimal = sum(values, Decimal(0))
        return total / len(values)

    def _rolling_max(self, pair_key: str) -> Decimal | None:
        """Max spread for *pair_key* across all window entries, or ``None``."""
        values = [e[pair_key] for e in self._window if pair_key in e]
        return max(values) if values else None

    def _pairs_in_window(self, min_count: int) -> set[str]:
        """Return pair keys seen in at least *min_count* window entries."""
        counts: dict[str, int] = {}
        for entry in self._window:
            for key in entry:
                counts[key] = counts.get(key, 0) + 1
        return {k for k, v in counts.items() if v >= min_count}

    # -- detectors ---------------------------------------------------------

    def _detect_convergence(self) -> list[TrendAlert]:
        """Detect pairs whose spread dropped below the rolling average."""
        alerts: list[TrendAlert] = []
        current = self._window[-1]
        threshold = Decimal(str(self._config.convergence_threshold_pct))
        now = datetime.now(tz=UTC)
        for pair_key, spread in current.items():
            avg = self._rolling_avg(pair_key)
            if avg is None:
                continue
            if spread < avg * (1 - threshold):
                pid, kid = pair_key.split("/", 1)
                alerts.append(
                    TrendAlert(
                        alert_type=AlertType.convergence,
                        poly_event_id=pid,
                        kalshi_event_id=kid,
                        spread_before=avg,
                        spread_after=spread,
                        message=(
                            f"Spread converging: {pair_key} dropped "
                            f"from {avg:.2%} avg to {spread:.2%}"
                        ),
                        dispatched_at=now,
                    )
                )
        return alerts

    def _detect_divergence(self) -> list[TrendAlert]:
        """Detect pairs whose spread rose above the rolling average."""
        alerts: list[TrendAlert] = []
        current = self._window[-1]
        threshold = Decimal(str(self._config.divergence_threshold_pct))
        now = datetime.now(tz=UTC)
        for pair_key, spread in current.items():
            avg = self._rolling_avg(pair_key)
            if avg is None:
                continue
            if spread > avg * (1 + threshold):
                pid, kid = pair_key.split("/", 1)
                alerts.append(
                    TrendAlert(
                        alert_type=AlertType.divergence,
                        poly_event_id=pid,
                        kalshi_event_id=kid,
                        spread_before=avg,
                        spread_after=spread,
                        message=(
                            f"Spread diverging: {pair_key} rose from {avg:.2%} avg to {spread:.2%}"
                        ),
                        dispatched_at=now,
                    )
                )
        return alerts

    def _detect_new_highs(self) -> list[TrendAlert]:
        """Detect pairs whose current spread exceeds all previous highs."""
        alerts: list[TrendAlert] = []
        current = self._window[-1]
        prev = list(self._window)[:-1]
        now = datetime.now(tz=UTC)
        for pair_key, spread in current.items():
            prev_vals = [e[pair_key] for e in prev if pair_key in e]
            if not prev_vals:
                continue
            prev_max = max(prev_vals)
            if spread > prev_max:
                pid, kid = pair_key.split("/", 1)
                alerts.append(
                    TrendAlert(
                        alert_type=AlertType.new_high,
                        poly_event_id=pid,
                        kalshi_event_id=kid,
                        spread_before=prev_max,
                        spread_after=spread,
                        message=(
                            f"New high spread: {pair_key} at {spread:.2%} (prev max {prev_max:.2%})"
                        ),
                        dispatched_at=now,
                    )
                )
        return alerts

    def _detect_disappeared(self) -> list[TrendAlert]:
        """Detect frequent pairs missing from the latest scan."""
        alerts: list[TrendAlert] = []
        prev = list(self._window)[:-1]
        current_pairs = set(self._window[-1].keys())
        now = datetime.now(tz=UTC)

        prev_counts: dict[str, int] = {}
        for entry in prev:
            for key in entry:
                prev_counts[key] = prev_counts.get(key, 0) + 1

        for pair_key in {k for k, v in prev_counts.items() if v >= 3} - current_pairs:
            last_spread: Decimal | None = None
            for entry in reversed(prev):
                if pair_key in entry:
                    last_spread = entry[pair_key]
                    break
            pid, kid = pair_key.split("/", 1)
            cnt = prev_counts[pair_key]
            alerts.append(
                TrendAlert(
                    alert_type=AlertType.disappeared,
                    poly_event_id=pid,
                    kalshi_event_id=kid,
                    spread_before=last_spread,
                    spread_after=None,
                    message=f"Opportunity disappeared: {pair_key} (was in {cnt} recent scans)",
                    dispatched_at=now,
                )
            )
        return alerts

    def _detect_health(self, scan_result: dict[str, Any]) -> list[TrendAlert]:
        """Detect scanner health issues (consecutive failures or zero opps)."""
        alerts: list[TrendAlert] = []
        opps: list[ArbOpportunity] = scan_result.get("_raw_opps", [])
        errors: list[str] = scan_result.get("errors", [])
        now = datetime.now(tz=UTC)
        has_errors, has_opps = len(errors) > 0, len(opps) > 0

        if has_errors and not has_opps:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

        if not has_opps:
            self._consecutive_zero_opps += 1
        else:
            self._consecutive_zero_opps = 0

        if self._consecutive_failures >= self._config.max_consecutive_failures:
            alerts.append(
                TrendAlert(
                    alert_type=AlertType.health_consecutive_failures,
                    message=f"Scanner health: {self._consecutive_failures} consecutive scan failures",
                    dispatched_at=now,
                )
            )
        if self._consecutive_zero_opps >= self._config.zero_opp_alert_scans:
            alerts.append(
                TrendAlert(
                    alert_type=AlertType.health_zero_opps,
                    message=(
                        f"Scanner health: {self._consecutive_zero_opps} "
                        f"consecutive scans with zero opportunities"
                    ),
                    dispatched_at=now,
                )
            )
        return alerts

    # -- cooldown ----------------------------------------------------------

    def _apply_cooldown(self, alerts: list[TrendAlert]) -> list[TrendAlert]:
        """Filter out alerts still within the cooldown window."""
        now = datetime.now(tz=UTC)
        cooldown = timedelta(minutes=self._config.cooldown_minutes)
        filtered: list[TrendAlert] = []
        for alert in alerts:
            key = (
                alert.alert_type.value,
                f"{alert.poly_event_id or ''}/{alert.kalshi_event_id or ''}",
            )
            last = self._cooldowns.get(key)
            if last is not None and (now - last) < cooldown:
                logger.debug("alert_cooldown_skip", alert_type=key[0], pair=key[1])
                continue
            self._cooldowns[key] = now
            filtered.append(alert)
        return filtered
