"""Tests for the vector embedding pre-filter module."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.models.config import EmbeddingConfig, Settings, StorageConfig
from arb_scanner.models.market import Market, Venue
from arb_scanner.matching.embedding import (
    _market_key,
    _market_text,
    generate_embeddings,
)

_NOW = datetime.now(tz=timezone.utc)


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


def _voyage_response(count: int, dims: int = 512) -> dict[str, Any]:
    """Build a mock Voyage API response with *count* embeddings."""
    data = [{"embedding": [0.1] * dims, "index": i} for i in range(count)]
    return {"data": data}


# ---------------------------------------------------------------------------
# EmbeddingConfig defaults
# ---------------------------------------------------------------------------


class TestEmbeddingConfigDefaults:
    """Tests for EmbeddingConfig default values."""

    def test_defaults(self) -> None:
        """Verify all defaults are populated correctly."""
        cfg = EmbeddingConfig()
        assert cfg.enabled is True
        assert cfg.model == "voyage-3-lite"
        assert cfg.api_key == ""
        assert cfg.cosine_threshold == 0.60
        assert cfg.dimensions == 512


class TestEmbeddingConfigCustom:
    """Tests for EmbeddingConfig with custom values."""

    def test_custom_values(self) -> None:
        """Verify custom overrides are respected."""
        cfg = EmbeddingConfig(
            enabled=False,
            model="voyage-3",
            api_key="sk-test",
            cosine_threshold=0.75,
            dimensions=1024,
        )
        assert cfg.enabled is False
        assert cfg.model == "voyage-3"
        assert cfg.api_key == "sk-test"
        assert cfg.cosine_threshold == 0.75
        assert cfg.dimensions == 1024


class TestEmbeddingConfigInSettings:
    """Tests for EmbeddingConfig within Settings."""

    def test_settings_has_embedding(self) -> None:
        """Verify Settings includes embedding with defaults."""
        s = Settings(
            storage=StorageConfig(database_url="postgresql://localhost/test"),
            fees={
                "polymarket": {
                    "taker_fee_pct": "0.0",
                    "fee_model": "on_winnings",
                },
                "kalshi": {
                    "taker_fee_pct": "0.07",
                    "fee_model": "per_contract",
                },
            },
        )
        assert isinstance(s.embedding, EmbeddingConfig)
        assert s.embedding.enabled is True


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestMarketKey:
    """Tests for the _market_key helper."""

    def test_format(self) -> None:
        """Verify key is 'venue:event_id'."""
        m = _make_market(venue=Venue.KALSHI, event_id="abc-123")
        assert _market_key(m) == "kalshi:abc-123"


class TestMarketText:
    """Tests for the _market_text helper."""

    def test_format(self) -> None:
        """Verify text is 'title. resolution_criteria'."""
        m = _make_market(title="Will it rain?", resolution_criteria="YES if rain")
        assert _market_text(m) == "Will it rain?. YES if rain"


# ---------------------------------------------------------------------------
# generate_embeddings
# ---------------------------------------------------------------------------


class TestGenerateEmbeddings:
    """Tests for the generate_embeddings async function."""

    @pytest.mark.asyncio()
    async def test_success_two_markets(self) -> None:
        """Two markets should produce a dict with two keys of correct length."""
        m1 = _make_market(venue=Venue.POLYMARKET, event_id="p1")
        m2 = _make_market(venue=Venue.KALSHI, event_id="k1")
        config = EmbeddingConfig(api_key="sk-test")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _voyage_response(2)

        with patch("arb_scanner.matching.embedding.httpx.AsyncClient") as mock_cls:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client_instance

            result = await generate_embeddings([m1, m2], config)

        assert "polymarket:p1" in result
        assert "kalshi:k1" in result
        assert len(result["polymarket:p1"]) == 512
        assert len(result["kalshi:k1"]) == 512

    @pytest.mark.asyncio()
    async def test_batching_200_markets(self) -> None:
        """200 markets should produce exactly 2 API calls (128 + 72)."""
        markets = [_make_market(event_id=f"e{i}") for i in range(200)]
        config = EmbeddingConfig(api_key="sk-test")

        def _make_response(count: int) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = _voyage_response(count)
            return resp

        call_count = 0

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            batch_size = len(kwargs["json"]["input"])
            call_count += 1
            return _make_response(batch_size)

        with patch("arb_scanner.matching.embedding.httpx.AsyncClient") as mock_cls:
            client_instance = AsyncMock()
            client_instance.post = _mock_post  # type: ignore[assignment]
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client_instance

            result = await generate_embeddings(markets, config)

        assert call_count == 2
        assert len(result) == 200

    @pytest.mark.asyncio()
    async def test_api_error_returns_empty(self) -> None:
        """HTTP errors should be caught, returning an empty dict."""
        m = _make_market()
        config = EmbeddingConfig(api_key="sk-test")

        with patch("arb_scanner.matching.embedding.httpx.AsyncClient") as mock_cls:
            client_instance = AsyncMock()
            client_instance.post.side_effect = httpx_error()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client_instance

            result = await generate_embeddings([m], config)

        assert result == {}

    @pytest.mark.asyncio()
    async def test_empty_market_list(self) -> None:
        """An empty market list should return an empty dict immediately."""
        config = EmbeddingConfig(api_key="sk-test")
        result = await generate_embeddings([], config)
        assert result == {}

    @pytest.mark.asyncio()
    async def test_disabled_config_skips(self) -> None:
        """When enabled=False, no API call should be made."""
        m = _make_market()
        config = EmbeddingConfig(enabled=False, api_key="sk-test")

        with patch("arb_scanner.matching.embedding.httpx.AsyncClient") as mock_cls:
            result = await generate_embeddings([m], config)
            mock_cls.assert_not_called()

        assert result == {}

    @pytest.mark.asyncio()
    async def test_empty_api_key_skips(self) -> None:
        """When api_key is empty, no API call should be made."""
        m = _make_market()
        config = EmbeddingConfig(api_key="")

        with patch("arb_scanner.matching.embedding.httpx.AsyncClient") as mock_cls:
            result = await generate_embeddings([m], config)
            mock_cls.assert_not_called()

        assert result == {}


def httpx_error() -> Exception:
    """Create a generic connection error for testing."""
    return ConnectionError("simulated network failure")
