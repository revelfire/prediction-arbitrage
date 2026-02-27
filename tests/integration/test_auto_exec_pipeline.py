"""Integration tests for the auto-execution pipeline end-to-end flow.

Tests the full pipeline from opportunity ingestion through criteria evaluation,
sizing, critic, slippage check, and execution -- all with mocked external
dependencies (no database, no Claude API, no venue connections).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.execution.auto_pipeline import AutoExecutionPipeline
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.execution.trade_critic import TradeCritic
from arb_scanner.models._auto_exec_config import AutoExecutionConfig, CriticConfig
from arb_scanner.models.config import (
    ClaudeConfig,
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)


def _test_settings() -> Settings:
    """Build minimal Settings for integration tests."""
    return Settings(
        storage=StorageConfig(database_url="postgresql://test@localhost/test"),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="percent_winnings"),
            kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
        ),
    )


def _build_pipeline(
    *,
    mode: str = "auto",
    critic_enabled: bool = False,
    auto_overrides: dict | None = None,
) -> tuple[AutoExecutionPipeline, dict]:
    """Build a pipeline with all mocked dependencies.

    Returns:
        Tuple of (pipeline, mocks_dict) for assertions.
    """
    ac_kwargs: dict = {
        "enabled": True,
        "mode": mode,
        "min_spread_pct": 0.03,
        "max_spread_pct": 0.50,
        "min_confidence": 0.60,
        "max_size_usd": 50.0,
        "min_size_usd": 5.0,
        "base_size_usd": 25.0,
        "daily_loss_limit_usd": 200.0,
        "max_daily_trades": 50,
        "max_slippage_pct": 0.02,
    }
    if auto_overrides:
        ac_kwargs.update(auto_overrides)
    ac_kwargs.setdefault("critic", CriticConfig(enabled=critic_enabled))
    auto_config = AutoExecutionConfig(**ac_kwargs)

    settings = _test_settings()
    settings.auto_execution = auto_config

    critic = TradeCritic(auto_config.critic, ClaudeConfig(api_key="fake"))
    breakers = CircuitBreakerManager(auto_config)

    auto_repo = AsyncMock()
    auto_repo.get_open_positions = AsyncMock(return_value=[])
    auto_repo.insert_log = AsyncMock()

    capital = MagicMock()
    capital.daily_pnl = Decimal("0")
    capital.current_exposure = Decimal("0")
    capital.total_balance = Decimal("500")
    capital.poly_balance = Decimal("250")
    capital.kalshi_balance = Decimal("250")

    exec_result = MagicMock(
        id="r-int-1",
        status="complete",
        actual_spread=Decimal("0.04"),
        slippage_from_ticket=Decimal("0.005"),
    )
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value=exec_result)

    poly = MagicMock()
    poly.is_configured = MagicMock(return_value=True)
    poly.get_book_depth = AsyncMock(
        return_value={"asks": [{"price": "0.55", "size": "500"}]},
    )
    kalshi = MagicMock()
    kalshi.is_configured = MagicMock(return_value=True)
    kalshi.get_book_depth = AsyncMock(
        return_value={"asks": [{"price": "0.42", "size": "500"}]},
    )

    pipeline = AutoExecutionPipeline(
        config=settings,
        auto_config=auto_config,
        orchestrator=orchestrator,
        critic=critic,
        breakers=breakers,
        auto_repo=auto_repo,
        capital=capital,
        poly=poly,
        kalshi=kalshi,
    )

    mocks = {
        "orchestrator": orchestrator,
        "auto_repo": auto_repo,
        "capital": capital,
        "breakers": breakers,
    }
    return pipeline, mocks


def _valid_opportunity(**overrides: object) -> dict:
    """Build an opportunity that passes all criteria."""
    opp: dict = {
        "arb_id": "int-arb-001",
        "spread_pct": 0.06,
        "confidence": 0.85,
        "category": "nba",
        "ticket_type": "arbitrage",
        "title": "Lakers vs Celtics total points",
        "poly_yes_price": 0.55,
        "kalshi_yes_price": 0.45,
        "poly_depth": 200,
        "kalshi_depth": 150,
        "price_age_seconds": 5,
    }
    opp.update(overrides)
    return opp


class TestPipelineEndToEnd:
    """End-to-end integration tests for the auto-execution pipeline."""

    @pytest.mark.asyncio()
    async def test_full_happy_path(self) -> None:
        """Opportunity flows through all gates and executes successfully."""
        pipeline, mocks = _build_pipeline()

        with patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(True, Decimal("0.001"), Decimal("0.001")),
        ):
            entry = await pipeline.process_opportunity(
                _valid_opportunity(),
                source="integration_test",
            )

        assert entry is not None
        assert entry.status == "executed"
        assert entry.execution_result_id == "r-int-1"
        assert entry.size_usd > Decimal("0")
        assert entry.source == "integration_test"

        mocks["orchestrator"].execute.assert_awaited_once()
        mocks["auto_repo"].insert_log.assert_awaited()

    @pytest.mark.asyncio()
    async def test_criteria_rejection_does_not_execute(self) -> None:
        """Opportunity rejected by criteria never reaches execution."""
        pipeline, mocks = _build_pipeline(
            auto_overrides={"min_spread_pct": 0.10},
        )

        with patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(True, Decimal("0"), Decimal("0")),
        ):
            entry = await pipeline.process_opportunity(
                _valid_opportunity(spread_pct=0.02),
            )

        assert entry is not None
        assert entry.status == "rejected"
        mocks["orchestrator"].execute.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_circuit_breaker_blocks_execution(self) -> None:
        """Tripped circuit breaker prevents execution."""
        pipeline, mocks = _build_pipeline(
            auto_overrides={"daily_loss_limit_usd": 10.0},
        )
        mocks["breakers"].check_loss(Decimal("-50"))
        assert mocks["breakers"].is_any_tripped() is True

        with patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(True, Decimal("0"), Decimal("0")),
        ):
            entry = await pipeline.process_opportunity(_valid_opportunity())

        assert entry is not None
        assert entry.status == "breaker_blocked"
        mocks["orchestrator"].execute.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_execution_failure_trips_breaker(self) -> None:
        """Failed execution increments failure counter on circuit breaker."""
        pipeline, mocks = _build_pipeline(
            auto_overrides={"max_consecutive_failures": 2},
        )
        mocks["orchestrator"].execute = AsyncMock(
            side_effect=RuntimeError("exchange timeout"),
        )

        with patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(True, Decimal("0.001"), Decimal("0.001")),
        ):
            entry1 = await pipeline.process_opportunity(
                _valid_opportunity(arb_id="fail-1"),
            )
            entry2 = await pipeline.process_opportunity(
                _valid_opportunity(arb_id="fail-2"),
            )

        assert entry1 is not None
        assert entry1.status == "failed"
        assert entry2 is not None
        assert entry2.status == "failed"
        assert mocks["breakers"].is_any_tripped() is True

    @pytest.mark.asyncio()
    async def test_kill_prevents_all_execution(self) -> None:
        """Kill switch stops all future processing."""
        pipeline, mocks = _build_pipeline()
        pipeline.kill()

        with patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
        ):
            result = await pipeline.process_opportunity(_valid_opportunity())

        assert result is None
        mocks["orchestrator"].execute.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_slippage_rejection_prevents_execution(self) -> None:
        """Excessive slippage prevents execution."""
        pipeline, mocks = _build_pipeline()

        with patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(False, Decimal("0.05"), Decimal("0.03")),
        ):
            entry = await pipeline.process_opportunity(_valid_opportunity())

        assert entry is not None
        assert entry.status == "rejected"
        mocks["orchestrator"].execute.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_mode_change_flow(self) -> None:
        """Mode transitions affect processing."""
        pipeline, mocks = _build_pipeline(mode="off")

        with patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(True, Decimal("0"), Decimal("0")),
        ):
            result1 = await pipeline.process_opportunity(_valid_opportunity())
            assert result1 is None

            pipeline.set_mode("auto")
            result2 = await pipeline.process_opportunity(
                _valid_opportunity(arb_id="after-enable"),
            )
            assert result2 is not None
            assert result2.status == "executed"
