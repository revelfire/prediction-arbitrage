"""Entry and exit signal generation for flippening trades."""

from __future__ import annotations

from datetime import UTC, datetime
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

_CONFIDENCE_NEUTRAL = 0.80
_CONFIDENCE_FLOOR = 0.50
_MAGNITUDE_NEUTRAL = 0.20
_MAGNITUDE_FLOOR = 0.05
_TARGET_TIGHTEN_MAX = 0.35
_STOP_TIGHTEN_MAX = 0.35
_HOLD_REDUCTION_MAX = 0.55


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
    ) -> EntrySignal | None:
        """Generate an entry signal from a detected flippening event.

        Args:
            event: Detected spike event.
            current_ask: Current ask price for the entry side.
            baseline: Pre-spike baseline odds.

        Returns:
            EntrySignal with entry price, targets, and sizing,
            or None if the entry price is below min_entry_price.
        """
        min_price = Decimal(str(self._config.min_entry_price))
        if current_ask < min_price:
            logger.info(
                "entry_rejected_low_price",
                event_id=event.id,
                current_ask=float(current_ask),
                min_entry_price=float(min_price),
            )
            return None

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
        reversion_pct, stop_pct, max_hold = _adaptive_exit_profile(
            event,
            reversion_pct=reversion_pct,
            stop_pct=stop_pct,
            max_hold=max_hold,
            min_hold_minutes=max(int(self._config.min_hold_seconds / 60), 1),
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
            max_hold_minutes=max_hold,
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
        elapsed_sec = (update.timestamp - entry.created_at).total_seconds()
        elapsed_min = elapsed_sec / 60.0

        min_hold = self._resolve_min_hold_seconds(entry)
        if elapsed_sec < min_hold:
            return None

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
        # Also check wall-clock time (WS event timestamps lag when markets go quiet)
        wall_min = (datetime.now(UTC) - entry.created_at).total_seconds() / 60.0
        if wall_min >= entry.max_hold_minutes:
            return _build_exit(entry, ExitReason.TIMEOUT, current_bid, wall_min, update.timestamp)
        return None

    def create_ticket(self, entry: EntrySignal, event: FlippeningEvent) -> ExecutionTicket | None:
        """Create an execution ticket for the flippening trade.

        Args:
            entry: Entry signal with pricing.
            event: Originating flippening event.

        Returns:
            ExecutionTicket with both legs, or None if unprofitable.
        """
        if not entry.entry_price:
            return None
        num_contracts = entry.suggested_size_usd / entry.entry_price
        expected_cost = entry.suggested_size_usd
        expected_profit = (entry.target_exit_price - entry.entry_price) * num_contracts
        min_profit = Decimal(str(self._config.min_expected_profit_usd))
        if expected_profit < min_profit:
            logger.debug("flip_ticket_skipped_below_min_profit", event_id=event.id)
            return None
        side_token = event.token_for_side(entry.side)
        leg_1: dict[str, object] = {
            "venue": "polymarket",
            "action": "buy",
            "side": entry.side,
            "token_id": side_token,
            "market_id": event.market_id,
            "price": str(entry.entry_price),
            "size_usd": str(entry.suggested_size_usd),
            "contracts": str(num_contracts.quantize(Decimal("0.01"))),
        }
        leg_2: dict[str, object] = {
            "venue": "polymarket",
            "action": "sell",
            "side": entry.side,
            "token_id": side_token,
            "market_id": event.market_id,
            "price": str(entry.target_exit_price),
            "size_usd": str(entry.suggested_size_usd),
            "contracts": str(num_contracts.quantize(Decimal("0.01"))),
            "note": "limit sell — place manually when entry filled",
        }
        return ExecutionTicket(
            arb_id=event.id,
            leg_1=leg_1,
            leg_2=leg_2,
            expected_cost=expected_cost,
            expected_profit=expected_profit,
            ticket_type="flippening",
            status="pending",
        )

    def _resolve_min_hold_seconds(self, entry: EntrySignal) -> float:
        """Resolve the minimum hold duration before exit checks begin.

        Args:
            entry: Active entry signal.

        Returns:
            Minimum hold duration in seconds.
        """
        return float(self._config.min_hold_seconds)


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


def _adaptive_exit_profile(
    event: FlippeningEvent,
    *,
    reversion_pct: float,
    stop_pct: float,
    max_hold: int,
    min_hold_minutes: int,
) -> tuple[float, float, int]:
    """Adapt target/stop/hold for weaker signals to reduce time-at-risk."""
    penalty = _signal_penalty(event)
    if penalty <= 0:
        return reversion_pct, stop_pct, max_hold

    reversion = max(reversion_pct * (1 - (_TARGET_TIGHTEN_MAX * penalty)), 0.05)
    stop = max(stop_pct * (1 - (_STOP_TIGHTEN_MAX * penalty)), 0.01)
    hold_scaled = int(round(max_hold * (1 - (_HOLD_REDUCTION_MAX * penalty))))
    hold = max(min_hold_minutes, min(hold_scaled, max_hold))
    return reversion, stop, hold


def _signal_penalty(event: FlippeningEvent) -> float:
    """Compute [0,1] penalty for weaker confidence/magnitude signals."""
    conf = float(event.confidence)
    conf_clamped = max(_CONFIDENCE_FLOOR, min(conf, 1.0))
    conf_penalty = max(_CONFIDENCE_NEUTRAL - conf_clamped, 0.0) / max(
        _CONFIDENCE_NEUTRAL - _CONFIDENCE_FLOOR,
        0.01,
    )

    magnitude = abs(float(event.spike_magnitude_pct))
    mag_clamped = max(_MAGNITUDE_FLOOR, min(magnitude, 1.0))
    mag_penalty = max(_MAGNITUDE_NEUTRAL - mag_clamped, 0.0) / max(
        _MAGNITUDE_NEUTRAL - _MAGNITUDE_FLOOR,
        0.01,
    )
    return max(0.0, min(max(conf_penalty, mag_penalty), 1.0))


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
