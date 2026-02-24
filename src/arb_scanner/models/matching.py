"""Match result models for cross-venue contract matching."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class MatchResult(BaseModel):
    """Result of matching two contracts across venues for equivalence.

    Captures match confidence, resolution equivalence assessment,
    identified risks, and whether it is safe to arbitrage.
    """

    poly_event_id: str
    kalshi_event_id: str
    match_confidence: float = Field(ge=0.0, le=1.0)
    resolution_equivalent: bool
    resolution_risks: list[str]
    safe_to_arb: bool
    reasoning: str
    matched_at: datetime
    ttl_expires: datetime

    @model_validator(mode="after")
    def resolution_implies_safe(self) -> "MatchResult":
        """Validate that non-equivalent resolution means not safe to arb."""
        if not self.resolution_equivalent and self.safe_to_arb:
            raise ValueError("safe_to_arb must be False when resolution_equivalent is False")
        return self
