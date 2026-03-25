"""Fire-and-forget feed of exit signals to the auto-execution pipeline."""

from __future__ import annotations

import structlog

from arb_scanner.models.config import Settings
from arb_scanner.models.flippening import EntrySignal, ExitSignal, FlippeningEvent

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.orch_exit",
)


async def _feed_exit_pipeline(
    event: FlippeningEvent,
    entry: EntrySignal,
    exit_sig: ExitSignal,
    config: Settings,
) -> None:
    """Feed an exit signal to AutoExecutionPipeline.process_exit() if mode=auto.

    Swallows all exceptions — failures MUST NOT disrupt the live engine.

    Args:
        event: Flippening event identifying the market.
        entry: Original entry signal for P&L context.
        exit_sig: Exit signal with reason and target price.
        config: Application settings (pipeline stored on _flip_pipeline attr).
    """
    try:
        from arb_scanner.execution.flip_pipeline import FlipAutoExecutionPipeline

        pipeline: FlipAutoExecutionPipeline | None = getattr(config, "_flip_pipeline", None)
        if pipeline is None or pipeline.mode != "auto":
            return
        submitted = await pipeline.process_exit(exit_sig, entry, event)
        if submitted:
            await _notify_sell(event, entry, exit_sig, config)
    except Exception:
        logger.warning("flip_pipeline_exit_feed_failed", market_id=event.market_id)


async def _notify_sell(
    event: FlippeningEvent,
    entry: EntrySignal,
    exit_sig: ExitSignal,
    config: Settings,
) -> None:
    """Send SELL notification after exit pipeline fires.

    Args:
        event: Flippening event.
        entry: Original entry signal.
        exit_sig: Exit signal with reason/price.
        config: Application settings.
    """
    try:
        from arb_scanner.notifications.trade_webhook import dispatch_trade_alert

        notif = config.notifications
        contracts = int(entry.suggested_size_usd / entry.entry_price) if entry.entry_price else 0
        await dispatch_trade_alert(
            action="sell",
            market_title=event.market_title or event.market_id[:20],
            side=entry.side,
            size_contracts=contracts,
            price=exit_sig.exit_price,
            arb_id=event.id,
            slack_url=notif.effective_auto_exec_slack,
            dashboard_url=notif.dashboard_url,
            auth_token=config.dashboard.auth_token or "",
        )
    except Exception:
        logger.warning("sell_notification_failed", market_id=event.market_id)
