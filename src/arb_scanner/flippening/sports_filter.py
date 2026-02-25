"""Sports market discovery and categorization from Polymarket data."""

from __future__ import annotations

import json
from datetime import datetime

import structlog

from arb_scanner.models.flippening import SportsMarket
from arb_scanner.models.market import Market

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.sports_filter",
)


def classify_sports_markets(
    markets: list[Market],
    allowed_sports: list[str],
) -> list[SportsMarket]:
    """Filter and classify Polymarket markets as sports events.

    Inspects ``raw_data`` metadata (groupSlug, tags, groupItemTitle) to
    identify sports markets matching the allowed sports list.

    Args:
        markets: All active Polymarket markets.
        allowed_sports: Lowercase sport identifiers to monitor.

    Returns:
        Markets identified as sports events with metadata.
    """
    allowed = set(s.lower() for s in allowed_sports)
    results: list[SportsMarket] = []
    for market in markets:
        sport = _detect_sport(market.raw_data, allowed)
        if sport is None:
            continue
        token_id = _extract_token_id(market.raw_data)
        if not token_id:
            logger.debug(
                "skipped_no_token_id",
                event_id=market.event_id,
            )
            continue
        start_time = _extract_game_start(market.raw_data)
        results.append(
            SportsMarket(
                market=market,
                sport=sport,
                game_start_time=start_time,
                token_id=token_id,
            )
        )
    logger.info(
        "sports_classification_complete",
        total_markets=len(markets),
        sports_markets=len(results),
        sports=list({sm.sport for sm in results}),
    )
    return results


def _detect_sport(
    raw_data: dict[str, object],
    allowed: set[str],
) -> str | None:
    """Detect sport from Gamma API raw_data fields.

    Checks groupSlug prefix, tags, and groupItemTitle for matches.

    Args:
        raw_data: Raw market dict from Gamma API.
        allowed: Set of allowed lowercase sport identifiers.

    Returns:
        Lowercase sport string or None if not a sports market.
    """
    slug = str(raw_data.get("groupSlug", "")).lower()
    for sport in allowed:
        if slug.startswith(f"{sport}-") or slug == sport:
            return sport

    tags = raw_data.get("tags", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    if isinstance(tags, list):
        for tag in tags:
            tag_lower = str(tag).lower()
            for sport in allowed:
                if sport in tag_lower:
                    return sport

    title = str(raw_data.get("groupItemTitle", "")).lower()
    for sport in allowed:
        if sport in title:
            return sport

    return None


def _extract_game_start(
    raw_data: dict[str, object],
) -> datetime | None:
    """Extract game start time from raw market metadata.

    Args:
        raw_data: Raw market dict from Gamma API.

    Returns:
        Parsed datetime or None if unavailable.
    """
    for field in ("startDate", "game_start_time", "startDateIso"):
        value = raw_data.get(field)
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(
                    value.replace("Z", "+00:00"),
                )
            except (ValueError, TypeError):
                continue
    return None


def _extract_token_id(raw_data: dict[str, object]) -> str:
    """Extract the CLOB token ID from raw market metadata.

    Args:
        raw_data: Raw market dict from Gamma API.

    Returns:
        Token ID string, or empty string if unavailable.
    """
    clob_ids = raw_data.get("clobTokenIds")
    if isinstance(clob_ids, str):
        try:
            parsed: list[str] = json.loads(clob_ids)
            if parsed:
                return parsed[0]
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(clob_ids, list) and clob_ids:
        return str(clob_ids[0])

    condition_id = raw_data.get("conditionId", "")
    if isinstance(condition_id, str):
        return condition_id
    return str(condition_id) if condition_id else ""
