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


def build_critic_prompt(
    ticket: dict[str, Any],
    preflight: dict[str, Any],
    market_context: dict[str, Any],
) -> str:
    """Build the user prompt for the AI critic.

    Args:
        ticket: Execution ticket data.
        preflight: Preflight check results.
        market_context: Additional market context.

    Returns:
        Formatted user prompt string.
    """
    spread = market_context.get("spread_pct", "N/A")
    confidence = market_context.get("confidence", "N/A")
    category = market_context.get("category", "unknown")
    title = market_context.get("title", "Unknown market")
    poly_price = market_context.get("poly_yes_price", "N/A")
    kalshi_price = market_context.get("kalshi_yes_price", "N/A")
    poly_depth = market_context.get("poly_depth", "N/A")
    kalshi_depth = market_context.get("kalshi_depth", "N/A")
    price_age_sec = market_context.get("price_age_seconds", "N/A")
    mechanical_flags = market_context.get("mechanical_flags", [])

    flags_text = "\n".join(f"  - {f}" for f in mechanical_flags) if mechanical_flags else "  None"

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

Ticket type: {ticket.get("ticket_type", "unknown")}
Arb ID: {ticket.get("arb_id", "unknown")}

Evaluate for kill signals and respond with JSON only."""
