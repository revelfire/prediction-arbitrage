"""Tests for flip-ws-validate CLI command and helpers."""

from __future__ import annotations

import json
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from arb_scanner.cli._ws_validate_helpers import (
    render_ws_validate_table,
    run_ws_validate,
    save_jsonl,
)
from arb_scanner.models.config import FlippeningConfig


class TestRunWsValidate:
    """Tests for run_ws_validate()."""

    @pytest.mark.asyncio
    async def test_returns_error_without_websockets(self) -> None:
        """Reports error when websockets not installed."""
        import builtins

        real_import = builtins.__import__

        def _block(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "websockets":
                raise ImportError
            return real_import(name, *args, **kwargs)

        config = FlippeningConfig(enabled=True)
        with patch.object(builtins, "__import__", side_effect=_block):
            result = await run_ws_validate(config, None, 10, 5)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handles_timeout(self) -> None:
        """Gracefully handles timeout with no messages."""
        config = FlippeningConfig(enabled=True)

        async def _mock_ws(*args: Any, **kwargs: Any) -> Any:
            import asyncio

            await asyncio.sleep(10)

        with patch(
            "arb_scanner.cli._ws_validate_helpers.asyncio.timeout", side_effect=TimeoutError
        ):
            result = await run_ws_validate(config, ["tok-1"], 100, 0)
        assert result.get("total_messages", 0) == 0


class TestRenderWsValidateTable:
    """Tests for render_ws_validate_table()."""

    def test_renders_error(self, capsys: Any) -> None:
        """Renders error message."""
        render_ws_validate_table({"error": "no websockets"})
        captured = capsys.readouterr()
        assert "Error" in captured.out
        assert "no websockets" in captured.out

    def test_renders_empty_report(self, capsys: Any) -> None:
        """Renders report with zero messages."""
        render_ws_validate_table(
            {
                "total_messages": 0,
                "type_distribution": {},
                "key_frequency": {},
                "schema_match_rate": 1.0,
                "unique_schemas": 0,
                "samples": {},
            }
        )
        captured = capsys.readouterr()
        assert "0 messages" in captured.out

    def test_renders_full_report(self, capsys: Any) -> None:
        """Renders a non-empty report."""
        render_ws_validate_table(
            {
                "total_messages": 50,
                "type_distribution": {
                    "price_update": {"count": 40, "pct": 80.0},
                    "heartbeat": {"count": 10, "pct": 20.0},
                },
                "key_frequency": {"price": {"count": 40, "pct": 80.0}},
                "schema_match_rate": 0.85,
                "unique_schemas": 2,
                "samples": {"price_update": '{"price":"0.5"}'},
            }
        )
        captured = capsys.readouterr()
        assert "50 messages" in captured.out
        assert "price_update" in captured.out
        assert "85.0%" in captured.out


class TestSaveJsonl:
    """Tests for save_jsonl()."""

    def test_saves_messages(self) -> None:
        """Writes one line per message."""
        msgs = ['{"a": 1}', '{"b": 2}']
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        count = save_jsonl(msgs, path)
        assert count == 2
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}

    def test_saves_empty(self) -> None:
        """Empty message list writes empty file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        count = save_jsonl([], path)
        assert count == 0
