"""Game lifecycle tracking and state management for live sports."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog

from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import (
    Baseline,
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    GamePhase,
    PriceUpdate,
    SportsMarket,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.game_manager",
)

_DRIFT_RATE_THRESHOLD = Decimal("0.02")  # 2 pts/min
_DRIFT_DURATION_MINUTES = 5
_PRICE_HISTORY_MAXLEN = 200


@dataclass
class GameState:
    """Internal state for a single monitored game."""

    market_id: str
    market_title: str
    token_id: str
    sport: str
    phase: GamePhase
    baseline: Baseline | None = None
    active_signal: EntrySignal | None = None
    price_history: deque[PriceUpdate] = field(
        default_factory=lambda: deque(maxlen=_PRICE_HISTORY_MAXLEN),
    )
    game_start_time: datetime | None = None
    entered_live_at: datetime | None = None
    drift_accumulator: list[tuple[datetime, Decimal]] = field(
        default_factory=list,
    )
    needs_baseline: bool = False


class GameManager:
    """Manages lifecycle and state for all monitored sports games.

    Tracks games through upcoming → live → completed, captures baselines,
    and delegates to spike detector and reversion monitor.
    """

    def __init__(self, config: FlippeningConfig) -> None:
        """Initialise with flippening configuration.

        Args:
            config: Flippening engine configuration.
        """
        self._config = config
        self._games: dict[str, GameState] = {}

    @property
    def active_game_count(self) -> int:
        """Return number of non-completed games."""
        return sum(1 for g in self._games.values() if g.phase != GamePhase.COMPLETED)

    def has_open_signal(self, market_id: str) -> bool:
        """Check if a game has an active entry signal.

        Args:
            market_id: Market identifier.

        Returns:
            True if an entry signal is open for this game.
        """
        state = self._games.get(market_id)
        return state is not None and state.active_signal is not None

    def initialize(self, sports_markets: list[SportsMarket]) -> None:
        """Set up game states for discovered sports markets.

        Args:
            sports_markets: Sports markets to monitor.
        """
        now = datetime.now(tz=UTC)
        pre_game = timedelta(minutes=self._config.pre_game_window_minutes)
        for sm in sports_markets:
            mid = sm.market.event_id
            if mid in self._games:
                continue
            if sm.game_start_time and sm.game_start_time > now + pre_game:
                continue

            is_live = sm.game_start_time is not None and sm.game_start_time <= now
            phase = GamePhase.LIVE if is_live else GamePhase.UPCOMING
            state = GameState(
                market_id=mid,
                market_title=sm.market.title,
                token_id=sm.token_id,
                sport=sm.sport,
                phase=phase,
                game_start_time=sm.game_start_time,
            )
            self._games[mid] = state
            if is_live:
                state.needs_baseline = True
            logger.info(
                "game_initialized",
                market_id=mid,
                sport=sm.sport,
                phase=phase.value,
            )

    def capture_baseline(
        self,
        state: GameState,
        update: PriceUpdate,
        late_join: bool,
    ) -> Baseline:
        """Capture baseline odds from a price update.

        Args:
            state: Game state to update.
            update: Current price data.
            late_join: Whether this is a late join to an in-progress game.

        Returns:
            The captured baseline.
        """
        yes_mid = (update.yes_bid + update.yes_ask) / 2
        no_mid = (update.no_bid + update.no_ask) / 2
        baseline = Baseline(
            market_id=state.market_id,
            token_id=state.token_id,
            yes_price=yes_mid,
            no_price=no_mid,
            sport=state.sport,
            game_start_time=state.game_start_time,
            captured_at=update.timestamp,
            late_join=late_join,
        )
        state.baseline = baseline
        state.entered_live_at = update.timestamp
        logger.info(
            "baseline_captured",
            market_id=state.market_id,
            yes=float(yes_mid),
            no=float(no_mid),
            late_join=late_join,
        )
        return baseline

    def process(
        self,
        update: PriceUpdate,
    ) -> tuple[FlippeningEvent | None, ExitSignal | None]:
        """Process a price update for its corresponding game.

        Advances lifecycle, updates price history, and returns any
        detected flippening event or exit signal.

        Args:
            update: Real-time price update.

        Returns:
            Tuple of (flippening_event, exit_signal). Either or both
            may be None.
        """
        state = self._games.get(update.market_id)
        if state is None or state.phase == GamePhase.COMPLETED:
            return None, None

        state.price_history.append(update)

        if state.needs_baseline:
            self.capture_baseline(state, update, late_join=True)
            state.needs_baseline = False

        was_live = state.phase == GamePhase.LIVE
        self._advance_lifecycle(state, update)

        # Check resolution after lifecycle advances (may have gone COMPLETED)
        current_phase = GamePhase(state.phase.value)  # avoid mypy narrowing
        if was_live and current_phase == GamePhase.COMPLETED:
            exit_sig = self._check_resolution(state, update)
            if exit_sig is not None:
                return None, exit_sig

        if current_phase != GamePhase.LIVE or state.baseline is None:
            return None, None

        self._update_drift(state, update)

        return None, None

    def get_state(self, market_id: str) -> GameState | None:
        """Get the current state for a game.

        Args:
            market_id: Market identifier.

        Returns:
            GameState or None if not tracked.
        """
        return self._games.get(market_id)

    def set_active_signal(
        self,
        market_id: str,
        signal: EntrySignal,
    ) -> None:
        """Record an active entry signal for a game.

        Args:
            market_id: Market identifier.
            signal: The entry signal to track.
        """
        state = self._games.get(market_id)
        if state is not None:
            state.active_signal = signal

    def clear_active_signal(self, market_id: str) -> None:
        """Clear the active signal for a game, allowing new entries.

        Args:
            market_id: Market identifier.
        """
        state = self._games.get(market_id)
        if state is not None:
            state.active_signal = None

    def remove_game(self, market_id: str) -> None:
        """Remove a completed game from tracking.

        Args:
            market_id: Market identifier to remove.
        """
        self._games.pop(market_id, None)
        logger.info("game_removed", market_id=market_id)

    def _advance_lifecycle(
        self,
        state: GameState,
        update: PriceUpdate,
    ) -> None:
        """Transition game phase if conditions are met.

        Args:
            state: Game state to potentially advance.
            update: Current price update.
        """
        if state.phase == GamePhase.UPCOMING:
            should_go_live = (
                state.game_start_time is not None and update.timestamp >= state.game_start_time
            )
            if should_go_live:
                state.phase = GamePhase.LIVE
                if state.baseline is None:
                    self.capture_baseline(state, update, late_join=False)
                logger.info(
                    "game_went_live",
                    market_id=state.market_id,
                )

        if state.phase == GamePhase.LIVE:
            yes_mid = (update.yes_bid + update.yes_ask) / 2
            if yes_mid >= Decimal("0.99") or yes_mid <= Decimal("0.01"):
                state.phase = GamePhase.COMPLETED
                logger.info(
                    "game_completed",
                    market_id=state.market_id,
                )

    def _update_drift(
        self,
        state: GameState,
        update: PriceUpdate,
    ) -> None:
        """Track gradual baseline drift and update if appropriate.

        Gradual drift (< 2 pts/min sustained over 5+ minutes) updates
        the baseline. Sharp moves do not.

        Args:
            state: Game state with drift accumulator.
            update: Current price update.
        """
        if state.baseline is None:
            return
        yes_mid = (update.yes_bid + update.yes_ask) / 2
        state.drift_accumulator.append((update.timestamp, yes_mid))

        cutoff = update.timestamp - timedelta(minutes=_DRIFT_DURATION_MINUTES)
        state.drift_accumulator = [(t, p) for t, p in state.drift_accumulator if t >= cutoff]

        if len(state.drift_accumulator) < 2:
            return

        first_t, first_p = state.drift_accumulator[0]
        last_t, last_p = state.drift_accumulator[-1]
        elapsed_min = (last_t - first_t).total_seconds() / 60.0
        if elapsed_min < _DRIFT_DURATION_MINUTES:
            return

        total_drift = abs(last_p - first_p)
        drift_per_min = total_drift / Decimal(str(max(elapsed_min, 0.01)))

        if drift_per_min < _DRIFT_RATE_THRESHOLD:
            no_mid = (update.no_bid + update.no_ask) / 2
            state.baseline = Baseline(
                market_id=state.baseline.market_id,
                token_id=state.baseline.token_id,
                yes_price=yes_mid,
                no_price=no_mid,
                sport=state.baseline.sport,
                game_start_time=state.baseline.game_start_time,
                captured_at=update.timestamp,
                late_join=state.baseline.late_join,
            )
            state.drift_accumulator.clear()
            logger.debug(
                "baseline_drift_updated",
                market_id=state.market_id,
                new_yes=float(yes_mid),
            )

    def _check_resolution(
        self,
        state: GameState,
        update: PriceUpdate,
    ) -> ExitSignal | None:
        """Check if game resolved while a signal is active.

        Args:
            state: Game state to check.
            update: Current price update.

        Returns:
            ExitSignal if resolved with active position, else None.
        """
        if state.active_signal is None:
            return None
        if state.phase != GamePhase.COMPLETED:
            return None

        entry = state.active_signal
        yes_mid = (update.yes_bid + update.yes_ask) / 2
        if entry.side == "yes":
            exit_price = Decimal("1.00") if yes_mid >= Decimal("0.99") else Decimal("0.00")
        else:
            exit_price = Decimal("1.00") if yes_mid <= Decimal("0.01") else Decimal("0.00")

        pnl = exit_price - entry.entry_price
        pnl_pct = pnl / entry.entry_price if entry.entry_price else Decimal("0")
        hold = Decimal(
            str(
                (update.timestamp - entry.created_at).total_seconds() / 60.0,
            )
        )

        state.active_signal = None
        return ExitSignal(
            event_id=entry.event_id,
            side=entry.side,
            exit_price=exit_price,
            exit_reason=ExitReason.RESOLUTION,
            realized_pnl=pnl,
            realized_pnl_pct=pnl_pct,
            hold_minutes=hold,
            created_at=update.timestamp,
        )
