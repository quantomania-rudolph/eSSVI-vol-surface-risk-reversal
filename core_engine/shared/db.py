"""Minimal database logging for Theta Data ingestion (optional)."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

import asyncpg

from .config import CFG, Config


class IngestionLogger:
    """Buffered ingestion_log writer — avoids per-request connection churn."""

    def __init__(self, flush_size: int = 50):
        self._buf: list[tuple] = []
        self._flush_size = flush_size
        self._lock = asyncio.Lock()

    async def log(
        self,
        conn: asyncpg.Connection,
        ticker: Optional[str],
        endpoint: str,
        params: dict,
        response_ms: int,
        http_status: int,
        rows: int,
        err: Optional[str],
    ) -> None:
        self._buf.append(
            (ticker, endpoint, params, response_ms, http_status, rows, err)
        )
        if len(self._buf) >= self._flush_size:
            await self.flush(conn)

    async def flush(self, conn: asyncpg.Connection) -> None:
        async with self._lock:
            if not self._buf:
                return
            await conn.executemany(
                """
                INSERT INTO ingestion_log
                    (ticker, endpoint, request_params, response_ms, http_status,
                     rows_ingested, error_message)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7)
                """,
                [
                    (
                        t,
                        ep,
                        json.dumps(p or {}),
                        ms,
                        st,
                        n,
                        e,
                    )
                    for t, ep, p, ms, st, n, e in self._buf
                ],
            )
            self._buf.clear()