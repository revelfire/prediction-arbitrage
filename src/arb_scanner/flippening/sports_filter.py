"""Sports market discovery and categorization from Polymarket data."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import structlog

from arb_scanner.flippening.sport_keywords import fuzzy_match_sport, get_sport_keywords
from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import SportsMarket
from arb_scanner.models.market import Market

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.sports_filter",
)

_last_alert_time: dict[str, datetime] = {}  # rate-limit tracker
_sport_zero_count: dict[str, int] = {}  # per-sport consecutive zero-result counter


@dataclasses.dataclass
class DiscoveryHealthSnapshot:
    """Health metrics from a single sports market classification run."""

    total_scanned: int
    sports_found: int
    hit_rate: float
    by_sport: dict[str, int]
    overrides_applied: int
    exclusions_applied: int
    unclassified_candidates: int
    unclassified_sample: list[dict[str, str]] = dataclasses.field(default_factory=list)


def classify_sports_markets(
    markets: list[Market],
    allowed_sports: list[str],
    config: FlippeningConfig | None = None,
) -> tuple[list[SportsMarket], DiscoveryHealthSnapshot]:
    """Multi-pass classification: slug/tag/title, overrides, fuzzy, exclusions."""
    allowed = set(s.lower() for s in allowed_sports)
    manual_overrides: dict[str, str] = {}  # market_id -> sport
    excluded_ids: set[str] = set()
    config_keywords: dict[str, list[str]] = {}
    if config is not None:
        for mo in config.manual_market_ids:
            manual_overrides[mo.market_id] = mo.sport
        excluded_ids = set(config.excluded_market_ids)
        config_keywords = config.sport_keywords
    keyword_map: dict[str, list[str]] = {
        sport: get_sport_keywords(config_keywords, sport) for sport in allowed
    }

    # Pass 1: Automated (slug / tag / title).
    results: list[SportsMarket] = []
    unmatched: list[Market] = []
    market_ids_in_api: set[str] = set()
    for market in markets:
        detected = _detect_sport(market.raw_data, allowed)
        mid = _market_key(market)
        market_ids_in_api.add(mid)

        if detected is not None:
            sport, method = detected
            token_id = _extract_token_id(market.raw_data)
            if not token_id:
                logger.debug("skipped_no_token_id", event_id=market.event_id)
                continue
            results.append(
                SportsMarket(
                    market=market,
                    sport=sport,
                    game_start_time=_extract_game_start(market.raw_data),
                    token_id=token_id,
                    classification_method=method,
                )
            )
        else:
            unmatched.append(market)

    # Pass 2: Manual overrides (only for markets not already matched).
    automated_ids = {_market_key(sm.market) for sm in results}
    overrides_applied = 0
    still_unmatched: list[Market] = []

    for market in unmatched:
        mid = _market_key(market)
        if mid in manual_overrides:
            sport = manual_overrides[mid]
            token_id = _extract_token_id(market.raw_data)
            if not token_id:
                logger.debug("skipped_no_token_id_override", event_id=market.event_id)
                still_unmatched.append(market)
                continue
            logger.info("manual_override_applied", market_id=mid, sport=sport)
            results.append(
                SportsMarket(
                    market=market,
                    sport=sport,
                    game_start_time=_extract_game_start(market.raw_data),
                    token_id=token_id,
                    classification_method="manual_override",
                )
            )
            overrides_applied += 1
        else:
            still_unmatched.append(market)
    for mid in manual_overrides:  # EC-002: warn for override IDs missing from API.
        if mid not in market_ids_in_api and mid not in automated_ids:
            logger.warning("override_market_not_found", market_id=mid, error_code="EC-002")
    # Pass 3: Fuzzy keyword matching.
    fuzzy_unmatched: list[Market] = []
    for market in still_unmatched:
        title = str(market.raw_data.get("groupItemTitle", ""))
        question = str(market.raw_data.get("question", ""))
        fuzzy_sport: str | None = fuzzy_match_sport(title, question, allowed, keyword_map)
        if fuzzy_sport is not None:
            token_id = _extract_token_id(market.raw_data)
            if not token_id:
                logger.debug("skipped_no_token_id_fuzzy", event_id=market.event_id)
                fuzzy_unmatched.append(market)
                continue
            results.append(
                SportsMarket(
                    market=market,
                    sport=fuzzy_sport,
                    game_start_time=_extract_game_start(market.raw_data),
                    token_id=token_id,
                    classification_method="fuzzy",
                )
            )
        else:
            fuzzy_unmatched.append(market)
    # Pass 4: Exclusion filter.
    exclusions_applied = 0
    filtered: list[SportsMarket] = []
    for sm in results:
        mid = _market_key(sm.market)
        if mid in excluded_ids:
            exclusions_applied += 1
        else:
            filtered.append(sm)
    by_sport: dict[str, int] = {}
    for sm in filtered:
        by_sport[sm.sport] = by_sport.get(sm.sport, 0) + 1
    total = len(markets)
    found = len(filtered)
    hit_rate = found / total if total > 0 else 0.0
    unclassified_top = [
        {
            "title": str(m.raw_data.get("groupItemTitle", m.title)),
            "slug": str(m.raw_data.get("groupSlug", "")),
        }
        for m in fuzzy_unmatched[:10]
    ]
    health = DiscoveryHealthSnapshot(
        total_scanned=total,
        sports_found=found,
        hit_rate=hit_rate,
        by_sport=by_sport,
        overrides_applied=overrides_applied,
        exclusions_applied=exclusions_applied,
        unclassified_candidates=len(fuzzy_unmatched),
        unclassified_sample=unclassified_top,
    )

    logger.info(
        "sports_classification_complete",
        total_markets=total,
        sports_markets=found,
        hit_rate=round(hit_rate, 4),
        by_sport=by_sport,
        overrides_applied=overrides_applied,
        exclusions_applied=exclusions_applied,
        unclassified_candidates=health.unclassified_candidates,
    )
    return filtered, health


def _should_alert(category: str, cooldown_minutes: int) -> bool:
    """Return True and record the time if cooldown has elapsed for category."""
    now = datetime.now(tz=UTC)
    last = _last_alert_time.get(category)
    if last is None or now - last >= timedelta(minutes=cooldown_minutes):
        _last_alert_time[category] = now
        return True
    return False


def check_degradation(
    current: DiscoveryHealthSnapshot,
    previous: DiscoveryHealthSnapshot | None,
    config: FlippeningConfig,
    allowed_sports: list[str],
) -> list[str]:
    """Return alert strings for detected discovery degradation."""
    if current.total_scanned == 0:  # EC-005: API returned nothing.
        return []
    alerts: list[str] = []
    cooldown = config.discovery_alert_cooldown_minutes
    if (
        current.hit_rate < config.min_hit_rate_pct
        and previous is not None
        and previous.hit_rate < config.min_hit_rate_pct
        and _should_alert("hit_rate_low", cooldown)
    ):
        alerts.append(
            f"Classification hit rate {current.hit_rate:.4f} below threshold "
            f"{config.min_hit_rate_pct} for 2 consecutive cycles"
        )
    if (
        current.sports_found == 0
        and previous is not None
        and previous.sports_found > 0
        and _should_alert("sports_zero_drop", cooldown)
    ):
        alerts.append(
            f"Sports market discovery dropped to 0 results (previous: {previous.sports_found})"
        )
    # Per-sport 3-cycle dropout: alert when a sport returns 0 for 3 cycles.
    for sport in allowed_sports:
        if current.by_sport.get(sport, 0) == 0:
            _sport_zero_count[sport] = _sport_zero_count.get(sport, 0) + 1
        else:
            _sport_zero_count[sport] = 0
        if _sport_zero_count[sport] >= 3 and _should_alert(f"sport_dropout_{sport}", cooldown):
            alerts.append(f"Sport '{sport}' returned 0 results for 3 consecutive cycles")
    return alerts


def _market_key(market: Market) -> str:
    """Return conditionId when available, else event_id."""
    cid = market.raw_data.get("conditionId")
    if isinstance(cid, str) and cid:
        return cid
    return market.event_id


def _detect_sport(raw_data: dict[str, object], allowed: set[str]) -> tuple[str, str] | None:
    """Return (sport, method) from groupSlug/tags/groupItemTitle, or None."""
    slug = str(raw_data.get("groupSlug", "")).lower()
    for sport in allowed:
        if slug.startswith(f"{sport}-") or slug == sport:
            return sport, "slug"

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
                    return sport, "tag"

    title = str(raw_data.get("groupItemTitle", "")).lower()
    for sport in allowed:
        if sport in title:
            return sport, "title"

    return None


def _extract_game_start(raw_data: dict[str, object]) -> datetime | None:
    """Return parsed game start datetime, or None."""
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
    """Return CLOB token ID, or empty string."""
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
