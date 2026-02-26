"""SQL query constants for tick capture and replay tables."""

INSERT_TICK = """
INSERT INTO flippening_price_ticks
    (market_id, token_id, yes_bid, yes_ask, no_bid, no_ask,
     timestamp, synthetic_spread, book_depth_bids, book_depth_asks)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
"""

INSERT_DRIFT = """
INSERT INTO flippening_baseline_drifts
    (market_id, old_yes, new_yes, drift_reason, drifted_at)
VALUES ($1, $2, $3, $4, $5)
"""

SELECT_TICKS_BY_MARKET = """
SELECT market_id, token_id, yes_bid, yes_ask, no_bid, no_ask,
       timestamp, synthetic_spread, book_depth_bids, book_depth_asks
FROM flippening_price_ticks
WHERE market_id = $1 AND timestamp >= $2 AND timestamp <= $3
ORDER BY timestamp
"""

SELECT_DRIFTS_BY_MARKET = """
SELECT market_id, old_yes, new_yes, drift_reason, drifted_at
FROM flippening_baseline_drifts
WHERE market_id = $1 AND drifted_at >= $2 AND drifted_at <= $3
ORDER BY drifted_at
"""

SELECT_DISTINCT_MARKETS = """
SELECT DISTINCT t.market_id
FROM flippening_price_ticks t
JOIN flippening_baselines b ON b.market_id = t.market_id
WHERE (b.sport = $1 OR b.category = $1)
  AND t.timestamp >= $2
  AND t.timestamp <= $3
"""

SELECT_BASELINE = """
SELECT market_id, token_id, baseline_yes, baseline_no, sport,
       category, category_type, baseline_strategy,
       game_start_time, captured_at, late_join
FROM flippening_baselines
WHERE market_id = $1
ORDER BY captured_at DESC
LIMIT 1
"""

DELETE_OLD_TICKS = """
DELETE FROM flippening_price_ticks
WHERE timestamp < $1
"""
