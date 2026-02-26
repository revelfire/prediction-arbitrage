"""SQL query constants for the flippening engine tables."""

INSERT_BASELINE = """
INSERT INTO flippening_baselines
    (market_id, token_id, baseline_yes, baseline_no, sport,
     game_start_time, captured_at, late_join,
     category, category_type, baseline_strategy)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
"""

INSERT_EVENT = """
INSERT INTO flippening_events
    (id, market_id, market_title, baseline_yes, spike_price,
     spike_magnitude, spike_direction, confidence, sport, detected_at,
     category, category_type)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""

INSERT_SIGNAL = """
INSERT INTO flippening_signals
    (id, event_id, signal_type, side, price, target_exit,
     stop_loss, suggested_size, exit_reason, realized_pnl,
     hold_minutes, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""

GET_ACTIVE_SIGNALS = """
SELECT
    e.id AS event_id,
    e.market_id,
    e.market_title,
    e.sport,
    e.confidence,
    e.detected_at,
    s.id AS signal_id,
    s.side,
    s.price AS entry_price,
    s.target_exit,
    s.stop_loss,
    s.suggested_size,
    s.created_at AS entry_at
FROM flippening_signals s
JOIN flippening_events e ON s.event_id = e.id
LEFT JOIN flippening_signals x
    ON x.event_id = s.event_id AND x.signal_type = 'exit'
WHERE s.signal_type = 'entry' AND x.id IS NULL
ORDER BY s.created_at DESC
LIMIT $1
"""

GET_HISTORY = """
SELECT
    e.id AS event_id,
    e.market_id,
    e.market_title,
    e.sport,
    e.category,
    e.category_type,
    e.baseline_yes,
    e.spike_price,
    e.spike_magnitude,
    e.confidence,
    entry.side,
    entry.price AS entry_price,
    entry.target_exit,
    entry.suggested_size,
    entry.created_at AS entry_at,
    ex.price AS exit_price,
    ex.exit_reason,
    ex.realized_pnl,
    ex.hold_minutes,
    ex.created_at AS exit_at
FROM flippening_events e
JOIN flippening_signals entry
    ON entry.event_id = e.id AND entry.signal_type = 'entry'
JOIN flippening_signals ex
    ON ex.event_id = e.id AND ex.signal_type = 'exit'
WHERE ($2::TEXT IS NULL OR e.sport = $2 OR e.category = $2)
  AND ($3::TEXT IS NULL OR e.category_type = $3)
ORDER BY ex.created_at DESC
LIMIT $1
"""

GET_STATS = """
SELECT
    e.sport,
    e.category,
    e.category_type,
    COUNT(*) AS total_signals,
    COUNT(*) FILTER (WHERE ex.exit_reason = 'reversion') AS wins,
    ROUND(
        COUNT(*) FILTER (WHERE ex.exit_reason = 'reversion')::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 2
    ) AS win_rate_pct,
    ROUND(AVG(ex.realized_pnl), 6) AS avg_pnl,
    ROUND(AVG(ex.hold_minutes), 2) AS avg_hold_minutes,
    ROUND(SUM(ex.realized_pnl), 6) AS total_pnl
FROM flippening_events e
JOIN flippening_signals entry
    ON entry.event_id = e.id AND entry.signal_type = 'entry'
JOIN flippening_signals ex
    ON ex.event_id = e.id AND ex.signal_type = 'exit'
WHERE ($1::TEXT IS NULL OR e.sport = $1 OR e.category = $1)
  AND ($2::TEXT IS NULL OR e.category_type = $2)
  AND ($3::TIMESTAMPTZ IS NULL OR e.detected_at >= $3)
GROUP BY e.sport, e.category, e.category_type
ORDER BY total_pnl DESC
"""

GET_RECENT_EVENTS = """
SELECT id, market_id, market_title, baseline_yes, spike_price,
       spike_magnitude, spike_direction, confidence, sport,
       category, category_type, detected_at
FROM flippening_events
WHERE ($2::TEXT IS NULL OR sport = $2 OR category = $2)
ORDER BY detected_at DESC
LIMIT $1
"""

INSERT_DISCOVERY_HEALTH = """
INSERT INTO flippening_discovery_health (
    cycle_timestamp, total_scanned, sports_found, hit_rate,
    by_sport, overrides_applied, exclusions_applied, unclassified_candidates
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8);
"""

GET_DISCOVERY_HEALTH = """
SELECT cycle_timestamp, total_scanned, sports_found, hit_rate,
       by_sport, overrides_applied, exclusions_applied, unclassified_candidates
FROM flippening_discovery_health
ORDER BY cycle_timestamp DESC
LIMIT $1;
"""

INSERT_FLIP_TICKET = """
INSERT INTO execution_tickets (
    arb_id, leg_1, leg_2, expected_cost, expected_profit, status, ticket_type
) VALUES ($1, $2, $3, $4, $5, $6, $7);
"""

INSERT_WS_TELEMETRY = """
INSERT INTO ws_telemetry (
    snapshot_time, messages_received, messages_parsed,
    messages_failed, messages_ignored, schema_match_rate,
    book_cache_hit_rate, connection_state
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8);
"""

GET_WS_TELEMETRY = """
SELECT snapshot_time, messages_received, messages_parsed,
       messages_failed, messages_ignored, schema_match_rate,
       book_cache_hit_rate, connection_state
FROM ws_telemetry
ORDER BY snapshot_time DESC
LIMIT $1;
"""
