"""Execution orchestrator: preflight validation and concurrent order placement."""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any, cast

import httpx
import structlog

from arb_scanner.execution.base import VenueExecutor, contracts_from_usd
from arb_scanner.execution.capital_manager import CapitalManager
from arb_scanner.execution.liquidity import validate_liquidity
from arb_scanner.models.config import ExecutionConfig
from arb_scanner.models.execution import (
    ExecutionResult,
    OrderRequest,
    OrderResponse,
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
        entry_legs = _collect_entry_legs(ticket)
        if not entry_legs:
            checks.append(
                PreflightCheck(
                    name="ticket_data",
                    passed=False,
                    message="No executable entry legs found in ticket payload",
                )
            )
            return PreflightResult(checks=checks)
        await self._hydrate_poly_tokens(entry_legs)
        required_venues = {str(leg["venue"]) for leg in entry_legs}

        checks.append(self._check_enabled())
        checks.append(self._check_credentials(required_venues))

        await self._capital.refresh_balances()
        poly_bal = self._capital.poly_balance
        kalshi_bal = self._capital.kalshi_balance
        checks.append(self._check_balances(poly_bal, kalshi_bal, required_venues))

        suggested = _suggest_size_for_venues(
            required_venues,
            poly_bal=poly_bal,
            kalshi_bal=kalshi_bal,
            config=self._config,
        )
        reserve_ok, reserve_msg = _check_venue_reserve_for_venues(
            required_venues,
            suggested,
            poly_bal=poly_bal,
            kalshi_bal=kalshi_bal,
            min_reserve_usd=Decimal(str(self._config.min_reserve_usd)),
        )
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

        liq = await self._check_liquidity(entry_legs, suggested)
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
            poly_depth_contracts=(
                liq.poly_depth_contracts if "polymarket" in required_venues else None
            ),
            kalshi_depth_contracts=(
                liq.kalshi_depth_contracts if "kalshi" in required_venues else None
            ),
        )

    async def execute(self, arb_id: str, size_usd: Decimal) -> ExecutionResult:
        """Place both legs of an arbitrage trade concurrently.

        Args:
            arb_id: The ticket identifier.
            size_usd: Trade size in USD.

        Returns:
            ExecutionResult with order details and status.
        """
        existing = await self._exec_repo.get_result(arb_id)
        if existing is not None:
            logger.info("execution_duplicate_skipped", arb_id=arb_id, status=existing["status"])
            return ExecutionResult(**{k: existing[k] for k in existing.keys()})

        ticket = await self._ticket_repo.get_ticket(arb_id)
        if ticket is None:
            return _failed_result(arb_id, "Ticket not found")
        entry_legs = _collect_entry_legs(ticket)
        if not entry_legs:
            return _failed_result(arb_id, "No executable entry legs in ticket")
        await self._hydrate_poly_tokens(entry_legs)
        ticket_type = str(ticket.get("ticket_type", "arbitrage")).lower()

        requests = _build_order_requests(entry_legs, size_usd, ticket_type=ticket_type)
        if not requests:
            return _failed_result(arb_id, "Size too small for executable hedge")

        jobs: list[tuple[str, str, OrderRequest]] = []
        for leg, req in zip(entry_legs, requests, strict=True):
            if req is None:
                return _failed_result(arb_id, f"Missing venue identifier for leg: {leg}")
            order_id = str(uuid.uuid4())
            await self._exec_repo.insert_order(
                order_id=order_id,
                arb_id=arb_id,
                venue=req.venue,
                venue_order_id=None,
                side=req.side,
                requested_price=req.price,
                fill_price=None,
                size_usd=req.size_usd,
                size_contracts=req.size_contracts,
                status="submitting",
                error_message=None,
            )
            jobs.append((req.venue, order_id, req))

        coros = [self._executor_for_venue(venue).place_order(req) for venue, _oid, req in jobs]
        responses = await asyncio.gather(*coros, return_exceptions=False)

        success_count = 0
        poly_oid: str | None = None
        kalshi_oid: str | None = None
        fill_prices: list[Decimal] = []
        successful_venues: list[tuple[str, str]] = []
        successful_cost = _ZERO
        quoted_success_cost = _ZERO
        for (venue, order_id, req), resp in zip(jobs, responses, strict=True):
            await self._exec_repo.update_order_status(
                order_id,
                resp.status,
                fill_price=resp.fill_price,
                venue_order_id=resp.venue_order_id,
                error_message=resp.error_message,
            )
            if resp.status in ("submitted", "filled"):
                success_count += 1
                successful_venues.append((venue, order_id))
                quoted_success_cost += req.price * Decimal(req.size_contracts)
                successful_cost += _resolved_order_cost(req, resp)
                if resp.fill_price is not None:
                    fill_prices.append(resp.fill_price)
            if venue == "polymarket":
                poly_oid = order_id
            elif venue == "kalshi":
                kalshi_oid = order_id

        result_status: ResultStatus
        geoblock = any(
            r.error_message and r.error_message.startswith("GEOBLOCK:") for r in responses
        )
        if success_count == len(jobs):
            result_status = "complete"
        elif success_count > 0:
            result_status = "partial"
            logger.warning(
                "partial_execution", arb_id=arb_id, successes=success_count, total=len(jobs)
            )
            await self._cancel_partial_legs(successful_venues, arb_id)
        else:
            result_status = "failed"

        total_cost = successful_cost
        slippage = (successful_cost - quoted_success_cost) if success_count > 0 else None
        actual_spread: Decimal | None = None
        actual_pnl: Decimal | None = None
        if result_status == "complete" and ticket_type != "flippening" and len(jobs) >= 2:
            actual_pnl = _locked_arb_pnl(jobs, responses)
            if total_cost > _ZERO and actual_pnl is not None:
                actual_spread = actual_pnl / total_cost
        if fill_prices:
            logger.info(
                "execution_fill_prices",
                arb_id=arb_id,
                fill_prices=[str(p) for p in fill_prices],
            )

        result_id = str(uuid.uuid4())
        await self._exec_repo.insert_result(
            result_id=result_id,
            arb_id=arb_id,
            total_cost_usd=total_cost,
            actual_spread=actual_spread,
            slippage_from_ticket=slippage,
            poly_order_id=poly_oid,
            kalshi_order_id=kalshi_oid,
            status=result_status,
        )

        if result_status == "complete":
            await self._ticket_repo.update_status(arb_id, "executed")
            market_id = _extract_market_id(ticket)
            self._capital.record_fill(arb_id, market_id, total_cost, pnl=actual_pnl)
            if ticket_type != "flippening":
                await self._ticket_repo.insert_action(
                    action_id=str(uuid.uuid4()),
                    ticket_id=arb_id,
                    action="execute",
                    actual_entry_price=(
                        (total_cost / Decimal(jobs[0][2].size_contracts)).quantize(
                            Decimal("0.0001")
                        )
                        if jobs and jobs[0][2].size_contracts > 0
                        else None
                    ),
                    actual_size_usd=total_cost,
                    actual_pnl=actual_pnl,
                    slippage=slippage,
                    notes="auto_exec_arb_locked_spread",
                )

        return ExecutionResult(
            id=result_id,
            arb_id=arb_id,
            total_cost_usd=total_cost,
            actual_spread=actual_spread,
            actual_pnl=actual_pnl,
            slippage_from_ticket=slippage,
            poly_order_id=poly_oid,
            kalshi_order_id=kalshi_oid,
            status=result_status,
            error_message="GEOBLOCK" if geoblock else None,
        )

    async def _check_liquidity(
        self,
        entry_legs: list[dict[str, Any]],
        suggested_size: Decimal,
    ) -> Any:
        """Run liquidity checks for the venues present in entry legs."""
        poly_leg = next((leg for leg in entry_legs if leg.get("venue") == "polymarket"), None)
        kalshi_leg = next((leg for leg in entry_legs if leg.get("venue") == "kalshi"), None)

        check_poly = poly_leg is not None
        check_kalshi = kalshi_leg is not None

        poly_book: dict[str, Any] = {"bids": [], "asks": []}
        kalshi_book: dict[str, Any] = {"bids": [], "asks": []}
        price_poly = _ZERO
        price_kalshi = _ZERO
        pre_warnings: list[str] = []

        if poly_leg is not None:
            token_id = _poly_token_from_leg(poly_leg)
            if token_id:
                raw_poly_book = await self._poly.get_book_depth(token_id)
                poly_book = _book_for_liquidity(
                    "polymarket", raw_poly_book, side=_parse_side(poly_leg)
                )
                price_poly = _current_best_ask(poly_book)
            else:
                pre_warnings.append(
                    "Polymarket token_id missing in ticket leg (cannot fetch order book depth)"
                )
            if price_poly <= _ZERO:
                price_poly = Decimal(str(poly_leg.get("price", "0")))

        if kalshi_leg is not None:
            ticker = _kalshi_ticker_from_leg(kalshi_leg)
            if ticker:
                raw_kalshi_book = await self._kalshi.get_book_depth(ticker)
                kalshi_book = _book_for_liquidity(
                    "kalshi",
                    raw_kalshi_book,
                    side=_parse_side(kalshi_leg),
                )
                price_kalshi = _current_best_ask(kalshi_book)
            else:
                pre_warnings.append(
                    "Kalshi ticker missing in ticket leg (cannot fetch order book depth)"
                )
            if price_kalshi <= _ZERO:
                price_kalshi = Decimal(str(kalshi_leg.get("price", "0")))

        liq = validate_liquidity(
            poly_book,
            kalshi_book,
            suggested_size,
            price_poly,
            price_kalshi,
            self._config,
            check_poly=check_poly,
            check_kalshi=check_kalshi,
        )
        if pre_warnings:
            liq = liq.model_copy(update={"warnings": pre_warnings + liq.warnings, "passed": False})
        return liq

    async def _hydrate_poly_tokens(self, entry_legs: list[dict[str, Any]]) -> None:
        """Backfill missing Polymarket token IDs for legacy flip tickets."""
        for leg in entry_legs:
            if leg.get("venue") != "polymarket":
                continue
            token_id = _poly_token_from_leg(leg)
            if token_id:
                leg["token_id"] = token_id
                continue
            market_id = str(leg.get("market_id", "")).strip()
            if not market_id:
                continue
            try:
                token = await self._ticket_repo.get_flip_token_for_market(market_id)
            except Exception:
                logger.warning("flip_token_lookup_failed", market_id=market_id)
                token = ""
            if not token:
                token = await self._lookup_token_from_market_url(leg)
            if token:
                leg["token_id"] = token

    async def _lookup_token_from_market_url(self, leg: dict[str, Any]) -> str:
        """Resolve token_id from market slug using Gamma API for legacy tickets."""
        market_url = str(leg.get("market_url", "")).strip()
        slug = _slug_from_market_url(market_url)
        if not slug:
            return ""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"slug": slug, "limit": 1},
                )
                resp.raise_for_status()
                data = resp.json()
            if not isinstance(data, list) or not data:
                return ""
            market = data[0]
            if not isinstance(market, dict):
                return ""
            token_ids = _parse_clob_token_ids(market.get("clobTokenIds"))
            if not token_ids:
                return ""
            side = _parse_side(leg)
            idx = 0 if side == "yes" else 1
            return token_ids[idx] if idx < len(token_ids) else token_ids[0]
        except Exception:
            logger.warning("flip_token_lookup_slug_failed", slug=slug)
            return ""

    async def _cancel_partial_legs(
        self,
        successful: list[tuple[str, str]],
        arb_id: str,
    ) -> None:
        """Attempt to cancel orders from a partial execution.

        Args:
            successful: List of (venue, order_id) tuples for successful legs.
            arb_id: The ticket identifier.
        """
        for venue, order_id in successful:
            try:
                ok = await self.cancel_order(order_id)
                if ok:
                    logger.info(
                        "partial_exec_leg_cancelled",
                        arb_id=arb_id,
                        venue=venue,
                        order_id=order_id,
                    )
                else:
                    logger.warning(
                        "partial_exec_cancel_failed",
                        arb_id=arb_id,
                        venue=venue,
                        order_id=order_id,
                        reason="cancel returned false — manual review needed",
                    )
            except Exception as exc:
                logger.error(
                    "partial_exec_cancel_error",
                    arb_id=arb_id,
                    venue=venue,
                    error=str(exc),
                )

    def _executor_for_venue(self, venue: str) -> VenueExecutor:
        """Resolve venue string to the correct executor."""
        return self._poly if venue == "polymarket" else self._kalshi

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

    def _check_credentials(self, required_venues: set[str]) -> PreflightCheck:
        """Check if required venue credentials are configured."""
        poly_ok = self._poly.is_configured()
        kalshi_ok = self._kalshi.is_configured()
        parts = []
        if "polymarket" in required_venues and not poly_ok:
            parts.append("Polymarket credentials missing")
        if "kalshi" in required_venues and not kalshi_ok:
            parts.append("Kalshi credentials missing")
        ok = not parts
        msg = "Required venues configured" if ok else "; ".join(parts)
        return PreflightCheck(name="credentials", passed=ok, message=msg)

    def _check_balances(
        self,
        poly: Decimal,
        kalshi: Decimal,
        required_venues: set[str],
    ) -> PreflightCheck:
        """Check if required venue balances are positive."""
        ok = True
        if "polymarket" in required_venues:
            ok = ok and poly > _ZERO
        if "kalshi" in required_venues:
            ok = ok and kalshi > _ZERO
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
        error_message=msg,
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
    action = _parse_action(leg)
    side = _parse_side(leg)
    return f"{action}_{side}"


