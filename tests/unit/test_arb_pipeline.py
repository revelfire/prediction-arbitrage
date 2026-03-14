"""Unit tests for the arbitrage auto-execution pipeline."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.execution.arb_critic import ArbTradeCritic
from arb_scanner.execution.arb_pipeline import ArbAutoExecutionPipeline
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models._auto_exec_config import AutoExecutionConfig, CriticConfig
from arb_scanner.models.auto_execution import CriticVerdict
from arb_scanner.models.config import ClaudeConfig, Settings
from arb_scanner.models.execution import ExecutionResult


def _settings() -> Settings:
    from arb_scanner.models.config import FeeSchedule, FeesConfig, StorageConfig

    return Settings(
        storage=StorageConfig(database_url="postgresql://test:test@localhost/test"),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="percent_winnings"),
            kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
        ),
    )


def _auto_config(**kw: object) -> AutoExecutionConfig:
    return AutoExecutionConfig(enabled=True, mode="auto", **kw)  # type: ignore[arg-type]


def _opp(**overrides: object) -> dict:
    base = {
        "arb_id": "arb-1",
        "confidence": 0.85,
        "spread_pct": 0.08,
        "category": "nba",
        "ticket_type": "arbitrage",
        "poly_yes_price": 0.55,
        "kalshi_yes_price": 0.47,
        "poly_depth": 500,
        "kalshi_depth": 300,
        "title": "NBA Game Spread",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def _exec_result(status: str = "complete") -> ExecutionResult:
    return ExecutionResult(
        id="exec-1",
        arb_id="arb-1",
        total_cost_usd=Decimal("25.00"),
        actual_spread=Decimal("0.07"),
        slippage_from_ticket=Decimal("0.01"),
        poly_order_id="po-1",
        kalshi_order_id="ko-1",
        status=status,  # type: ignore[arg-type]
    )


def _pipeline(
    auto_config: AutoExecutionConfig | None = None,
    critic_verdict: CriticVerdict | None = None,
    exec_result: ExecutionResult | None = None,
) -> tuple[ArbAutoExecutionPipeline, dict]:
    ac = auto_config or _auto_config()
    breakers = CircuitBreakerManager(ac)
    critic = ArbTradeCritic(CriticConfig(), ClaudeConfig(api_key="test"))
    critic.evaluate = AsyncMock(  # type: ignore[method-assign]
        return_value=critic_verdict or CriticVerdict(approved=True, skipped=True),
    )

    capital = MagicMock()
    capital.daily_pnl = Decimal("0")
    capital.current_exposure = Decimal("0")
    capital.total_balance = Decimal("1000")
    capital.poly_balance = Decimal("500")
    capital.kalshi_balance = Decimal("500")
    capital.refresh_balances = AsyncMock()

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value=exec_result or _exec_result())

    poly = MagicMock()
    poly.is_configured = MagicMock(return_value=False)

    kalshi = MagicMock()
    kalshi.is_configured = MagicMock(return_value=False)

    auto_repo = MagicMock()
    auto_repo.get_open_positions = AsyncMock(return_value=[])
    auto_repo.insert_log = AsyncMock()

    pipeline = ArbAutoExecutionPipeline(
        config=_settings(),
        auto_config=ac,
        orchestrator=orchestrator,
        critic=critic,
        breakers=breakers,
        capital=capital,
        poly=poly,
        kalshi=kalshi,
        auto_repo=auto_repo,
    )

    deps = {
        "breakers": breakers,
        "critic": critic,
        "capital": capital,
        "orchestrator": orchestrator,
        "poly": poly,
        "kalshi": kalshi,
        "auto_repo": auto_repo,
    }
    return pipeline, deps


class TestArbPipeline:
    """Tests for ArbAutoExecutionPipeline."""

    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        """Full pipeline: evaluate → critic → slippage → execute → log."""
        pipeline, deps = _pipeline()
        entry = await pipeline.process_opportunity(_opp())
        assert entry is not None
        assert entry.status == "executed"
        assert deps["orchestrator"].execute.called
        assert deps["auto_repo"].insert_log.called

    @pytest.mark.asyncio
    async def test_rejects_when_evaluator_fails(self) -> None:
        """Rejects when evaluator criteria not met."""
        pipeline, deps = _pipeline(auto_config=_auto_config(min_confidence=0.99))
        entry = await pipeline.process_opportunity(_opp(confidence=0.50))
        assert entry is not None
        assert entry.status == "rejected"
        assert not deps["orchestrator"].execute.called

    @pytest.mark.asyncio
    async def test_rejects_when_critic_kills(self) -> None:
        """Rejects when critic disapproves."""
        verdict = CriticVerdict(approved=False, reasoning="high risk", risk_flags=["test"])
        pipeline, deps = _pipeline(critic_verdict=verdict)
        entry = await pipeline.process_opportunity(_opp())
        assert entry is not None
        assert entry.status == "critic_rejected"
        assert not deps["orchestrator"].execute.called

    @pytest.mark.asyncio
    async def test_rejects_on_slippage_exceeded(self) -> None:
        """Rejects when slippage check fails."""
        pipeline, deps = _pipeline()
        with patch(
            "arb_scanner.execution.arb_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(False, Decimal("0.05"), Decimal("0.03")),
        ):
            entry = await pipeline.process_opportunity(_opp())
            assert entry is not None
            assert entry.status == "rejected"
            assert not deps["orchestrator"].execute.called

    @pytest.mark.asyncio
    async def test_records_failure_on_execution_error(self) -> None:
        """Records failure and trips breaker on execution error."""
        pipeline, deps = _pipeline()
        deps["orchestrator"].execute.side_effect = RuntimeError("exec failed")
        entry = await pipeline.process_opportunity(_opp())
        assert entry is not None
        assert entry.status == "failed"
        assert entry.criteria_snapshot.get("execution_error") == "exec failed"
        assert deps["breakers"]._failure_count >= 1

    @pytest.mark.asyncio
    async def test_records_success_resets_breaker(self) -> None:
        """Successful trade resets failure counter."""
        pipeline, deps = _pipeline()
        deps["breakers"].record_failure()
        assert deps["breakers"]._failure_count == 1
        entry = await pipeline.process_opportunity(_opp())
        assert entry is not None
        assert entry.status == "executed"
        assert deps["breakers"]._failure_count == 0

    @pytest.mark.asyncio
    async def test_mode_off_skips(self) -> None:
        """Pipeline skips when mode is off."""
        pipeline, _ = _pipeline()
        pipeline.set_mode("off")
        result = await pipeline.process_opportunity(_opp())
        assert result is None

    @pytest.mark.asyncio
    async def test_kill_prevents_trades(self) -> None:
        """Kill switch prevents all trades."""
        pipeline, _ = _pipeline()
        pipeline.kill()
        result = await pipeline.process_opportunity(_opp())
        assert result is None

    @pytest.mark.asyncio
    async def test_partial_result_records_failure(self) -> None:
        """Partial execution records as partial status with breaker failure."""
        pipeline, deps = _pipeline(exec_result=_exec_result(status="partial"))
        entry = await pipeline.process_opportunity(_opp())
        assert entry is not None
        assert entry.status == "partial"
        assert deps["breakers"]._failure_count >= 1
