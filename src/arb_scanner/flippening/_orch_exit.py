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
        await pipeline.process_exit(exit_sig, entry, event)
    except Exception:
        logger.warning("flip_pipeline_exit_feed_failed", market_id=event.market_id)
