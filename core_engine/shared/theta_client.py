"""Async HTTP client for Theta Terminal v3. Caller controls concurrency."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Optional

import aiohttp

from .config import CFG, Config

log = logging.getLogger("core_engine.shared.theta")


class ThetaTerminalDown(RuntimeError):
    pass


def heartbeat(cfg: Config = CFG, verbose: bool = True) -> dict:
    """Verify Java Terminal responds. Synchronous, blocking (uses urllib)."""
    url = f"{cfg.THETA_BASE}/v3/stock/snapshot/quote"
    params = "symbol=SPY&format=json"
    last_err: Exception | None = None
    for attempt in range(1, cfg.HEARTBEAT_RETRIES + 1):
        try:
            req = urllib.request.Request(
                f"{url}?{params}", headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=cfg.THETA_TIMEOUT_S) as resp:
                if resp.status != 200:
                    raise ThetaTerminalDown(f"HTTP {resp.status}")
                payload = resp.read().decode("utf-8", errors="replace")
                if verbose:
                    log.info("Heartbeat OK (attempt %s): %s", attempt, payload[:120])
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    return {"raw": payload.strip()}
        except (urllib.error.URLError, ThetaTerminalDown, TimeoutError) as exc:
            last_err = exc
            wait = cfg.HEARTBEAT_BACKOFF_S * (2 ** (attempt - 1))
            log.warning(
                "Heartbeat attempt %s/%s failed: %s. Retry in %.1fs",
                attempt,
                cfg.HEARTBEAT_RETRIES,
                exc,
                wait,
            )
            time.sleep(wait)
    raise ThetaTerminalDown(
        f"Theta Terminal at {cfg.THETA_BASE} unreachable after "
        f"{cfg.HEARTBEAT_RETRIES} attempts: {last_err}"
    )


class AsyncThetaClient:
    """Async HTTP client for Theta Terminal v3.

    Concurrency is caller-controlled. The client provides:
    - Retry logic (3 attempts with exponential backoff)
    - Optional per-request rate limiting (REQUESTS_PER_SECOND)
    - Response parsing (via parse.py)

    The caller MUST manage its own asyncio.Semaphore and
    acquire it before calling get(). This lets the caller
    use separate semaphores for different endpoint tiers:
        opt_sem = asyncio.Semaphore(4)   # Standard tier options
        stk_sem = asyncio.Semaphore(2)   # Value tier stock
    """

    RETRY_STATUS = frozenset({429, 471, 472, 474, 502, 503, 504, 570, 571})

    def __init__(self, cfg: Config = CFG):
        self.cfg = cfg
        self._min_interval = (
            1.0 / cfg.REQUESTS_PER_SECOND if cfg.REQUESTS_PER_SECOND > 0 else 0.0
        )
        self._next_slot = 0.0
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=self.cfg.THETA_TIMEOUT_S)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session:
            await self._session.close()
            self._session = None

    async def _throttle(self) -> None:
        """Token-bucket rate limiter, if REQUESTS_PER_SECOND > 0."""
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_slot = now + self._min_interval

    async def get(
        self,
        path: str,
        params: Optional[dict] = None,
        ticker: Optional[str] = None,
    ) -> tuple[int, Any]:
        """Execute a GET request against Theta Terminal v3.

        Args:
            path: URL path relative to THETA_BASE, e.g. "/v3/option/history/greeks/first_order"
            params: Query parameters as a dict
            ticker: Optional ticker for logging (unused without IngestionLogger)

        Returns:
            (status_code, parsed_body) where parsed_body is a dict, list[dict],
            pd.DataFrame, or None. status == -1 on unrecoverable error.
        """
        assert self._session is not None
        url = f"{self.cfg.THETA_BASE}{path}"
        q = dict(params or {})
        q.setdefault("format", "json")
        last_exc: Exception | None = None

        for attempt in range(3):
            try:
                await self._throttle()
                async with self._session.get(url, params=q) as resp:
                    body = await resp.text()
                    status = resp.status
                    if 200 <= status < 300:
                        from .parse import parse_response_body

                        return status, parse_response_body(body, status)
                    if status in self.RETRY_STATUS:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    return status, {"error": body[:500], "status": status}
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (2 ** attempt))

        return -1, {"error": str(last_exc)}