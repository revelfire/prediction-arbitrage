"""Scan log model for tracking scan cycle metadata."""

from datetime import datetime

from pydantic import BaseModel


class ScanLog(BaseModel):
    """Record of a single scan cycle with metrics and errors."""

    id: str
    started_at: datetime
    completed_at: datetime | None = None
    poly_markets_fetched: int = 0
    kalshi_markets_fetched: int = 0
    candidate_pairs: int = 0
    llm_evaluations: int = 0
    opportunities_found: int = 0
    errors: list[str] = []
