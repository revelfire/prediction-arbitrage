# 014 — Live Price Ticker

## Overview

Add a real-time price ticker to the flippening dashboard tab showing live prices for all monitored markets. The ticker displays baseline, current price, deviation from baseline, and a sparkline of recent price movement. Updates stream via Server-Sent Events (SSE) from the flip-watch process — no polling.

## Motivation

Currently, the only way to see live price data is via flip-watch CLI logs. Operators need a visual, at-a-glance view of all active markets to spot developing spikes, monitor positions, and understand market behavior in real time.

## Functional Requirements

### FR-001: SSE Price Stream Endpoint

Add `GET /api/flippening/price-stream` SSE endpoint that emits price updates for all currently monitored markets. Each event contains: `market_id`, `market_title`, `category`, `category_type`, `yes_mid`, `baseline_yes`, `deviation_pct`, `spread`, `timestamp`, `book_depth_bids`, `book_depth_asks`.

The endpoint reads from a shared in-memory ring buffer that the flip-watch orchestrator writes to. If flip-watch is not running, the endpoint returns an empty stream with a `status: idle` initial event.

### FR-002: Price Ticker UI Component

Add a "Live Prices" section to the Flippenings dashboard tab. For each monitored market, display a row with:

- Market title (truncated to 40 chars)
- Category badge (colored by category_type)
- Current YES mid price
- Baseline YES price
- Deviation % (green if within threshold, amber if approaching spike, red if spiked)
- Sparkline showing last 30 price points (using Chart.js sparkline)
- Spread value
- Book depth indicator (bid/ask count)

### FR-003: Color-Coded Deviation

Deviation colors:
- **Green** (< 50% of spike threshold): Normal range
- **Amber** (50-100% of spike threshold): Approaching spike territory
- **Red** (> spike threshold): Active spike, likely has entry signal

### FR-004: Sorting and Filtering

- Default sort: by absolute deviation % (largest first, most interesting on top)
- Optional filters: by category, by category_type
- Toggle: show/hide markets with no recent updates (stale > 5 min)

### FR-005: Ring Buffer Architecture

The orchestrator writes to an in-memory `PriceRingBuffer` (maxlen per market: 60 points, ~5 minutes at 5s intervals). The SSE endpoint reads from this buffer. No database queries for live data — purely in-memory for latency.

### FR-006: Connection Management

SSE connections auto-reconnect on disconnect. Show a "Disconnected" banner in the UI when the EventSource connection drops. Maximum 10 concurrent SSE connections.

## Edge Cases

- EC-001: No flip-watch running → Show "Flippening engine not active" placeholder.
- EC-002: Market goes stale (no update for 5+ min) → Dim the row, show "Stale" badge.
- EC-003: Browser tab backgrounded → Pause sparkline animation to reduce CPU.

## Success Criteria

- SC-001: Price updates appear in < 500ms of WebSocket receipt by flip-watch.
- SC-002: Sparklines render correctly for 50+ markets simultaneously.
- SC-003: SSE reconnection works within 3 seconds of disconnect.
- SC-004: No performance degradation in flip-watch from ring buffer writes.

## Out of Scope

- Historical price charts (covered by separate analytics features).
- Order book visualization (depth beyond bid/ask counts).
- Price alerts from the dashboard (alerts are via webhooks only).
