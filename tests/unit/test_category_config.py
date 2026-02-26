"""Tests for CategoryConfig model and FlippeningConfig auto-migration validator."""

from __future__ import annotations

import pytest

from arb_scanner.models.config import (
    CategoryConfig,
    FlippeningConfig,
    SportOverride,
)


class TestCategoryConfigValidation:
    """CategoryConfig validates category_type and baseline_strategy."""

    def test_valid_sport_category(self) -> None:
        cfg = CategoryConfig(category_type="sport", baseline_strategy="first_price")
        assert cfg.category_type == "sport"
        assert cfg.baseline_strategy == "first_price"

    def test_valid_crypto_rolling_window(self) -> None:
        cfg = CategoryConfig(
            category_type="crypto",
            baseline_strategy="rolling_window",
            baseline_window_minutes=30,
            discovery_keywords=["bitcoin", "btc"],
        )
        assert cfg.category_type == "crypto"
        assert cfg.baseline_strategy == "rolling_window"
        assert cfg.baseline_window_minutes == 30

    def test_valid_entertainment_pre_event(self) -> None:
        cfg = CategoryConfig(
            category_type="entertainment",
            baseline_strategy="pre_event_snapshot",
            event_window_hours=5.0,
        )
        assert cfg.baseline_strategy == "pre_event_snapshot"
        assert cfg.event_window_hours == 5.0

    def test_all_valid_category_types(self) -> None:
        for ct in ("sport", "entertainment", "politics", "crypto", "economics", "corporate"):
            cfg = CategoryConfig(category_type=ct)
            assert cfg.category_type == ct

    def test_invalid_category_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="category_type must be one of"):
            CategoryConfig(category_type="invalid")

    def test_invalid_baseline_strategy_rejected(self) -> None:
        with pytest.raises(ValueError, match="baseline_strategy must be one of"):
            CategoryConfig(baseline_strategy="invalid")

    def test_defaults(self) -> None:
        cfg = CategoryConfig()
        assert cfg.enabled is True
        assert cfg.confidence_modifier == 1.0
        assert cfg.spike_threshold_pct is None
        assert cfg.min_confidence is None
        assert cfg.max_hold_minutes is None
        assert cfg.discovery_keywords == []
        assert cfg.discovery_slugs == []
        assert cfg.discovery_tags == []


class TestFlippeningConfigAutoMigration:
    """FlippeningConfig auto-converts legacy sports list to categories."""

    def test_empty_sports_and_categories(self) -> None:
        cfg = FlippeningConfig(sports=[], categories={})
        assert cfg.categories == {}

    def test_sports_list_generates_categories(self) -> None:
        cfg = FlippeningConfig(sports=["nba", "nhl"], categories={})
        assert "nba" in cfg.categories
        assert "nhl" in cfg.categories
        assert cfg.categories["nba"].category_type == "sport"
        assert cfg.categories["nba"].baseline_strategy == "first_price"

    def test_sport_overrides_merged_into_categories(self) -> None:
        cfg = FlippeningConfig(
            sports=["nfl"],
            sport_overrides={
                "nfl": SportOverride(spike_threshold_pct=0.12, confidence_modifier=1.1)
            },
            categories={},
        )
        assert cfg.categories["nfl"].spike_threshold_pct == 0.12
        assert cfg.categories["nfl"].confidence_modifier == 1.1

    def test_sport_keywords_merged_into_categories(self) -> None:
        cfg = FlippeningConfig(
            sports=["nba"],
            sport_keywords={"nba": ["lakers", "celtics"]},
            categories={},
        )
        assert cfg.categories["nba"].discovery_keywords == ["lakers", "celtics"]

    def test_categories_takes_precedence_over_sports(self) -> None:
        cfg = FlippeningConfig(
            sports=["nba"],
            categories={"btc": CategoryConfig(category_type="crypto")},
        )
        assert "btc" in cfg.categories
        assert "nba" not in cfg.categories

    def test_discovery_slugs_auto_generated_for_sports(self) -> None:
        cfg = FlippeningConfig(sports=["epl"], categories={})
        assert cfg.categories["epl"].discovery_slugs == ["epl-"]

    def test_min_confidence_from_sport_override(self) -> None:
        cfg = FlippeningConfig(
            sports=["nba"],
            sport_overrides={"nba": SportOverride(min_confidence=0.7)},
            categories={},
        )
        assert cfg.categories["nba"].min_confidence == 0.7

    def test_default_sports_list_generates_six_categories(self) -> None:
        cfg = FlippeningConfig()
        assert len(cfg.categories) == 6
        for sport in ("nba", "nhl", "nfl", "mlb", "epl", "ufc"):
            assert sport in cfg.categories
            assert cfg.categories[sport].category_type == "sport"


class TestCategoryConfigOverlay:
    """Category-specific values overlay global FlippeningConfig defaults."""

    def test_spike_threshold_overlay(self) -> None:
        cfg = FlippeningConfig(
            spike_threshold_pct=0.15,
            categories={"nfl": CategoryConfig(category_type="sport", spike_threshold_pct=0.12)},
        )
        nfl = cfg.categories["nfl"]
        assert nfl.spike_threshold_pct == 0.12
        assert cfg.spike_threshold_pct == 0.15

    def test_none_means_use_global(self) -> None:
        cfg = FlippeningConfig(
            spike_threshold_pct=0.15,
            categories={"nba": CategoryConfig(category_type="sport")},
        )
        assert cfg.categories["nba"].spike_threshold_pct is None

    def test_mixed_categories(self) -> None:
        cfg = FlippeningConfig(
            categories={
                "nba": CategoryConfig(category_type="sport"),
                "btc": CategoryConfig(
                    category_type="crypto",
                    baseline_strategy="rolling_window",
                    spike_threshold_pct=0.10,
                ),
            },
        )
        assert cfg.categories["nba"].baseline_strategy == "first_price"
        assert cfg.categories["btc"].baseline_strategy == "rolling_window"
        assert cfg.categories["btc"].spike_threshold_pct == 0.10
