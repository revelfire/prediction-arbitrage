"""Unit tests for auto-execution configuration models."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.models._auto_exec_config import AutoExecutionConfig, CriticConfig
from arb_scanner.models.config import (
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)


class TestCriticConfig:
    """Tests for CriticConfig defaults and overrides."""

    def test_defaults(self) -> None:
        """CriticConfig has sensible defaults."""
        c = CriticConfig()
        assert c.enabled is True
        assert c.model == "claude-haiku-4-5-20251001"
        assert c.timeout_seconds == 5.0
        assert c.skip_below_spread_pct == 0.05
        assert c.anomaly_spread_pct == 0.30
        assert c.max_risk_flags == 3
        assert c.price_staleness_seconds == 60
        assert c.min_book_depth_contracts == 10

    def test_custom_values(self) -> None:
        """CriticConfig accepts custom overrides."""
        c = CriticConfig(
            enabled=False,
            model="claude-sonnet-4-20250514",
            timeout_seconds=10.0,
            max_risk_flags=5,
        )
        assert c.enabled is False
        assert c.model == "claude-sonnet-4-20250514"
        assert c.timeout_seconds == 10.0
        assert c.max_risk_flags == 5


class TestAutoExecutionConfig:
    """Tests for AutoExecutionConfig defaults and overrides."""

    def test_defaults(self) -> None:
        """AutoExecutionConfig has correct default values."""
        ac = AutoExecutionConfig()
        assert ac.enabled is False
        assert ac.mode == "off"
        assert ac.min_spread_pct == 0.03
        assert ac.max_spread_pct == 0.50
        assert ac.min_confidence == 0.70
        assert ac.max_size_usd == 50.0
        assert ac.min_size_usd == 5.0
        assert ac.base_size_usd == 25.0
        assert ac.max_per_market_usd == 100.0
        assert ac.max_slippage_pct == 0.02
        assert ac.daily_loss_limit_usd == 200.0
        assert ac.max_consecutive_failures == 3
        assert ac.max_daily_trades == 50
        assert ac.cooldown_seconds == 30
        assert ac.require_both_venues is True
        assert ac.allowed_categories == []
        assert ac.blocked_categories == []
        assert ac.allowed_ticket_types == ["arbitrage", "flippening"]

    def test_nested_critic(self) -> None:
        """AutoExecutionConfig includes nested CriticConfig."""
        ac = AutoExecutionConfig()
        assert isinstance(ac.critic, CriticConfig)
        assert ac.critic.enabled is True

    def test_custom_values(self) -> None:
        """AutoExecutionConfig accepts custom overrides."""
        ac = AutoExecutionConfig(
            enabled=True,
            mode="auto",
            min_spread_pct=0.05,
            max_size_usd=100.0,
            blocked_categories=["politics"],
        )
        assert ac.enabled is True
        assert ac.mode == "auto"
        assert ac.min_spread_pct == 0.05
        assert ac.blocked_categories == ["politics"]


class TestSettingsIncludesAutoExecution:
    """Tests that Settings includes the auto_execution field."""

    def test_auto_execution_field(self) -> None:
        """Settings has auto_execution attribute with correct type."""
        s = Settings(
            storage=StorageConfig(database_url="postgresql://test:test@localhost/test"),
            fees=FeesConfig(
                polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="percent_winnings"),
                kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
            ),
        )
        assert isinstance(s.auto_execution, AutoExecutionConfig)
        assert s.auto_execution.enabled is False
        assert isinstance(s.auto_execution.critic, CriticConfig)
