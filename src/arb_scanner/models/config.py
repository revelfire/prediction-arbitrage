"""Configuration models for the arb scanner application."""

from __future__ import annotations

from decimal import Decimal

import structlog
from pydantic import BaseModel, model_validator

from arb_scanner.models._auto_exec_config import AutoExecutionConfig

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="models.config")


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
    min_volume_24h: Decimal = Decimal("0")
    max_markets: int = 0


class KalshiVenueConfig(BaseModel):
    """Configuration for the Kalshi venue."""

    base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    enabled: bool = True
    rate_limit_per_sec: int = 5
    min_volume_24h: Decimal = Decimal("0")
    max_markets: int = 0
    exclude_ticker_prefixes: list[str] = ["KXMVESPORTSMULTIGAME"]


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
    max_semantic_pairs: int = 50


class EmbeddingConfig(BaseModel):
    """Configuration for the vector embedding pre-filter."""

    enabled: bool = True
    provider: str = "local"
    model: str = "BAAI/bge-small-en-v1.5"
    api_key: str = ""
    cosine_threshold: float = 0.60
    dimensions: int = 384


class ScanConfig(BaseModel):
    """Configuration for the scanning loop."""

    interval_seconds: int = 60
    mode: str = "continuous"


class ArbThresholds(BaseModel):
    """Thresholds for arbitrage detection."""

    min_net_spread_pct: Decimal = Decimal("0.02")
    min_size_usd: Decimal = Decimal("10")
    thin_liquidity_threshold: Decimal = Decimal("50")
    min_expected_profit_usd: Decimal = Decimal("1.00")
    max_ticket_size_usd: Decimal = Decimal("500")
    min_ask_price: Decimal = Decimal("0.02")
    max_net_spread_pct: Decimal = Decimal("1.00")
    min_cost_per_contract: Decimal = Decimal("0.10")


class NotificationConfig(BaseModel):
    """Configuration for webhook notifications."""

    slack_webhook: str = ""
    discord_webhook: str = ""
    flippening_slack_webhook: str = ""
    auto_exec_slack_webhook: str = ""
    enabled: bool = True
    min_spread_to_notify_pct: Decimal = Decimal("0.02")

    @property
    def effective_flippening_slack(self) -> str:
        """Flippening-specific Slack URL, falling back to slack_webhook."""
        return self.flippening_slack_webhook or self.slack_webhook

    @property
    def effective_auto_exec_slack(self) -> str:
        """Auto-exec-specific Slack URL, falling back to slack_webhook."""
        return self.auto_exec_slack_webhook or self.slack_webhook


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
    port: int = 8061
    auth_token: str | None = None


class ConfidenceWeights(BaseModel):
    """Weights for the flippening confidence scoring formula."""

    magnitude: float = 0.45
    strength: float = 0.30
    speed: float = 0.25

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "ConfidenceWeights":
        """Validate that weights sum to 1.0 within tolerance."""
        total = self.magnitude + self.strength + self.speed
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Confidence weights must sum to 1.0, got {total}")
        return self


class SportOverride(BaseModel):
    """Per-sport threshold overrides for flippening detection."""

    spike_threshold_pct: float | None = None
    confidence_modifier: float = 1.0
    min_confidence: float | None = None


class ManualOverride(BaseModel):
    """Manual market override for market discovery."""

    market_id: str
    sport: str


VALID_CATEGORY_TYPES = frozenset(
    {"sport", "entertainment", "politics", "crypto", "economics", "corporate"},
)
VALID_BASELINE_STRATEGIES = frozenset({"first_price", "rolling_window", "pre_event_snapshot"})


class CategoryConfig(BaseModel):
    """Configuration for a single market category."""

    category_type: str = "sport"
    enabled: bool = True
    baseline_strategy: str = "first_price"
    baseline_window_minutes: int = 30
    spike_threshold_pct: float | None = None
    confidence_modifier: float = 1.0
    min_confidence: float | None = None
    reversion_target_pct: float | None = None
    stop_loss_pct: float | None = None
    max_hold_minutes: int | None = None
    min_hold_seconds: int | None = None
    late_join_penalty: float | None = None
    event_window_hours: float = 4.0
    discovery_keywords: list[str] = []
    discovery_tags: list[str] = []
    discovery_slugs: list[str] = []

    @model_validator(mode="after")
    def validate_enums(self) -> CategoryConfig:
        """Validate category_type and baseline_strategy values."""
        if self.category_type not in VALID_CATEGORY_TYPES:
            raise ValueError(
                f"category_type must be one of {sorted(VALID_CATEGORY_TYPES)}, "
                f"got '{self.category_type}'"
            )
        if self.baseline_strategy not in VALID_BASELINE_STRATEGIES:
            raise ValueError(
                f"baseline_strategy must be one of {sorted(VALID_BASELINE_STRATEGIES)}, "
                f"got '{self.baseline_strategy}'"
            )
        return self


