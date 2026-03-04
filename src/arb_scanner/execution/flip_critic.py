"""Flippening-specific AI trade critic -- pre-execution risk gate."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from arb_scanner.execution._critic_prompts import (
    FLIPPENING_CRITIC_SYSTEM_PROMPT,
    build_flip_critic_prompt,
)
from arb_scanner.models._auto_exec_config import CriticConfig
from arb_scanner.models.auto_execution import CriticVerdict
from arb_scanner.models.config import ClaudeConfig

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.flip_critic",
    pipeline="flip",
)


class FlipTradeCritic:
    """AI risk gate for flippening trades (single-venue mean reversion).

    Runs mechanical checks first; only calls Claude when flags are detected.
    Fails open on API errors. Does NOT check venue depth (single-venue).

    Args:
        critic_config: Critic-specific configuration.
        claude_config: Claude API configuration (for api_key fallback).
    """

    def __init__(
        self,
        critic_config: CriticConfig,
        claude_config: ClaudeConfig,
    ) -> None:
        """Initialize the flippening trade critic.

        Args:
            critic_config: Critic-specific configuration.
            claude_config: Claude API configuration.
        """
        self._config = critic_config
        self._api_key = critic_config.api_key or claude_config.api_key
        self._consecutive_timeouts: int = 0

    async def evaluate(
        self,
        ticket: dict[str, Any],
        market_context: dict[str, Any],
    ) -> CriticVerdict:
        """Evaluate a flippening trade for risk signals.

        Args:
            ticket: Execution ticket data (arb_id, etc.).
            market_context: Market context including entry_price, side, deviation.

        Returns:
            CriticVerdict with approval decision.
        """
        if not self._config.enabled:
            return CriticVerdict(approved=True, skipped=True)

        arb_id = ticket.get("arb_id", "?")
        flags = self._check_mechanical_flags(market_context)
        market_context["mechanical_flags"] = flags

        if not flags:
            logger.info("flip_critic_clean", arb_id=arb_id)
            return CriticVerdict(approved=True, skipped=True)

        logger.info("flip_critic_flags", arb_id=arb_id, count=len(flags), flags=flags)

        if len(flags) > self._config.max_risk_flags:
            return CriticVerdict(
                approved=False,
                risk_flags=flags,
                reasoning=f"Too many mechanical flags ({len(flags)})",
                confidence=0.9,
            )

        return await self._call_claude(ticket, market_context)

    def _check_mechanical_flags(
        self,
        context: dict[str, Any],
    ) -> list[str]:
        """Run mechanical risk checks for flippening trades.

        No venue depth checks — flippening is single-venue Polymarket only.

        Args:
            context: Market context dict.

        Returns:
            List of risk flag strings.
        """
        flags: list[str] = []

        age = context.get("price_age_seconds", 0)
        if age > self._config.price_staleness_seconds:
            flags.append(f"stale_data: price age {age}s")

        deviation = context.get("baseline_deviation_pct", 0)
        if isinstance(deviation, (int, float)) and deviation > 0.90:
            flags.append(f"anomalous_deviation: {deviation:.2%}")

        title = context.get("title", "").lower()
        risk_terms = ["cancelled", "postponed", "suspended", "voided", "disputed"]
        for term in risk_terms:
            if term in title:
                flags.append(f"category_risk: '{term}' in title")
                break

        return flags

    async def _call_claude(
        self,
        ticket: dict[str, Any],
        market_context: dict[str, Any],
    ) -> CriticVerdict:
        """Call Claude for flippening risk evaluation.

        Args:
            ticket: Execution ticket data.
            market_context: Market context.

        Returns:
            CriticVerdict from Claude or fail-open verdict on error.
        """
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=self._api_key)
            prompt = build_flip_critic_prompt(ticket, market_context)

            async with asyncio.timeout(self._config.timeout_seconds):
                response = await client.messages.create(
                    model=self._config.model,
                    max_tokens=512,
                    system=FLIPPENING_CRITIC_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )

            block = response.content[0]
            raw = block.text if hasattr(block, "text") else str(block)
            verdict = _parse_verdict(raw, market_context.get("mechanical_flags", []))
            self._consecutive_timeouts = 0
            logger.info(
                "flip_critic_verdict",
                approved=verdict.approved,
                confidence=verdict.confidence,
            )
            return verdict

        except TimeoutError:
            self._consecutive_timeouts += 1
            max_t = self._config.max_consecutive_timeouts
            if self._consecutive_timeouts >= max_t:
                return CriticVerdict(
                    approved=False,
                    error=f"timeout_breaker: {self._consecutive_timeouts} consecutive",
                    risk_flags=market_context.get("mechanical_flags", []),
                    reasoning=f"Claude timed out {self._consecutive_timeouts} times",
                )
            return CriticVerdict(
                approved=True,
                error="timeout",
                risk_flags=market_context.get("mechanical_flags", []),
            )
        except Exception as exc:
            logger.warning("flip_critic_error", error=str(exc))
            return CriticVerdict(approved=True, error=str(exc))


def _parse_verdict(raw: str, mechanical_flags: list[str]) -> CriticVerdict:
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
