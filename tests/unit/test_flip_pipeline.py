"""Unit tests for the flippening auto-execution pipeline."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.execution.flip_critic import FlipTradeCritic
from arb_scanner.execution.flip_pipeline import FlipAutoExecutionPipeline
from arb_scanner.execution.flip_position_repo import FlipPositionRepo
from arb_scanner.models._auto_exec_config import AutoExecutionConfig, CriticConfig
from arb_scanner.models.auto_execution import CriticVerdict
from arb_scanner.models.config import ClaudeConfig, Settings
from arb_scanner.models.execution import OrderResponse


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
        "arb_id": "flip-1",
        "confidence": 0.85,
        "spread_pct": 0.15,
        "category": "nba",
        "ticket_type": "flippening",
        "entry_price": 0.45,
        "side": "yes",
        "token_id": "tok-123",
        "market_id": "mkt-1",
        "title": "NBA Game Spread",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def _pipeline(
    auto_config: AutoExecutionConfig | None = None,
    critic_verdict: CriticVerdict | None = None,
    order_response: OrderResponse | None = None,
) -> tuple[FlipAutoExecutionPipeline, dict]:
    ac = auto_config or _auto_config()
    breakers = CircuitBreakerManager(ac)
    critic = FlipTradeCritic(CriticConfig(), ClaudeConfig(api_key="test"))
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

    poly = MagicMock()
    poly.place_order = AsyncMock(
        return_value=order_response or OrderResponse(status="filled", venue_order_id="v-1"),
    )

    position_repo = MagicMock(spec=FlipPositionRepo)
    position_repo.insert_position = AsyncMock(return_value="pos-1")
    position_repo.get_open_positions = AsyncMock(return_value=[])

    auto_repo = MagicMock()
    auto_repo.get_open_positions = AsyncMock(return_value=[])
    auto_repo.insert_log = AsyncMock()

    exec_repo = MagicMock()

    exit_executor = MagicMock()
    exit_executor.execute_exit = AsyncMock(return_value="exit-1")

    pipeline = FlipAutoExecutionPipeline(
        config=_settings(),
        auto_config=ac,
        critic=critic,
        breakers=breakers,
        capital=capital,
        poly=poly,
        position_repo=position_repo,
        auto_repo=auto_repo,
        exec_repo=exec_repo,
        exit_executor=exit_executor,
    )

    deps = {
        "breakers": breakers,
        "critic": critic,
        "capital": capital,
        "poly": poly,
        "position_repo": position_repo,
        "auto_repo": auto_repo,
        "exit_executor": exit_executor,
    }
    return pipeline, deps


class TestFlipPipeline:
    """Tests for FlipAutoExecutionPipeline."""

    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        """Full pipeline: evaluate → critic → place_order → register → log."""
        pipeline, deps = _pipeline()
        entry = await pipeline.process_opportunity(_opp())
        assert entry is not None
        assert entry.status == "executed"
        assert deps["poly"].place_order.called
        assert deps["position_repo"].insert_position.called
        assert deps["auto_repo"].insert_log.called

    @pytest.mark.asyncio
    async def test_rejects_when_evaluator_fails(self) -> None:
        """Rejects when evaluator criteria not met."""
        pipeline, deps = _pipeline(auto_config=_auto_config(min_confidence=0.99))
        entry = await pipeline.process_opportunity(_opp(confidence=0.50))
        assert entry is not None
        assert entry.status == "rejected"
        assert not deps["poly"].place_order.called

    @pytest.mark.asyncio
    async def test_rejects_when_critic_kills(self) -> None:
        """Rejects when critic disapproves."""
        verdict = CriticVerdict(approved=False, reasoning="high risk", risk_flags=["test"])
        pipeline, deps = _pipeline(critic_verdict=verdict)
        entry = await pipeline.process_opportunity(_opp())
        assert entry is not None
        assert entry.status == "critic_rejected"
        assert not deps["poly"].place_order.called

    @pytest.mark.asyncio
    async def test_records_failure_on_execution_error(self) -> None:
        """Records failure and trips breaker on execution error."""
        pipeline, deps = _pipeline()
        deps["poly"].place_order.side_effect = RuntimeError("order failed")
        entry = await pipeline.process_opportunity(_opp())
        assert entry is not None
        assert entry.status == "failed"
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
    async def test_process_exit_delegates(self) -> None:
        """process_exit delegates to exit_executor."""
        from datetime import datetime, timezone

        from arb_scanner.models.flippening import (
            EntrySignal,
            ExitReason,
            ExitSignal,
            FlippeningEvent,
            SpikeDirection,
        )

        pipeline, deps = _pipeline()
        now = datetime.now(timezone.utc)
        exit_sig = ExitSignal(
            event_id="evt-1",
            side="yes",
            exit_price=Decimal("0.50"),
            exit_reason=ExitReason.REVERSION,
            realized_pnl=Decimal("0.05"),
            realized_pnl_pct=Decimal("10"),
            hold_minutes=Decimal("5"),
            created_at=now,
        )
        entry_sig = EntrySignal(
            event_id="evt-1",
            side="yes",
            entry_price=Decimal("0.45"),
            target_exit_price=Decimal("0.50"),
            stop_loss_price=Decimal("0.40"),
            suggested_size_usd=Decimal("25"),
            expected_profit_pct=Decimal("10"),
            max_hold_minutes=45,
            created_at=now,
        )
        event = FlippeningEvent(
            market_id="mkt-1",
            market_title="NBA Game",
            baseline_yes=Decimal("0.40"),
            spike_price=Decimal("0.55"),
            spike_magnitude_pct=Decimal("0.15"),
            spike_direction=SpikeDirection.FAVORITE_DROP,
            confidence=Decimal("0.85"),
            sport="nba",
            detected_at=now,
        )
        await pipeline.process_exit(exit_sig, entry_sig, event)
        assert deps["exit_executor"].execute_exit.called

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
