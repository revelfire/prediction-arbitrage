"""Live test fixtures and skip markers for API integration tests."""

import os

import pytest

requires_live = pytest.mark.skipif(
    os.environ.get("LIVE_TESTS") != "1",
    reason="Set LIVE_TESTS=1 to run live API tests",
)

requires_anthropic = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Set ANTHROPIC_API_KEY to run Claude live tests",
)
