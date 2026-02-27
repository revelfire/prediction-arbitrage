"""Configuration models for automated execution pipeline."""

from __future__ import annotations

from pydantic import BaseModel


class CriticConfig(BaseModel):
    """Configuration for the AI trade critic gate."""

    enabled: bool = True
    model: str = "claude-haiku-4-5-20251001"
    api_key: str = ""
    timeout_seconds: float = 5.0
    skip_below_spread_pct: float = 0.05
    anomaly_spread_pct: float = 0.30
    max_risk_flags: int = 3
    price_staleness_seconds: int = 60
    min_book_depth_contracts: int = 10


class AutoExecutionConfig(BaseModel):
    """Configuration for the autonomous execution pipeline."""

    enabled: bool = False
    mode: str = "off"
    min_spread_pct: float = 0.03
    max_spread_pct: float = 0.50
    min_confidence: float = 0.70
    min_liquidity_usd: float = 100.0
    max_size_usd: float = 50.0
    min_size_usd: float = 5.0
    base_size_usd: float = 25.0
    max_per_market_usd: float = 100.0
    max_slippage_pct: float = 0.02
    daily_loss_limit_usd: float = 200.0
    max_consecutive_failures: int = 3
    max_daily_trades: int = 50
    cooldown_seconds: int = 30
    require_both_venues: bool = True
    allowed_categories: list[str] = []
    blocked_categories: list[str] = []
    allowed_ticket_types: list[str] = ["arbitrage", "flippening"]
    critic: CriticConfig = CriticConfig()
