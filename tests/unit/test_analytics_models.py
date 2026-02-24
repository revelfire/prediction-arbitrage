"""Unit tests for analytics Pydantic models."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from arb_scanner.models.analytics import (
    HourlyBucket,
    PairSummary,
    ScanHealthSummary,
    SpreadSnapshot,
)

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# SpreadSnapshot
# ---------------------------------------------------------------------------


def _make_snapshot(**overrides: object) -> SpreadSnapshot:
    """Build a SpreadSnapshot with sensible defaults, applying overrides."""
    defaults: dict[str, object] = {
        "detected_at": _NOW,
        "net_spread_pct": Decimal("0.05"),
        "annualized_return": Decimal("12.5"),
        "depth_risk": False,
        "max_size": Decimal("500"),
    }
    defaults.update(overrides)
    return SpreadSnapshot(**defaults)  # type: ignore[arg-type]


class TestSpreadSnapshotValid:
    """Tests for valid SpreadSnapshot construction."""

    def test_valid_construction_all_fields(self) -> None:
        """Verify a well-formed SpreadSnapshot with all fields."""
        snap = _make_snapshot()
        assert snap.detected_at == _NOW
        assert snap.net_spread_pct == Decimal("0.05")
        assert snap.annualized_return == Decimal("12.5")
        assert snap.depth_risk is False
        assert snap.max_size == Decimal("500")

    def test_annualized_return_optional_none(self) -> None:
        """Verify annualized_return defaults to None when omitted."""
        snap = SpreadSnapshot(
            detected_at=_NOW,
            net_spread_pct=Decimal("0.03"),
            depth_risk=True,
            max_size=Decimal("100"),
        )
        assert snap.annualized_return is None

    def test_annualized_return_explicit_none(self) -> None:
        """Verify annualized_return can be set to None explicitly."""
        snap = _make_snapshot(annualized_return=None)
        assert snap.annualized_return is None

    def test_zero_spread(self) -> None:
        """Verify zero net_spread_pct is accepted."""
        snap = _make_snapshot(net_spread_pct=Decimal("0"))
        assert snap.net_spread_pct == Decimal("0")

    def test_negative_spread(self) -> None:
        """Verify negative net_spread_pct is accepted (inverted arb)."""
        snap = _make_snapshot(net_spread_pct=Decimal("-0.02"))
        assert snap.net_spread_pct == Decimal("-0.02")

    def test_depth_risk_true(self) -> None:
        """Verify depth_risk can be True."""
        snap = _make_snapshot(depth_risk=True)
        assert snap.depth_risk is True

    def test_decimal_precision_preserved(self) -> None:
        """Verify high-precision Decimal values are preserved."""
        snap = _make_snapshot(net_spread_pct=Decimal("0.123456789"))
        assert snap.net_spread_pct == Decimal("0.123456789")


class TestSpreadSnapshotRequired:
    """Tests for required field enforcement on SpreadSnapshot."""

    def test_missing_detected_at(self) -> None:
        """Verify missing detected_at raises ValidationError."""
        with pytest.raises(ValidationError):
            SpreadSnapshot(
                net_spread_pct=Decimal("0.05"),
                depth_risk=False,
                max_size=Decimal("500"),
            )  # type: ignore[call-arg]

    def test_missing_net_spread_pct(self) -> None:
        """Verify missing net_spread_pct raises ValidationError."""
        with pytest.raises(ValidationError):
            SpreadSnapshot(
                detected_at=_NOW,
                depth_risk=False,
                max_size=Decimal("500"),
            )  # type: ignore[call-arg]

    def test_missing_depth_risk(self) -> None:
        """Verify missing depth_risk raises ValidationError."""
        with pytest.raises(ValidationError):
            SpreadSnapshot(
                detected_at=_NOW,
                net_spread_pct=Decimal("0.05"),
                max_size=Decimal("500"),
            )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# PairSummary
# ---------------------------------------------------------------------------


def _make_pair_summary(**overrides: object) -> PairSummary:
    """Build a PairSummary with sensible defaults, applying overrides."""
    defaults: dict[str, object] = {
        "poly_event_id": "poly-evt-001",
        "kalshi_event_id": "kalshi-evt-001",
        "peak_spread": Decimal("0.08"),
        "min_spread": Decimal("0.01"),
        "avg_spread": Decimal("0.04"),
        "total_detections": 42,
        "first_seen": _NOW,
        "last_seen": _NOW,
    }
    defaults.update(overrides)
    return PairSummary(**defaults)  # type: ignore[arg-type]


class TestPairSummaryValid:
    """Tests for valid PairSummary construction."""

    def test_valid_construction(self) -> None:
        """Verify a well-formed PairSummary with all fields."""
        ps = _make_pair_summary()
        assert ps.poly_event_id == "poly-evt-001"
        assert ps.kalshi_event_id == "kalshi-evt-001"
        assert ps.peak_spread == Decimal("0.08")
        assert ps.min_spread == Decimal("0.01")
        assert ps.avg_spread == Decimal("0.04")
        assert ps.total_detections == 42
        assert ps.first_seen == _NOW
        assert ps.last_seen == _NOW

    def test_large_detection_count(self) -> None:
        """Verify large total_detections values are accepted."""
        ps = _make_pair_summary(total_detections=999_999)
        assert ps.total_detections == 999_999

    def test_zero_detections(self) -> None:
        """Verify zero total_detections is accepted."""
        ps = _make_pair_summary(total_detections=0)
        assert ps.total_detections == 0


class TestPairSummaryRequired:
    """Tests for required field enforcement on PairSummary."""

    def test_missing_poly_event_id(self) -> None:
        """Verify missing poly_event_id raises ValidationError."""
        with pytest.raises(ValidationError):
            PairSummary(
                kalshi_event_id="k-1",
                peak_spread=Decimal("0.08"),
                min_spread=Decimal("0.01"),
                avg_spread=Decimal("0.04"),
                total_detections=1,
                first_seen=_NOW,
                last_seen=_NOW,
            )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# HourlyBucket
# ---------------------------------------------------------------------------


class TestHourlyBucketValid:
    """Tests for valid HourlyBucket construction."""

    def test_valid_construction(self) -> None:
        """Verify a well-formed HourlyBucket with all fields."""
        hb = HourlyBucket(
            hour=_NOW,
            avg_spread=Decimal("0.035"),
            max_spread=Decimal("0.07"),
            detection_count=15,
        )
        assert hb.hour == _NOW
        assert hb.avg_spread == Decimal("0.035")
        assert hb.max_spread == Decimal("0.07")
        assert hb.detection_count == 15

    def test_zero_detection_count(self) -> None:
        """Verify zero detection_count is accepted."""
        hb = HourlyBucket(
            hour=_NOW,
            avg_spread=Decimal("0"),
            max_spread=Decimal("0"),
            detection_count=0,
        )
        assert hb.detection_count == 0


class TestHourlyBucketRequired:
    """Tests for required field enforcement on HourlyBucket."""

    def test_missing_hour(self) -> None:
        """Verify missing hour raises ValidationError."""
        with pytest.raises(ValidationError):
            HourlyBucket(
                avg_spread=Decimal("0.05"),
                max_spread=Decimal("0.07"),
                detection_count=10,
            )  # type: ignore[call-arg]

    def test_wrong_type_detection_count(self) -> None:
        """Verify non-integer detection_count raises ValidationError."""
        with pytest.raises(ValidationError):
            HourlyBucket(
                hour=_NOW,
                avg_spread=Decimal("0.05"),
                max_spread=Decimal("0.07"),
                detection_count="not_a_number",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# ScanHealthSummary
# ---------------------------------------------------------------------------


class TestScanHealthSummaryValid:
    """Tests for valid ScanHealthSummary construction."""

    def test_valid_construction(self) -> None:
        """Verify a well-formed ScanHealthSummary with all fields."""
        sh = ScanHealthSummary(
            hour=_NOW,
            scan_count=10,
            avg_duration_s=2.5,
            total_llm_calls=50,
            total_opps=3,
            total_errors=1,
        )
        assert sh.hour == _NOW
        assert sh.scan_count == 10
        assert sh.avg_duration_s == 2.5
        assert sh.total_llm_calls == 50
        assert sh.total_opps == 3
        assert sh.total_errors == 1

    def test_zero_errors(self) -> None:
        """Verify zero total_errors is accepted."""
        sh = ScanHealthSummary(
            hour=_NOW,
            scan_count=5,
            avg_duration_s=1.0,
            total_llm_calls=25,
            total_opps=0,
            total_errors=0,
        )
        assert sh.total_errors == 0

    def test_float_duration(self) -> None:
        """Verify fractional avg_duration_s is accepted."""
        sh = ScanHealthSummary(
            hour=_NOW,
            scan_count=1,
            avg_duration_s=0.123,
            total_llm_calls=1,
            total_opps=0,
            total_errors=0,
        )
        assert sh.avg_duration_s == pytest.approx(0.123)


class TestScanHealthSummaryRequired:
    """Tests for required field enforcement on ScanHealthSummary."""

    def test_missing_scan_count(self) -> None:
        """Verify missing scan_count raises ValidationError."""
        with pytest.raises(ValidationError):
            ScanHealthSummary(
                hour=_NOW,
                avg_duration_s=1.0,
                total_llm_calls=10,
                total_opps=0,
                total_errors=0,
            )  # type: ignore[call-arg]