class FlippeningConfig(BaseModel):
    """Configuration for the flippening mean reversion engine."""

    enabled: bool = False
    sports: list[str] = [
        "nba",
        "nhl",
        "nfl",
        "mlb",
        "epl",
        "ufc",
    ]
    spike_threshold_pct: float = 0.15
    spike_window_minutes: int = 10
    min_confidence: float = 0.60
    reversion_target_pct: float = 0.70
    stop_loss_pct: float = 0.15
    min_entry_price: float = 0.05
    base_position_usd: float = 100.0
    max_position_usd: float = 500.0
    max_hold_minutes: int = 45
    min_hold_seconds: int = 30
    pre_game_window_minutes: int = 30
    ws_reconnect_max_seconds: int = 60
    late_join_penalty: float = 0.80
    polling_interval_seconds: float = 5.0
    confidence_weights: ConfidenceWeights = ConfidenceWeights()
    categories: dict[str, CategoryConfig] = {}
    sport_overrides: dict[str, SportOverride] = {}
    manual_market_ids: list[ManualOverride] = []
    excluded_market_ids: list[str] = []
    sport_keywords: dict[str, list[str]] = {}
    min_expected_profit_usd: float = 1.0
    min_hit_rate_pct: float = 0.01
    discovery_alert_cooldown_minutes: int = 60
    ws_telemetry_interval_seconds: int = 60
    ws_schema_match_pct: float = 0.50
    orderbook_cache_ttl_seconds: float = 10.0
    orderbook_cache_max_size: int = 200
    synthetic_spread_penalty: float = 0.85
    ws_telemetry_persist_interval_seconds: int = 300
    capture_ticks: bool = True
    tick_retention_days: int = 90
    tick_buffer_size: int = 100
    tick_flush_interval_seconds: float = 5.0
    min_baseline_price: float = 0.05
    max_baseline_price: float = 0.95
    max_deviation_recapture_pct: float = 500.0
    alert_batch_interval_seconds: float = 600.0
    alert_max_per_batch: int = 10

    @model_validator(mode="after")
    def migrate_sports_to_categories(self) -> FlippeningConfig:
        """Auto-convert legacy sports list to categories when categories is empty."""
        if self.categories:
            if self.sports != FlippeningConfig.model_fields["sports"].default:
                logger.warning("categories_and_sports_both_set")
            return self
        if not self.sports:
            return self
        for sport in self.sports:
            override = self.sport_overrides.get(sport, SportOverride())
            kw = self.sport_keywords.get(sport, [])
            self.categories[sport] = CategoryConfig(
                category_type="sport",
                baseline_strategy="first_price",
                spike_threshold_pct=override.spike_threshold_pct,
                confidence_modifier=override.confidence_modifier,
                min_confidence=override.min_confidence,
                discovery_keywords=kw,
                discovery_slugs=[f"{sport}-"],
            )
        return self


class PolyExecConfig(BaseModel):
    """Polymarket execution venue configuration."""

    chain_id: int = 137
    clob_api_url: str = "https://clob.polymarket.com"
    signature_type: int = 0
    funder: str = ""
    usdc_contract: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


class KalshiExecConfig(BaseModel):
    """Kalshi execution venue configuration."""

    api_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"


class ExecutionConfig(BaseModel):
    """Configuration for one-click trade execution."""

    enabled: bool = False
    max_size_usd: float = 100.0
    max_slippage_pct: float = 0.02
    price_staleness_seconds: int = 30
    pct_of_balance: float = 0.02
    max_pct_per_venue: float = 0.05
    max_exposure_pct: float = 0.25
    min_reserve_usd: float = 50.0
    daily_loss_limit_usd: float = 100.0
    max_open_positions: int = 5
    max_per_market_pct: float = 0.10
    cooldown_after_loss_seconds: int = 300
    min_book_depth_contracts: int = 5
    polymarket: PolyExecConfig = PolyExecConfig()
    kalshi: KalshiExecConfig = KalshiExecConfig()


class TicketLifecycleConfig(BaseModel):
    """Cross-cutting ticket lifecycle configuration."""

    expire_interval_minutes: int = 60
    max_pending_hours: int = 24
    retention_days: int = 90


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
    flippening: FlippeningConfig = FlippeningConfig()
    execution: ExecutionConfig = ExecutionConfig()
    auto_execution: AutoExecutionConfig = AutoExecutionConfig()
    ticket_lifecycle: TicketLifecycleConfig = TicketLifecycleConfig()
