"""Exponential backoff retry logic for ThetaData fetches."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from dataingestion.config import (
    FETCH_BASE_DELAY,
    FETCH_MAX_DELAY,
    FETCH_MAX_RETRIES,
    FETCH_NON_RETRYABLE_STATUS,
    FETCH_RETRYABLE_STATUS,
)

if TYPE_CHECKING:
    from core_engine.shared.theta_client import AsyncThetaClient

log = logging.getLogger("dataingestion.retry")


def _is_retryable_error(error: Exception) -> bool:
    """Determine whether an exception should trigger a retry.

    Retryable categories:
        - HTTP 5xx (server errors) and 429 (rate-limit) — checked via
          error.status attribute.
        - asyncio.TimeoutError, ConnectionError, OSError.

    Non-retryable categories (raises immediately):
        - HTTP 4xx except 429 (client errors).
        - Any exception without a recognized retryable characteristic.

    Args:
        error: The exception raised during a fetch attempt.

    Returns:
        True if the error is retryable, False otherwise.
    """
    # Check for HTTP status code (works with any exception that has a .status attr)
    status = getattr(error, "status", 0)
    if isinstance(status, int):
        # Known non-retryable statuses → fail fast
        if status in FETCH_NON_RETRYABLE_STATUS:
            return False
        # Known retryable statuses → retry
        if status in FETCH_RETRYABLE_STATUS:
            return True

    # Check for timeout/connection errors
    if isinstance(error, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True

    # Default: non-retryable
    return False


async def fetch_with_retry(fetch_func: Callable[..., Any], *args, _sem: asyncio.Semaphore | None = None, **kwargs: Any) -> Any:
    """Execute an async fetch function with exponential backoff retry.

    Retries on retryable errors (5xx, 429, timeouts, connection errors)
    up to FETCH_MAX_RETRIES times with exponential backoff and 10% jitter.
    Non-retryable errors (4xx except 429) propagate immediately.

    The semaphore is acquired per attempt, then released during backoff
    sleep so that a stalled fetch does not block the semaphore for others.

    Args:
        fetch_func: Async callable that returns a result (e.g. DataFrame).
        *args: Passed through to fetch_func.
        _sem: Optional asyncio.Semaphore — acquired for each attempt only,
            released during sleep/backoff.
        **kwargs: Passed through to fetch_func.

    Returns:
        The result from a successful call to fetch_func (typically a DataFrame).

    Raises:
        The last exception if all retries are exhausted or the error is
        non-retryable.
    """
    last_exception: Exception | None = None

    for attempt in range(FETCH_MAX_RETRIES + 1):
        try:
            if _sem is not None:
                wait_start = time.monotonic()
                async with _sem:
                    wait_s = time.monotonic() - wait_start
                    log.debug("semaphore_acquired", extra={
                        "wait_seconds": round(wait_s, 4),
                    })
                    result = await fetch_func(*args, **kwargs)
            else:
                result = await fetch_func(*args, **kwargs)
            return result
        except Exception as e:
            last_exception = e

            if not _is_retryable_error(e):
                log.warning("Non-retryable error in fetch, failing immediately: %s", e)
                raise

            if attempt < FETCH_MAX_RETRIES:
                delay = min(FETCH_BASE_DELAY * (2 ** attempt), FETCH_MAX_DELAY)
                delay += delay * 0.1  # Jitter: 10% of delay
                log.warning(
                    "Fetch failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, FETCH_MAX_RETRIES, delay, e
                )
                await asyncio.sleep(delay)
            else:
                log.error("Fetch failed after %d retries: %s", FETCH_MAX_RETRIES, e)

    raise last_exception  # type: ignore[misc]