"""SQL query constants for WebSocket telemetry dashboard endpoints."""

GET_WS_TELEMETRY_LATEST = """
SELECT snapshot_time, messages_received, messages_parsed,
       messages_failed, messages_ignored, schema_match_rate,
       book_cache_hit_rate, connection_state
FROM ws_telemetry
ORDER BY snapshot_time DESC
LIMIT 1;
"""

GET_WS_TELEMETRY_HISTORY = """
SELECT snapshot_time, messages_received, messages_parsed,
       messages_failed, messages_ignored, schema_match_rate,
       book_cache_hit_rate, connection_state
FROM ws_telemetry
WHERE snapshot_time >= $1
ORDER BY snapshot_time;
"""

GET_WS_TELEMETRY_EVENTS = """
SELECT
    t.snapshot_time AS event_time,
    CASE
        WHEN t.connection_state = 'disconnected' THEN 'ws_disconnected'
        WHEN t.connection_state = 'connected'
             AND prev.connection_state = 'disconnected' THEN 'stall_reconnect'
        WHEN t.connection_state = 'connected'
             AND prev.connection_state IS NULL THEN 'ws_connected'
        WHEN t.messages_received = prev.messages_received
             AND t.connection_state = 'connected' THEN 'stall_detected'
        ELSE 'state_change'
    END AS event_type,
    prev.connection_state AS prev_state,
    t.connection_state AS new_state,
    t.messages_received AS messages_received_at_event
FROM ws_telemetry t
LEFT JOIN LATERAL (
    SELECT connection_state, messages_received
    FROM ws_telemetry p
    WHERE p.snapshot_time < t.snapshot_time
    ORDER BY p.snapshot_time DESC
    LIMIT 1
) prev ON TRUE
WHERE t.connection_state != 'connected'
   OR (prev.connection_state IS NOT NULL
       AND prev.connection_state != t.connection_state)
   OR (prev.messages_received IS NOT NULL
       AND t.messages_received = prev.messages_received
       AND t.connection_state = 'connected')
ORDER BY t.snapshot_time DESC
LIMIT $1;
"""

COUNT_STALL_EVENTS_1H = """
SELECT COUNT(*) AS cnt
FROM ws_telemetry t
LEFT JOIN LATERAL (
    SELECT messages_received
    FROM ws_telemetry p
    WHERE p.snapshot_time < t.snapshot_time
    ORDER BY p.snapshot_time DESC
    LIMIT 1
) prev ON TRUE
WHERE t.snapshot_time >= NOW() - INTERVAL '1 hour'
  AND t.connection_state = 'connected'
  AND prev.messages_received IS NOT NULL
  AND t.messages_received = prev.messages_received;
"""