def _parse_action(leg: dict[str, Any]) -> str:
    """Parse buy/sell from a leg action string."""
    action = str(leg.get("action", "")).lower().strip()
    if action.startswith("sell"):
        return "sell"
    if action.startswith("buy"):
        return "buy"
    return "buy"


def _parse_side(leg: dict[str, Any]) -> str:
    """Parse yes/no from explicit side field or action text."""
    side = str(leg.get("side", "")).lower().strip()
    if side in ("yes", "no"):
        return side
    action = str(leg.get("action", "")).lower()
    if " no" in action or action.endswith("no"):
        return "no"
    return "yes"


def _parse_venue(
    leg: dict[str, Any],
    *,
    ticket_type: str,
    index: int,
) -> str:
    """Parse venue from leg payload with backwards-compatible fallback."""
    venue = str(leg.get("venue", "")).lower().strip()
    if venue in ("polymarket", "kalshi"):
        return venue
    if ticket_type == "flippening":
        return "polymarket"
    return "polymarket" if index == 1 else "kalshi"


def _collect_entry_legs(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect executable entry legs from a ticket payload."""
    ticket_type = str(ticket.get("ticket_type", "arbitrage")).lower()
    legs: list[dict[str, Any]] = []
    for idx, key in ((1, "leg_1"), (2, "leg_2")):
        leg = _parse_leg(ticket.get(key))
        if not leg:
            continue
        action = _parse_action(leg)
        if action != "buy":
            continue
        venue = _parse_venue(leg, ticket_type=ticket_type, index=idx)
        if venue not in ("polymarket", "kalshi"):
            continue
        normalized = dict(leg)
        normalized["venue"] = venue
        normalized["action"] = action
        normalized["side"] = _parse_side(leg)
        legs.append(normalized)
    return legs


def _poly_token_from_leg(leg: dict[str, Any]) -> str:
    """Resolve polymarket token_id from a leg."""
    token_id = str(leg.get("token_id", "")).strip()
    if token_id:
        return token_id
    market_id = str(leg.get("market_id", "")).strip()
    return market_id if market_id.isdigit() else ""


def _kalshi_ticker_from_leg(leg: dict[str, Any]) -> str:
    """Resolve Kalshi ticker from a leg."""
    ticker = str(leg.get("ticker", "")).strip()
    if ticker:
        return ticker
    return str(leg.get("market_id", "")).strip()


def _current_best_ask(book: dict[str, Any]) -> Decimal:
    """Extract the best (lowest) ask price from a processed order book.

    Args:
        book: Order book dict with an "asks" list of price-level dicts.

    Returns:
        Best ask price, or Decimal("0") if no asks available.
    """
    asks = book.get("asks", [])
    if not asks or not isinstance(asks, list):
        return _ZERO
    first = asks[0]
    if not isinstance(first, dict):
        return _ZERO
    price = Decimal(str(first.get("price", "0")))
    return price if price > _ZERO else _ZERO


def _book_for_liquidity(venue: str, raw_book: Any, *, side: str) -> dict[str, Any]:
    """Select the side-relevant asks array for liquidity estimation."""
    if not isinstance(raw_book, dict):
        return {"bids": [], "asks": []}
    if venue == "kalshi":
        asks_key = "asks_yes" if side == "yes" else "asks_no"
        asks = raw_book.get(asks_key, raw_book.get("asks", []))
        return {"bids": [], "asks": asks if isinstance(asks, list) else []}
    asks = raw_book.get("asks", [])
    return {"bids": [], "asks": asks if isinstance(asks, list) else []}


def _build_order_request_from_leg(leg: dict[str, Any], size_usd: Decimal) -> OrderRequest | None:
    """Build an OrderRequest from a normalized entry leg."""
    venue = str(leg.get("venue", "")).strip()
    price = Decimal(str(leg.get("price", "0")))
    side = cast(OrderSide, _map_side(leg))
    size_contracts = contracts_from_usd(size_usd, price)
    if venue == "polymarket":
        token_id = _poly_token_from_leg(leg)
        if not token_id:
            return None
        return OrderRequest(
            venue="polymarket",
            side=side,
            price=price,
            size_usd=size_usd,
            size_contracts=size_contracts,
            token_id=token_id,
        )
    if venue == "kalshi":
        ticker = _kalshi_ticker_from_leg(leg)
        if not ticker:
            return None
        return OrderRequest(
            venue="kalshi",
            side=side,
            price=price,
            size_usd=size_usd,
            size_contracts=size_contracts,
            ticker=ticker,
        )
    return None


def _build_order_request_with_contracts(
    leg: dict[str, Any],
    size_contracts: int,
) -> OrderRequest | None:
    """Build an OrderRequest using an explicit contract count."""
    price = Decimal(str(leg.get("price", "0")))
    return _build_order_request_from_leg(leg, price * Decimal(size_contracts))


def _build_order_requests(
    entry_legs: list[dict[str, Any]],
    size_usd: Decimal,
    *,
    ticket_type: str,
) -> list[OrderRequest | None]:
    """Build executable order requests, matching contracts for arb entries."""
    if ticket_type == "flippening" or len(entry_legs) <= 1:
        return [_build_order_request_from_leg(leg, size_usd) for leg in entry_legs]

    contract_counts = [
        contracts_from_usd(size_usd, Decimal(str(leg.get("price", "0")))) for leg in entry_legs
    ]
    matched_contracts = min(contract_counts, default=0)
    if matched_contracts <= 0:
        return []
    return [_build_order_request_with_contracts(leg, matched_contracts) for leg in entry_legs]


def _resolved_order_cost(req: OrderRequest, resp: OrderResponse) -> Decimal:
    """Return the executed or quoted cost for a successful order response."""
    fill_price = resp.fill_price if resp.fill_price is not None else req.price
    return Decimal(str(fill_price)) * Decimal(req.size_contracts)


def _locked_arb_pnl(
    jobs: list[tuple[str, str, OrderRequest]],
    responses: list[OrderResponse],
) -> Decimal | None:
    """Compute locked-in arb P&L for a fully matched YES/NO pair."""
    if not jobs or len(jobs) != len(responses):
        return None
    if any(resp.status not in ("submitted", "filled") for resp in responses):
        return None
    size_contracts = jobs[0][2].size_contracts
    if size_contracts <= 0:
        return None
    if any(req.size_contracts != size_contracts for _venue, _oid, req in jobs):
        return None
    total_cost = sum(
        (
            _resolved_order_cost(req, resp)
            for (_v, _o, req), resp in zip(jobs, responses, strict=True)
        ),
        _ZERO,
    )
    return Decimal(size_contracts) - total_cost


def _suggest_size_for_venues(
    required_venues: set[str],
    *,
    poly_bal: Decimal,
    kalshi_bal: Decimal,
    config: ExecutionConfig,
) -> Decimal:
    """Suggest trade size based on balances for required venues only."""
    if not required_venues:
        return _ZERO
    balances: list[Decimal] = []
    if "polymarket" in required_venues:
        balances.append(poly_bal)
    if "kalshi" in required_venues:
        balances.append(kalshi_bal)
    if not balances:
        return _ZERO
    min_bal = min(balances)
    pct_size = min_bal * Decimal(str(config.pct_of_balance))
    venue_cap = min_bal * Decimal(str(config.max_pct_per_venue))
    hard_cap = Decimal(str(config.max_size_usd))
    return max(min(pct_size, venue_cap, hard_cap).quantize(Decimal("0.01")), _ZERO)


def _check_venue_reserve_for_venues(
    required_venues: set[str],
    size_usd: Decimal,
    *,
    poly_bal: Decimal,
    kalshi_bal: Decimal,
    min_reserve_usd: Decimal,
) -> tuple[bool, str]:
    """Check reserve constraint for required venues only."""
    if "polymarket" in required_venues:
        poly_after = poly_bal - size_usd
        if poly_after < min_reserve_usd:
            return False, (
                f"Polymarket balance would drop to ${poly_after:.2f} "
                f"(reserve: ${min_reserve_usd:.2f})"
            )
    if "kalshi" in required_venues:
        kalshi_after = kalshi_bal - size_usd
        if kalshi_after < min_reserve_usd:
            return False, (
                f"Kalshi balance would drop to ${kalshi_after:.2f} "
                f"(reserve: ${min_reserve_usd:.2f})"
            )
    return True, "Venue reserves OK"


def _slug_from_market_url(market_url: str) -> str:
    """Extract event slug from a polymarket event URL."""
    marker = "/event/"
    if marker not in market_url:
        return ""
    slug = market_url.split(marker, 1)[1].strip("/")
    return slug.split("?", 1)[0]


def _parse_clob_token_ids(raw: Any) -> list[str]:
    """Parse clobTokenIds field from Gamma payload."""
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x)]
    return []


def _extract_market_id(ticket: dict[str, Any]) -> str:
    """Extract a market identifier from ticket for concentration tracking."""
    leg = _parse_leg(ticket.get("leg_1"))
    return str(leg.get("market_id", leg.get("token_id", ticket.get("arb_id", ""))))
