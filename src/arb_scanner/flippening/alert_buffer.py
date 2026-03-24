"""Non-blocking alert buffer for batched flippening alert dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from arb_scanner.flippening.alert_formatter import (
    ENTRY_EMOJI,
    EXIT_COLOR_MAP,
    EXIT_EMOJI_MAP,
    dispatch_flip_alert,
    label,
)

if TYPE_CHECKING:
    import httpx

    from arb_scanner.models.config import Settings
    from arb_scanner.models.flippening import (
        EntrySignal,
        ExitSignal,
        FlippeningEvent,
    )

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.alert_buffer",
)


@dataclass
class _BufferedAlert:
    """Internal container for a buffered alert."""

    event: FlippeningEvent
    entry: EntrySignal
    exit_signal: ExitSignal | None
    score: float
    has_open_position: bool = False


class AlertBuffer:
    """Buffer flippening alerts and flush as a single batched message.

    Designed to be non-blocking: ``append_entry()`` and ``append_exit()``
    are synchronous and O(1). ``flush()`` is async but swallows all
    exceptions to avoid disrupting the live engine.
    """

    def __init__(self) -> None:
        """Initialise empty alert buffer."""
        self._entries: dict[str, _BufferedAlert] = {}
        self._exits: dict[str, _BufferedAlert] = {}

    @property
    def pending(self) -> int:
        """Number of alerts awaiting flush."""
        return len(self._entries) + len(self._exits)

    def append_entry(
        self,
        event: FlippeningEvent,
        entry: EntrySignal,
        *,
        has_open_position: bool = False,
    ) -> None:
        """Buffer an entry alert (sync, non-blocking).

        Deduplicates by market_id, keeping the higher-scoring alert.

        Args:
            event: Detected flippening event.
            entry: Generated entry signal.
            has_open_position: True when an auto-exec position is already open.
        """
        score = float(entry.expected_profit_pct) * float(event.confidence)
        existing = self._entries.get(event.market_id)
        if existing is None or score > existing.score:
            self._entries[event.market_id] = _BufferedAlert(
                event=event,
                entry=entry,
                exit_signal=None,
                score=score,
                has_open_position=has_open_position,
            )

    def append_exit(
        self,
        event: FlippeningEvent,
        entry: EntrySignal,
        exit_signal: ExitSignal,
    ) -> None:
        """Accept exit alert (silenced — only logged, not dispatched).

        Args:
            event: Original flippening event.
            entry: Entry signal that was active.
            exit_signal: Exit signal with P&L.
        """
        logger.info(
            "exit_signal_logged",
            market_id=event.market_id,
            reason=exit_signal.exit_reason.value,
            pnl=float(exit_signal.realized_pnl),
        )

    async def flush(self, config: Settings, client: httpx.AsyncClient) -> int:
        """Rank, cap, and dispatch buffered alerts as one batch.

        Swallows all exceptions — failures MUST NOT disrupt the live
        engine.

        Args:
            config: Application settings (webhook URLs, batch cap).
            client: Shared httpx async client.

        Returns:
            Number of alerts dispatched (0 if empty or on error).
        """
        if not self._entries and not self._exits:
            return 0

        entries = list(self._entries.values())
        exits = list(self._exits.values())
        self._entries.clear()
        self._exits.clear()

        cap = config.flippening.alert_max_per_batch
        entries.sort(key=lambda a: a.score, reverse=True)
        exits.sort(key=lambda a: a.score, reverse=True)

        selected: list[_BufferedAlert] = []
        selected.extend(entries[:cap])
        remaining = cap - len(selected)
        if remaining > 0:
            selected.extend(exits[:remaining])

        total = len(selected)
        if total == 0:
            return 0

        try:
            notif = config.notifications
            entry_alerts = [a for a in selected if a.exit_signal is None]
            exit_alerts = [a for a in selected if a.exit_signal is not None]
            slack = _build_batch_slack(entry_alerts, exit_alerts, total)
            discord = _build_batch_discord(entry_alerts, exit_alerts)
            await dispatch_flip_alert(
                slack if notif.effective_flippening_slack else None,
                discord if notif.discord_webhook else None,
                slack_url=notif.effective_flippening_slack,
                discord_url=notif.discord_webhook,
                client=client,
            )
            logger.info("alert_batch_flushed", count=total)
            return total
        except Exception:
            logger.exception("alert_batch_flush_failed", dropped=total)
            return 0


def _build_batch_slack(
    entries: list[_BufferedAlert],
    exits: list[_BufferedAlert],
    total: int,
) -> dict[str, Any]:
    """Build a single Slack payload for a batch of alerts.

    Args:
        entries: Entry alerts to include.
        exits: Exit alerts to include.
        total: Total alert count for the header.

    Returns:
        Slack webhook JSON payload.
    """
    lines: list[str] = [
        f":chart_with_upwards_trend: Flippening Digest ({total} signals)",
    ]
    if entries:
        lines.append("───")
        lines.append(f"{ENTRY_EMOJI} *Entries*")
        for a in entries:
            e, s = a.event, a.entry
            open_tag = ":green_circle: *[OPEN POSITION]* " if a.has_open_position else ""
            lines.append(
                f"  {open_tag}*{label(e)}* {e.market_title}"
                f" — Spike {float(e.spike_magnitude_pct):.0%}"
                f" | Conf {float(e.confidence):.0%}"
                f" | Target ${float(s.target_exit_price):.2f}"
                f" | Size ${float(s.suggested_size_usd):.0f}",
            )
    if exits:
        lines.append("───")
        lines.append(":moneybag: *Exits*")
        for a in exits:
            assert a.exit_signal is not None  # noqa: S101
            e, s, x = a.event, a.entry, a.exit_signal
            emoji = EXIT_EMOJI_MAP.get(x.exit_reason, ":question:")
            reason = x.exit_reason.value.replace("_", " ").title()
            pnl_usd = float(x.realized_pnl * s.suggested_size_usd)
            lines.append(
                f"  {emoji} *{label(e)}* {e.market_title}"
                f" — {reason}"
                f" | P&L ${pnl_usd:+.2f} ({float(x.realized_pnl_pct):+.0%})"
                f" | {float(x.hold_minutes):.0f} min",
            )
    return {"text": "\n".join(lines)}


def _build_batch_discord(
    entries: list[_BufferedAlert],
    exits: list[_BufferedAlert],
) -> dict[str, Any]:
    """Build a single Discord payload with up to 10 embeds.

    Args:
        entries: Entry alerts to include.
        exits: Exit alerts to include.

    Returns:
        Discord webhook JSON payload.
    """
    embeds: list[dict[str, Any]] = []
    for a in entries:
        e, s = a.event, a.entry
        color = 3066993 if a.has_open_position else 15105570  # green if open, else orange
        title_prefix = "🟢 OPEN POSITION — " if a.has_open_position else ""
        fields: list[dict[str, Any]] = []
        if a.has_open_position:
            fields.append({"name": "⚠️ Status", "value": "Position already open", "inline": False})
        fields += [
            {"name": "Spike", "value": f"{float(e.spike_magnitude_pct):.0%}", "inline": True},
            {"name": "Confidence", "value": f"{float(e.confidence):.0%}", "inline": True},
            {"name": "Target", "value": f"${float(s.target_exit_price):.2f}", "inline": True},
            {"name": "Size", "value": f"${float(s.suggested_size_usd):.0f}", "inline": True},
        ]
        embeds.append(
            {
                "title": f"{title_prefix}Entry: {label(e)} — {e.market_title}",
                "color": color,
                "fields": fields,
            }
        )
    for a in exits:
        assert a.exit_signal is not None  # noqa: S101
        e, s, x = a.event, a.entry, a.exit_signal
        reason = x.exit_reason.value.replace("_", " ").title()
        color = EXIT_COLOR_MAP.get(x.exit_reason, 9807270)
        pnl_usd = float(x.realized_pnl * s.suggested_size_usd)
        embeds.append(
            {
                "title": f"Exit: {label(e)} — {reason}",
                "color": color,
                "fields": [
                    {
                        "name": "P&L",
                        "value": f"${pnl_usd:+.2f} ({float(x.realized_pnl_pct):+.0%})",
                        "inline": True,
                    },
                    {"name": "Hold", "value": f"{float(x.hold_minutes):.0f} min", "inline": True},
                ],
            }
        )
    total = len(entries) + len(exits)
    return {
        "content": f"Flippening Digest ({total} signals)",
        "embeds": embeds[:10],
    }
