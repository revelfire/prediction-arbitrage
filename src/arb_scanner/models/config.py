"""Configuration models for the arb scanner application."""

from decimal import Decimal

from pydantic import BaseModel


class FeeSchedule(BaseModel):
    """Fee schedule for a single venue."""

    maker_fee_pct: Decimal = Decimal("0.0")
    taker_fee_pct: Decimal
    fee_model: str
    fee_cap: Decimal | None = None


class PolymarketVenueConfig(BaseModel):
    """Configuration for the Polymarket venue."""

    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    enabled: bool = True
    rate_limit_per_sec: int = 10


class KalshiVenueConfig(BaseModel):
    """Configuration for the Kalshi venue."""

    base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    enabled: bool = True
    rate_limit_per_sec: int = 20


class VenuesConfig(BaseModel):
    """Aggregated venue configuration."""

    polymarket: PolymarketVenueConfig = PolymarketVenueConfig()
    kalshi: KalshiVenueConfig = KalshiVenueConfig()


class ClaudeConfig(BaseModel):
    """Configuration for the Claude API integration."""

    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    batch_size: int = 5
    match_cache_ttl_hours: int = 24


class EmbeddingConfig(BaseModel):
    """Configuration for the vector embedding pre-filter."""

    enabled: bool = True
    model: str = "voyage-3-lite"
    api_key: str = ""
    cosine_threshold: float = 0.60
    dimensions: int = 512


class ScanConfig(BaseModel):
    """Configuration for the scanning loop."""

    interval_seconds: int = 60
    mode: str = "continuous"


class ArbThresholds(BaseModel):
    """Thresholds for arbitrage detection."""

    min_net_spread_pct: Decimal = Decimal("0.02")
    min_size_usd: Decimal = Decimal("10")
    thin_liquidity_threshold: Decimal = Decimal("50")


class NotificationConfig(BaseModel):
    """Configuration for webhook notifications."""

    slack_webhook: str = ""
    discord_webhook: str = ""
    enabled: bool = True
    min_spread_to_notify_pct: Decimal = Decimal("0.02")


class StorageConfig(BaseModel):
    """Configuration for database storage."""

    database_url: str


class LoggingConfig(BaseModel):
    """Configuration for structured logging."""

    level: str = "INFO"
    format: str = "json"


class FeesConfig(BaseModel):
    """Fee configuration for all venues."""

    polymarket: FeeSchedule
    kalshi: FeeSchedule


class TrendAlertConfig(BaseModel):
    """Configuration for trend-based alerting on spread movements."""

    enabled: bool = True
    window_size: int = 10
    convergence_threshold_pct: float = 0.25
    divergence_threshold_pct: float = 0.50
    cooldown_minutes: int = 15
    max_consecutive_failures: int = 3
    zero_opp_alert_scans: int = 5


class DashboardConfig(BaseModel):
    """Configuration for the web dashboard."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8000


class Settings(BaseModel):
    """Top-level application settings.

    Aggregates all subsystem configurations into a single model.
    """

    venues: VenuesConfig = VenuesConfig()
    claude: ClaudeConfig = ClaudeConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    scanning: ScanConfig = ScanConfig()
    arb_thresholds: ArbThresholds = ArbThresholds()
    notifications: NotificationConfig = NotificationConfig()
    storage: StorageConfig
    logging: LoggingConfig = LoggingConfig()
    fees: FeesConfig
    trend_alerts: TrendAlertConfig = TrendAlertConfig()
    dashboard: DashboardConfig = DashboardConfig()
