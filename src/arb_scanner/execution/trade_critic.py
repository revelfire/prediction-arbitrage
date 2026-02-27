"""AI trade critic -- pre-execution risk gate using Claude."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from arb_scanner.execution._critic_prompts import (
    CRITIC_SYSTEM_PROMPT,
    build_critic_prompt,
)
from arb_scanner.models._auto_exec_config import CriticConfig
from arb_scanner.models.auto_execution import CriticVerdict
from arb_scanner.models.config import ClaudeConfig

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.trade_critic",
)


class TradeCritic:
    """AI-powered trade critic that evaluates risk before execution.

    Runs mechanical checks first; only calls Claude when red flags
    are detected. Fails open on API errors.

    Args:
        critic_config: Critic-specific configuration.
        claude_config: Claude API configuration (for api_key fallback).
    """

    def __init__(
        self,
        critic_config: CriticConfig,
        claude_config: ClaudeConfig,
    ) -> None:
        """Initialize the trade critic.

        Args:
            critic_config: Critic-specific configuration.
            claude_config: Claude API configuration.
        """
        self._config = critic_config
        self._api_key = critic_config.api_key or claude_config.api_key

    async def evaluate(
        self,
        ticket: dict[str, Any],
        preflight: dict[str, Any],
        market_context: dict[str, Any],
    ) -> CriticVerdict:
        """Evaluate a trade for risk signals.

        Args:
            ticket: Execution ticket data.
            preflight: Preflight check results.
            market_context: Market context including prices and depth.

        Returns:
            CriticVerdict with approval decision.
        """
        if not self._config.enabled:
            return CriticVerdict(approved=True, skipped=True)

        flags = self._check_mechanical_flags(ticket, preflight, market_context)
        market_context["mechanical_flags"] = flags

        if not flags:
            logger.debug("critic_skipped", reason="no_mechanical_flags")
            return CriticVerdict(approved=True, skipped=True)

        if len(flags) > self._config.max_risk_flags:
            return CriticVerdict(
                approved=False,
                risk_flags=flags,
                reasoning=f"Too many mechanical flags ({len(flags)})",
                confidence=0.9,
            )

        return await self._call_critic(ticket, preflight, market_context)

    def _check_mechanical_flags(
        self,
        ticket: dict[str, Any],
        preflight: dict[str, Any],
        context: dict[str, Any],
    ) -> list[str]:
        """Run mechanical risk checks before calling Claude.

        Args:
            ticket: Execution ticket data.
            preflight: Preflight check results.
            context: Market context.

        Returns:
            List of risk flag strings.
        """
        flags: list[str] = []

        age = context.get("price_age_seconds", 0)
        if age > self._config.price_staleness_seconds:
            flags.append(f"stale_data: price age {age}s")

        spread = context.get("spread_pct", 0)
        if isinstance(spread, (int, float)) and spread > self._config.anomaly_spread_pct:
            flags.append(f"anomalous_spread: {spread:.2%}")

        for venue in ("poly_depth", "kalshi_depth"):
            depth = context.get(venue, 0)
            if isinstance(depth, (int, float)) and depth < self._config.min_book_depth_contracts:
                flags.append(f"low_depth_{venue}: {depth}")

        poly_yes = context.get("poly_yes_price", 0)
        kalshi_yes = context.get("kalshi_yes_price", 0)
        if isinstance(poly_yes, (int, float)) and isinstance(kalshi_yes, (int, float)):
            for price in (poly_yes, kalshi_yes):
                if price > 0:
                    complement = 1.0 - price
                    if abs(complement + price - 1.0) > 0.05:
                        flags.append(f"price_symmetry: yes={price:.3f}")
                        break

        title = context.get("title", "").lower()
        risk_terms = ["cancelled", "postponed", "suspended", "voided", "disputed"]
        for term in risk_terms:
            if term in title:
                flags.append(f"category_risk: '{term}' in title")
                break

        return flags

    async def _call_critic(
        self,
        ticket: dict[str, Any],
        preflight: dict[str, Any],
        market_context: dict[str, Any],
    ) -> CriticVerdict:
        """Call Claude for risk evaluation.

        Args:
            ticket: Execution ticket data.
            preflight: Preflight check results.
            market_context: Market context.

        Returns:
            CriticVerdict from Claude or fail-open verdict on error.
        """
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=self._api_key)
            prompt = build_critic_prompt(ticket, preflight, market_context)

            async with asyncio.timeout(self._config.timeout_seconds):
                response = await client.messages.create(
                    model=self._config.model,
                    max_tokens=512,
                    system=CRITIC_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )

            block = response.content[0]
            raw = block.text if hasattr(block, "text") else str(block)
            return self._parse_verdict(raw, market_context.get("mechanical_flags", []))

        except TimeoutError:
            logger.warning("critic_timeout")
            return CriticVerdict(
                approved=True,
                error="timeout",
                risk_flags=market_context.get("mechanical_flags", []),
            )
        except Exception as exc:
            logger.warning("critic_error", error=str(exc))
            return CriticVerdict(approved=True, error=str(exc))

    def _parse_verdict(self, raw: str, mechanical_flags: list[str]) -> CriticVerdict:
        """Parse Claude's JSON response into a CriticVerdict.

        Args:
            raw: Raw text response from Claude.
            mechanical_flags: Flags from mechanical checks.

        Returns:
            Parsed CriticVerdict.
        """
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[: -len("```")]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("critic_parse_failed", raw=raw[:200])
            return CriticVerdict(
                approved=True,
                error="parse_failed",
                risk_flags=mechanical_flags,
            )

        return CriticVerdict(
            approved=data.get("approved", True),
            risk_flags=data.get("risk_flags", mechanical_flags),
            reasoning=data.get("reasoning", ""),
            confidence=data.get("confidence", 0.5),
        )
