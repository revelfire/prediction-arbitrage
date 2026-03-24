"""Market discovery and categorization from Polymarket data."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import structlog

from arb_scanner.flippening.category_keywords import fuzzy_match_category, get_category_keywords
from arb_scanner.models.config import CategoryConfig, FlippeningConfig
from arb_scanner.models.flippening import CategoryMarket
from arb_scanner.models.market import Market

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.market_classifier",
)

_last_alert_time: dict[str, datetime] = {}
_category_zero_count: dict[str, int] = {}


@dataclasses.dataclass
class DiscoveryHealthSnapshot:
    """Health metrics from a single market classification run."""

    total_scanned: int
    markets_found: int
    hit_rate: float
    by_category: dict[str, int]
    by_category_type: dict[str, int]
    overrides_applied: int
    exclusions_applied: int
    unclassified_candidates: int
    unclassified_sample: list[dict[str, str]] = dataclasses.field(default_factory=list)


def classify_markets(
    markets: list[Market],
    categories: dict[str, CategoryConfig],
    config: FlippeningConfig | None = None,
) -> tuple[list[CategoryMarket], DiscoveryHealthSnapshot]:
    """Multi-pass classification: slug/tag/title, overrides, fuzzy, exclusions."""
    enabled = {cid: c for cid, c in categories.items() if c.enabled}
    manual_overrides: dict[str, str] = {}
    excluded_ids: set[str] = set()
    if config is not None:
        for mo in config.manual_market_ids:
            manual_overrides[mo.market_id] = mo.sport
        excluded_ids = set(config.excluded_market_ids)
    keyword_map: dict[str, list[str]] = {
        cid: get_category_keywords(cat, cid) for cid, cat in enabled.items()
    }

    results: list[CategoryMarket] = []
    unmatched: list[Market] = []
    market_ids_in_api: set[str] = set()
    for market in markets:
        detected = _detect_category(market.raw_data, enabled)
        mid = _market_key(market)
        market_ids_in_api.add(mid)
        if detected is not None:
            cat_id, method = detected
            yes_tid, no_tid = _extract_token_ids(market.raw_data)
            if not yes_tid:
                continue
            cat_cfg = enabled[cat_id]
            results.append(_build_category_market(market, cat_id, cat_cfg, yes_tid, method, no_tid))
        else:
            unmatched.append(market)

    automated_ids = {_market_key(sm.market) for sm in results}
    overrides_applied = 0
    still_unmatched: list[Market] = []
    for market in unmatched:
        mid = _market_key(market)
        if mid in manual_overrides and manual_overrides[mid] in enabled:
            cat_id = manual_overrides[mid]
            yes_tid, no_tid = _extract_token_ids(market.raw_data)
            if not yes_tid:
                still_unmatched.append(market)
                continue
            cat_cfg = enabled[cat_id]
            results.append(
                _build_category_market(market, cat_id, cat_cfg, yes_tid, "manual_override", no_tid)
            )
            overrides_applied += 1
        else:
            still_unmatched.append(market)
    for mid in manual_overrides:
        if mid not in market_ids_in_api and mid not in automated_ids:
            logger.warning("override_market_not_found", market_id=mid, error_code="EC-002")

    fuzzy_unmatched: list[Market] = []
    for market in still_unmatched:
        title = str(market.raw_data.get("groupItemTitle", ""))
        question = str(market.raw_data.get("question", ""))
        fuzzy_cat = fuzzy_match_category(title, question, enabled, keyword_map)
        if fuzzy_cat is not None:
            yes_tid, no_tid = _extract_token_ids(market.raw_data)
            if not yes_tid:
                fuzzy_unmatched.append(market)
                continue
            cat_cfg = enabled[fuzzy_cat]
            results.append(
                _build_category_market(market, fuzzy_cat, cat_cfg, yes_tid, "fuzzy", no_tid)
            )
        else:
            fuzzy_unmatched.append(market)

    exclusions_applied = 0
    filtered: list[CategoryMarket] = []
    for sm in results:
        mid = _market_key(sm.market)
        if mid in excluded_ids:
            exclusions_applied += 1
        else:
            filtered.append(sm)

    by_category, by_category_type = _compute_breakdowns(filtered)
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
        markets_found=found,
        hit_rate=hit_rate,
        by_category=by_category,
        by_category_type=by_category_type,
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
        by_sport=by_category,
        overrides_applied=overrides_applied,
        exclusions_applied=exclusions_applied,
        unclassified_candidates=health.unclassified_candidates,
    )
    return filtered, health


def _build_category_market(
    market: Market,
    cat_id: str,
    cat_cfg: CategoryConfig,
    token_id: str,
    method: str,
    no_token_id: str = "",
) -> CategoryMarket:
    """Build a CategoryMarket from discovery results."""
    return CategoryMarket(
        market=market,
        sport=cat_id,
        category=cat_id,
        category_type=cat_cfg.category_type,
        game_start_time=_extract_game_start(market.raw_data),
        token_id=token_id,
        no_token_id=no_token_id,
        classification_method=method,
    )


def _compute_breakdowns(filtered: list[CategoryMarket]) -> tuple[dict[str, int], dict[str, int]]:
    """Compute by_category and by_category_type counts."""
    by_cat: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for sm in filtered:
        by_cat[sm.category] = by_cat.get(sm.category, 0) + 1
        by_type[sm.category_type] = by_type.get(sm.category_type, 0) + 1
    return by_cat, by_type


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
    categories: dict[str, CategoryConfig],
) -> list[str]:
    """Return alert strings for detected discovery degradation."""
    if current.total_scanned == 0:
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
        current.markets_found == 0
        and previous is not None
        and previous.markets_found > 0
        and _should_alert("markets_zero_drop", cooldown)
    ):
        alerts.append(f"Market discovery dropped to 0 results (previous: {previous.markets_found})")
    for cat_id in categories:
        if current.by_category.get(cat_id, 0) == 0:
            _category_zero_count[cat_id] = _category_zero_count.get(cat_id, 0) + 1
        else:
            _category_zero_count[cat_id] = 0
        if _category_zero_count[cat_id] >= 3 and _should_alert(f"cat_dropout_{cat_id}", cooldown):
            alerts.append(f"Category '{cat_id}' returned 0 results for 3 consecutive cycles")
    return alerts


def _market_key(market: Market) -> str:
    """Return conditionId when available, else event_id."""
    cid = market.raw_data.get("conditionId")
    if isinstance(cid, str) and cid:
        return cid
    return market.event_id


def _detect_category(
    raw_data: dict[str, object],
    categories: dict[str, CategoryConfig],
) -> tuple[str, str] | None:
    """Return (category_id, method) from slug/tag/title, or None."""
    slug = str(raw_data.get("groupSlug") or raw_data.get("slug") or "").lower()
    for cat_id, cat_cfg in sorted(categories.items()):
        slugs = cat_cfg.discovery_slugs or [f"{cat_id}-"]
        for prefix in slugs:
            if slug.startswith(prefix) or slug == cat_id:
                return cat_id, "slug"

    tags = raw_data.get("tags", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    if isinstance(tags, list):
        for tag in tags:
            tag_lower = str(tag).lower()
            for cat_id, cat_cfg in sorted(categories.items()):
                tag_patterns = cat_cfg.discovery_tags or [cat_id]
                for pat in tag_patterns:
                    if pat in tag_lower:
                        return cat_id, "tag"

    title = str(raw_data.get("groupItemTitle", "")).lower()
    for cat_id in sorted(categories):
        if cat_id in title:
            return cat_id, "title"
    return None


def _extract_game_start(raw_data: dict[str, object]) -> datetime | None:
    """Return parsed game start datetime, or None."""
    for field in ("startDate", "game_start_time", "startDateIso"):
        value = raw_data.get(field)
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
    return None


def _extract_token_id(raw_data: dict[str, object]) -> str:
    """Return YES CLOB token ID, or empty string."""
    yes_id, _no_id = _extract_token_ids(raw_data)
    return yes_id


def _extract_token_ids(raw_data: dict[str, object]) -> tuple[str, str]:
    """Return (yes_token_id, no_token_id) from Polymarket raw data.

    Args:
        raw_data: Raw market payload with clobTokenIds.

    Returns:
        Tuple of (yes_token_id, no_token_id). Either may be empty.
    """
    clob_ids = raw_data.get("clobTokenIds")
    tokens: list[str] = []
    if isinstance(clob_ids, str):
        try:
            parsed: list[str] = json.loads(clob_ids)
            tokens = [str(x) for x in parsed if x]
        except (json.JSONDecodeError, TypeError):
            pass
    elif isinstance(clob_ids, list):
        tokens = [str(x) for x in clob_ids if x]
    if len(tokens) >= 2:
        return tokens[0], tokens[1]
    if len(tokens) == 1:
        return tokens[0], ""
    condition_id = raw_data.get("conditionId", "")
    cid = str(condition_id) if condition_id else ""
    return cid, ""
