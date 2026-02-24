"""Async retry decorator with exponential backoff and jitter."""

import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

import structlog

_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="retry")


def _get_retry_after(exc: BaseException) -> float | None:
    """Extract Retry-After header value from an exception's response."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    retry_after = headers.get("Retry-After")
    if retry_after is None:
        return None
    try:
        return float(retry_after)
    except (ValueError, TypeError):
        return None


def async_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> Callable[[_F], _F]:
    """Async decorator that retries on exception with exponential backoff and jitter.

    Supports Retry-After header if the exception has a response attribute.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Base delay in seconds for exponential backoff.
        max_delay: Maximum delay cap in seconds.

    Returns:
        A decorator that wraps an async function with retry logic.
    """

    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt >= max_retries:
                        break
                    delay = _compute_delay(exc, attempt, base_delay, max_delay)
                    logger.warning(
                        "retry_attempt",
                        function=func.__name__,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return cast(_F, wrapper)

    return decorator


def _compute_delay(
    exc: BaseException,
    attempt: int,
    base_delay: float,
    max_delay: float,
) -> float:
    """Compute the retry delay, respecting Retry-After header if available."""
    retry_after = _get_retry_after(exc)
    if retry_after is not None:
        return retry_after
    jitter: float = random.uniform(0, 1)
    delay: float = base_delay * (2**attempt) + jitter
    return float(min(delay, max_delay))
