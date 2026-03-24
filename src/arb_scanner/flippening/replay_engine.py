"""Replay engine for backtesting flippening signals against stored ticks."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.flippening.baseline_strategy import BaselineCapture
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import (
    Baseline,
    EntrySignal,
    PriceUpdate,
)
from arb_scanner.models.replay import ReplaySignal
from arb_scanner.storage.tick_repository import TickRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.replay_engine",
)

_PRICE_HISTORY_MAXLEN = 200


class ReplayEngine:
    """Replay stored price ticks through spike/signal pipeline.

    Reuses the production ``SpikeDetector`` and ``SignalGenerator``
    with optional config overrides for parameter tuning.
    """

    def __init__(
        self,
        tick_repo: TickRepository,
        base_config: FlippeningConfig,
    ) -> None:
        """Initialise the replay engine.

        Args:
            tick_repo: Repository for tick and baseline data.
            base_config: Default flippening configuration.
        """
        self._tick_repo = tick_repo
        self._base_config = base_config

    async def replay_market(
        self,
        market_id: str,
        since: datetime,
        until: datetime,
        overrides: dict[str, Any] | None = None,
        category_hint: str | None = None,
    ) -> list[ReplaySignal]:
        """Replay a single market's ticks through the pipeline.

        Args:
            market_id: Market to replay.
            since: Start of time range (inclusive).
            until: End of time range (inclusive).
            overrides: Optional config field overrides.

        Returns:
            List of hypothetical signals produced.

        Raises:
            ValidationError: If overrides produce invalid config.
        """
        config = _apply_overrides(self._base_config, overrides)
        baseline = await self._load_baseline(market_id, since, until, category_hint)
        if baseline is None:
            logger.warning("replay_skip_no_baseline", market_id=market_id)
            return []

        drifts = await self._load_drifts(
            market_id,
            since,
            until,
            baseline.captured_at,
        )
        return await self._run_replay(
            market_id,
            since,
            until,
            config,
            baseline,
            drifts,
        )

    async def replay_markets(
        self,
        market_ids: list[str],
        since: datetime,
        until: datetime,
        overrides: dict[str, Any] | None = None,
        category_hint: str | None = None,
    ) -> list[ReplaySignal]:
        """Replay an explicit set of markets in the supplied time range."""
        signals: list[ReplaySignal] = []
        for market_id in market_ids:
            signals.extend(
                await self.replay_market(
                    market_id,
                    since,
                    until,
                    overrides=overrides,
                    category_hint=category_hint,
                ),
            )
        return signals

    async def replay_category(
        self,
        category: str,
        since: datetime,
        until: datetime,
        overrides: dict[str, Any] | None = None,
    ) -> list[ReplaySignal]:
        """Replay all markets for a category in the time range.

        Args:
            category: Category identifier (e.g. "nba", "btc_threshold").
            since: Start of time range.
            until: End of time range.
            overrides: Optional config field overrides.

        Returns:
            Concatenated list of signals from all markets.
        """
        market_ids = await self._tick_repo.get_market_ids(category, since, until)
        return await self.replay_markets(
            market_ids,
            since,
            until,
            overrides=overrides,
            category_hint=category,
        )

    async def replay_sport(
        self,
        sport: str,
        since: datetime,
        until: datetime,
        overrides: dict[str, Any] | None = None,
    ) -> list[ReplaySignal]:
        """Replay all markets for a sport (alias for replay_category)."""
        return await self.replay_category(sport, since, until, overrides)

    async def _load_baseline(
        self,
        market_id: str,
        since: datetime,
        until: datetime,
        category_hint: str | None = None,
    ) -> Baseline | None:
        """Load the most recent baseline for a market."""
        row = await self._tick_repo.get_baseline(market_id)
        if row is not None:
            return Baseline(
                market_id=row["market_id"],
                token_id=row["token_id"],
                yes_price=row["baseline_yes"],
                no_price=row["baseline_no"],
                sport=row["sport"],
                category=row.get("category", row["sport"]),
                category_type=row.get("category_type", "sport"),
                baseline_strategy=row.get("baseline_strategy", "first_price"),
                game_start_time=row["game_start_time"],
                captured_at=row["captured_at"],
                late_join=row["late_join"],
            )

        first_tick = await self._tick_repo.get_first_tick(market_id, since, until)
        if first_tick is None:
            return None

        context = await self._tick_repo.get_market_context(market_id)
        sport, category, category_type = self._resolve_market_context(context, category_hint)
        baseline = BaselineCapture.capture_first_price(
            market_id=market_id,
            token_id=first_tick["token_id"],
            sport=sport,
            category=category,
            category_type=category_type,
            game_start_time=None,
            update=_record_to_update(first_tick),
            late_join=True,
        )
        logger.info(
            "replay_baseline_reconstructed",
            market_id=market_id,
            category=category,
            category_type=category_type,
            captured_at=baseline.captured_at,
        )
        return baseline

    def _resolve_market_context(
        self,
        row: Any,
        category_hint: str | None,
    ) -> tuple[str, str, str]:
        """Resolve category metadata for replay fallback baselines."""
        if row is not None:
            sport = str(row.get("sport") or "")
            category = str(row.get("category") or sport or category_hint or "")
            category_type = str(
                row.get("category_type")
                or self._category_type_for(category or category_hint)
                or "sport"
            )
            base = sport or category or category_hint or "uncategorized"
            return base, category or base, category_type

        hint = category_hint or "uncategorized"
        return hint, hint, self._category_type_for(hint)

    def _category_type_for(self, category: str | None) -> str:
        """Return the configured category_type for a category name."""
        if not category:
            return "sport"
        cat_cfg = self._base_config.categories.get(category)
        if cat_cfg is not None:
            return cat_cfg.category_type
        return "sport"

    async def _load_drifts(
        self,
        market_id: str,
        since: datetime,
        until: datetime,
        baseline_captured_at: datetime,
    ) -> list[dict[str, Any]]:
        """Load drift records, filtering those before baseline."""
        rows = await self._tick_repo.get_drifts(market_id, since, until)
        return [dict(r) for r in rows if r["drifted_at"] >= baseline_captured_at]

    async def _run_replay(
        self,
        market_id: str,
        since: datetime,
        until: datetime,
        config: FlippeningConfig,
        baseline: Baseline,
        drifts: list[dict[str, Any]],
    ) -> list[ReplaySignal]:
        """Core replay loop processing ticks through spike/signal."""
        spike_det = SpikeDetector(config)
        signal_gen = SignalGenerator(config)
        history: deque[PriceUpdate] = deque(maxlen=_PRICE_HISTORY_MAXLEN)
        active: EntrySignal | None = None
        current_baseline = baseline
        drift_idx = 0
        signals: list[ReplaySignal] = []

        async for record in self._tick_repo.stream_ticks(
            market_id,
            since,
            until,
        ):
            update = _record_to_update(record)
            history.append(update)

            # Apply drifts at correct timestamps
            while drift_idx < len(drifts):
                d = drifts[drift_idx]
                if d["drifted_at"] <= update.timestamp:
                    current_baseline = Baseline(
                        market_id=current_baseline.market_id,
                        token_id=current_baseline.token_id,
                        yes_price=d["new_yes"],
                        no_price=current_baseline.no_price,
                        sport=current_baseline.sport,
                        category=current_baseline.category,
                        category_type=current_baseline.category_type,
                        baseline_strategy=current_baseline.baseline_strategy,
                        game_start_time=current_baseline.game_start_time,
                        captured_at=d["drifted_at"],
                        late_join=current_baseline.late_join,
                    )
                    drift_idx += 1
                else:
                    break

            if active is None:
                event = spike_det.check_spike(
                    update,
                    current_baseline,
                    history,
                )
                if event is not None:
                    ask = (
                        update.yes_ask
                        if current_baseline.yes_price >= Decimal("0.50")
                        else update.no_ask
                    )
                    active = signal_gen.create_entry(
                        event,
                        ask,
                        current_baseline,
                    )
                    if active is None:
                        event = None
            else:
                exit_sig = signal_gen.check_exit(update, active)
                if exit_sig is not None:
                    signals.append(
                        _build_replay_signal(
                            market_id,
                            active,
                            exit_sig,
                            update.timestamp,
                        )
                    )
                    active = None

        return signals


def _apply_overrides(
    base: FlippeningConfig,
    overrides: dict[str, Any] | None,
) -> FlippeningConfig:
    """Apply config overrides with validation.

    Args:
        base: Base configuration.
        overrides: Field overrides to apply.

    Returns:
        New config with overrides applied.

    Raises:
        ValidationError: If merged config is invalid.
    """
    if not overrides:
        return base
    merged = base.model_dump()
    merged.update(overrides)
    return FlippeningConfig.model_validate(merged)


def _record_to_update(record: Any) -> PriceUpdate:
    """Convert a DB record to a PriceUpdate model."""
    return PriceUpdate(
        market_id=record["market_id"],
        token_id=record["token_id"],
        yes_bid=record["yes_bid"],
        yes_ask=record["yes_ask"],
        no_bid=record["no_bid"],
        no_ask=record["no_ask"],
        timestamp=record["timestamp"],
        synthetic_spread=record["synthetic_spread"],
        book_depth_bids=record["book_depth_bids"],
        book_depth_asks=record["book_depth_asks"],
    )


def _build_replay_signal(
    market_id: str,
    entry: EntrySignal,
    exit_sig: Any,
    exit_ts: datetime,
) -> ReplaySignal:
    """Build a ReplaySignal from entry + exit data."""
    return ReplaySignal(
        market_id=market_id,
        entry_price=entry.entry_price,
        exit_price=exit_sig.exit_price,
        exit_reason=exit_sig.exit_reason,
        realized_pnl=exit_sig.realized_pnl,
        hold_minutes=exit_sig.hold_minutes,
        confidence=Decimal(str(entry.expected_profit_pct)),
        side=entry.side,
        entry_at=entry.created_at,
        exit_at=exit_ts,
    )
