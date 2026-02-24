"""Analytics models for historical spread tracking and scan health."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


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
