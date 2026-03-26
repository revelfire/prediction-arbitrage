"""T017 - Comprehensive unit tests for all Pydantic models."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.config import (
    ArbThresholds,
    ClaudeConfig,
    FeeSchedule,
    FeesConfig,
    KalshiVenueConfig,
    LoggingConfig,
    NotificationConfig,
    PolymarketVenueConfig,
    ScanConfig,
    Settings,
    StorageConfig,
    TrendAlertConfig,
    VenuesConfig,
)
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult
from arb_scanner.models.scan_log import ScanLog

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Venue enum
# ---------------------------------------------------------------------------


class TestVenue:
    """Tests for the Venue enum."""

    def test_polymarket_value(self) -> None:
        """Verify POLYMARKET enum value."""
        assert Venue.POLYMARKET.value == "polymarket"

    def test_kalshi_value(self) -> None:
        """Verify KALSHI enum value."""
        assert Venue.KALSHI.value == "kalshi"

    def test_venue_is_str_enum(self) -> None:
        """Verify Venue members are str instances."""
        assert isinstance(Venue.POLYMARKET, str)
        assert isinstance(Venue.KALSHI, str)

    def test_venue_members_count(self) -> None:
        """Verify exactly two venue members exist."""
        assert len(Venue) == 2


# ---------------------------------------------------------------------------
# Market model
# ---------------------------------------------------------------------------


def _make_market(**overrides: object) -> Market:
    """Build a Market with sensible defaults, applying overrides."""
    defaults: dict[str, object] = {
        "venue": Venue.POLYMARKET,
        "event_id": "evt-1",
        "title": "Test market",
        "description": "desc",
        "resolution_criteria": "criteria",
        "yes_bid": Decimal("0.40"),
        "yes_ask": Decimal("0.45"),
        "no_bid": Decimal("0.50"),
        "no_ask": Decimal("0.55"),
        "volume_24h": Decimal("1000"),
        "fees_pct": Decimal("0.00"),
        "fee_model": "on_winnings",
        "last_updated": _NOW,
    }
    defaults.update(overrides)
    return Market(**defaults)  # type: ignore[arg-type]


class TestMarketValid:
    """Tests for valid Market construction."""

    def test_valid_construction(self, poly_market: Market) -> None:
        """Verify a well-formed Market can be created."""
        assert poly_market.venue == Venue.POLYMARKET
        assert poly_market.event_id == "poly-evt-001"

    def test_optional_expiry_none(self) -> None:
        """Verify expiry defaults to None."""
        m = _make_market()
        assert m.expiry is None

    def test_optional_expiry_set(self) -> None:
        """Verify expiry can be set explicitly."""
        m = _make_market(expiry=_FUTURE)
        assert m.expiry == _FUTURE

    def test_raw_data_defaults_empty(self) -> None:
        """Verify raw_data defaults to empty dict."""
        m = _make_market()
        assert m.raw_data == {}


class TestMarketPriceInRange:
    """Tests for the price_in_range validator on Market."""

    @pytest.mark.parametrize("field", ["yes_bid", "yes_ask", "no_bid", "no_ask"])
    def test_price_at_zero(self, field: str) -> None:
        """Verify boundary value 0.0 is accepted."""
        overrides: dict[str, object] = {field: Decimal("0.0")}
        # Ensure bid <= ask constraint is satisfied when setting bid to 0
        if field == "yes_bid":
            overrides["yes_ask"] = Decimal("0.45")
        elif field == "yes_ask":
            overrides["yes_bid"] = Decimal("0.0")
        elif field == "no_bid":
            overrides["no_ask"] = Decimal("0.55")
        elif field == "no_ask":
            overrides["no_bid"] = Decimal("0.0")
        m = _make_market(**overrides)
        assert getattr(m, field) == Decimal("0.0")

    @pytest.mark.parametrize("field", ["yes_bid", "yes_ask", "no_bid", "no_ask"])
    def test_price_above_one_rejected(self, field: str) -> None:
        """Verify values above 1.0 are rejected."""
        with pytest.raises(ValidationError, match="Price must be in"):
            _make_market(**{field: Decimal("1.01")})

    @pytest.mark.parametrize("field", ["yes_bid", "yes_ask", "no_bid", "no_ask"])
    def test_negative_price_rejected(self, field: str) -> None:
        """Verify negative values are rejected."""
        with pytest.raises(ValidationError, match="Price must be in"):
            _make_market(**{field: Decimal("-0.01")})

    def test_price_at_one_accepted(self) -> None:
        """Verify boundary value 1.0 is accepted for ask prices."""
        m = _make_market(yes_ask=Decimal("1.0"), no_ask=Decimal("1.0"))
        assert m.yes_ask == Decimal("1.0")


class TestMarketBidLteAsk:
    """Tests for the bid_lte_ask model validator on Market."""

    def test_yes_bid_exceeds_ask_rejected(self) -> None:
        """Verify yes_bid > yes_ask raises a validation error."""
        with pytest.raises(ValidationError, match="yes_bid"):
            _make_market(yes_bid=Decimal("0.50"), yes_ask=Decimal("0.40"))

    def test_no_bid_exceeds_ask_rejected(self) -> None:
        """Verify no_bid > no_ask raises a validation error."""
        with pytest.raises(ValidationError, match="no_bid"):
            _make_market(no_bid=Decimal("0.60"), no_ask=Decimal("0.55"))

    def test_bid_equals_ask_accepted(self) -> None:
        """Verify bid == ask is valid."""
        m = _make_market(yes_bid=Decimal("0.45"), yes_ask=Decimal("0.45"))
        assert m.yes_bid == m.yes_ask


class TestMarketFieldValidators:
    """Tests for event_id, title, and fee_model validators."""

    def test_empty_event_id_rejected(self) -> None:
        """Verify empty event_id raises a validation error."""
        with pytest.raises(ValidationError, match="event_id must be non-empty"):
            _make_market(event_id="")

    def test_whitespace_event_id_rejected(self) -> None:
        """Verify whitespace-only event_id raises a validation error."""
        with pytest.raises(ValidationError, match="event_id must be non-empty"):
            _make_market(event_id="   ")

    def test_empty_title_rejected(self) -> None:
        """Verify empty title raises a validation error."""
        with pytest.raises(ValidationError, match="title must be non-empty"):
            _make_market(title="")

    def test_whitespace_title_rejected(self) -> None:
        """Verify whitespace-only title raises a validation error."""
        with pytest.raises(ValidationError, match="title must be non-empty"):
            _make_market(title="  \t ")

    @pytest.mark.parametrize("fee_model", ["on_winnings", "per_contract"])
    def test_valid_fee_models(self, fee_model: str) -> None:
        """Verify accepted fee_model values."""
        m = _make_market(fee_model=fee_model)
        assert m.fee_model == fee_model

    def test_invalid_fee_model_rejected(self) -> None:
        """Verify unknown fee_model raises a validation error."""
        with pytest.raises(ValidationError, match="fee_model must be one of"):
            _make_market(fee_model="flat_rate")


# ---------------------------------------------------------------------------
# MatchResult model
# ---------------------------------------------------------------------------


def _make_match(**overrides: object) -> MatchResult:
    """Build a MatchResult with sensible defaults, applying overrides."""
    defaults: dict[str, object] = {
        "poly_event_id": "poly-1",
        "kalshi_event_id": "kalshi-1",
        "match_confidence": 0.9,
        "resolution_equivalent": True,
        "resolution_risks": [],
        "safe_to_arb": True,
        "reasoning": "Same event.",
        "matched_at": _NOW,
        "ttl_expires": _FUTURE,
    }
    defaults.update(overrides)
    return MatchResult(**defaults)  # type: ignore[arg-type]


class TestMatchResultValid:
    """Tests for valid MatchResult construction."""

    def test_valid_construction(self, match_result: MatchResult) -> None:
        """Verify a well-formed MatchResult can be created."""
        assert match_result.poly_event_id == "poly-evt-001"
        assert match_result.safe_to_arb is True

    def test_non_equivalent_and_not_safe(self) -> None:
        """Verify non-equivalent + not safe is valid."""
        mr = _make_match(resolution_equivalent=False, safe_to_arb=False)
        assert mr.resolution_equivalent is False
        assert mr.safe_to_arb is False


class TestMatchResultConfidence:
    """Tests for the match_confidence field constraints."""

    def test_confidence_at_zero(self) -> None:
        """Verify boundary value 0.0 is accepted."""
        mr = _make_match(match_confidence=0.0)
        assert mr.match_confidence == 0.0

    def test_confidence_at_one(self) -> None:
        """Verify boundary value 1.0 is accepted."""
        mr = _make_match(match_confidence=1.0)
        assert mr.match_confidence == 1.0

    def test_confidence_above_one_rejected(self) -> None:
        """Verify values above 1.0 are rejected."""
        with pytest.raises(ValidationError, match="less than or equal to 1"):
            _make_match(match_confidence=1.01)

    def test_confidence_below_zero_rejected(self) -> None:
        """Verify negative values are rejected."""
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            _make_match(match_confidence=-0.1)


class TestMatchResultResolutionImpliesSafe:
    """Tests for the resolution_implies_safe model validator."""

    def test_not_equivalent_but_safe_rejected(self) -> None:
        """Verify resolution_equivalent=False + safe_to_arb=True is rejected."""
        with pytest.raises(ValidationError, match="safe_to_arb must be False"):
            _make_match(resolution_equivalent=False, safe_to_arb=True)

    def test_equivalent_and_safe_accepted(self) -> None:
        """Verify resolution_equivalent=True + safe_to_arb=True is accepted."""
        mr = _make_match(resolution_equivalent=True, safe_to_arb=True)
        assert mr.safe_to_arb is True

    def test_equivalent_but_not_safe_accepted(self) -> None:
        """Verify resolution_equivalent=True + safe_to_arb=False is accepted."""
        mr = _make_match(resolution_equivalent=True, safe_to_arb=False)
        assert mr.safe_to_arb is False


# ---------------------------------------------------------------------------
# ArbOpportunity model
# ---------------------------------------------------------------------------


def _make_arb(**overrides: object) -> ArbOpportunity:
    """Build an ArbOpportunity with sensible defaults, applying overrides."""
    match = _make_match()
    poly = _make_market(venue=Venue.POLYMARKET)
    kalshi = _make_market(venue=Venue.KALSHI, event_id="kalshi-1")
    defaults: dict[str, object] = {
        "match": match,
        "poly_market": poly,
        "kalshi_market": kalshi,
        "buy_venue": Venue.KALSHI,
        "sell_venue": Venue.POLYMARKET,
        "cost_per_contract": Decimal("0.90"),
        "gross_profit": Decimal("0.10"),
        "net_profit": Decimal("0.03"),
        "net_spread_pct": Decimal("0.03"),
        "max_size": Decimal("100"),
        "depth_risk": False,
        "detected_at": _NOW,
    }
    defaults.update(overrides)
    return ArbOpportunity(**defaults)  # type: ignore[arg-type]


class TestArbOpportunityValid:
    """Tests for valid ArbOpportunity construction."""

    def test_valid_construction(self, arb_opportunity: ArbOpportunity) -> None:
        """Verify a well-formed ArbOpportunity can be created."""
        assert arb_opportunity.buy_venue == Venue.KALSHI
        assert arb_opportunity.sell_venue == Venue.POLYMARKET

    def test_id_auto_generated(self) -> None:
        """Verify id is auto-generated as UUID string."""
        arb = _make_arb()
        assert len(arb.id) == 36  # UUID format: 8-4-4-4-12

    def test_annualized_return_optional(self) -> None:
        """Verify annualized_return defaults to None."""
        arb = _make_arb()
        assert arb.annualized_return is None


class TestArbOpportunityVenuesDiffer:
    """Tests for the venues_differ model validator."""

    def test_same_venues_rejected(self) -> None:
        """Verify same buy_venue and sell_venue raises a validation error."""
        with pytest.raises(ValidationError, match="must be different"):
            _make_arb(buy_venue=Venue.POLYMARKET, sell_venue=Venue.POLYMARKET)

    def test_opposite_venues_accepted(self) -> None:
        """Verify different buy_venue and sell_venue is valid."""
        arb = _make_arb(buy_venue=Venue.POLYMARKET, sell_venue=Venue.KALSHI)
        assert arb.buy_venue != arb.sell_venue


class TestArbOpportunityCostBelowOne:
    """Tests for the cost_below_one field validator."""

    def test_cost_at_one_rejected(self) -> None:
        """Verify cost_per_contract == 1.0 is rejected."""
        with pytest.raises(ValidationError, match="must be < 1.0"):
            _make_arb(cost_per_contract=Decimal("1.0"))

    def test_cost_above_one_rejected(self) -> None:
        """Verify cost_per_contract > 1.0 is rejected."""
        with pytest.raises(ValidationError, match="must be < 1.0"):
            _make_arb(cost_per_contract=Decimal("1.5"))

    def test_cost_just_below_one_accepted(self) -> None:
        """Verify cost_per_contract just below 1.0 is accepted."""
        arb = _make_arb(cost_per_contract=Decimal("0.999"))
        assert arb.cost_per_contract == Decimal("0.999")


# ---------------------------------------------------------------------------
# ExecutionTicket model
# ---------------------------------------------------------------------------


class TestExecutionTicketValid:
    """Tests for valid ExecutionTicket construction."""

    def test_valid_construction(self, execution_ticket: ExecutionTicket) -> None:
        """Verify a well-formed ExecutionTicket can be created."""
        assert execution_ticket.status == "pending"
        assert execution_ticket.arb_id == "test-arb-001"

    def test_default_status_is_pending(self) -> None:
        """Verify default status is 'pending'."""
        ticket = ExecutionTicket(
            arb_id="arb-1",
            leg_1={"side": "buy"},
            leg_2={"side": "sell"},
            expected_cost=Decimal("0.90"),
            expected_profit=Decimal("0.03"),
        )
        assert ticket.status == "pending"


class TestExecutionTicketStatusValid:
    """Tests for the status_valid field validator."""

    @pytest.mark.parametrize("status", ["pending", "approved", "expired", "executed", "cancelled"])
    def test_valid_statuses(self, status: str) -> None:
        """Verify all accepted status values."""
        ticket = ExecutionTicket(
            arb_id="arb-1",
            leg_1={},
            leg_2={},
            expected_cost=Decimal("1"),
            expected_profit=Decimal("0.10"),
            status=status,
        )
        assert ticket.status == status

    @pytest.mark.parametrize("status", ["rejected", "active", ""])
    def test_invalid_statuses_rejected(self, status: str) -> None:
        """Verify unknown status values raise a validation error."""
        with pytest.raises(ValidationError, match="status must be one of"):
            ExecutionTicket(
                arb_id="arb-1",
                leg_1={},
                leg_2={},
                expected_cost=Decimal("1"),
                expected_profit=Decimal("0.10"),
                status=status,
            )


# ---------------------------------------------------------------------------
# ScanLog model
# ---------------------------------------------------------------------------


class TestScanLog:
    """Tests for the ScanLog model."""

    def test_valid_construction(self, scan_log: ScanLog) -> None:
        """Verify a well-formed ScanLog can be created."""
        assert scan_log.id == "scan-001"
        assert scan_log.opportunities_found == 2

    def test_defaults_applied(self) -> None:
        """Verify default values for optional fields."""
        log = ScanLog(id="scan-minimal", started_at=_NOW)
        assert log.completed_at is None
        assert log.poly_markets_fetched == 0
        assert log.kalshi_markets_fetched == 0
        assert log.candidate_pairs == 0
        assert log.llm_evaluations == 0
        assert log.opportunities_found == 0
        assert log.errors == []


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class TestFeeSchedule:
    """Tests for the FeeSchedule config model."""

    def test_valid_construction(self) -> None:
        """Verify a well-formed FeeSchedule can be created."""
        fs = FeeSchedule(
            taker_fee_pct=Decimal("0.07"),
            fee_model="per_contract",
        )
        assert fs.maker_fee_pct == Decimal("0.0")
        assert fs.fee_cap is None

    def test_with_all_fields(self) -> None:
        """Verify all fields can be set."""
        fs = FeeSchedule(
            maker_fee_pct=Decimal("0.01"),
            taker_fee_pct=Decimal("0.05"),
            fee_model="on_winnings",
            fee_cap=Decimal("0.10"),
        )
        assert fs.fee_cap == Decimal("0.10")


class TestPolymarketVenueConfig:
    """Tests for PolymarketVenueConfig."""

    def test_defaults(self) -> None:
        """Verify default values are set correctly."""
        cfg = PolymarketVenueConfig()
        assert cfg.gamma_base_url == "https://gamma-api.polymarket.com"
        assert cfg.clob_base_url == "https://clob.polymarket.com"
        assert cfg.enabled is True
        assert cfg.rate_limit_per_sec == 10


class TestKalshiVenueConfig:
    """Tests for KalshiVenueConfig."""

    def test_defaults(self) -> None:
        """Verify default values are set correctly."""
        cfg = KalshiVenueConfig()
        assert cfg.base_url == "https://api.elections.kalshi.com/trade-api/v2"
        assert cfg.enabled is True
        assert cfg.rate_limit_per_sec == 5
        assert cfg.max_relevant_events == 100
        assert cfg.rate_limit_cooldown_seconds == 5.0


class TestVenuesConfig:
    """Tests for VenuesConfig."""

    def test_defaults(self) -> None:
        """Verify nested venue configs have defaults."""
        cfg = VenuesConfig()
        assert cfg.polymarket.enabled is True
        assert cfg.kalshi.enabled is True


class TestClaudeConfig:
    """Tests for ClaudeConfig."""

    def test_defaults(self) -> None:
        """Verify default values are set correctly."""
        cfg = ClaudeConfig()
        assert cfg.model == "claude-sonnet-4-20250514"
        assert cfg.api_key == ""
        assert cfg.batch_size == 5
        assert cfg.match_cache_ttl_hours == 24


class TestScanConfig:
    """Tests for ScanConfig."""

    def test_defaults(self) -> None:
        """Verify default values are set correctly."""
        cfg = ScanConfig()
        assert cfg.interval_seconds == 60
        assert cfg.mode == "continuous"


class TestArbThresholds:
    """Tests for ArbThresholds."""

    def test_defaults(self) -> None:
        """Verify default threshold values."""
        cfg = ArbThresholds()
        assert cfg.min_net_spread_pct == Decimal("0.02")
        assert cfg.min_size_usd == Decimal("10")
        assert cfg.thin_liquidity_threshold == Decimal("50")


class TestNotificationConfig:
    """Tests for NotificationConfig."""

    def test_defaults(self) -> None:
        """Verify default notification values."""
        cfg = NotificationConfig()
        assert cfg.slack_webhook == ""
        assert cfg.discord_webhook == ""
        assert cfg.flippening_slack_webhook == ""
        assert cfg.auto_exec_slack_webhook == ""
        assert cfg.enabled is True
        assert cfg.min_spread_to_notify_pct == Decimal("0.02")

    def test_effective_flippening_slack_uses_dedicated(self) -> None:
        """Dedicated flippening URL takes priority over slack_webhook."""
        cfg = NotificationConfig(
            slack_webhook="https://general",
            flippening_slack_webhook="https://flippening",
        )
        assert cfg.effective_flippening_slack == "https://flippening"

    def test_effective_flippening_slack_falls_back(self) -> None:
        """Falls back to slack_webhook when flippening URL is empty."""
        cfg = NotificationConfig(slack_webhook="https://general")
        assert cfg.effective_flippening_slack == "https://general"

    def test_effective_auto_exec_slack_uses_dedicated(self) -> None:
        """Dedicated auto-exec URL takes priority over slack_webhook."""
        cfg = NotificationConfig(
            slack_webhook="https://general",
            auto_exec_slack_webhook="https://autoexec",
        )
        assert cfg.effective_auto_exec_slack == "https://autoexec"

    def test_effective_auto_exec_slack_falls_back(self) -> None:
        """Falls back to slack_webhook when auto-exec URL is empty."""
        cfg = NotificationConfig(slack_webhook="https://general")
        assert cfg.effective_auto_exec_slack == "https://general"


class TestStorageConfig:
    """Tests for StorageConfig."""

    def test_requires_database_url(self) -> None:
        """Verify database_url is required."""
        with pytest.raises(ValidationError):
            StorageConfig()  # type: ignore[call-arg]

    def test_valid_construction(self) -> None:
        """Verify a well-formed StorageConfig can be created."""
        cfg = StorageConfig(database_url="postgresql://localhost/test")
        assert cfg.database_url == "postgresql://localhost/test"


class TestLoggingConfig:
    """Tests for LoggingConfig."""

    def test_defaults(self) -> None:
        """Verify default logging values."""
        cfg = LoggingConfig()
        assert cfg.level == "INFO"
        assert cfg.format == "json"


class TestFeesConfig:
    """Tests for FeesConfig."""

    def test_valid_construction(self) -> None:
        """Verify a well-formed FeesConfig can be created."""
        cfg = FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0"), fee_model="on_winnings"),
            kalshi=FeeSchedule(taker_fee_pct=Decimal("0.07"), fee_model="per_contract"),
        )
        assert cfg.polymarket.taker_fee_pct == Decimal("0")
        assert cfg.kalshi.taker_fee_pct == Decimal("0.07")


class TestTrendAlertConfig:
    """Tests for TrendAlertConfig."""

    def test_defaults(self) -> None:
        """Verify default values are set correctly."""
        cfg = TrendAlertConfig()
        assert cfg.enabled is True
        assert cfg.window_size == 10
        assert cfg.convergence_threshold_pct == 0.25
        assert cfg.divergence_threshold_pct == 0.50
        assert cfg.cooldown_minutes == 15
        assert cfg.max_consecutive_failures == 3
        assert cfg.zero_opp_alert_scans == 5

    def test_custom_values(self) -> None:
        """Verify custom values override defaults."""
        cfg = TrendAlertConfig(
            enabled=False,
            window_size=20,
            convergence_threshold_pct=0.10,
            divergence_threshold_pct=1.0,
            cooldown_minutes=30,
            max_consecutive_failures=5,
            zero_opp_alert_scans=10,
        )
        assert cfg.enabled is False
        assert cfg.window_size == 20
        assert cfg.convergence_threshold_pct == 0.10
        assert cfg.divergence_threshold_pct == 1.0
        assert cfg.cooldown_minutes == 30
        assert cfg.max_consecutive_failures == 5
        assert cfg.zero_opp_alert_scans == 10


class TestSettings:
    """Tests for the top-level Settings model."""

    def _minimal_settings_data(self) -> dict[str, object]:
        """Return the minimal dict required to construct Settings."""
        return {
            "storage": {"database_url": "postgresql://localhost/test"},
            "fees": {
                "polymarket": {
                    "taker_fee_pct": 0.0,
                    "fee_model": "on_winnings",
                },
                "kalshi": {
                    "taker_fee_pct": 0.07,
                    "fee_model": "per_contract",
                },
            },
        }

    def test_minimal_construction(self) -> None:
        """Verify Settings can be built with only required fields."""
        s = Settings(**self._minimal_settings_data())  # type: ignore[arg-type]
        assert s.storage.database_url == "postgresql://localhost/test"
        assert s.venues.polymarket.enabled is True  # default

    def test_missing_storage_rejected(self) -> None:
        """Verify missing required 'storage' raises a validation error."""
        data = self._minimal_settings_data()
        del data["storage"]
        with pytest.raises(ValidationError):
            Settings(**data)  # type: ignore[arg-type]

    def test_missing_fees_rejected(self) -> None:
        """Verify missing required 'fees' raises a validation error."""
        data = self._minimal_settings_data()
        del data["fees"]
        with pytest.raises(ValidationError):
            Settings(**data)  # type: ignore[arg-type]

    def test_full_construction(self) -> None:
        """Verify Settings with all sections explicitly set."""
        data = self._minimal_settings_data()
        data["venues"] = {
            "polymarket": {"enabled": False},
            "kalshi": {"rate_limit_per_sec": 5},
        }
        data["scanning"] = {"interval_seconds": 30}
        s = Settings(**data)  # type: ignore[arg-type]
        assert s.venues.polymarket.enabled is False
        assert s.venues.kalshi.rate_limit_per_sec == 5
        assert s.scanning.interval_seconds == 30

    def test_trend_alerts_default(self) -> None:
        """Verify trend_alerts defaults to TrendAlertConfig with defaults."""
        s = Settings(**self._minimal_settings_data())  # type: ignore[arg-type]
        assert isinstance(s.trend_alerts, TrendAlertConfig)
        assert s.trend_alerts.enabled is True
        assert s.trend_alerts.window_size == 10

    def test_trend_alerts_override(self) -> None:
        """Verify trend_alerts can be overridden via dict."""
        data = self._minimal_settings_data()
        data["trend_alerts"] = {"enabled": False, "window_size": 25}
        s = Settings(**data)  # type: ignore[arg-type]
        assert s.trend_alerts.enabled is False
        assert s.trend_alerts.window_size == 25
