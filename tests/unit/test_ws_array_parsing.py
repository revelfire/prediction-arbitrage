"""Tests for multi-event WebSocket array parsing."""

from __future__ import annotations

import json

from arb_scanner.flippening.ws_parser import parse_ws_message


class TestWsArrayParsing:
    """Verify all events in JSON arrays are parsed."""

    def test_array_with_multiple_events(self) -> None:
        """Multiple price_change events in array all parsed."""
        events = [
            {
                "event_type": "price_change",
                "market": "mkt1",
                "price_changes": [
                    {
                        "asset_id": "tok_a",
                        "best_bid": "0.45",
                        "best_ask": "0.55",
                    }
                ],
            },
            {
                "event_type": "price_change",
                "market": "mkt2",
                "price_changes": [
                    {
                        "asset_id": "tok_b",
                        "best_bid": "0.30",
                        "best_ask": "0.35",
                    }
                ],
            },
        ]
        results = parse_ws_message(json.dumps(events))
        assert len(results) == 2
        assert results[0].token_id == "tok_a"
        assert results[1].token_id == "tok_b"

    def test_array_with_mixed_valid_invalid(self) -> None:
        """Only valid events from an array are returned."""
        events = [
            {"not_an_event": True},
            {
                "event_type": "price_change",
                "market": "mkt1",
                "price_changes": [
                    {
                        "asset_id": "tok_a",
                        "best_bid": "0.45",
                        "best_ask": "0.55",
                    }
                ],
            },
        ]
        results = parse_ws_message(json.dumps(events))
        assert len(results) == 1
        assert results[0].token_id == "tok_a"

    def test_empty_array_returns_empty_list(self) -> None:
        """Empty JSON array returns empty list."""
        results = parse_ws_message("[]")
        assert results == []

    def test_single_dict_returns_single_list(self) -> None:
        """Single dict event returns single-element list."""
        event = {
            "event_type": "price_change",
            "market": "mkt1",
            "price_changes": [
                {
                    "asset_id": "tok_a",
                    "best_bid": "0.45",
                    "best_ask": "0.55",
                }
            ],
        }
        results = parse_ws_message(json.dumps(event))
        assert len(results) == 1

    def test_pong_returns_empty_list(self) -> None:
        """PONG heartbeat returns empty list (not None)."""
        results = parse_ws_message("PONG")
        assert results == []

    def test_price_change_uses_last_entry(self) -> None:
        """Multi-entry price_changes uses the last valid entry."""
        event = {
            "event_type": "price_change",
            "market": "mkt1",
            "price_changes": [
                {
                    "asset_id": "tok_a",
                    "best_bid": "0.40",
                    "best_ask": "0.45",
                },
                {
                    "asset_id": "tok_a",
                    "best_bid": "0.42",
                    "best_ask": "0.47",
                },
            ],
        }
        results = parse_ws_message(json.dumps(event))
        assert len(results) == 1
        assert float(results[0].yes_bid) == 0.42
