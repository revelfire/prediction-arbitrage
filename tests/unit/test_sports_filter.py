"""Tests for sports market discovery and classification."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from arb_scanner.flippening.sports_filter import (
    DiscoveryHealthSnapshot,
    _detect_sport,
    _extract_game_start,
    _extract_token_id,
    classify_sports_markets,
)
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=UTC)
_ALLOWED = ["nba", "nhl", "nfl", "epl"]


def _market(
    event_id: str = "m1",
    raw: dict[str, object] | None = None,
) -> Market:
    return Market(
        venue=Venue.POLYMARKET,
        event_id=event_id,
        title="Test Market",
        description="desc",
        resolution_criteria="criteria",
        yes_bid=Decimal("0.65"),
        yes_ask=Decimal("0.67"),
        no_bid=Decimal("0.32"),
        no_ask=Decimal("0.34"),
        volume_24h=Decimal("10000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
        raw_data=raw or {},
    )


class TestDetectSport:
    """Tests for _detect_sport."""

    def test_matches_group_slug_prefix(self) -> None:
        """NBA slug prefix is detected with method 'slug'."""
        result = _detect_sport(
            {"groupSlug": "nba-lakers-vs-celtics"},
            set(_ALLOWED),
        )
        assert result == ("nba", "slug")

    def test_matches_nfl_slug(self) -> None:
        """NFL slug prefix is detected with method 'slug'."""
        result = _detect_sport(
            {"groupSlug": "nfl-super-bowl"},
            set(_ALLOWED),
        )
        assert result == ("nfl", "slug")

    def test_no_match_returns_none(self) -> None:
        """Non-sports slug returns None."""
        result = _detect_sport(
            {"groupSlug": "politics-2026"},
            set(_ALLOWED),
        )
        assert result is None

    def test_falls_back_to_tags(self) -> None:
        """Falls back to tags when slug doesn't match, method is 'tag'."""
        result = _detect_sport(
            {"groupSlug": "other", "tags": ["NBA", "Basketball"]},
            set(_ALLOWED),
        )
        assert result == ("nba", "tag")

    def test_falls_back_to_tags_json_string(self) -> None:
        """Parses JSON string tags, method is 'tag'."""
        result = _detect_sport(
            {"groupSlug": "other", "tags": '["NHL", "Hockey"]'},
            set(_ALLOWED),
        )
        assert result == ("nhl", "tag")

    def test_falls_back_to_group_item_title(self) -> None:
        """Falls back to groupItemTitle keywords, method is 'title'."""
        result = _detect_sport(
            {
                "groupSlug": "sports",
                "tags": [],
                "groupItemTitle": "EPL: Arsenal vs Chelsea",
            },
            set(_ALLOWED),
        )
        assert result == ("epl", "title")

    def test_respects_allowed_filter(self) -> None:
        """Ignores sports not in the allowed list."""
        result = _detect_sport(
            {"groupSlug": "cricket-test-match"},
            set(_ALLOWED),  # cricket not in _ALLOWED
        )
        assert result is None


class TestExtractGameStart:
    """Tests for _extract_game_start."""

    def test_parses_iso_datetime(self) -> None:
        """Parses a startDate ISO string."""
        result = _extract_game_start(
            {"startDate": "2026-02-25T19:30:00Z"},
        )
        assert result is not None
        assert result.year == 2026

    def test_returns_none_for_missing(self) -> None:
        """Returns None when no start time fields exist."""
        result = _extract_game_start({})
        assert result is None

    def test_returns_none_for_invalid(self) -> None:
        """Returns None for unparseable values."""
        result = _extract_game_start({"startDate": "not-a-date"})
        assert result is None


class TestExtractTokenId:
    """Tests for _extract_token_id."""

    def test_parses_clob_token_ids_json(self) -> None:
        """Parses clobTokenIds JSON string."""
        result = _extract_token_id(
            {"clobTokenIds": '["tok-abc", "tok-def"]'},
        )
        assert result == "tok-abc"

    def test_parses_clob_token_ids_list(self) -> None:
        """Parses clobTokenIds as a list."""
        result = _extract_token_id(
            {"clobTokenIds": ["tok-xyz"]},
        )
        assert result == "tok-xyz"

    def test_falls_back_to_condition_id(self) -> None:
        """Falls back to conditionId."""
        result = _extract_token_id(
            {"conditionId": "cond-123"},
        )
        assert result == "cond-123"

    def test_empty_when_nothing_available(self) -> None:
        """Returns empty string when no token info."""
        result = _extract_token_id({})
        assert result == ""


class TestClassifySportsMarkets:
    """Tests for classify_sports_markets."""

    def test_classifies_nba_market(self) -> None:
        """NBA market is classified correctly and health snapshot is valid."""
        m = _market(
            raw={"groupSlug": "nba-game", "clobTokenIds": '["t1"]'},
        )
        markets, health = classify_sports_markets([m], _ALLOWED)
        assert len(markets) == 1
        assert markets[0].sport == "nba"
        assert markets[0].classification_method == "slug"
        assert isinstance(health, DiscoveryHealthSnapshot)
        assert health.total_scanned == 1
        assert health.sports_found == 1
        assert health.hit_rate == 1.0
        assert health.by_sport == {"nba": 1}
        assert health.overrides_applied == 0
        assert health.exclusions_applied == 0

    def test_ignores_non_sports(self) -> None:
        """Non-sports market is excluded and health reflects zero found."""
        m = _market(raw={"groupSlug": "politics"})
        markets, health = classify_sports_markets([m], _ALLOWED)
        assert len(markets) == 0
        assert health.sports_found == 0
        assert health.hit_rate == 0.0

    def test_skips_market_without_token_id(self) -> None:
        """Market without token ID is skipped even when sport is detected."""
        m = _market(raw={"groupSlug": "nba-game"})
        markets, health = classify_sports_markets([m], _ALLOWED)
        assert len(markets) == 0

    def test_returns_tuple(self) -> None:
        """Return value is a two-element tuple."""
        result = classify_sports_markets([], _ALLOWED)
        assert isinstance(result, tuple)
        assert len(result) == 2
        markets, health = result
        assert isinstance(markets, list)
        assert isinstance(health, DiscoveryHealthSnapshot)
        assert health.total_scanned == 0
        assert health.hit_rate == 0.0
