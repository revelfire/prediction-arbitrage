"""Tests for market_classifier: category-based market classification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arb_scanner.flippening.category_keywords import fuzzy_match_category, get_category_keywords
from arb_scanner.flippening.market_classifier import (
    DiscoveryHealthSnapshot,
    _category_zero_count,
    _detect_category,
    _extract_game_start,
    _extract_token_id,
    _last_alert_time,
    _should_alert,
    check_degradation,
    classify_markets,
)
from arb_scanner.models.config import CategoryConfig, FlippeningConfig
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=UTC)


def _market(
    event_id: str = "ev1",
    title: str = "Test Market",
    slug: str = "",
    tags: object = None,
    clob_ids: str = '["tok1"]',
    condition_id: str = "cond1",
    group_title: str = "",
) -> Market:
    raw: dict[str, object] = {
        "groupSlug": slug,
        "groupItemTitle": group_title or title,
        "clobTokenIds": clob_ids,
        "conditionId": condition_id,
    }
    if tags is not None:
        raw["tags"] = tags
    return Market(
        venue=Venue.POLYMARKET,
        event_id=event_id,
        title=title,
        description="Test market description",
        resolution_criteria="Official result",
        yes_bid=Decimal("0.50"),
        yes_ask=Decimal("0.52"),
        no_bid=Decimal("0.47"),
        no_ask=Decimal("0.49"),
        volume_24h=Decimal("10000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
        raw_data=raw,
    )


def _cats(**overrides: CategoryConfig) -> dict[str, CategoryConfig]:
    base = {
        "nba": CategoryConfig(category_type="sport", discovery_slugs=["nba-"]),
        "nhl": CategoryConfig(category_type="sport", discovery_slugs=["nhl-"]),
        "cbb": CategoryConfig(category_type="sport", discovery_slugs=["cbb-"]),
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    _last_alert_time.clear()
    _category_zero_count.clear()


class TestDetectCategory:
    """_detect_category finds categories by slug, tag, and title."""

    def test_slug_match(self) -> None:
        cats = _cats()
        raw: dict[str, object] = {"groupSlug": "nba-lakers-celtics"}
        assert _detect_category(raw, cats) == ("nba", "slug")

    def test_tag_match(self) -> None:
        cats = _cats()
        raw: dict[str, object] = {"groupSlug": "some-other", "tags": ["NBA", "Basketball"]}
        assert _detect_category(raw, cats) == ("nba", "tag")

    def test_title_match(self) -> None:
        cats = _cats()
        raw: dict[str, object] = {"groupSlug": "x", "groupItemTitle": "Will the nba game..."}
        assert _detect_category(raw, cats) == ("nba", "title")

    def test_no_match(self) -> None:
        cats = _cats()
        raw: dict[str, object] = {"groupSlug": "politics-election", "groupItemTitle": "Election"}
        assert _detect_category(raw, cats) is None

    def test_custom_discovery_slugs(self) -> None:
        cats = {"btc": CategoryConfig(category_type="crypto", discovery_slugs=["bitcoin-", "btc-"])}
        raw: dict[str, object] = {"groupSlug": "btc-price-100k"}
        assert _detect_category(raw, cats) == ("btc", "slug")

    def test_cbb_slug_match(self) -> None:
        cats = _cats()
        raw: dict[str, object] = {"groupSlug": "cbb-stjohn-duke-2026-03-27-total-141pt5"}
        assert _detect_category(raw, cats) == ("cbb", "slug")

    def test_custom_discovery_tags(self) -> None:
        cats = {
            "oscars": CategoryConfig(
                category_type="entertainment", discovery_tags=["academy awards"]
            )
        }
        raw: dict[str, object] = {"groupSlug": "x", "tags": ["Academy Awards 2026"]}
        assert _detect_category(raw, cats) == ("oscars", "tag")

    def test_alphabetical_tiebreak(self) -> None:
        """EC-002: First match in sorted order wins."""
        cats = {
            "aaa": CategoryConfig(category_type="sport", discovery_slugs=["game-"]),
            "zzz": CategoryConfig(category_type="sport", discovery_slugs=["game-"]),
        }
        raw: dict[str, object] = {"groupSlug": "game-123"}
        result = _detect_category(raw, cats)
        assert result is not None
        assert result[0] == "aaa"


class TestClassifyMarkets:
    """classify_markets returns CategoryMarket list and health snapshot."""

    def test_basic_classification(self) -> None:
        markets = [_market(slug="nba-lakers-celtics")]
        cats = _cats()
        results, health = classify_markets(markets, cats)
        assert len(results) == 1
        assert results[0].category == "nba"
        assert results[0].category_type == "sport"
        assert results[0].sport == "nba"
        assert health.markets_found == 1
        assert health.by_category == {"nba": 1}
        assert health.by_category_type == {"sport": 1}

    def test_fuzzy_keyword_match(self) -> None:
        cats = {
            "nba": CategoryConfig(
                category_type="sport",
                discovery_slugs=["nba-"],
                discovery_keywords=["lakers"],
            )
        }
        markets = [_market(slug="x", group_title="Will the lakers win?")]
        results, health = classify_markets(markets, cats)
        assert len(results) == 1
        assert results[0].classification_method == "fuzzy"

    def test_excluded_markets_filtered(self) -> None:
        config = FlippeningConfig(
            categories=_cats(),
            excluded_market_ids=["cond1"],
        )
        markets = [_market(slug="nba-game")]
        results, health = classify_markets(markets, config.categories, config)
        assert len(results) == 0
        assert health.exclusions_applied == 1

    def test_health_snapshot_fields(self) -> None:
        markets = [_market(slug="nba-1"), _market(event_id="ev2", slug="other", condition_id="c2")]
        cats = _cats()
        _, health = classify_markets(markets, cats)
        assert health.total_scanned == 2
        assert health.markets_found == 1
        assert health.unclassified_candidates == 1
        assert health.hit_rate == 0.5

    def test_disabled_category_skipped(self) -> None:
        cats = {
            "nba": CategoryConfig(category_type="sport", enabled=False, discovery_slugs=["nba-"])
        }
        markets = [_market(slug="nba-game")]
        results, _ = classify_markets(markets, cats)
        assert len(results) == 0

    def test_non_sport_category_discovery(self) -> None:
        cats = {
            "btc": CategoryConfig(
                category_type="crypto",
                discovery_slugs=["btc-"],
                discovery_keywords=["bitcoin"],
            )
        }
        markets = [_market(slug="btc-price-100k")]
        results, health = classify_markets(markets, cats)
        assert len(results) == 1
        assert results[0].category == "btc"
        assert results[0].category_type == "crypto"
        assert health.by_category_type == {"crypto": 1}


class TestCheckDegradation:
    """check_degradation alerts on classification issues."""

    def _snap(self, **kwargs: object) -> DiscoveryHealthSnapshot:
        defaults: dict[str, object] = {
            "total_scanned": 100,
            "markets_found": 5,
            "hit_rate": 0.05,
            "by_category": {},
            "by_category_type": {},
            "overrides_applied": 0,
            "exclusions_applied": 0,
            "unclassified_candidates": 0,
        }
        defaults.update(kwargs)
        return DiscoveryHealthSnapshot(**defaults)  # type: ignore[arg-type]

    def _cfg(self, **kwargs: object) -> FlippeningConfig:
        defaults: dict[str, object] = {
            "min_hit_rate_pct": 0.01,
            "discovery_alert_cooldown_minutes": 60,
        }
        defaults.update(kwargs)
        return FlippeningConfig(**defaults)  # type: ignore[arg-type]

    def test_hit_rate_alert_two_consecutive(self) -> None:
        cfg = self._cfg(min_hit_rate_pct=0.05)
        cats = _cats()
        prev = self._snap(hit_rate=0.01)
        cur = self._snap(hit_rate=0.02)
        alerts = check_degradation(cur, prev, cfg, cats)
        assert any("2 consecutive cycles" in a for a in alerts)

    def test_zero_drop_alert(self) -> None:
        cfg = self._cfg()
        cats = _cats()
        prev = self._snap(markets_found=10)
        cur = self._snap(markets_found=0, hit_rate=0.0)
        alerts = check_degradation(cur, prev, cfg, cats)
        assert any("dropped to 0" in a for a in alerts)

    def test_per_category_dropout_three_cycles(self) -> None:
        cfg = self._cfg()
        cats = _cats()
        snap = self._snap(by_category={"nhl": 2})
        for _ in range(2):
            check_degradation(snap, None, cfg, cats)
        alerts = check_degradation(snap, None, cfg, cats)
        assert any("'nba'" in a and "3 consecutive" in a for a in alerts)

    def test_ec005_empty_api(self) -> None:
        cfg = self._cfg()
        cats = _cats()
        cur = self._snap(total_scanned=0, markets_found=0, hit_rate=0.0)
        alerts = check_degradation(cur, None, cfg, cats)
        assert alerts == []


class TestExtractHelpers:
    """Token ID and game start extraction."""

    def test_extract_token_id_from_json_string(self) -> None:
        assert _extract_token_id({"clobTokenIds": '["tok_abc"]'}) == "tok_abc"

    def test_extract_token_id_from_list(self) -> None:
        assert _extract_token_id({"clobTokenIds": ["tok_abc"]}) == "tok_abc"

    def test_extract_token_id_fallback_condition(self) -> None:
        assert _extract_token_id({"conditionId": "cond123"}) == "cond123"

    def test_extract_game_start_iso(self) -> None:
        result = _extract_game_start({"startDate": "2026-03-01T20:00:00Z"})
        assert result is not None
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_extract_game_start_prefers_game_start_time(self) -> None:
        result = _extract_game_start(
            {
                "startDate": "2026-03-25T15:04:27.521592Z",
                "gameStartTime": "2026-03-26 23:30:00+00",
            }
        )
        assert result is not None
        assert result == datetime(2026, 3, 26, 23, 30, tzinfo=UTC)

    def test_extract_game_start_from_nested_event(self) -> None:
        result = _extract_game_start(
            {
                "events": [
                    {
                        "startTime": "2026-03-26T23:30:00Z",
                    }
                ]
            }
        )
        assert result is not None
        assert result == datetime(2026, 3, 26, 23, 30, tzinfo=UTC)

    def test_extract_game_start_date_only_defaults_to_utc(self) -> None:
        result = _extract_game_start({"startDateIso": "2026-03-01"})
        assert result is not None
        assert result == datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

    def test_extract_game_start_none(self) -> None:
        assert _extract_game_start({}) is None


class TestRateLimiting:
    """_should_alert suppresses duplicate alerts within cooldown."""

    def test_first_call_fires(self) -> None:
        assert _should_alert("test", 60) is True

    def test_second_within_cooldown_suppressed(self) -> None:
        _should_alert("test", 60)
        assert _should_alert("test", 60) is False

    def test_after_cooldown_fires(self) -> None:
        _last_alert_time["test"] = datetime.now(tz=UTC) - timedelta(minutes=61)
        assert _should_alert("test", 60) is True


class TestFuzzyAmbiguousKeywords:
    """Ambiguous surnames removed from default keywords to prevent misclassification."""

    def test_soccer_team_not_classified_as_ufc(self) -> None:
        """Deportivo Pereira (soccer) must not match UFC keywords."""
        cats = {
            "ufc": CategoryConfig(category_type="sport", discovery_slugs=["ufc-"]),
        }
        keyword_map = {cid: get_category_keywords(c, cid) for cid, c in cats.items()}
        result = fuzzy_match_category(
            "Millonarios FC vs. Deportivo Pereira: O/U 1.5",
            "",
            cats,
            keyword_map,
        )
        assert result is None

    def test_ufc_slug_still_matches(self) -> None:
        """UFC markets with explicit slugs still classify correctly."""
        cats = {
            "ufc": CategoryConfig(category_type="sport", discovery_slugs=["ufc-"]),
        }
        raw: dict[str, object] = {"groupSlug": "ufc-309-pereira-vs-adesanya"}
        assert _detect_category(raw, cats) == ("ufc", "slug")

    def test_explicit_ufc_keyword_still_matches(self) -> None:
        """Markets with 'ufc' in the title still match via keyword."""
        cats = {
            "ufc": CategoryConfig(category_type="sport", discovery_slugs=["ufc-"]),
        }
        keyword_map = {cid: get_category_keywords(c, cid) for cid, c in cats.items()}
        result = fuzzy_match_category(
            "UFC 309: Main Card Predictions",
            "",
            cats,
            keyword_map,
        )
        assert result == "ufc"
