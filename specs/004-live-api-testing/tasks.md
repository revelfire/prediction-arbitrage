# Tasks: Live API Integration Testing

**Input**: `/specs/004-live-api-testing/spec.md`
**Depends on**: `003-pgvector-embedding-prefilter` (complete)

## Autonomous Execution Notes

- Fix bugs as you find them
- Greenfield pre-1.0 — modify existing code directly
- All 323 existing mocked tests MUST continue to pass
- Live tests are additive — they run alongside but are gated by env vars

---

## Phase 1: Setup + Bug Fixes

- [x] T001 Add `live` pytest marker to `pyproject.toml` under `[tool.pytest.ini_options]` markers. Add `-m "not live"` to the default `addopts` so `uv run pytest` never runs live tests.
- [x] T002 Fix Kalshi `volume_fp` bug in `src/arb_scanner/ingestion/kalshi.py`: change `raw.get("volume_fp", "0")` to check `volume_dollars_24h_fp` first (the actual field name from the Kalshi API), then fall back. Also update the test fixture `tests/fixtures/kalshi_markets.json` to include `volume_fp` if needed, or fix the field name to match reality.
- [x] T003 Create `tests/live/__init__.py` and `tests/live/conftest.py` with shared fixtures: `live_poly_client` (real PolymarketClient), `live_kalshi_client` (real KalshiClient), skipif decorator `requires_live = pytest.mark.skipif(os.environ.get("LIVE_TESTS") != "1", reason="Set LIVE_TESTS=1")`.

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 2: Live Tests

- [x] T004 [P] Create `tests/live/test_polymarket_live.py` (~8 tests, all marked `@pytest.mark.live`):
  - Fetch one page from Gamma API, assert ≥1 market returned
  - Assert each market has `outcomePrices` (verify it's a JSON string or list)
  - Assert `id` or `condition_id` field present
  - Construct Market model from first result — assert valid
  - Assert prices are in [0, 1] range
  - Fetch CLOB orderbook for first market's token ID — assert has bids/asks
  - Assert `endDate` or `end_date_iso` parses to valid datetime
  - Test that PolymarketClient.fetch_markets() returns list[Market] with len > 0

- [x] T005 [P] Create `tests/live/test_kalshi_live.py` (~8 tests, all marked `@pytest.mark.live`):
  - Fetch one page from Kalshi API, assert ≥1 market returned
  - Assert `yes_bid_dollars` and `yes_ask_dollars` are present and 4-decimal strings
  - Assert volume field (`volume_dollars_24h_fp` or `volume_fp`) is present
  - Assert cursor pagination works (first page returns non-empty cursor or markets)
  - Construct Market model from first result — assert valid
  - Fetch orderbook for first market's ticker — assert has `yes` and `no` arrays
  - Assert orderbook prices are numeric and in reasonable range
  - Test that KalshiClient.fetch_markets() returns list[Market] with len > 0

- [x] T006 [P] Create `tests/live/test_claude_live.py` (~4 tests, all marked `@pytest.mark.live`, also gated on ANTHROPIC_API_KEY):
  - Create two Market fixtures (one poly, one kalshi) with similar titles
  - Call evaluate_pairs with a single pair — assert returns list[MatchResult]
  - Assert match_confidence is float in [0, 1]
  - Assert reasoning is non-empty string
  - Assert response completes within 30 seconds

- [x] T007 Create `tests/live/test_scan_live.py` (~2 tests, marked `@pytest.mark.live`, gated on ANTHROPIC_API_KEY):
  - Import and call `run_scan` with real config (not dry-run), but add a market limit to keep it fast
  - Assert scan completes without exception
  - Assert scan result contains `scan_id`, `markets_scanned`, `candidate_pairs`

**Quality gate**: All 5 gates. Verify `uv run pytest` (default) skips all live tests. Then run `LIVE_TESTS=1 uv run pytest tests/live/ -v` to verify live tests work.

---

## Phase 3: Polish

- [x] T008 Run full quality gate suite. Fix any failures. Verify coverage ≥70%.
- [x] T009 Update CLAUDE.md: add live test instructions, note the `LIVE_TESTS=1` env var.

---

## Total: 9 tasks across 3 phases
