# Feature Specification: Live API Integration Testing

**Feature**: `004-live-api-testing` | **Date**: 2026-02-24 | **Status**: Draft
**Depends on**: `003-pgvector-embedding-prefilter` (complete)

## Problem Statement

All integration tests use mocked HTTP responses. The mocks hide real-world bugs: the Kalshi client reads `volume_fp` but the real API returns `volume_dollars_24h_fp`, Polymarket field names have dual-fallback logic never tested against live data, and the Claude semantic matcher's JSON parsing has never been validated against a real model response. We need live API smoke tests to catch these issues.

## Solution

Add a `tests/live/` directory with environment-gated live API tests. Fix any bugs discovered. These tests hit real APIs (Polymarket and Kalshi are public, Claude requires API key) and validate that our parsing logic handles actual response formats.

## Functional Requirements

### FR-001: Live Polymarket Tests
Fetch real markets from Gamma API and a real orderbook from CLOB API. Validate field presence, JSON-string parsing, and Market model construction.

### FR-002: Live Kalshi Tests
Fetch real markets and orderbook from Kalshi API. Validate `*_dollars` field presence, cursor pagination, and orderbook structure. Fix the `volume_fp` → `volume_dollars_24h_fp` bug.

### FR-003: Live Claude Tests
Send a real pair to Claude via the semantic matcher. Validate response is valid JSON, produces a valid MatchResult, and completes within token limits.

### FR-004: Live Scan Smoke Test
Run `arb-scanner scan` against real APIs (no dry-run) with a small market limit. Validate the full pipeline completes without errors.

### FR-005: Test Gating
Live tests MUST be gated by environment variables (`LIVE_TESTS=1`) and excluded from default `pytest` runs. The default `uv run pytest` MUST NOT hit any external API.

### FR-006: Bug Fixes
Fix all bugs discovered during live testing, including the known `volume_fp` field name mismatch in the Kalshi client.

## Success Criteria

- SC-001: Live Polymarket test fetches ≥1 real market and constructs a valid Market model
- SC-002: Live Kalshi test fetches ≥1 real market with correct volume field
- SC-003: Live Claude test produces a valid MatchResult from a real API call
- SC-004: All existing 323 mocked tests still pass
- SC-005: `uv run pytest` (no env vars) skips all live tests
