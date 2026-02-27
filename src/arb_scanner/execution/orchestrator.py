"""Execution orchestrator: preflight validation and concurrent order placement."""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any, cast

import structlog

from arb_scanner.execution.base import VenueExecutor, contracts_from_usd
from arb_scanner.execution.capital_manager import CapitalManager
from arb_scanner.execution.liquidity import validate_liquidity
from arb_scanner.models.config import ExecutionConfig
from arb_scanner.models.execution import (
    ExecutionResult,
    OrderRequest,
    OrderSide,
    PreflightCheck,
    PreflightResult,
    ResultStatus,
)
from arb_scanner.storage.execution_repository import ExecutionRepository
from arb_scanner.storage.ticket_repository import TicketRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.orchestrator",
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class ExecutionOrchestrator:
    """Coordinates preflight validation and two-leg order execution."""

    def __init__(
        self,
        config: ExecutionConfig,
        capital: CapitalManager,
        poly: VenueExecutor,
        kalshi: VenueExecutor,
        exec_repo: ExecutionRepository,
        ticket_repo: TicketRepository,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            config: Execution configuration.
            capital: Capital manager for sizing and limits.
            poly: Polymarket executor.
            kalshi: Kalshi executor.
            exec_repo: Execution order repository.
            ticket_repo: Ticket repository for status updates.
        """
        self._config = config
        self._capital = capital
        self._poly = poly
        self._kalshi = kalshi
        self._exec_repo = exec_repo
        self._ticket_repo = ticket_repo

    async def preflight(self, arb_id: str) -> PreflightResult:
        """Run all pre-execution validation checks.

        Args:
            arb_id: The ticket identifier.

        Returns:
            PreflightResult with check details and suggested size.
        """
        checks: list[PreflightCheck] = []
        ticket = await self._ticket_repo.get_ticket(arb_id)
        if ticket is None:
            checks.append(PreflightCheck(name="ticket", passed=False, message="Ticket not found"))
            return PreflightResult(checks=checks)

        checks.append(self._check_enabled())
        checks.append(self._check_credentials())

        await self._capital.refresh_balances()
        poly_bal = self._capital.poly_balance
        kalshi_bal = self._capital.kalshi_balance
        checks.append(self._check_balances(poly_bal, kalshi_bal))

        suggested = self._capital.suggest_size()
        reserve_ok, reserve_msg = self._capital.check_venue_reserve(suggested)
        checks.append(PreflightCheck(name="reserve", passed=reserve_ok, message=reserve_msg))

        exp_cur, exp_rem, exp_blocked = self._capital.check_exposure()
        checks.append(
            PreflightCheck(
                name="exposure",
                passed=not exp_blocked,
                message=f"Exposure ${exp_cur:.2f}, remaining ${exp_rem:.2f}",
                value=exp_cur,
            )
        )

        pnl, pnl_limit, pnl_blocked = self._capital.check_daily_pnl()
        checks.append(
            PreflightCheck(
                name="daily_pnl",
                passed=not pnl_blocked,
                message=f"Daily P&L ${pnl:.2f} (limit -${pnl_limit:.2f})",
                value=pnl,
            )
        )

        cd_active, cd_remaining = self._capital.check_cooldown()
        checks.append(
            PreflightCheck(
                name="cooldown",
                passed=not cd_active,
                message=f"Cooldown: {cd_remaining}s remaining" if cd_active else "No cooldown",
            )
        )

        pos_cur, pos_max, pos_blocked = self._capital.check_open_positions()
        checks.append(
            PreflightCheck(
                name="open_positions",
                passed=not pos_blocked,
                message=f"{pos_cur}/{pos_max} open positions",
            )
        )

        market_id = _extract_market_id(ticket)
        conc_cur, conc_limit, conc_blocked = self._capital.check_concentration(market_id, suggested)
        checks.append(
            PreflightCheck(
                name="concentration",
                passed=not conc_blocked,
                message=f"Market exposure ${conc_cur:.2f} (limit ${conc_limit:.2f})",
                value=conc_cur,
            )
        )

        leg_1 = _parse_leg(ticket.get("leg_1"))
        leg_2 = _parse_leg(ticket.get("leg_2"))
        price_poly = Decimal(str(leg_1.get("price", "0")))
        price_kalshi = Decimal(str(leg_2.get("price", "0")))

        poly_book = await self._poly.get_book_depth(
            leg_1.get("token_id", leg_1.get("market_id", ""))
        )
        kalshi_book = await self._kalshi.get_book_depth(
            leg_2.get("ticker", leg_2.get("market_id", ""))
        )

        liq = validate_liquidity(
            poly_book, kalshi_book, suggested, price_poly, price_kalshi, self._config
        )
        liq_msg = "Liquidity OK" if liq.passed else "; ".join(liq.warnings)
        checks.append(PreflightCheck(name="liquidity", passed=liq.passed, message=liq_msg))

        return PreflightResult(
            checks=checks,
            suggested_size_usd=suggested,
            max_size_usd=Decimal(str(self._config.max_size_usd)),
            estimated_slippage_poly=liq.poly_slippage,
            estimated_slippage_kalshi=liq.kalshi_slippage,
            poly_balance=poly_bal,
            kalshi_balance=kalshi_bal,
            poly_depth_contracts=liq.poly_depth_contracts,
            kalshi_depth_contracts=liq.kalshi_depth_contracts,
        )

    async def execute(self, arb_id: str, size_usd: Decimal) -> ExecutionResult:
        """Place both legs of an arbitrage trade concurrently.

        Args:
            arb_id: The ticket identifier.
            size_usd: Trade size in USD.

        Returns:
            ExecutionResult with order details and status.
        """
        ticket = await self._ticket_repo.get_ticket(arb_id)
        if ticket is None:
            return _failed_result(arb_id, "Ticket not found")

        leg_1 = _parse_leg(ticket.get("leg_1"))
        leg_2 = _parse_leg(ticket.get("leg_2"))
        price_poly = Decimal(str(leg_1.get("price", "0")))
        price_kalshi = Decimal(str(leg_2.get("price", "0")))

        poly_req = OrderRequest(
            venue="polymarket",
            side=cast(OrderSide, _map_side(leg_1)),
            price=price_poly,
            size_usd=size_usd,
            size_contracts=contracts_from_usd(size_usd, price_poly),
            token_id=leg_1.get("token_id", leg_1.get("market_id", "")),
        )
        kalshi_req = OrderRequest(
            venue="kalshi",
            side=cast(OrderSide, _map_side(leg_2)),
            price=price_kalshi,
            size_usd=size_usd,
            size_contracts=contracts_from_usd(size_usd, price_kalshi),
            ticker=leg_2.get("ticker", leg_2.get("market_id", "")),
        )

        poly_oid = str(uuid.uuid4())
        kalshi_oid = str(uuid.uuid4())

        await self._exec_repo.insert_order(
            order_id=poly_oid,
            arb_id=arb_id,
            venue="polymarket",
            venue_order_id=None,
            side=poly_req.side,
            requested_price=price_poly,
            fill_price=None,
            size_usd=size_usd,
            size_contracts=poly_req.size_contracts,
            status="submitting",
            error_message=None,
        )
        await self._exec_repo.insert_order(
            order_id=kalshi_oid,
            arb_id=arb_id,
            venue="kalshi",
            venue_order_id=None,
            side=kalshi_req.side,
            requested_price=price_kalshi,
            fill_price=None,
            size_usd=size_usd,
            size_contracts=kalshi_req.size_contracts,
            status="submitting",
            error_message=None,
        )

        poly_resp, kalshi_resp = await asyncio.gather(
            self._poly.place_order(poly_req),
            self._kalshi.place_order(kalshi_req),
            return_exceptions=False,
        )

        await self._exec_repo.update_order_status(
            poly_oid,
            poly_resp.status,
            fill_price=poly_resp.fill_price,
            venue_order_id=poly_resp.venue_order_id,
            error_message=poly_resp.error_message,
        )
        await self._exec_repo.update_order_status(
            kalshi_oid,
            kalshi_resp.status,
            fill_price=kalshi_resp.fill_price,
            venue_order_id=kalshi_resp.venue_order_id,
            error_message=kalshi_resp.error_message,
        )

        poly_ok = poly_resp.status in ("submitted", "filled")
        kalshi_ok = kalshi_resp.status in ("submitted", "filled")

        result_status: ResultStatus
        if poly_ok and kalshi_ok:
            result_status = "complete"
        elif poly_ok or kalshi_ok:
            result_status = "partial"
            logger.warning(
                "partial_execution", arb_id=arb_id, poly=poly_resp.status, kalshi=kalshi_resp.status
            )
        else:
            result_status = "failed"

        total_cost = size_usd * 2
        ticket_cost = Decimal(str(ticket.get("expected_cost", "0")))
        slippage = total_cost - ticket_cost if ticket_cost > _ZERO else None

        result_id = str(uuid.uuid4())
        await self._exec_repo.insert_result(
            result_id=result_id,
            arb_id=arb_id,
            total_cost_usd=total_cost,
            actual_spread=None,
            slippage_from_ticket=slippage,
            poly_order_id=poly_oid,
            kalshi_order_id=kalshi_oid,
            status=result_status,
        )

        if result_status == "complete":
            await self._ticket_repo.update_status(arb_id, "executed")
            market_id = _extract_market_id(ticket)
            self._capital.record_fill(arb_id, market_id, size_usd)

        return ExecutionResult(
            id=result_id,
            arb_id=arb_id,
            total_cost_usd=total_cost,
            actual_spread=None,
            slippage_from_ticket=slippage,
            poly_order_id=poly_oid,
            kalshi_order_id=kalshi_oid,
            status=result_status,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending execution order.

        Args:
            order_id: Internal order UUID.

        Returns:
            True if cancelled successfully.
        """
        orders = await self._exec_repo.get_open_orders()
        target = next((o for o in orders if str(o["id"]) == order_id), None)
        if target is None:
            return False
        venue = target["venue"]
        vid = target.get("venue_order_id", "")
        if not vid:
            return False
        executor = self._poly if venue == "polymarket" else self._kalshi
        ok = await executor.cancel_order(vid)
        if ok:
            await self._exec_repo.update_order_status(order_id, "cancelled")
        return ok

    def _check_enabled(self) -> PreflightCheck:
        """Check if execution is enabled."""
        return PreflightCheck(
            name="enabled",
            passed=self._config.enabled,
            message="Execution enabled" if self._config.enabled else "Execution disabled",
        )

    def _check_credentials(self) -> PreflightCheck:
        """Check if both venue credentials are configured."""
        poly_ok = self._poly.is_configured()
        kalshi_ok = self._kalshi.is_configured()
        both = poly_ok and kalshi_ok
        parts = []
        if not poly_ok:
            parts.append("Polymarket credentials missing")
        if not kalshi_ok:
            parts.append("Kalshi credentials missing")
        msg = "Both venues configured" if both else "; ".join(parts)
        return PreflightCheck(name="credentials", passed=both, message=msg)

    def _check_balances(self, poly: Decimal, kalshi: Decimal) -> PreflightCheck:
        """Check if balances are positive."""
        ok = poly > _ZERO and kalshi > _ZERO
        return PreflightCheck(
            name="balances",
            passed=ok,
            message=f"Poly: ${poly:.2f}, Kalshi: ${kalshi:.2f}",
        )


def _failed_result(arb_id: str, msg: str) -> ExecutionResult:
    """Build a failed ExecutionResult."""
    return ExecutionResult(
        id=str(uuid.uuid4()),
        arb_id=arb_id,
        status="failed",
    )


def _parse_leg(raw: Any) -> dict[str, Any]:
    """Parse a ticket leg field (may be JSON string or dict)."""
    if isinstance(raw, str):
        return json.loads(raw)  # type: ignore[no-any-return]
    if isinstance(raw, dict):
        return raw
    return {}


def _map_side(leg: dict[str, Any]) -> str:
    """Map leg data to an OrderSide string."""
    action = str(leg.get("action", "buy")).lower()
    side = str(leg.get("side", "yes")).lower()
    return f"{action}_{side}"


def _extract_market_id(ticket: dict[str, Any]) -> str:
    """Extract a market identifier from ticket for concentration tracking."""
    leg = _parse_leg(ticket.get("leg_1"))
    return str(leg.get("market_id", leg.get("token_id", ticket.get("arb_id", ""))))
