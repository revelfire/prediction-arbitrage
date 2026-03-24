"""Tests for upload-scoped backtesting automation helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from arb_scanner.backtesting.automation import generate_config_suggestions, run_import_workflow
from arb_scanner.models.backtesting import ImportResult, ImportedTrade, TradeAction
from arb_scanner.models.config import FeeSchedule, FeesConfig, Settings, StorageConfig


def _ts(minutes: int = 0) -> datetime:
    return datetime(2026, 3, 3, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def _trade(
    market_name: str,
    timestamp: datetime,
    tx_hash: str,
) -> ImportedTrade:
    return ImportedTrade(
        market_name=market_name,
        action=TradeAction.Buy,
        usdc_amount=Decimal("10"),
        token_amount=Decimal("20"),
        token_name="Yes",
        timestamp=timestamp,
        tx_hash=tx_hash,
    )


def _config() -> Settings:
    return Settings(
        storage=StorageConfig(database_url="postgresql://test:test@localhost/test"),
        fees=FeesConfig(
            polymarket=FeeSchedule(
                taker_fee_pct=Decimal("0.02"),
                fee_model="percent_winnings",
            ),
            kalshi=FeeSchedule(
                taker_fee_pct=Decimal("0.07"),
                fee_model="per_contract",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_run_import_workflow_scopes_report_to_uploaded_trade_window() -> None:
    trades = [
        _trade("NBA Market", _ts(0), "0x1"),
        _trade("NBA Market", _ts(15), "0x2"),
    ]
    repo = AsyncMock()
    repo.import_trades.return_value = ImportResult(inserted=2, duplicates=0, errors=0)
    repo.upsert_category_performance = AsyncMock()
    repo.upsert_position = AsyncMock()
    repo._pool = object()
    flip_repo = AsyncMock()

    report = {
        "portfolio": {},
        "signal_alignment": {},
        "category_performance": [],
        "category_models": [],
        "positions": [],
        "trades": trades,
        "signals": [],
        "comparisons": [],
    }

    with (
        patch("arb_scanner.backtesting.automation.parse_csv_bytes", return_value=trades),
        patch(
            "arb_scanner.backtesting.automation.build_backtest_report_data",
            new_callable=AsyncMock,
            return_value=report,
        ) as mock_build,
        patch(
            "arb_scanner.backtesting.automation.generate_config_suggestions",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await run_import_workflow(
            b"csv",
            config=_config(),
            repo=repo,
            flip_repo=flip_repo,
        )

    kwargs = mock_build.await_args.kwargs
    assert kwargs["since"] == _ts(0)
    assert kwargs["until"] == _ts(16)


@pytest.mark.asyncio
async def test_generate_config_suggestions_replays_only_matched_uploaded_markets() -> None:
    config = _config()
    trades = [
        _trade("NBA Market", _ts(0), "0x1"),
        _trade("Esports Market", _ts(5), "0x2"),
    ]
    comparisons = [
        (
            trades[0],
            "aligned",
            {
                "market_id": "m-nba",
                "market_title": "NBA Market",
                "category": "nba",
                "sport": "nba",
                "entry_at": _ts(1),
                "exit_at": _ts(10),
            },
        ),
        (
            trades[1],
            "no_signal",
            None,
        ),
    ]
    engine = AsyncMock()
    engine.replay_market = AsyncMock(return_value=[])

    with patch("arb_scanner.backtesting.automation.ReplayEngine", return_value=engine):
        suggestions = await generate_config_suggestions(
            config,
            trades=trades,
            signal_history=[],
            comparisons=comparisons,
            pool=object(),
        )

    assert suggestions == []
    engine.replay_market.assert_awaited_once()
    call = engine.replay_market.await_args
    assert call.args[0] == "m-nba"
    assert call.kwargs["category_hint"] == "nba"
    assert call.args[1] == _ts(-15)
    assert call.args[2] == _ts(70)
