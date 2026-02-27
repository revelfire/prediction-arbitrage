"""SQL query constants for the repository layer."""

UPSERT_MARKET = """
INSERT INTO markets (
    venue, event_id, title, description, resolution_criteria,
    yes_bid, yes_ask, no_bid, no_ask, volume_24h,
    expiry, fees_pct, fee_model, last_updated, raw_data
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
ON CONFLICT (venue, event_id) DO UPDATE SET
    title=EXCLUDED.title, description=EXCLUDED.description,
    resolution_criteria=EXCLUDED.resolution_criteria,
    yes_bid=EXCLUDED.yes_bid, yes_ask=EXCLUDED.yes_ask,
    no_bid=EXCLUDED.no_bid, no_ask=EXCLUDED.no_ask,
    volume_24h=EXCLUDED.volume_24h, expiry=EXCLUDED.expiry,
    fees_pct=EXCLUDED.fees_pct, fee_model=EXCLUDED.fee_model,
    last_updated=EXCLUDED.last_updated, raw_data=EXCLUDED.raw_data;
"""

UPSERT_MATCH = """
INSERT INTO match_results (
    poly_event_id, kalshi_event_id, match_confidence,
    resolution_equivalent, resolution_risks, safe_to_arb,
    reasoning, matched_at, ttl_expires
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
ON CONFLICT (poly_event_id, kalshi_event_id) DO UPDATE SET
    match_confidence=EXCLUDED.match_confidence,
    resolution_equivalent=EXCLUDED.resolution_equivalent,
    resolution_risks=EXCLUDED.resolution_risks,
    safe_to_arb=EXCLUDED.safe_to_arb,
    reasoning=EXCLUDED.reasoning,
    matched_at=EXCLUDED.matched_at,
    ttl_expires=EXCLUDED.ttl_expires;
"""

INSERT_OPP = """
INSERT INTO arb_opportunities (
    id, poly_event_id, kalshi_event_id, buy_venue, sell_venue,
    cost_per_contract, gross_profit, net_profit, net_spread_pct,
    max_size, annualized_return, depth_risk, detected_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13);
"""

INSERT_TICKET = """
INSERT INTO execution_tickets (
    arb_id, leg_1, leg_2, expected_cost, expected_profit, status
) VALUES ($1,$2,$3,$4,$5,$6);
"""

INSERT_SCAN_LOG = """
INSERT INTO scan_logs (
    id, started_at, completed_at, poly_markets_fetched,
    kalshi_markets_fetched, candidate_pairs, llm_evaluations,
    opportunities_found, errors
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9);
"""

UPSERT_SCAN_LOG = """
INSERT INTO scan_logs (
    id, started_at, completed_at, poly_markets_fetched,
    kalshi_markets_fetched, candidate_pairs, llm_evaluations,
    opportunities_found, errors
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
ON CONFLICT (id) DO UPDATE SET
    completed_at=EXCLUDED.completed_at,
    poly_markets_fetched=EXCLUDED.poly_markets_fetched,
    kalshi_markets_fetched=EXCLUDED.kalshi_markets_fetched,
    candidate_pairs=EXCLUDED.candidate_pairs,
    llm_evaluations=EXCLUDED.llm_evaluations,
    opportunities_found=EXCLUDED.opportunities_found,
    errors=EXCLUDED.errors;
"""

GET_CACHED_MATCH = """
SELECT poly_event_id, kalshi_event_id, match_confidence,
       resolution_equivalent, resolution_risks, safe_to_arb,
       reasoning, matched_at, ttl_expires
FROM match_results
WHERE poly_event_id = $1 AND kalshi_event_id = $2
  AND ttl_expires > $3;
"""

GET_RECENT_OPPS = """
SELECT id, poly_event_id, kalshi_event_id, buy_venue, sell_venue,
       cost_per_contract, gross_profit, net_profit, net_spread_pct,
       max_size, annualized_return, depth_risk, detected_at
FROM arb_opportunities
ORDER BY detected_at DESC
LIMIT $1;
"""

