"""Integration tests for pipeline isolation — independent breakers, shared capital."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.execution.arb_critic import ArbTradeCritic
from arb_scanner.execution.arb_pipeline import ArbAutoExecutionPipeline
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.execution.flip_critic import FlipTradeCritic
from arb_scanner.execution.flip_pipeline import FlipAutoExecutionPipeline
from arb_scanner.execution.flip_position_repo import FlipPositionRepo
from arb_scanner.models._auto_exec_config import AutoExecutionConfig, CriticConfig
from arb_scanner.models.auto_execution import CriticVerdict
from arb_scanner.models.config import ClaudeConfig, FeeSchedule, FeesConfig, Settings, StorageConfig
from arb_scanner.models.execution import ExecutionResult, OrderResponse


def _settings() -> Settings:
    return Settings(
        storage=StorageConfig(database_url="postgresql://test:test@localhost/test"),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="percent_winnings"),
            kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
        ),
    )


def _auto_config() -> AutoExecutionConfig:
    return AutoExecutionConfig(enabled=True, mode="auto", max_consecutive_failures=3)


def _shared_capital() -> MagicMock:
    capital = MagicMock()
    capital.daily_pnl = Decimal("0")
    capital.current_exposure = Decimal("0")
    capital.total_balance = Decimal("1000")
    capital.poly_balance = Decimal("500")
    capital.kalshi_balance = Decimal("500")
    capital.refresh_balances = AsyncMock()
    return capital


def _make_pipelines() -> tuple[
    ArbAutoExecutionPipeline,
    FlipAutoExecutionPipeline,
    CircuitBreakerManager,
    CircuitBreakerManager,
    MagicMock,
]:
    """Create both pipelines with real breakers and shared capital."""
    ac = _auto_config()
    arb_breakers = CircuitBreakerManager(ac)
    flip_breakers = CircuitBreakerManager(ac)
    capital = _shared_capital()

    arb_critic = ArbTradeCritic(CriticConfig(), ClaudeConfig(api_key="test"))
    arb_critic.evaluate = AsyncMock(  # type: ignore[method-assign]
        return_value=CriticVerdict(approved=True, skipped=True),
    )

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(
        return_value=ExecutionResult(
            id="e-1",
            arb_id="arb-1",
            status="complete",
            total_cost_usd=Decimal("25"),
            poly_order_id="po-1",
            kalshi_order_id="ko-1",
        )
    )

    auto_repo = MagicMock()
    auto_repo.get_open_positions = AsyncMock(return_value=[])
    auto_repo.insert_log = AsyncMock()

    poly = MagicMock()
    poly.is_configured = MagicMock(return_value=False)
    poly.place_order = AsyncMock(return_value=OrderResponse(status="filled", venue_order_id="v-1"))

    kalshi = MagicMock()
    kalshi.is_configured = MagicMock(return_value=False)

    arb_pipeline = ArbAutoExecutionPipeline(
        config=_settings(),
        auto_config=ac,
        orchestrator=orchestrator,
        critic=arb_critic,
        breakers=arb_breakers,
        capital=capital,
        poly=poly,
        kalshi=kalshi,
        auto_repo=auto_repo,
    )

    flip_critic = FlipTradeCritic(CriticConfig(), ClaudeConfig(api_key="test"))
    flip_critic.evaluate = AsyncMock(  # type: ignore[method-assign]
        return_value=CriticVerdict(approved=True, skipped=True),
    )
    position_repo = MagicMock(spec=FlipPositionRepo)
    position_repo.insert_position = AsyncMock(return_value="pos-1")
    position_repo.get_open_positions = AsyncMock(return_value=[])

    flip_pipeline = FlipAutoExecutionPipeline(
        config=_settings(),
        auto_config=ac,
        critic=flip_critic,
        breakers=flip_breakers,
        capital=capital,
        poly=poly,
        position_repo=position_repo,
        auto_repo=auto_repo,
        exec_repo=MagicMock(),
    )

    return arb_pipeline, flip_pipeline, arb_breakers, flip_breakers, capital


class TestPipelineIsolation:
    """Verify independent breaker behavior and shared capital."""

    @pytest.mark.asyncio
    async def test_flip_failures_dont_trip_arb_breaker(self) -> None:
        """3 consecutive flip failures do NOT trip arb breaker."""
        arb_p, flip_p, arb_b, flip_b, _ = _make_pipelines()

        for _ in range(3):
            flip_b.record_failure()

        assert flip_b._failure_tripped is True
        assert arb_b._failure_tripped is False

    @pytest.mark.asyncio
    async def test_arb_failures_dont_trip_flip_breaker(self) -> None:
        """3 consecutive arb failures do NOT trip flip breaker."""
        arb_p, flip_p, arb_b, flip_b, _ = _make_pipelines()

        for _ in range(3):
            arb_b.record_failure()

        assert arb_b._failure_tripped is True
        assert flip_b._failure_tripped is False

    @pytest.mark.asyncio
    async def test_shared_capital_budget(self) -> None:
        """Capital manager daily budget is shared across both pipelines."""
        arb_p, flip_p, arb_b, flip_b, capital = _make_pipelines()

        # Arb pipeline sees the same capital via infra
        assert arb_p._infra.capital is capital
        assert flip_p._infra.capital is capital

        # Simulate a trade consuming budget
        capital.daily_pnl = Decimal("-100")
        assert arb_p._infra.capital.daily_pnl == Decimal("-100")
        assert flip_p._infra.capital.daily_pnl == Decimal("-100")

    @pytest.mark.asyncio
    async def test_mode_control_independent(self) -> None:
        """Mode control works independently on each pipeline."""
        arb_p, flip_p, _, _, _ = _make_pipelines()

        arb_p.set_mode("off")
        assert arb_p.mode == "off"
        assert flip_p.mode == "auto"

        flip_p.set_mode("manual")
        assert arb_p.mode == "off"
        assert flip_p.mode == "manual"

    @pytest.mark.asyncio
    async def test_kill_stops_pipeline(self) -> None:
        """Kill switch stops individual pipeline."""
        arb_p, flip_p, _, _, _ = _make_pipelines()

        arb_p.kill()
        assert arb_p._killed is True
        assert flip_p._killed is False

        opp = {
            "arb_id": "test-1",
            "confidence": 0.85,
            "spread_pct": 0.08,
            "category": "nba",
            "title": "Test",
        }
        result = await arb_p.process_opportunity(opp)
        assert result is None

        flip_opp = {
            "arb_id": "test-2",
            "confidence": 0.85,
            "spread_pct": 0.15,
            "category": "nba",
            "title": "Test",
            "entry_price": 0.45,
            "side": "yes",
            "token_id": "tok-1",
            "market_id": "mkt-1",
        }
        result = await flip_p.process_opportunity(flip_opp)
        assert result is not None

    @pytest.mark.asyncio
    async def test_flip_failure_probe_recovers_from_breaker_blocked(self) -> None:
        """Flip pipeline recovers via timed failure probe after breaker trip."""
        arb_p, flip_p, arb_b, flip_b, _ = _make_pipelines()
        del arb_p, arb_b
        flip_p._poly.place_order = AsyncMock(  # type: ignore[attr-defined]
            return_value=OrderResponse(status="failed", error_message="network timeout"),
        )

        for i in range(3):
            entry = await flip_p.process_opportunity(
                {
                    "arb_id": f"flip-fail-{i}",
                    "confidence": 0.90,
                    "spread_pct": 0.20,
                    "category": "nba",
                    "title": "Probe Recovery Test",
                    "entry_price": 0.45,
                    "side": "yes",
                    "token_id": "tok-1",
                    "market_id": "mkt-1",
                }
            )
            assert entry is not None
            assert entry.status == "failed"

        assert flip_b._failure_tripped is True

        blocked = await flip_p.process_opportunity(
            {
                "arb_id": "flip-blocked",
                "confidence": 0.90,
                "spread_pct": 0.20,
                "category": "nba",
                "title": "Probe Recovery Test",
                "entry_price": 0.45,
                "side": "yes",
                "token_id": "tok-1",
                "market_id": "mkt-1",
            }
        )
        assert blocked is not None
        assert blocked.status == "breaker_blocked"

        flip_b._failure_probe_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        flip_p._poly.place_order = AsyncMock(  # type: ignore[attr-defined]
            return_value=OrderResponse(status="filled", venue_order_id="v-recover"),
        )
        recovered = await flip_p.process_opportunity(
            {
                "arb_id": "flip-recover",
                "confidence": 0.90,
                "spread_pct": 0.20,
                "category": "nba",
                "title": "Probe Recovery Test",
                "entry_price": 0.45,
                "side": "yes",
                "token_id": "tok-1",
                "market_id": "mkt-1",
            }
        )
        assert recovered is not None
        assert recovered.status == "executed"
        assert flip_b._failure_tripped is False
        metrics = flip_b.get_failure_probe_metrics()
        assert metrics["attempts"] == 1
        assert metrics["successes"] == 1
