"""Unit tests for the auto-execution pipeline orchestrator."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.execution.auto_pipeline import AutoExecutionPipeline
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.execution.trade_critic import TradeCritic
from arb_scanner.models._auto_exec_config import AutoExecutionConfig
from arb_scanner.models.auto_execution import CriticVerdict
from arb_scanner.models.config import Settings, StorageConfig, FeesConfig, FeeSchedule


def _make_settings() -> Settings:
    """Build minimal Settings for tests."""
    return Settings(
        storage=StorageConfig(database_url="postgresql://test@localhost/test"),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="percent_winnings"),
            kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
        ),
    )


def _make_pipeline(
    *,
    mode: str = "auto",
    enabled: bool = True,
    auto_overrides: dict | None = None,
    critic_verdict: CriticVerdict | None = None,
    slippage_ok: bool = True,
    execute_result: MagicMock | None = None,
    open_positions: list | None = None,
) -> AutoExecutionPipeline:
    """Build an AutoExecutionPipeline with mocked dependencies."""
    ac_kwargs: dict = {"enabled": enabled, "mode": mode}
    if auto_overrides:
        ac_kwargs.update(auto_overrides)
    auto_config = AutoExecutionConfig(**ac_kwargs)

    settings = _make_settings()
    settings.auto_execution = auto_config

    critic = MagicMock(spec=TradeCritic)
    verdict = critic_verdict or CriticVerdict(approved=True, skipped=True)
    critic.evaluate = AsyncMock(return_value=verdict)

    breakers = CircuitBreakerManager(auto_config)

    auto_repo = AsyncMock()
    auto_repo.get_open_positions = AsyncMock(return_value=open_positions or [])
    auto_repo.insert_log = AsyncMock()

    capital = MagicMock()
    capital.daily_pnl = Decimal("0")
    capital.current_exposure = Decimal("0")
    capital.total_balance = Decimal("1000")
    capital.poly_balance = Decimal("500")
    capital.kalshi_balance = Decimal("500")

    orch = MagicMock()
    result = execute_result or MagicMock(
        id="r1",
        status="complete",
        actual_spread=Decimal("0.04"),
        slippage_from_ticket=Decimal("0.01"),
    )
    orch.execute = AsyncMock(return_value=result)

    poly = MagicMock()
    poly.is_configured = MagicMock(return_value=True)
    poly.get_book_depth = AsyncMock(return_value={"asks": [{"price": "0.55", "size": "500"}]})
    kalshi = MagicMock()
    kalshi.is_configured = MagicMock(return_value=True)
    kalshi.get_book_depth = AsyncMock(return_value={"asks": [{"price": "0.42", "size": "500"}]})

    pipeline = AutoExecutionPipeline(
        config=settings,
        auto_config=auto_config,
        orchestrator=orch,
        critic=critic,
        breakers=breakers,
        auto_repo=auto_repo,
        capital=capital,
        poly=poly,
        kalshi=kalshi,
    )

    # Override slippage check
    if not slippage_ok:
        _slip_patch = patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(False, Decimal("0.05"), Decimal("0.03")),
        )
        _slip_patch.start()
        pipeline._slippage_patch = _slip_patch  # type: ignore[attr-defined]
    else:
        _slip_patch = patch(
            "arb_scanner.execution.auto_pipeline.check_slippage",
            new_callable=AsyncMock,
            return_value=(True, Decimal("0.001"), Decimal("0.001")),
        )
        _slip_patch.start()
        pipeline._slippage_patch = _slip_patch  # type: ignore[attr-defined]

    return pipeline


def _stop_patches(pipeline: AutoExecutionPipeline) -> None:
    """Stop any active patches."""
    p = getattr(pipeline, "_slippage_patch", None)
    if p:
        p.stop()


def _base_opportunity(**overrides: object) -> dict:
    """Build a passing opportunity dict."""
    opp: dict = {
        "arb_id": "arb-001",
        "spread_pct": 0.05,
        "confidence": 0.85,
        "category": "nba",
        "ticket_type": "arbitrage",
        "title": "Lakers vs Celtics",
        "poly_yes_price": 0.55,
        "kalshi_yes_price": 0.45,
        "poly_depth": 100,
        "kalshi_depth": 80,
        "price_age_seconds": 5,
    }
    opp.update(overrides)
    return opp


class TestProcessOpportunity:
    """Tests for process_opportunity()."""

    @pytest.mark.asyncio()
    async def test_returns_none_when_mode_off(self) -> None:
        """Returns None when pipeline mode is 'off'."""
        pipeline = _make_pipeline(mode="off")
        try:
            result = await pipeline.process_opportunity(_base_opportunity())
            assert result is None
        finally:
            _stop_patches(pipeline)

    @pytest.mark.asyncio()
    async def test_returns_none_when_mode_manual(self) -> None:
        """Returns None when pipeline mode is 'manual'."""
        pipeline = _make_pipeline(mode="manual")
        try:
            result = await pipeline.process_opportunity(_base_opportunity())
            assert result is None
        finally:
            _stop_patches(pipeline)

    @pytest.mark.asyncio()
    async def test_rejects_when_criteria_fail(self) -> None:
        """Returns rejected entry when spread is too low."""
        pipeline = _make_pipeline(auto_overrides={"min_spread_pct": 0.10})
        try:
            result = await pipeline.process_opportunity(
                _base_opportunity(spread_pct=0.02),
            )
            assert result is not None
            assert result.status == "rejected"
        finally:
            _stop_patches(pipeline)

    @pytest.mark.asyncio()
    async def test_rejects_when_size_none(self) -> None:
        """Returns rejected entry when compute_auto_size returns None."""
        pipeline = _make_pipeline(auto_overrides={"min_size_usd": 999.0})
        try:
            result = await pipeline.process_opportunity(_base_opportunity())
            assert result is not None
            assert result.status == "rejected"
        finally:
            _stop_patches(pipeline)

    @pytest.mark.asyncio()
    async def test_rejects_when_critic_rejects(self) -> None:
        """Returns critic_rejected when critic says no."""
        verdict = CriticVerdict(
            approved=False,
            risk_flags=["stale"],
            reasoning="data old",
        )
        pipeline = _make_pipeline(critic_verdict=verdict)
        try:
            result = await pipeline.process_opportunity(_base_opportunity())
            assert result is not None
            assert result.status == "critic_rejected"
        finally:
            _stop_patches(pipeline)

    @pytest.mark.asyncio()
    async def test_rejects_when_slippage_exceeded(self) -> None:
        """Returns rejected when slippage check fails."""
        pipeline = _make_pipeline(slippage_ok=False)
        try:
            result = await pipeline.process_opportunity(_base_opportunity())
            assert result is not None
            assert result.status == "rejected"
            assert any(
                "slippage" in r for r in result.criteria_snapshot.get("rejection_reasons", [])
            )
        finally:
            _stop_patches(pipeline)

    @pytest.mark.asyncio()
    async def test_happy_path_executes(self) -> None:
        """All gates pass, execution succeeds."""
        pipeline = _make_pipeline()
        try:
            result = await pipeline.process_opportunity(_base_opportunity())
            assert result is not None
            assert result.status == "executed"
            assert result.execution_result_id == "r1"
            assert result.size_usd > Decimal("0")
        finally:
            _stop_patches(pipeline)

    @pytest.mark.asyncio()
    async def test_execution_failure_records_and_trips_breaker(self) -> None:
        """Execution exception records failure and trips breaker."""
        pipeline = _make_pipeline()
        pipeline._orchestrator.execute = AsyncMock(side_effect=RuntimeError("exchange down"))
        try:
            result = await pipeline.process_opportunity(_base_opportunity())
            assert result is not None
            assert result.status == "failed"
        finally:
            _stop_patches(pipeline)

    @pytest.mark.asyncio()
    async def test_locked_arb_id_skipped(self) -> None:
        """Second call for same arb_id is skipped while first is in progress."""
        pipeline = _make_pipeline()

        # Pre-lock the arb_id
        import asyncio

        lock = asyncio.Lock()
        await lock.acquire()
        pipeline._locks["arb-001"] = lock

        try:
            result = await pipeline.process_opportunity(
                _base_opportunity(arb_id="arb-001"),
            )
            assert result is None
        finally:
            lock.release()
            _stop_patches(pipeline)


class TestKill:
    """Tests for kill() method."""

    def test_kill_disables_pipeline(self) -> None:
        """kill() sets mode to off and killed flag."""
        pipeline = _make_pipeline()
        try:
            pipeline.kill()
            assert pipeline.mode == "off"
            assert pipeline._killed is True
        finally:
            _stop_patches(pipeline)


class TestSetMode:
    """Tests for set_mode() method."""

    def test_changes_mode(self) -> None:
        """set_mode updates the pipeline mode."""
        pipeline = _make_pipeline(mode="off")
        try:
            pipeline.set_mode("auto")
            assert pipeline.mode == "auto"
            assert pipeline._killed is False

            pipeline.set_mode("off")
            assert pipeline.mode == "off"
            assert pipeline._killed is True
        finally:
            _stop_patches(pipeline)
