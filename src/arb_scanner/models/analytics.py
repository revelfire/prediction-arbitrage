"""Analytics models for historical spread tracking and scan health."""

import enum
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class AlertType(str, enum.Enum):
    """Classification of trend-based alerts."""

    convergence = "convergence"
    divergence = "divergence"
    new_high = "new_high"
    disappeared = "disappeared"
    health_consecutive_failures = "health_consecutive_failures"
    health_zero_opps = "health_zero_opps"


class TrendAlert(BaseModel):
    """A single trend-based alert dispatched by the alerting engine."""

    alert_type: AlertType
    poly_event_id: str | None = None
    kalshi_event_id: str | None = None
    spread_before: Decimal | None = None
    spread_after: Decimal | None = None
    message: str
    dispatched_at: datetime


class SpreadSnapshot(BaseModel):
    """A single point-in-time spread observation for an arb pair."""

    detected_at: datetime
    net_spread_pct: Decimal
    annualized_return: Decimal | None = None
    depth_risk: bool
    max_size: Decimal


class PairSummary(BaseModel):
    """Aggregated statistics for a matched pair over a time window."""

    poly_event_id: str
    kalshi_event_id: str
    peak_spread: Decimal
    min_spread: Decimal
    avg_spread: Decimal
    total_detections: int
    first_seen: datetime
    last_seen: datetime


class HourlyBucket(BaseModel):
    """Hourly aggregation of spread observations."""

    hour: datetime
    avg_spread: Decimal
    max_spread: Decimal
    detection_count: int


class ScanHealthSummary(BaseModel):
    """Hourly health metrics for the scanning pipeline."""

    hour: datetime
    scan_count: int
    avg_duration_s: float
    total_llm_calls: int
    total_opps: int
    total_errors: int
