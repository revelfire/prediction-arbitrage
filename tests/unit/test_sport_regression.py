"""SC-001 regression: all 6 sports function identically after category refactor."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from arb_scanner.flippening.market_classifier import classify_markets
from arb_scanner.models.config import CategoryConfig, FlippeningConfig
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=UTC)


def _market(slug: str, clob_ids: str = '["tok1"]', condition_id: str = "") -> Market:
    cid = condition_id or slug
    raw: dict[str, object] = {
        "groupSlug": slug,
        "groupItemTitle": slug,
        "clobTokenIds": clob_ids,
        "conditionId": cid,
    }
    return Market(
        venue=Venue.POLYMARKET,
        event_id=slug,
        title=slug,
        description="Test market",
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


def _legacy_config() -> FlippeningConfig:
    """Build config using legacy sports list (auto-migrates to categories)."""
    return FlippeningConfig(
        sports=["nba", "nhl", "nfl", "mlb", "epl", "ufc"],
        categories={},
    )


def _explicit_config() -> FlippeningConfig:
    """Build config using explicit categories (new format)."""
    return FlippeningConfig(
        categories={
            sport: CategoryConfig(
                category_type="sport",
                baseline_strategy="first_price",
                discovery_slugs=[f"{sport}-"],
            )
            for sport in ("nba", "nhl", "nfl", "mlb", "epl", "ufc")
        },
    )


class TestSportRegressionSlugDiscovery:
    """All 6 sports discovered by slug in both legacy and explicit config."""

    SPORT_SLUGS = [
        ("nba", "nba-lakers-celtics"),
        ("nhl", "nhl-bruins-rangers"),
        ("nfl", "nfl-chiefs-eagles"),
        ("mlb", "mlb-yankees-dodgers"),
        ("epl", "epl-arsenal-chelsea"),
        ("ufc", "ufc-fight-night-300"),
    ]

    def test_legacy_config_discovers_all_sports(self) -> None:
        cfg = _legacy_config()
        markets = [_market(slug, condition_id=f"cond_{sport}") for sport, slug in self.SPORT_SLUGS]
        results, health = classify_markets(markets, cfg.categories, cfg)
        assert health.markets_found == 6
        found_cats = {r.category for r in results}
        assert found_cats == {"nba", "nhl", "nfl", "mlb", "epl", "ufc"}

    def test_explicit_config_discovers_all_sports(self) -> None:
        cfg = _explicit_config()
        markets = [_market(slug, condition_id=f"cond_{sport}") for sport, slug in self.SPORT_SLUGS]
        results, health = classify_markets(markets, cfg.categories, cfg)
        assert health.markets_found == 6
        found_cats = {r.category for r in results}
        assert found_cats == {"nba", "nhl", "nfl", "mlb", "epl", "ufc"}

    def test_legacy_and_explicit_produce_same_categories(self) -> None:
        markets = [_market(slug, condition_id=f"cond_{sport}") for sport, slug in self.SPORT_SLUGS]
        legacy_results, _ = classify_markets(markets, _legacy_config().categories, _legacy_config())
        explicit_results, _ = classify_markets(
            markets,
            _explicit_config().categories,
            _explicit_config(),
        )
        legacy_cats = sorted(r.category for r in legacy_results)
        explicit_cats = sorted(r.category for r in explicit_results)
        assert legacy_cats == explicit_cats


class TestSportRegressionFields:
    """CategoryMarket fields are correctly populated for sports."""

    def test_sport_equals_category(self) -> None:
        cfg = _legacy_config()
        markets = [_market("nba-game")]
        results, _ = classify_markets(markets, cfg.categories, cfg)
        assert len(results) == 1
        assert results[0].sport == "nba"
        assert results[0].category == "nba"
        assert results[0].category_type == "sport"

    def test_classification_method_slug(self) -> None:
        cfg = _legacy_config()
        markets = [_market("nhl-game")]
        results, _ = classify_markets(markets, cfg.categories, cfg)
        assert results[0].classification_method == "slug"

    def test_health_snapshot_backward_compat(self) -> None:
        """by_category keys match the old by_sport keys for sport categories."""
        cfg = _legacy_config()
        markets = [
            _market("nba-g1", condition_id="c1"),
            _market("nba-g2", condition_id="c2"),
            _market("nhl-g1", condition_id="c3"),
        ]
        _, health = classify_markets(markets, cfg.categories, cfg)
        assert health.by_category["nba"] == 2
        assert health.by_category["nhl"] == 1
