"""Unit tests for the ExecutionOrchestrator."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.execution.capital_manager import CapitalManager
from arb_scanner.execution.orchestrator import (
    ExecutionOrchestrator,
    _current_best_ask,
    _extract_market_id,
    _map_side,
    _parse_leg,
)
from arb_scanner.models.config import ExecutionConfig
from arb_scanner.models.execution import OrderResponse


def _make_orch(
    ticket: dict | None = None,
    poly_resp: OrderResponse | None = None,
    kalshi_resp: OrderResponse | None = None,
) -> ExecutionOrchestrator:
    """Build an orchestrator with mocked dependencies."""
    config = ExecutionConfig(enabled=True, min_book_depth_contracts=5)

    poly = MagicMock()
    poly.is_configured.return_value = True
    poly.get_balance = AsyncMock(return_value=Decimal("500"))
    poly.get_book_depth = AsyncMock(
        return_value={
            "asks": [{"price": "0.55", "size": "500"}],
            "bids": [],
        }
    )
    poly.place_order = AsyncMock(
        return_value=poly_resp
        or OrderResponse(
            venue_order_id="poly-123",
            status="filled",
            fill_price=Decimal("0.55"),
        )
    )
    poly.cancel_order = AsyncMock(return_value=True)

    kalshi = MagicMock()
    kalshi.is_configured.return_value = True
    kalshi.get_balance = AsyncMock(return_value=Decimal("400"))
    kalshi.get_book_depth = AsyncMock(
        return_value={
            "asks": [{"price": "0.42", "size": "500"}],
            "bids": [],
        }
    )
    kalshi.place_order = AsyncMock(
        return_value=kalshi_resp
        or OrderResponse(
            venue_order_id="kalshi-456",
            status="filled",
            fill_price=Decimal("0.42"),
        )
    )
    kalshi.cancel_order = AsyncMock(return_value=True)

    capital = CapitalManager(config, poly.get_balance, kalshi.get_balance)
    capital._poly_balance = Decimal("500")
    capital._kalshi_balance = Decimal("400")

    exec_repo = AsyncMock()
    exec_repo.insert_order = AsyncMock()
    exec_repo.update_order_status = AsyncMock()
    exec_repo.insert_result = AsyncMock()
    exec_repo.get_result = AsyncMock(return_value=None)
    exec_repo.get_open_orders = AsyncMock(return_value=[])

    ticket_repo = AsyncMock()
    default_ticket = ticket or {
        "arb_id": "t1",
        "status": "approved",
        "expected_cost": "0.97",
        "leg_1": json.dumps(
            {
                "action": "buy",
                "side": "yes",
                "price": 0.55,
                "market_id": "m1",
                "token_id": "tok1",
            }
        ),
        "leg_2": json.dumps(
            {
                "action": "buy",
                "side": "no",
                "price": 0.42,
                "market_id": "m2",
                "ticker": "KXTICKER",
            }
        ),
    }
    ticket_repo.get_ticket = AsyncMock(return_value=default_ticket)
    ticket_repo.update_status = AsyncMock()
    ticket_repo.get_flip_token_for_market = AsyncMock(return_value="")

    return ExecutionOrchestrator(
        config=config,
        capital=capital,
        poly=poly,
        kalshi=kalshi,
        exec_repo=exec_repo,
        ticket_repo=ticket_repo,
    )


def _flip_ticket() -> dict[str, object]:
    """Build a flippening-style single-venue ticket payload."""
    return {
        "arb_id": "flip-1",
        "ticket_type": "flippening",
        "status": "approved",
        "expected_cost": "10.00",
        "leg_1": json.dumps(
            {
                "action": "BUY YES",
                "side": "yes",
                "price": "0.50",
                "token_id": "tok-flip-1",
                "market_id": "m-flip",
                "market_title": "Flip ticket",
            }
        ),
        "leg_2": json.dumps(
            {
                "action": "SELL YES at target",
                "target_price": "0.60",
                "stop_loss": "0.45",
            }
        ),
    }


class TestPreflight:
    """Tests for preflight()."""

    @pytest.mark.asyncio()
    async def test_all_checks_pass(self) -> None:
        """Preflight passes when everything is valid."""
        orch = _make_orch()
        result = await orch.preflight("t1")
        assert result.all_passed is True
        assert len(result.checks) > 0
        assert result.poly_depth_contracts is not None
        assert result.kalshi_depth_contracts is not None

    @pytest.mark.asyncio()
    async def test_ticket_not_found(self) -> None:
        """Returns failed check when ticket is missing."""
        orch = _make_orch()
        orch._ticket_repo.get_ticket = AsyncMock(return_value=None)
        result = await orch.preflight("nonexistent")
        assert result.all_passed is False
        assert any(c.name == "ticket" for c in result.checks)

    @pytest.mark.asyncio()
    async def test_disabled_execution(self) -> None:
        """Enabled check fails when config.enabled is False."""
        orch = _make_orch()
        orch._config = ExecutionConfig(enabled=False)
        result = await orch.preflight("t1")
        enabled_check = next(c for c in result.checks if c.name == "enabled")
        assert enabled_check.passed is False

    @pytest.mark.asyncio()
    async def test_flippening_preflight_is_single_venue(self) -> None:
        """Flippening preflight checks liquidity only on Polymarket entry leg."""
        orch = _make_orch(ticket=_flip_ticket())
        orch._kalshi.is_configured.return_value = False
        orch._poly.get_book_depth = AsyncMock(
            return_value={
                "asks": [{"price": "0.50", "size": "500"}],
                "bids": [],
            }
        )
        result = await orch.preflight("flip-1")
        assert result.all_passed is True
        assert result.poly_depth_contracts is not None
        assert result.kalshi_depth_contracts is None
        orch._kalshi.get_book_depth.assert_not_awaited()
        cred_check = next(c for c in result.checks if c.name == "credentials")
        assert cred_check.passed is True

    @pytest.mark.asyncio()
    async def test_flippening_preflight_backfills_missing_token(self) -> None:
        """Legacy flip tickets can resolve token_id from repository baseline data."""
        ticket = _flip_ticket()
        leg_1 = json.loads(str(ticket["leg_1"]))
        leg_1.pop("token_id", None)
        ticket["leg_1"] = json.dumps(leg_1)
        orch = _make_orch(ticket=ticket)
        orch._ticket_repo.get_flip_token_for_market = AsyncMock(return_value="tok-from-baseline")
        orch._poly.get_book_depth = AsyncMock(
            return_value={
                "asks": [{"price": "0.50", "size": "500"}],
                "bids": [],
            }
        )

        result = await orch.preflight("flip-1")

        assert result.all_passed is True
        orch._ticket_repo.get_flip_token_for_market.assert_awaited_once_with("m-flip")

    @pytest.mark.asyncio()
    async def test_flippening_preflight_backfills_token_from_market_url(self) -> None:
        """If baseline lookup misses, token_id can be resolved from market URL metadata."""
        ticket = _flip_ticket()
        leg_1 = json.loads(str(ticket["leg_1"]))
        leg_1.pop("token_id", None)
        ticket["leg_1"] = json.dumps(leg_1)
        orch = _make_orch(ticket=ticket)
        orch._ticket_repo.get_flip_token_for_market = AsyncMock(return_value="")
        orch._lookup_token_from_market_url = AsyncMock(return_value="tok-from-url")  # type: ignore[method-assign]
        orch._poly.get_book_depth = AsyncMock(
            return_value={
                "asks": [{"price": "0.50", "size": "500"}],
                "bids": [],
            }
        )

        result = await orch.preflight("flip-1")

        assert result.all_passed is True
        orch._lookup_token_from_market_url.assert_awaited()


class TestExecute:
    """Tests for execute()."""

    @pytest.mark.asyncio()
    async def test_successful_execution(self) -> None:
        """Both legs fill successfully."""
        orch = _make_orch()
        result = await orch.execute("t1", Decimal("10"))
        assert result.status == "complete"
        assert result.total_cost_usd == Decimal("17.46")
        assert result.actual_pnl == Decimal("0.54")
        assert result.poly_order_id is not None
        assert result.kalshi_order_id is not None
        poly_req = orch._poly.place_order.await_args_list[0].args[0]
        kalshi_req = orch._kalshi.place_order.await_args_list[0].args[0]
        assert poly_req.size_contracts == 18
        assert kalshi_req.size_contracts == 18
        orch._ticket_repo.insert_action.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_partial_execution(self) -> None:
        """One leg fails, result is partial."""
        orch = _make_orch(
            kalshi_resp=OrderResponse(status="failed", error_message="Rejected"),
        )
        result = await orch.execute("t1", Decimal("10"))
        assert result.status == "partial"

    @pytest.mark.asyncio()
    async def test_both_fail(self) -> None:
        """Both legs fail, result is failed."""
        orch = _make_orch(
            poly_resp=OrderResponse(status="failed", error_message="Err"),
            kalshi_resp=OrderResponse(status="failed", error_message="Err"),
        )
        result = await orch.execute("t1", Decimal("10"))
        assert result.status == "failed"

    @pytest.mark.asyncio()
    async def test_ticket_not_found(self) -> None:
        """Returns failed result when ticket is missing."""
        orch = _make_orch()
        orch._ticket_repo.get_ticket = AsyncMock(return_value=None)
        result = await orch.execute("nonexistent", Decimal("10"))
        assert result.status == "failed"

    @pytest.mark.asyncio()
    async def test_records_fill_on_complete(self) -> None:
        """Complete execution records fill in capital manager."""
        orch = _make_orch()
        await orch.execute("t1", Decimal("10"))
        assert orch._capital.current_exposure > Decimal("0")

    @pytest.mark.asyncio()
    async def test_flippening_exec_places_only_poly_leg(self) -> None:
        """Flippening execution places one Polymarket leg."""
        orch = _make_orch(ticket=_flip_ticket())
        result = await orch.execute("flip-1", Decimal("10"))
        assert result.status == "complete"
        assert result.total_cost_usd == Decimal("11.00")
        orch._poly.place_order.assert_awaited_once()
        orch._kalshi.place_order.assert_not_awaited()
        assert orch._exec_repo.insert_order.await_count == 1

    @pytest.mark.asyncio()
    async def test_duplicate_execution_returns_existing_result(self) -> None:
        """Returns existing result without re-placing orders when result already exists."""
        orch = _make_orch()
        existing = {
            "id": "result-existing",
            "arb_id": "t1",
            "total_cost_usd": Decimal("10"),
            "actual_spread": None,
            "slippage_from_ticket": None,
            "poly_order_id": None,
            "kalshi_order_id": None,
            "status": "complete",
            "created_at": None,
        }
        orch._exec_repo.get_result = AsyncMock(return_value=existing)
        result = await orch.execute("t1", Decimal("10"))
        assert result.id == "result-existing"
        assert result.status == "complete"
        orch._poly.place_order.assert_not_awaited()
        orch._kalshi.place_order.assert_not_awaited()


class TestCancelOrder:
    """Tests for cancel_order()."""

    @pytest.mark.asyncio()
    async def test_cancel_existing(self) -> None:
        """Cancels when order is found with venue_order_id."""
        orch = _make_orch()
        orch._exec_repo.get_open_orders = AsyncMock(
            return_value=[
                {"id": "o1", "venue": "polymarket", "venue_order_id": "v1"},
            ]
        )
        result = await orch.cancel_order("o1")
        assert result is True

    @pytest.mark.asyncio()
    async def test_cancel_not_found(self) -> None:
        """Returns False when order isn't found."""
        orch = _make_orch()
        result = await orch.cancel_order("nonexistent")
        assert result is False


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_parse_leg_dict(self) -> None:
        """Dict input is returned as-is."""
        assert _parse_leg({"price": 0.5}) == {"price": 0.5}

    def test_parse_leg_json_string(self) -> None:
        """JSON string is parsed."""
        result = _parse_leg('{"price": 0.5}')
        assert result == {"price": 0.5}

    def test_parse_leg_none(self) -> None:
        """None returns empty dict."""
        assert _parse_leg(None) == {}

    def test_map_side(self) -> None:
        """Maps action + side to combined string."""
        assert _map_side({"action": "buy", "side": "yes"}) == "buy_yes"
        assert _map_side({"action": "SELL", "side": "NO"}) == "sell_no"
        assert _map_side({}) == "buy_yes"

    def test_extract_market_id(self) -> None:
        """Extracts market_id from ticket leg_1."""
        ticket = {"leg_1": json.dumps({"market_id": "m1"})}
        assert _extract_market_id(ticket) == "m1"

    def test_extract_market_id_fallback(self) -> None:
        """Falls back to token_id when market_id missing."""
        ticket = {"leg_1": json.dumps({"token_id": "tok1"})}
        assert _extract_market_id(ticket) == "tok1"

    def test_current_best_ask(self) -> None:
        """Extracts lowest ask from processed order book."""
        book = {"asks": [{"price": "0.55", "size": "100"}, {"price": "0.60", "size": "50"}]}
        assert _current_best_ask(book) == Decimal("0.55")

    def test_current_best_ask_empty(self) -> None:
        """Returns zero for empty asks."""
        assert _current_best_ask({"asks": []}) == Decimal("0")
        assert _current_best_ask({}) == Decimal("0")


class TestSlippageUsesCurrentPrice:
    """Verify preflight slippage uses current best ask, not stale ticket price."""

    @pytest.mark.asyncio()
    async def test_flip_slippage_uses_book_price_not_ticket_price(self) -> None:
        """Flippening ticket with stale price should not show absurd slippage."""
        ticket = _flip_ticket()
        leg_1 = json.loads(str(ticket["leg_1"]))
        leg_1["price"] = "0.21"  # stale spike price
        ticket["leg_1"] = json.dumps(leg_1)

        orch = _make_orch(ticket=ticket)
        orch._poly.get_book_depth = AsyncMock(
            return_value={
                "asks": [
                    {"price": "0.58", "size": "200"},
                    {"price": "0.59", "size": "300"},
                ],
                "bids": [],
            }
        )

        result = await orch.preflight("flip-1")
        # Slippage should be measured against current best ask (0.58),
        # not stale ticket price (0.21). With enough depth at 0.58,
        # slippage should be near-zero, not 175%.
        assert result.estimated_slippage_poly is not None
        assert result.estimated_slippage_poly < Decimal("0.10")
