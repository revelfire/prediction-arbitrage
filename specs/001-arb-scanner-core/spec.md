# Feature Specification: Cross-Venue Arbitrage Scanner

**Feature Branch**: `001-arb-scanner-core`
**Created**: 2026-02-24
**Status**: Draft
**Input**: User description: "Cross-venue prediction market arbitrage scanner with LLM-powered contract matching for Polymarket and Kalshi"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Single Scan Cycle (Priority: P1)

A trader opens the CLI and runs a single scan to check for current arbitrage opportunities across Polymarket and Kalshi. The system ingests active markets from both venues, matches semantically equivalent contracts, calculates net profit after venue-specific fees, and outputs a ranked list of opportunities to stdout.

**Why this priority**: This is the core value proposition — detecting mispricings. Without this, nothing else matters. A single scan cycle exercises the entire pipeline: ingestion, matching, calculation, and output.

**Independent Test**: Can be fully tested by running `arb-scanner scan` with mocked venue APIs and verifying the output contains correctly calculated arbitrage opportunities with accurate fee deductions.

**Acceptance Scenarios**:

1. **Given** both venue APIs are reachable and return active markets, **When** the user runs `arb-scanner scan`, **Then** the system outputs a JSON report listing all detected arbitrage opportunities with net spread, size, and venue breakdown.
2. **Given** a known mispricing exists (YES on venue A at 0.62 + NO on venue B at 0.35 = 0.97 cost for guaranteed $1.00 payout), **When** a scan completes, **Then** that pair appears in results with correct gross profit (0.03), correct net profit (after each venue's fee model), and correct annualized return if expiry is known.
3. **Given** no mispricings exist (all cross-venue pairs cost ≥ $1.00 after fees), **When** a scan completes, **Then** the system outputs an empty opportunity list with a summary indicating zero opportunities found.
4. **Given** API credentials are missing or invalid, **When** the user runs a scan, **Then** the system reports a clear error indicating which venue credentials are missing and exits with a non-zero code.

---

### User Story 2 - Continuous Monitoring with Alerts (Priority: P2)

A trader starts the scanner in continuous watch mode. It polls both venues at a configurable interval, detects new arbitrage opportunities as they emerge, and sends alerts via webhook (Slack or Discord) when opportunities exceed a configurable profit threshold.

**Why this priority**: Real arbitrage opportunities are time-sensitive. Continuous monitoring with instant alerts transforms the tool from "interesting analysis" to "actionable intelligence."

**Independent Test**: Can be tested by running the watch command with mocked APIs that introduce a mispricing after the second poll cycle, and verifying the webhook fires with the correct payload.

**Acceptance Scenarios**:

1. **Given** watch mode is started with a configurable interval, **When** a new arbitrage opportunity exceeding the minimum spread threshold appears, **Then** the system sends a webhook notification containing the opportunity details, venue breakdown, and execution ticket.
2. **Given** watch mode is running, **When** a previously detected opportunity disappears (prices converge), **Then** the system does not re-alert for the stale opportunity.
3. **Given** the webhook endpoint is unreachable, **When** an alert would fire, **Then** the system logs the failure, continues scanning, and retries on the next cycle.

---

### User Story 3 - LLM-Powered Contract Matching (Priority: P1)

The system must determine which contracts across Polymarket and Kalshi refer to the same underlying event, even when titles and resolution criteria differ significantly. A cheap pre-filter narrows candidates, then an LLM evaluates semantic equivalence, resolution risk, and arbitrage safety.

**Why this priority**: This is the hardest unsolved problem in cross-venue arbitrage. Naive title matching misses most opportunities and produces false positives. Without accurate matching, the scanner produces garbage. Co-equal with US1 because US1 depends on it.

**Independent Test**: Can be tested by providing a set of known contract pairs (some matching, some not) and verifying the matcher correctly identifies equivalences and flags resolution risks.

**Acceptance Scenarios**:

1. **Given** two contracts with different titles but the same underlying event (e.g., "Will BTC exceed $100k by Dec 31?" vs. "Bitcoin above 100000 end of year"), **When** the matcher evaluates them, **Then** it returns high match confidence, resolution_equivalent=true, and safe_to_arb=true.
2. **Given** two contracts that appear similar but have different resolution sources or time boundaries, **When** the matcher evaluates them, **Then** it returns resolution_equivalent=false, safe_to_arb=false, and lists specific resolution risks.
3. **Given** a large pool of active markets from each venue, **When** the pre-filter runs, **Then** it reduces candidate pairs by at least 80% before any LLM calls are made.
4. **Given** a pair was matched within the cache TTL window, **When** the same pair is encountered again, **Then** the system returns the cached result without making a new LLM call.

---

### User Story 4 - Execution Ticket Generation (Priority: P2)

For every qualified arbitrage opportunity, the system generates a structured execution ticket describing exactly what to buy/sell on each venue, at what price and size, with expected cost and profit. Tickets are for human review — the system never executes trades.

**Why this priority**: Execution tickets bridge detection and action. Without them, the user still has to manually figure out order parameters, which is error-prone and slow.

**Independent Test**: Can be tested by feeding a known arbitrage opportunity into the ticket generator and verifying the output contains correct venue, side, price, and size for both legs.

**Acceptance Scenarios**:

1. **Given** a detected arbitrage opportunity, **When** the ticket is generated, **Then** it contains two legs (one per venue) with venue name, side (YES/NO), price, size (constrained by minimum liquidity), expected cost, and expected profit.
2. **Given** the opportunity's maximum executable size is below the thin-liquidity threshold, **When** the ticket is generated, **Then** it includes a depth risk warning flag.
3. **Given** the user requests a report, **Then** it displays all pending execution tickets in a human-readable format sorted by net spread descending.

---

### User Story 5 - Match Audit Trail (Priority: P3)

A trader wants to review all cached contract matches to understand what the system considers equivalent (or not), verify match quality, and identify potential false positives/negatives.

**Why this priority**: Trust and transparency. Users need to inspect and validate the LLM's matching decisions before acting on arbitrage signals. This is a diagnostic/review feature, not core pipeline.

**Independent Test**: Can be tested by populating the match cache with known entries and running the match-audit command to verify all entries appear with their confidence scores and reasoning.

**Acceptance Scenarios**:

1. **Given** the match cache contains entries, **When** the user runs the match-audit command, **Then** the system outputs all cached matches with confidence scores, resolution equivalence flags, risk factors, and the LLM's reasoning.
2. **Given** expired cache entries exist (older than TTL), **When** match-audit runs, **Then** expired entries are marked as such but still displayed for review.

---

### Edge Cases

- What happens when one venue's API is down but the other is reachable? System should scan the available venue and log the unavailable one, not crash.
- How does the system handle a contract that exists on both venues but has been resolved/settled on one? Filter out resolved markets during ingestion.
- What if the LLM returns malformed output or refuses to evaluate a pair? Fall back to marking the pair as "inconclusive" with safe_to_arb=false; log the raw response for debugging.
- What if a venue changes its fee structure? Fee schedules live in configuration; updating config is sufficient with no code changes.
- What if two contracts match but have different notional values or settlement currencies? Flag as a resolution risk; do not mark safe_to_arb.
- What if rate limits are hit during ingestion? Implement exponential backoff with jitter; respect Retry-After headers; log rate-limit events.
- What if the database is unavailable at startup? Fail fast with a clear connection error, do not fall back to in-memory mode.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST ingest all active binary markets from Polymarket into a normalized data model including bid/ask prices, volume, expiry, and raw API response.
- **FR-002**: System MUST ingest all active binary markets from Kalshi (authenticated) into the same normalized data model.
- **FR-003**: System MUST normalize markets from both venues into a unified schema that preserves venue-specific attributes (fee model, event IDs) while enabling cross-venue comparison.
- **FR-004**: System MUST implement a two-pass matching pipeline: a cheap keyword-based pre-filter followed by LLM-powered semantic evaluation.
- **FR-005**: System MUST use an LLM to evaluate candidate contract pairs and return structured results including match confidence (0-1), resolution equivalence (boolean), resolution risks (list), safety-to-arb (boolean), and reasoning (text).
- **FR-006**: System MUST cache match results with a configurable TTL (default 24 hours) to avoid redundant LLM calls.
- **FR-007**: System MUST calculate arbitrage opportunities including: cost per contract, gross profit, net profit after venue-specific fees, net spread percentage, maximum executable size at quoted prices, and annualized return when expiry is known.
- **FR-008**: System MUST apply venue-specific fee models correctly: percentage-on-winnings for one venue model, per-contract flat fee for another venue model.
- **FR-009**: System MUST generate structured execution tickets for each qualified opportunity describing both legs (venue, side, price, size) without ever placing orders.
- **FR-010**: System MUST support configurable webhook notifications (Slack/Discord) when opportunities exceed a configurable threshold.
- **FR-011**: System MUST persist all markets, matches, opportunities, and scan logs to a database.
- **FR-012**: System MUST provide a CLI with commands for single scan, continuous watch, report generation, and match audit.
- **FR-013**: System MUST support a dry-run/mock mode that operates on test fixture data when API keys are not present.
- **FR-014**: System MUST flag opportunities with thin liquidity (max size below configurable threshold) as depth_risk.
- **FR-015**: System MUST respect venue API rate limits with exponential backoff and Retry-After header support.

### Key Entities

- **Market**: A binary prediction market on a single venue, capturing event identity, pricing (bid/ask for YES and NO), volume, expiry, fee rate, and raw API data.
- **MatchResult**: The outcome of evaluating two cross-venue contracts for semantic equivalence, including confidence score, resolution risks, and LLM reasoning. Cached with TTL.
- **ArbOpportunity**: A detected mispricing between two matched markets, with calculated spread, fees, profit, size, and risk flags.
- **ExecutionTicket**: A human-readable instruction describing what to buy/sell on each venue to capture an arbitrage opportunity. Never auto-executed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A complete scan cycle (ingest, match, calculate, output) completes in under 2 minutes for up to 1,000 active markets per venue.
- **SC-002**: The pre-filter reduces the all-pairs candidate set by at least 80% before LLM evaluation.
- **SC-003**: Match cache hit rate exceeds 90% during continuous monitoring (most contracts persist across scan cycles).
- **SC-004**: Fee calculations match hand-computed examples for at least 5 different scenarios covering both fee models.
- **SC-005**: The system correctly identifies arbitrage in at least 3 known historical mispricings (validated against manually computed expected values).
- **SC-006**: Webhook notifications are delivered within 10 seconds of opportunity detection.
- **SC-007**: The system runs for 24+ hours in watch mode without memory leaks, crashes, or data corruption.
- **SC-008**: All CLI commands produce valid, parseable output and exit with appropriate codes.
- **SC-009**: Dry-run mode with test fixtures produces deterministic, reproducible results without any network calls.
- **SC-010**: The system handles venue API downtime gracefully — scanning continues with the available venue and logs clear warnings for the unavailable one.

### Assumptions

- Polymarket's public API remains accessible without authentication for market data reads.
- Kalshi's API requires an API key but does not require per-request cryptographic signing beyond Bearer token auth.
- Binary markets (YES/NO) are the only market type in scope; multi-outcome markets are excluded.
- The user has a running PostgreSQL instance with pgvector extension available.
- The chosen LLM is cost-effective enough for evaluating ~200 candidate pairs per scan cycle.
