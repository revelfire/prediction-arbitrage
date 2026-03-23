"""Game lifecycle tracking and state management for live events."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog

from arb_scanner.flippening.baseline_strategy import BaselineCapture
from arb_scanner.flippening.drift_tracker import DriftInfo, update_drift
from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import (
    Baseline,
    CategoryMarket,
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    GamePhase,
    PriceUpdate,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.game_manager",
)
_PRICE_HISTORY_MAXLEN = 200


@dataclass
class GameState:
    """Internal state for a single monitored game."""

    market_id: str
    market_title: str
    token_id: str
    sport: str
    phase: GamePhase
    no_token_id: str = ""
    category: str = ""
    category_type: str = "sport"
    baseline_strategy: str = "first_price"
    event_window_hours: float = 4.0
    baseline: Baseline | None = None
    active_signal: EntrySignal | None = None
    active_event: FlippeningEvent | None = None
    price_history: deque[PriceUpdate] = field(
        default_factory=lambda: deque(maxlen=_PRICE_HISTORY_MAXLEN),
    )
    market_slug: str = ""
    game_start_time: datetime | None = None
    entered_live_at: datetime | None = None
    drift_accumulator: list[tuple[datetime, Decimal]] = field(default_factory=list)
    needs_baseline: bool = False


class GameManager:
    """Manages lifecycle and state for all monitored events."""

    def __init__(self, config: FlippeningConfig) -> None:
        """Initialise with flippening configuration."""
        self._config = config
        self._games: dict[str, GameState] = {}
        self._id_to_market: dict[str, str] = {}

    @property
    def active_game_count(self) -> int:
        """Return number of non-completed games."""
        return sum(1 for g in self._games.values() if g.phase != GamePhase.COMPLETED)

    def has_open_signal(self, market_id: str) -> bool:
        """Check if a game has an active entry signal."""
        state = self.get_state(market_id)
        return state is not None and state.active_signal is not None

    def iter_active_signals(self) -> list[tuple[str, GameState]]:
        """Return (market_id, state) pairs with active entry signals."""
        return [
            (mid, state) for mid, state in self._games.items() if state.active_signal is not None
        ]

    def initialize(self, category_markets: list[CategoryMarket]) -> None:
        """Set up game states for discovered category markets."""
        now = datetime.now(tz=UTC)
        pre_game = timedelta(minutes=self._config.pre_game_window_minutes)
        for sm in category_markets:
            mid = sm.market.event_id
            if mid in self._games:
                continue
            if sm.game_start_time and sm.game_start_time > now + pre_game:
                continue
            cat_type = getattr(sm, "category_type", "sport")
            if cat_type == "sport":
                if sm.game_start_time is None:
                    logger.info("game_skipped_no_start", market_id=mid)
                    continue
                ewh = self._get_event_window_hours(sm.category)
                if sm.game_start_time < now - timedelta(hours=ewh):
                    logger.info("game_skipped_stale", market_id=mid)
                    continue
            is_live = sm.game_start_time is not None and sm.game_start_time <= now
            phase = GamePhase.LIVE if is_live else GamePhase.UPCOMING
            cat_cfg = self._config.categories.get(sm.category)
            slug = str(sm.market.raw_data.get("slug", ""))
            state = GameState(
                market_id=mid,
                market_title=sm.market.title,
                token_id=sm.token_id,
                no_token_id=sm.no_token_id,
                sport=sm.sport,
                phase=phase,
                category=sm.category,
                category_type=sm.category_type,
                baseline_strategy=cat_cfg.baseline_strategy if cat_cfg else "first_price",
                event_window_hours=cat_cfg.event_window_hours if cat_cfg else 4.0,
                market_slug=slug,
                game_start_time=sm.game_start_time,
            )
            self._games[mid] = state
            self._id_to_market[sm.token_id] = mid
            cid = sm.market.raw_data.get("conditionId")
            if isinstance(cid, str) and cid:
                self._id_to_market[cid] = mid
            if is_live:
                state.needs_baseline = True
            logger.info(
                "game_initialized",
                market_id=mid,
                sport=sm.sport,
                category=sm.category,
                phase=phase.value,
            )

    def capture_baseline(
        self,
        state: GameState,
        update: PriceUpdate,
        late_join: bool,
    ) -> Baseline | None:
        """Capture baseline odds using the configured strategy."""
        baseline = self._capture_by_strategy(state, update, late_join)
        yes_price = float(baseline.yes_price)
        mn = self._config.min_baseline_price
        mx = self._config.max_baseline_price
        if yes_price < mn or yes_price > mx:
            logger.warning(
                "baseline_rejected_extreme",
                market_id=state.market_id,
                yes_price=yes_price,
                min_price=mn,
                max_price=mx,
            )
            state.baseline = None
            return None
        state.baseline = baseline
        state.entered_live_at = update.timestamp
        return baseline

    def _capture_by_strategy(
        self,
        state: GameState,
        update: PriceUpdate,
        late_join: bool,
    ) -> Baseline:
        """Delegate to the appropriate BaselineCapture strategy."""
        mid = state.market_id
        tid = state.token_id
        sp = state.sport
        cat = state.category
        ct = state.category_type
        gst = state.game_start_time
        if state.baseline_strategy == "pre_event_snapshot":
            cat_cfg = self._config.categories.get(cat)
            offset = cat_cfg.baseline_window_minutes if cat_cfg else 30
            result = BaselineCapture.capture_pre_event_snapshot(
                mid,
                tid,
                sp,
                cat,
                ct,
                gst,
                update,
                offset,
            )
            if result is not None:
                return result
        if state.baseline_strategy == "rolling_window":
            cat_cfg = self._config.categories.get(cat)
            window = cat_cfg.baseline_window_minutes if cat_cfg else 30
            result = BaselineCapture.capture_rolling_window(
                mid,
                tid,
                sp,
                cat,
                ct,
                gst,
                state.price_history,
                window,
            )
            if result is not None:
                return result
        return BaselineCapture.capture_first_price(
            mid,
            tid,
            sp,
            cat,
            ct,
            gst,
            update,
            late_join,
        )

    def process(
        self,
        update: PriceUpdate,
    ) -> tuple[FlippeningEvent | None, ExitSignal | None, DriftInfo | None]:
        """Process a price update for its corresponding game."""
        state = self.get_state(update.market_id) or self.get_state(update.token_id)
        if state is None or state.phase == GamePhase.COMPLETED:
            return None, None, None
        state.price_history.append(update)
        if state.needs_baseline:
            self.capture_baseline(state, update, late_join=True)
            state.needs_baseline = False
        elif state.baseline is not None:
            self._recapture_if_stale(state, update)
        was_live = state.phase == GamePhase.LIVE
        self._advance_lifecycle(state, update)
        current_phase = GamePhase(state.phase.value)
        if was_live and current_phase == GamePhase.COMPLETED:
            exit_sig = self._check_resolution(state, update)
            if exit_sig is not None:
                return None, exit_sig, None
        if current_phase != GamePhase.LIVE or state.baseline is None:
            return None, None, None
        if state.baseline_strategy == "rolling_window":
            self._refresh_rolling_baseline(state)
        new_bl, drift, state.drift_accumulator = update_drift(
            state.baseline,
            state.drift_accumulator,
            update,
        )
        if new_bl is not None:
            state.baseline = new_bl
        return None, None, drift

    def _refresh_rolling_baseline(self, state: GameState) -> None:
        """Refresh baseline via rolling window if enough data."""
        cat_cfg = self._config.categories.get(state.category)
        window = cat_cfg.baseline_window_minutes if cat_cfg else 30
        result = BaselineCapture.capture_rolling_window(
            market_id=state.market_id,
            token_id=state.token_id,
            sport=state.sport,
            category=state.category,
            category_type=state.category_type,
            game_start_time=state.game_start_time,
            price_history=state.price_history,
            window_minutes=window,
        )
        if result is not None:
            state.baseline = result

    def get_state(self, market_id: str) -> GameState | None:
        """Get state by event_id, condition_id, or token_id."""
        state = self._games.get(market_id)
        if state is not None:
            return state
        resolved = self._id_to_market.get(market_id)
        if resolved is not None:
            return self._games.get(resolved)
        return None

    def set_active_signal(
        self,
        market_id: str,
        signal: EntrySignal,
        event: FlippeningEvent | None = None,
    ) -> None:
        """Record an active entry signal (and originating event) for a game.

        Args:
            market_id: Market identifier.
            signal: Entry signal.
            event: Originating flippening event (stored for re-feed).
        """
        state = self.get_state(market_id)
        if state is not None:
            state.active_signal = signal
            state.active_event = event

    def clear_active_signal(self, market_id: str) -> None:
        """Clear the active signal for a game."""
        state = self._games.get(market_id)
        if state is not None:
            state.active_signal = None
            state.active_event = None

    def remove_game(self, market_id: str) -> None:
        """Remove a completed game from tracking."""
        self._games.pop(market_id, None)
        logger.info("game_removed", market_id=market_id)

    def _get_event_window_hours(self, category: str) -> float:
        """Return event_window_hours for a category."""
        cat_cfg = self._config.categories.get(category)
        return cat_cfg.event_window_hours if cat_cfg else 4.0

    def _recapture_if_stale(self, state: GameState, update: PriceUpdate) -> None:
        """Recapture baseline if deviation exceeds max_deviation_recapture_pct."""
        bl = state.baseline
        if bl is None or bl.yes_price <= 0:
            return
        yes_mid = (update.yes_bid + update.yes_ask) / 2
        dev_pct = abs(float((yes_mid - bl.yes_price) / bl.yes_price * 100))
        if dev_pct > self._config.max_deviation_recapture_pct:
            old_yes = float(bl.yes_price)
            self.capture_baseline(state, update, late_join=True)
            new_yes = float(state.baseline.yes_price) if state.baseline else None
            logger.info(
                "baseline_recaptured_stale",
                market_id=state.market_id,
                old_yes=old_yes,
                new_yes=new_yes,
                deviation_pct=dev_pct,
            )

    def _advance_lifecycle(self, state: GameState, update: PriceUpdate) -> None:
        """Transition game phase if conditions are met."""
        if state.phase == GamePhase.UPCOMING:
            should_go_live = (
                state.game_start_time is not None and update.timestamp >= state.game_start_time
            )
            if should_go_live:
                state.phase = GamePhase.LIVE
                if state.baseline is None:
                    self.capture_baseline(state, update, late_join=False)
                logger.info("game_went_live", market_id=state.market_id)
        if state.phase == GamePhase.LIVE:
            yes_mid = (update.yes_bid + update.yes_ask) / 2
            if yes_mid >= Decimal("0.99") or yes_mid <= Decimal("0.01"):
                state.phase = GamePhase.COMPLETED
                logger.info("game_completed", market_id=state.market_id)
                return
            if (
                state.category_type != "sport"
                and state.game_start_time is not None
                and state.baseline_strategy != "rolling_window"
                and update.timestamp
                > state.game_start_time + timedelta(hours=state.event_window_hours)
            ):
                state.phase = GamePhase.COMPLETED
                logger.info(
                    "event_window_completed",
                    market_id=state.market_id,
                    category_type=state.category_type,
                )

    def _check_resolution(self, state: GameState, update: PriceUpdate) -> ExitSignal | None:
        """Check if game resolved while a signal is active."""
        if state.active_signal is None or state.phase != GamePhase.COMPLETED:
            return None
        entry = state.active_signal
        yes_mid = (update.yes_bid + update.yes_ask) / 2
        if entry.side == "yes":
            exit_price = Decimal("1.00") if yes_mid >= Decimal("0.99") else Decimal("0.00")
        else:
            exit_price = Decimal("1.00") if yes_mid <= Decimal("0.01") else Decimal("0.00")
        pnl = exit_price - entry.entry_price
        pnl_pct = pnl / entry.entry_price if entry.entry_price else Decimal("0")
        hold = Decimal(str((update.timestamp - entry.created_at).total_seconds() / 60.0))
        state.active_signal = None
        state.active_event = None
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
