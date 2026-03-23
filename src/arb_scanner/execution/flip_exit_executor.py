"""Executor that places Polymarket sell orders for open flippening positions."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.execution.flip_position_math import compute_realized_pnl
from arb_scanner.execution.flip_position_repo import FlipPositionRepo
from arb_scanner.models.execution import OrderRequest, OrderSide
from arb_scanner.models.flippening import EntrySignal, ExitReason, ExitSignal, FlippeningEvent

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.flip_exit_executor",
)

_ZERO = Decimal("0")


class FlipExitExecutor:
    """Places Polymarket sell orders for open flippening positions.

    Only runs when a position was opened via auto-exec and an exit
    signal fires. On success the position is marked closed; on failure
    it is marked exit_failed and the operator is notified.

    Args:
        poly: Polymarket venue executor.
        exec_repo: Execution order repository.
        position_repo: Flippening auto-position repository.
        stop_loss_aggression_pct: Extra discount on stop-loss limit price.
    """

    def __init__(
        self,
        poly: Any,
        exec_repo: Any,
        position_repo: FlipPositionRepo,
        stop_loss_aggression_pct: float = 0.02,
    ) -> None:
        """Initialise the exit executor.

        Args:
            poly: Polymarket executor instance.
            exec_repo: Execution order repository.
            position_repo: Open position repository.
            stop_loss_aggression_pct: Fraction to discount the stop-loss limit.
        """
        self._poly = poly
        self._exec_repo = exec_repo
        self._position_repo = position_repo
        self._aggression = Decimal(str(stop_loss_aggression_pct))

    async def execute_exit(
        self,
        exit_sig: ExitSignal,
        entry_sig: EntrySignal,
        event: FlippeningEvent,
    ) -> str | None:
        """Place a sell order for the open position on event.market_id.

        Args:
            exit_sig: Exit signal with reason and target price.
            entry_sig: Original entry signal (for P&L calculation).
            event: Flippening event identifying the market.

        Returns:
            Internal execution order ID on success, None if no open position.
        """
        position = await self._position_repo.get_open_position(event.market_id)
        if position is None:
            logger.info("exit_skipped_no_position", market_id=event.market_id)
            return None

        req = _build_sell_request(position, exit_sig, self._aggression)
        order_id = str(uuid.uuid4())

        await self._exec_repo.insert_order(
            order_id=order_id,
            arb_id=position["arb_id"],
            venue="polymarket",
            venue_order_id=None,
            side=req.side,
            requested_price=req.price,
            fill_price=None,
            size_usd=_ZERO,
            size_contracts=req.size_contracts,
            status="submitting",
            error_message=None,
        )

        try:
            resp = await self._poly.place_order(req)
            await self._exec_repo.update_order_status(
                order_id,
                resp.status,
                fill_price=resp.fill_price,
                venue_order_id=resp.venue_order_id,
                error_message=resp.error_message,
            )
        except Exception as exc:
            await self._exec_repo.update_order_status(order_id, "failed", error_message=str(exc))
            await self._position_repo.mark_exit_failed(event.market_id)
            logger.error("flip_exit_order_failed", market_id=event.market_id, error=str(exc))
            raise

        if resp.status not in ("filled", "submitted", "partially_filled"):
            await self._position_repo.mark_exit_failed(event.market_id)
            logger.warning(
                "flip_exit_response_failed",
                market_id=event.market_id,
                status=resp.status,
            )
            return None

        effective_fill = Decimal(str(resp.fill_price)) if resp.fill_price is not None else None
        # Treat status=filled as terminal. For status=submitted, allow immediate
        # completion only when venue already reports a fill price.
        if resp.status == "filled" or (resp.status == "submitted" and effective_fill is not None):
            final_exit_price = effective_fill or req.price
            pnl = compute_realized_pnl(
                Decimal(str(position["entry_price"])),
                final_exit_price,
                position["size_contracts"],
            )
            await self._position_repo.close_position(
                event.market_id,
                exit_order_id=order_id,
                exit_price=final_exit_price,
                realized_pnl=pnl,
                exit_reason=exit_sig.exit_reason.value,
            )
            logger.info(
                "flip_exit_placed",
                market_id=event.market_id,
                side=req.side,
                price=float(final_exit_price),
                contracts=req.size_contracts,
                reason=exit_sig.exit_reason.value,
            )
            return order_id

        await self._position_repo.mark_exit_pending(
            event.market_id,
            exit_order_id=order_id,
            exit_price=req.price,
            exit_reason=exit_sig.exit_reason.value,
        )
        logger.info(
            "flip_exit_pending",
            market_id=event.market_id,
            side=req.side,
            price=float(req.price),
            contracts=req.size_contracts,
            reason=exit_sig.exit_reason.value,
        )
        return order_id


def _build_sell_request(
    position: dict[str, Any],
    exit_sig: ExitSignal,
    aggression: Decimal,
) -> OrderRequest:
    """Construct a sell OrderRequest from an open position and exit signal.

    Timeout and stop-loss exits use aggressive pricing to ensure
    immediate fill.  Timeout exits price at $0.01 (acts as market sell
    on the CLOB — Polymarket gives price improvement at best bid).
    Stop-loss exits apply 2x aggression discount.  Reversion exits
    apply the standard discount.

    Args:
        position: Open position record from DB.
        exit_sig: Exit signal with target price.
        aggression: Base price discount fraction (e.g. 0.02 = 2%).

    Returns:
        OrderRequest ready for PolymarketExecutor.place_order().
    """
    if exit_sig.exit_reason == ExitReason.TIMEOUT:
        price = Decimal("0.01")
    else:
        price = Decimal(str(exit_sig.exit_price))
        discount = aggression * 2 if exit_sig.exit_reason == ExitReason.STOP_LOSS else aggression
        price = (price * (1 - discount)).quantize(Decimal("0.0001"))

    side_str = position["side"]
    sell_side: OrderSide = f"sell_{side_str}"  # type: ignore[assignment]

    return OrderRequest(
        venue="polymarket",
        side=sell_side,
        price=price,
        size_usd=Decimal("0"),
        size_contracts=int(position["size_contracts"]),
        token_id=str(position["token_id"]),
    )
