# 016 — WebSocket Telemetry Monitor

## Overview

Add a "WS Health" section to the dashboard showing real-time WebSocket connection telemetry: messages per second, schema match rate, stall detection, reconnect history, and order book cache performance. Provides live visibility into the price data pipeline's reliability.

## Motivation

The Polymarket CLOB WebSocket is the single data source for live price updates. Connection issues, schema drift, and stalls directly impact signal quality. Currently, telemetry is only visible in structlog output and periodic DB snapshots. A dashboard view gives operators instant awareness of data pipeline health.

## Functional Requirements

### FR-001: Connection Status Banner

Top-of-section status indicator:
- **Connected** (green): WS active, messages flowing
- **Stalled** (amber): No messages received for 2+ telemetry intervals
- **Disconnected** (red): WS connection lost, reconnecting
- **Idle** (gray): flip-watch not running

### FR-002: Throughput Chart

Real-time line chart showing messages per second over the last hour. Data sourced from `flippening_ws_telemetry` table. Overlay the 30-second rolling average. Show both `cum_received` delta and `cum_parsed_ok` delta to visualize parse failure rate.

### FR-003: Schema Match Rate Gauge

Circular gauge showing the current schema match rate (0-100%). Color-coded: green (> 90%), amber (50-90%), red (< 50%). Show the configured threshold as a reference mark. Historical trend line below the gauge.

### FR-004: Stall and Reconnect Log

Reverse-chronological table of stall events and reconnections:
- Timestamp
- Event type (stall_detected, stall_reconnect, ws_connected, ws_disconnected)
- Duration (time between disconnect and reconnect)
- Messages missed estimate (based on average throughput)

Data sourced from structlog events persisted to `flippening_ws_telemetry` snapshots.

### FR-005: Order Book Cache Performance

Metrics panel showing:
- Cache hit rate (%)
- Cache size (current / max)
- Synthetic spread count (enrichments that used cached or synthetic data)
- Average enrichment latency

### FR-006: REST API Endpoints

- `GET /api/flippening/ws-telemetry` — Latest telemetry snapshot.
- `GET /api/flippening/ws-telemetry/history?hours=24` — Historical snapshots.
- `GET /api/flippening/ws-telemetry/events?limit=50` — Stall/reconnect events.

## Edge Cases

- EC-001: flip-watch not running → Show idle state with "Start flip-watch to see telemetry" message.
- EC-002: Schema drift detected → Flash the schema gauge red and show a notification banner.
- EC-003: Very high stall frequency (> 3/hour) → Show "Unstable connection" warning.

## Success Criteria

- SC-001: Throughput chart updates every 5 seconds via polling.
- SC-002: Schema match gauge reflects actual parser match rate within one telemetry interval.
- SC-003: Stall events appear in the log within 10 seconds of detection.
- SC-004: Cache hit rate is accurately computed from telemetry snapshots.

## Out of Scope

- WebSocket reconnection controls from the dashboard (reconnection is automatic).
- Raw message inspector (too much data volume).
- Historical telemetry beyond 30 days.
