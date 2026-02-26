"""Entry and exit signal generation for flippening trades."""

from __future__ import annotations

from decimal import Decimal

import structlog

from arb_scanner.models.arbitrage import ExecutionTicket
from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import (
    Baseline,
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    PriceUpdate,
    SpikeDirection,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.signal_generator",
)


class SignalGenerator:
    """Generate entry/exit signals and execution tickets.

    Creates trade recommendations when a spike is detected and
    monitors for reversion, stop-loss, or timeout exits.
    """

    def __init__(self, config: FlippeningConfig) -> None:
        """Initialise with flippening configuration.

        Args:
            config: Flippening engine configuration.
        """
        self._config = config

    def create_entry(
        self,
        event: FlippeningEvent,
        current_ask: Decimal,
        baseline: Baseline,
    ) -> EntrySignal:
        """Generate an entry signal from a detected flippening event.

        Args:
            event: Detected spike event.
            current_ask: Current ask price for the entry side.
            baseline: Pre-spike baseline odds.

        Returns:
            EntrySignal with entry price, targets, and sizing.
        """
        cat_cfg = self._config.categories.get(event.category or event.sport)
        reversion_pct = (
            cat_cfg.reversion_target_pct
            if cat_cfg and cat_cfg.reversion_target_pct is not None
            else self._config.reversion_target_pct
        )
        stop_pct = (
            cat_cfg.stop_loss_pct
            if cat_cfg and cat_cfg.stop_loss_pct is not None
            else self._config.stop_loss_pct
        )
        max_hold = (
            cat_cfg.max_hold_minutes
            if cat_cfg and cat_cfg.max_hold_minutes is not None
            else self._config.max_hold_minutes
        )

        side = _determine_side(event, baseline)
        entry_price = current_ask
        baseline_price = baseline.yes_price if side == "yes" else baseline.no_price
        reversion_amt = (baseline_price - entry_price) * Decimal(str(reversion_pct))
        target_exit = entry_price + reversion_amt
        stop_loss = entry_price * (Decimal("1") - Decimal(str(stop_pct)))

        raw_size = Decimal(str(self._config.base_position_usd)) * event.confidence
        suggested_size = min(
            raw_size.quantize(Decimal("0.01")),
            Decimal(str(self._config.max_position_usd)),
        )
        expected_profit_pct = (
            (target_exit - entry_price) / entry_price if entry_price else Decimal("0")
        )

        signal = EntrySignal(
            event_id=event.id,
            side=side,
            entry_price=entry_price,
            target_exit_price=target_exit,
            stop_loss_price=stop_loss,
            suggested_size_usd=suggested_size,
            expected_profit_pct=expected_profit_pct,
            max_hold_minutes=max_hold,
            created_at=event.detected_at,
        )
        logger.info(
            "entry_signal_created",
            event_id=event.id,
            side=side,
            entry=float(entry_price),
            target=float(target_exit),
            stop=float(stop_loss),
            size=float(suggested_size),
        )
        return signal

    def check_exit(
        self,
        update: PriceUpdate,
        entry: EntrySignal,
    ) -> ExitSignal | None:
        """Check whether exit conditions are met.

        Args:
            update: Current price update.
            entry: Active entry signal to monitor.

        Returns:
            ExitSignal if exit condition met, else None.
        """
        current_bid = update.yes_bid if entry.side == "yes" else update.no_bid
        elapsed_min = (update.timestamp - entry.created_at).total_seconds() / 60.0

        if current_bid >= entry.target_exit_price:
            return _build_exit(
                entry, ExitReason.REVERSION, current_bid, elapsed_min, update.timestamp
            )
        if current_bid <= entry.stop_loss_price:
            return _build_exit(
                entry, ExitReason.STOP_LOSS, current_bid, elapsed_min, update.timestamp
            )
        if elapsed_min >= entry.max_hold_minutes:
            return _build_exit(
                entry, ExitReason.TIMEOUT, current_bid, elapsed_min, update.timestamp
            )
        return None

    def create_ticket(self, entry: EntrySignal, event: FlippeningEvent) -> ExecutionTicket:
        """Create an execution ticket for the flippening trade.

        Args:
            entry: Entry signal with pricing.
            event: Originating flippening event.

        Returns:
            ExecutionTicket with both legs.
        """
        leg_1: dict[str, object] = {
            "venue": "polymarket",
            "action": "buy",
            "side": entry.side,
            "price": str(entry.entry_price),
            "size_usd": str(entry.suggested_size_usd),
        }
        leg_2: dict[str, object] = {
            "venue": "polymarket",
            "action": "sell",
            "side": entry.side,
            "price": str(entry.target_exit_price),
            "size_usd": str(entry.suggested_size_usd),
            "note": "limit sell — place manually when entry filled",
        }
        expected_cost = entry.entry_price * entry.suggested_size_usd
        expected_profit = (entry.target_exit_price - entry.entry_price) * entry.suggested_size_usd
        return ExecutionTicket(
            arb_id=event.id,
            leg_1=leg_1,
            leg_2=leg_2,
            expected_cost=expected_cost,
            expected_profit=expected_profit,
            ticket_type="flippening",
            status="pending",
        )


def _determine_side(event: FlippeningEvent, baseline: Baseline) -> str:
    """Determine which side to buy."""
    if event.spike_direction == SpikeDirection.FAVORITE_DROP and baseline.yes_price >= Decimal(
        "0.50"
    ):
        return "yes"
    if event.spike_direction == SpikeDirection.UNDERDOG_RISE and baseline.yes_price < Decimal(
        "0.50"
    ):
        return "no"
    if event.spike_direction == SpikeDirection.FAVORITE_DROP:
        return "no"
    return "yes"


def _build_exit(
    entry: EntrySignal,
    reason: ExitReason,
    exit_price: Decimal,
    elapsed_min: float,
    timestamp: object,
) -> ExitSignal:
    """Build an ExitSignal with P&L calculations."""
    from datetime import datetime

    pnl = exit_price - entry.entry_price
    pnl_pct = pnl / entry.entry_price if entry.entry_price else Decimal("0")
    ts = timestamp if isinstance(timestamp, datetime) else datetime.now()
    signal = ExitSignal(
        event_id=entry.event_id,
        side=entry.side,
        exit_price=exit_price,
        exit_reason=reason,
        realized_pnl=pnl,
        realized_pnl_pct=pnl_pct,
        hold_minutes=Decimal(str(round(elapsed_min, 2))),
        created_at=ts,
    )
    logger.info(
        "exit_signal_created",
        event_id=entry.event_id,
        reason=reason.value,
        pnl=float(pnl),
        hold_min=round(elapsed_min, 1),
    )
    return signal