GET_TICKETS_BY_STATUS = """
SELECT arb_id, leg_1, leg_2, expected_cost, expected_profit,
       status, created_at, COALESCE(ticket_type, 'arbitrage') AS ticket_type
FROM execution_tickets
WHERE ($1::text IS NULL OR status = $1)
  AND expected_profit > 0
ORDER BY created_at DESC
LIMIT $2;
"""

GET_PENDING_TICKETS = """
SELECT arb_id, leg_1, leg_2, expected_cost, expected_profit,
       status, created_at
FROM execution_tickets
WHERE status = 'pending'
  AND expected_profit > 0
ORDER BY created_at DESC;
"""

UPDATE_TICKET_STATUS = """
UPDATE execution_tickets SET status = $2 WHERE arb_id = $1;
"""

EXPIRE_STALE_TICKETS = """
UPDATE execution_tickets
SET status = 'expired'
WHERE status = 'pending'
  AND created_at < NOW() - make_interval(hours => $1)
RETURNING arb_id;
"""

GET_ALL_MATCHES = """
SELECT poly_event_id, kalshi_event_id, match_confidence,
       resolution_equivalent, resolution_risks, safe_to_arb,
       reasoning, matched_at, ttl_expires
FROM match_results
WHERE ($1::boolean OR ttl_expires > NOW())
  AND match_confidence >= $2
ORDER BY matched_at DESC;
"""

GET_TICKETS_WITH_OPPS = """
SELECT t.arb_id, t.leg_1, t.leg_2, t.expected_cost,
       t.expected_profit, t.status, t.created_at,
       o.poly_event_id, o.kalshi_event_id, o.net_spread_pct
FROM execution_tickets t
JOIN arb_opportunities o ON t.arb_id = o.id
WHERE t.expected_profit > 0
ORDER BY t.created_at DESC
LIMIT $1;
"""

GET_TICKET_DETAIL = """
SELECT t.arb_id, t.leg_1, t.leg_2, t.expected_cost,
       t.expected_profit, t.status, t.created_at,
       COALESCE(t.ticket_type, 'arbitrage') AS ticket_type,
       o.poly_event_id, o.kalshi_event_id, o.buy_venue, o.sell_venue,
       o.cost_per_contract, o.gross_profit, o.net_profit,
       o.net_spread_pct, o.max_size, o.annualized_return,
       o.depth_risk, o.detected_at,
       pm.title AS poly_title, pm.raw_data AS poly_raw_data,
       pm.yes_bid AS poly_yes_bid, pm.yes_ask AS poly_yes_ask,
       pm.no_bid AS poly_no_bid, pm.no_ask AS poly_no_ask,
       pm.volume_24h AS poly_volume,
       km.title AS kalshi_title, km.raw_data AS kalshi_raw_data,
       km.yes_bid AS kalshi_yes_bid, km.yes_ask AS kalshi_yes_ask,
       km.no_bid AS kalshi_no_bid, km.no_ask AS kalshi_no_ask,
       km.volume_24h AS kalshi_volume
FROM execution_tickets t
LEFT JOIN arb_opportunities o ON t.arb_id = o.id
LEFT JOIN markets pm ON pm.venue = 'polymarket' AND pm.event_id = o.poly_event_id
LEFT JOIN markets km ON km.venue = 'kalshi' AND km.event_id = o.kalshi_event_id
WHERE t.arb_id = $1;
"""

UPDATE_MARKET_EMBEDDING = """
UPDATE markets SET title_embedding = $3
WHERE venue = $1 AND event_id = $2;
"""

UPDATE_MARKET_EMBEDDING_384 = """
UPDATE markets SET title_embedding_384 = $3
WHERE venue = $1 AND event_id = $2;
"""

GET_CACHED_EMBEDDINGS_384 = """
SELECT venue, event_id, title_embedding_384
FROM markets
WHERE title_embedding_384 IS NOT NULL
  AND (venue, event_id) IN (SELECT * FROM UNNEST($1::text[], $2::text[]));
"""

GET_CACHED_EMBEDDINGS_512 = """
SELECT venue, event_id, title_embedding
FROM markets
WHERE title_embedding IS NOT NULL
  AND (venue, event_id) IN (SELECT * FROM UNNEST($1::text[], $2::text[]));
"""
