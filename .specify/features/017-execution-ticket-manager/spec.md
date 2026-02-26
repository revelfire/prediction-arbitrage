# 017 — Execution Ticket Manager

## Overview

Add a full-featured execution ticket management section to the dashboard for viewing, approving, expiring, and annotating flippening execution tickets. Track which tickets were acted on by the operator and record actual vs. expected P&L for performance analysis.

## Motivation

The system generates execution tickets (structured trade recommendations) but currently provides minimal UI for managing them. Operators must manually track which tickets they acted on and what the actual results were. A proper ticket manager closes the feedback loop between signal generation and real-world execution.

## Functional Requirements

### FR-001: Ticket List View

Display all execution tickets in a sortable, filterable table:
- Ticket ID (short UUID)
- Market title
- Category / Category type
- Side (YES/NO)
- Entry price / Target exit / Stop loss
- Suggested size ($)
- Expected profit %
- Status (pending / approved / executed / expired / cancelled)
- Created timestamp
- Time since creation

Default sort: newest first. Highlight active (pending/approved) tickets.

### FR-002: Ticket Actions

Each ticket supports these actions via buttons:
- **Approve**: Mark ticket as operator-approved (intent to trade). Changes status to `approved`.
- **Execute**: Mark ticket as executed (operator actually placed the trade). Prompts for actual entry price.
- **Expire**: Mark ticket as expired (opportunity passed). Adds expiry timestamp.
- **Cancel**: Mark ticket as cancelled (operator decided against it). Optionally add a reason note.

Actions are implemented via `PATCH /api/flippening/tickets/{ticket_id}` endpoint.

### FR-003: Execution Tracking

When a ticket is marked as "executed", the operator provides:
- Actual entry price (may differ from suggested)
- Actual size (may differ from suggested)
- Optional: execution notes

When the corresponding exit signal fires, the system records:
- Actual exit price
- Actual P&L (computed from actual entry + exit)
- Slippage (actual entry - suggested entry)

### FR-004: Performance Summary

Aggregated metrics panel:
- Total tickets generated (all time / last 7 days / last 24 hours)
- Tickets executed vs. total approved (execution rate)
- Average expected P&L vs. average actual P&L
- Average slippage
- Win rate on executed tickets
- Total actual P&L
- Breakdown by category

### FR-005: Ticket Detail Modal

Clicking a ticket opens a detail modal showing:
- Full market information
- Entry signal details (confidence, spike magnitude, direction)
- Baseline data at time of signal
- Exit signal details (if closed)
- Price chart around the signal window (if tick data available)
- Notes / annotations

### FR-006: Annotation System

Operators can add free-text notes to any ticket. Notes persist in the database. Use cases:
- "Skipped — spread too wide on Polymarket"
- "Executed manually at 0.42 instead of 0.40"
- "False signal — baseline was drifting"

### FR-007: REST API Endpoints

- `GET /api/flippening/tickets?status=pending&category=nba&limit=50` — List tickets with filters.
- `GET /api/flippening/tickets/{ticket_id}` — Single ticket detail.
- `PATCH /api/flippening/tickets/{ticket_id}` — Update status, add execution data, annotate.
- `GET /api/flippening/tickets/summary?days=7` — Performance summary.

### FR-008: Ticket Expiry Rules

Tickets auto-expire when:
- The max hold time passes without operator action.
- The corresponding market resolves.
- A new contradictory signal is generated for the same market.

Auto-expired tickets show "auto_expired" status with the reason.

## Database Changes

### New table: `flippening_ticket_actions`

```sql
CREATE TABLE flippening_ticket_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id TEXT NOT NULL REFERENCES flippening_tickets(id),
    action TEXT NOT NULL,  -- approve, execute, expire, cancel, annotate
    actual_entry_price NUMERIC,
    actual_size_usd NUMERIC,
    actual_exit_price NUMERIC,
    actual_pnl NUMERIC,
    slippage NUMERIC,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Modify `flippening_tickets`

Add columns: `status TEXT DEFAULT 'pending'`, `category TEXT`, `category_type TEXT`.

## Edge Cases

- EC-001: Ticket for resolved market → Auto-expire with reason "market_resolved".
- EC-002: Multiple executions of same ticket → Only allow one `execute` action; subsequent attempts rejected.
- EC-003: Actual entry price outside reasonable range → Warn but allow (operator knows best).
- EC-004: No tickets exist → Show "No tickets yet — run flip-watch to generate signals" placeholder.

## Success Criteria

- SC-001: Ticket status updates reflect in < 1 second after action.
- SC-002: Performance summary correctly computes actual vs. expected P&L.
- SC-003: Annotations persist and display correctly after page refresh.
- SC-004: Auto-expiry triggers within one scan cycle of the expiry condition.
- SC-005: All quality gates pass.

## Out of Scope

- Automated order placement (system is detection-only, per constitution Principle I).
- Real-time ticket notifications in the dashboard (use existing webhook alerts).
- Bulk ticket operations (approve/expire multiple at once).
- Integration with exchange APIs for actual execution verification.
