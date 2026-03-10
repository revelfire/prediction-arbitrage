"""Prompt templates for the AI trade critic."""

from __future__ import annotations

from typing import Any

CRITIC_SYSTEM_PROMPT = """You are a trade risk analyst for a prediction market \
arbitrage system. Your job is to evaluate trades for kill signals -- reasons \
NOT to execute.

Focus on:
1. Stale or suspicious pricing data
2. Anomalous spreads that may indicate data errors
3. Resolution ambiguity or rule changes
4. Category-specific risks (event cancellations, rule changes)
5. Liquidity concerns or one-sided order books
6. Market manipulation indicators

You are NOT evaluating the full trade thesis. The system has already validated \
that an arbitrage opportunity exists. You are looking for red flags that the \
mechanical checks may have missed.

Respond with ONLY a JSON object:
{
    "approved": true/false,
    "risk_flags": ["flag1", "flag2"],
    "reasoning": "Brief explanation",
    "confidence": 0.0-1.0
}

If you find no concerning signals, set approved=true with an empty risk_flags \
list. Only reject (approved=false) when you identify concrete risk factors."""

FLIPPENING_CRITIC_SYSTEM_PROMPT = """You are a trade risk analyst for a prediction \
market mean-reversion system. Your job is to evaluate trades for kill signals -- \
reasons NOT to execute.

This is a FLIPPENING trade (single-venue mean reversion on Polymarket only). \
There is NO Kalshi leg. The strategy buys a contract whose price has spiked above \
its historical baseline, betting that it will revert.

Focus on:
1. Market resolution imminent (game already over, event resolved)
2. Legitimate reason for the price spike (real news, game-changing event)
3. Entry price so extreme (>0.90 or <0.10) that reversion is unlikely
4. Market liquidity concerns on Polymarket
5. Category-specific risks (event cancellations, rule changes)

Do NOT flag large baseline deviation as suspicious -- that IS the signal. \
Do NOT flag missing Kalshi data -- this is a single-venue trade.

Respond with ONLY a JSON object:
{
    "approved": true/false,
    "risk_flags": ["flag1", "flag2"],
    "reasoning": "Brief explanation",
    "confidence": 0.0-1.0
}

If you find no concerning signals, set approved=true with an empty risk_flags \
list. Only reject (approved=false) when you identify concrete risk factors."""


def _extract_common_fields(
    ticket: dict[str, Any],
    market_context: dict[str, Any],
) -> tuple[str, str, str, str, str]:
    """Extract fields shared by both prompt builders.

    Returns:
        Tuple of (confidence, category, title, flags_text, arb_id).
    """
    confidence = market_context.get("confidence", "N/A")
    category = market_context.get("category", "unknown")
    title = market_context.get("title", "Unknown market")
    mechanical_flags = market_context.get("mechanical_flags", [])
    flags_text = "\n".join(f"  - {f}" for f in mechanical_flags) if mechanical_flags else "  None"
    arb_id = ticket.get("arb_id", "unknown")
    return confidence, category, title, flags_text, arb_id


def build_flip_critic_prompt(
    ticket: dict[str, Any],
    market_context: dict[str, Any],
) -> str:
    """Build the user prompt for the flippening trade critic.

    Args:
        ticket: Execution ticket data.
        market_context: Additional market context.

    Returns:
        Formatted user prompt string.
    """
    confidence, category, title, flags_text, arb_id = _extract_common_fields(ticket, market_context)
    entry_price = market_context.get("entry_price", "N/A")
    side = market_context.get("side", "YES")
    deviation_pct = market_context.get("baseline_deviation_pct", "N/A")
    market_id = market_context.get("market_id", "N/A")
    price_age_sec = market_context.get("price_age_seconds", "N/A")
    return f"""Evaluate this mean-reversion trade for risk:

Market: {title}
Category: {category}
Market ID: {market_id}
Strategy: Buy {side} on Polymarket (single venue, no Kalshi leg)
Entry price: {entry_price}
Baseline deviation: {deviation_pct} (larger = stronger reversion signal)
Confidence: {confidence}
Price age: {price_age_sec}s

Mechanical flags raised:
{flags_text}

Arb ID: {arb_id}

Evaluate for kill signals and respond with JSON only."""


def build_arb_critic_prompt(
    ticket: dict[str, Any],
    market_context: dict[str, Any],
) -> str:
    """Build the user prompt for the arbitrage trade critic.

    Args:
        ticket: Execution ticket data.
        market_context: Additional market context.

    Returns:
        Formatted user prompt string.
    """
    confidence, category, title, flags_text, arb_id = _extract_common_fields(ticket, market_context)
    spread = market_context.get("spread_pct", "N/A")
    poly_price = market_context.get("poly_yes_price", "N/A")
    kalshi_price = market_context.get("kalshi_yes_price", "N/A")
    poly_depth = market_context.get("poly_depth", "N/A")
    kalshi_depth = market_context.get("kalshi_depth", "N/A")
    price_age_sec = market_context.get("price_age_seconds", "N/A")

    return f"""Evaluate this arbitrage trade for risk:

Market: {title}
Category: {category}
Spread: {spread}
Confidence: {confidence}

Polymarket YES price: {poly_price}
Kalshi YES price: {kalshi_price}
Polymarket book depth: {poly_depth} contracts
Kalshi book depth: {kalshi_depth} contracts
Price age: {price_age_sec}s

Mechanical flags raised:
{flags_text}

Arb ID: {arb_id}

Evaluate for kill signals and respond with JSON only."""
