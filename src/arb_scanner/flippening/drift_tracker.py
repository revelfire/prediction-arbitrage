"""Baseline drift detection and tracking for the flippening engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import structlog

from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import Baseline, PriceUpdate

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.drift_tracker",
)

DRIFT_RATE_THRESHOLD = Decimal("0.02")  # 2 pts/min
DRIFT_DURATION_MINUTES = 5

#: Drift info: (market_id, old_yes, new_yes, timestamp)
DriftInfo = tuple[str, Decimal, Decimal, datetime]


def update_drift(
    baseline: Baseline | None,
    drift_accumulator: list[tuple[datetime, Decimal]],
    update: PriceUpdate,
    config: FlippeningConfig | None = None,
) -> tuple[Baseline | None, DriftInfo | None, list[tuple[datetime, Decimal]]]:
    """Track gradual baseline drift and produce a new baseline if appropriate.

    Gradual drift (less than 2 pts/min sustained over 5+ minutes) updates
    the baseline. Sharp moves do not trigger drift updates.

    Args:
        baseline: Current baseline (None means no tracking).
        drift_accumulator: Running list of (timestamp, yes_mid) tuples.
        update: Current price update.
        config: Optional config (reserved for future per-category drift).

    Returns:
        Tuple of (new_baseline_or_None, drift_info_or_None, updated_accumulator).
    """
    if baseline is None:
        return None, None, drift_accumulator

    yes_mid = (update.yes_bid + update.yes_ask) / 2
    drift_accumulator.append((update.timestamp, yes_mid))

    cutoff = update.timestamp - timedelta(minutes=DRIFT_DURATION_MINUTES)
    drift_accumulator = [(t, p) for t, p in drift_accumulator if t >= cutoff]

    if len(drift_accumulator) < 2:
        return None, None, drift_accumulator

    first_t, first_p = drift_accumulator[0]
    last_t, last_p = drift_accumulator[-1]
    elapsed_min = (last_t - first_t).total_seconds() / 60.0
    if elapsed_min < DRIFT_DURATION_MINUTES:
        return None, None, drift_accumulator

    total_drift = abs(last_p - first_p)
    drift_per_min = total_drift / Decimal(str(max(elapsed_min, 0.01)))

    if drift_per_min >= DRIFT_RATE_THRESHOLD:
        return None, None, drift_accumulator

    old_yes = baseline.yes_price
    no_mid = (update.no_bid + update.no_ask) / 2
    new_baseline = Baseline(
        market_id=baseline.market_id,
        token_id=baseline.token_id,
        yes_price=yes_mid,
        no_price=no_mid,
        sport=baseline.sport,
        category=baseline.category,
        category_type=baseline.category_type,
        baseline_strategy=baseline.baseline_strategy,
        game_start_time=baseline.game_start_time,
        captured_at=update.timestamp,
        late_join=baseline.late_join,
    )
    logger.debug(
        "baseline_drift_updated",
        market_id=baseline.market_id,
        new_yes=float(yes_mid),
    )
    drift_info: DriftInfo = (baseline.market_id, old_yes, yes_mid, update.timestamp)
    return new_baseline, drift_info, []
