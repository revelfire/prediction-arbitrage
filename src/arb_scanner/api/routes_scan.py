"""API routes for triggering scan cycles."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from arb_scanner.api.deps import get_config
from arb_scanner.models.config import Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.scan")
router = APIRouter()


@router.post("/api/scan")
async def trigger_scan(
    config: Settings = Depends(get_config),
) -> dict[str, Any]:
    """Trigger an immediate scan cycle.

    Args:
        config: Injected application settings.

    Returns:
        Scan result dictionary with summary statistics.
    """
    from arb_scanner.cli.orchestrator import run_scan

    try:
        result = await run_scan(config, dry_run=False)
        # Remove internal _raw_opps key before returning
        result.pop("_raw_opps", None)
        return result
    except Exception as exc:
        logger.error("scan_trigger_failed", error=str(exc))
        raise HTTPException(500, f"Scan failed: {exc}") from exc
