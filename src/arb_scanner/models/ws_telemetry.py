"""Pydantic models for WebSocket telemetry dashboard data."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class ConnectionState(str, enum.Enum):
    """WebSocket connection status for the dashboard banner."""

    connected = "connected"
    stalled = "stalled"
    disconnected = "disconnected"
    idle = "idle"


class WsTelemetrySnapshot(BaseModel):
    """A single telemetry snapshot from the ws_telemetry table.

    Maps directly to one row returned by the history or latest queries.
    """

    snapshot_time: datetime
    messages_received: int = 0
    messages_parsed: int = 0
    messages_failed: int = 0
    messages_ignored: int = 0
    schema_match_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    book_cache_hit_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    connection_state: str = "unknown"


class WsTelemetryEvent(BaseModel):
    """A stall or reconnect event derived from telemetry snapshots.

    Events are produced by detecting connection_state transitions
    between consecutive snapshots.
    """

    event_time: datetime
    event_type: str
    prev_state: str | None = None
    new_state: str
    messages_received_at_event: int = 0


class WsHealthSummary(BaseModel):
    """Aggregated WS health for the dashboard metrics panel.

    Computed from the latest telemetry snapshot plus derived stats.
    """

    status: ConnectionState = ConnectionState.idle
    schema_match_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    schema_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    cache_hit_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    cache_size: int = 0
    cache_max_size: int = 200
    total_received: int = 0
    total_parsed: int = 0
    total_failed: int = 0
    stall_events_1h: int = 0
