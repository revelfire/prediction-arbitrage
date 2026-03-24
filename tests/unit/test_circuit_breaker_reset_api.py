"""Tests for circuit breaker reset API with pipeline targeting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arb_scanner.api.routes_auto_execution import (
    _breaker_reset_response,
    _get_breaker_targets,
    _reset_breaker,
)
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models._auto_exec_config import AutoExecutionConfig


def _make_breakers() -> CircuitBreakerManager:
    return CircuitBreakerManager(AutoExecutionConfig())


def _make_request(
    arb: CircuitBreakerManager | None = None,
    flip: CircuitBreakerManager | None = None,
) -> MagicMock:
    req = MagicMock()
    state = SimpleNamespace()
    if arb is not None:
        state.arb_breakers = arb
    if flip is not None:
        state.flip_breakers = flip
    req.app.state = state
    return req


class TestGetBreakerTargets:
    """Verify pipeline-targeted breaker resolution."""

    def test_arb_only(self) -> None:
        """Pipeline 'arb' returns only arb breakers."""
        arb = _make_breakers()
        req = _make_request(arb=arb, flip=_make_breakers())
        targets = _get_breaker_targets(req, "arb")
        assert len(targets) == 1
        assert targets[0][0] == "arb"

    def test_flip_only(self) -> None:
        """Pipeline 'flip' returns only flip breakers."""
        flip = _make_breakers()
        req = _make_request(arb=_make_breakers(), flip=flip)
        targets = _get_breaker_targets(req, "flip")
        assert len(targets) == 1
        assert targets[0][0] == "flip"

    def test_all_returns_both(self) -> None:
        """Pipeline 'all' returns both arb and flip breakers."""
        req = _make_request(arb=_make_breakers(), flip=_make_breakers())
        targets = _get_breaker_targets(req, "all")
        assert len(targets) == 2
        names = {t[0] for t in targets}
        assert names == {"arb", "flip"}

    def test_raises_when_no_breakers(self) -> None:
        """Raises HTTPException when no breakers are initialised."""
        from fastapi import HTTPException

        req = _make_request()  # No breakers
        with pytest.raises(HTTPException) as exc_info:
            _get_breaker_targets(req, "all")
        assert exc_info.value.status_code == 503


class TestResetBreaker:
    """Verify individual breaker reset dispatch."""

    def test_reset_failure(self) -> None:
        """Resetting failure breaker clears it."""
        b = _make_breakers()
        for _ in range(3):
            b.record_failure()
        assert b.is_any_tripped()
        _reset_breaker(b, "failure")
        assert not b.is_any_tripped()

    def test_reset_loss(self) -> None:
        """Resetting loss breaker clears it."""
        b = _make_breakers()
        b.check_loss(Decimal("-999"))
        _reset_breaker(b, "loss")
        state = b.get_state()
        assert not any(s.tripped for s in state if s.breaker_type.value == "loss")

    def test_reset_anomaly(self) -> None:
        """Resetting anomaly breaker clears it."""
        b = _make_breakers()
        b.check_anomaly(0.99)
        _reset_breaker(b, "anomaly")
        state = b.get_state()
        assert not any(s.tripped for s in state if s.breaker_type.value == "anomaly")

    def test_reset_all(self) -> None:
        """Resetting all clears all breakers."""
        b = _make_breakers()
        for _ in range(3):
            b.record_failure()
        b.check_anomaly(0.99)
        _reset_breaker(b, "all")
        assert not b.is_any_tripped()

    def test_unknown_type_raises(self) -> None:
        """Unknown breaker type raises HTTPException."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _reset_breaker(_make_breakers(), "invalid")
        assert exc_info.value.status_code == 400


class TestBreakerResetResponse:
    """Verify response format includes pipeline-specific states."""

    def test_response_includes_pipeline_breakers(self) -> None:
        """Response includes per-pipeline breaker states."""
        arb = _make_breakers()
        flip = _make_breakers()
        targets = [("arb", arb), ("flip", flip)]
        resp = _breaker_reset_response("all", "all", targets)
        assert resp["status"] == "reset"
        assert resp["pipeline"] == "all"
        assert "arb_breakers" in resp
        assert "flip_breakers" in resp
        assert len(resp["arb_breakers"]) == 3
        assert len(resp["flip_breakers"]) == 3


# Need Decimal import for test_reset_loss
from decimal import Decimal  # noqa: E402
