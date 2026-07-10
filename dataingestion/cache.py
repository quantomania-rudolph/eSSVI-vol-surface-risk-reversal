"""Bounded LRU cache with TTL for DataFrame caching."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


@dataclass
class CacheEntry:
    """Single cache entry with metadata."""
    data: pd.DataFrame
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hits: int = 0


class BoundedCache:
    """LRU cache with TTL and max size."""

    def __init__(self, max_size: int, ttl_hours: int):
        self.max_size = max_size
        self.ttl = timedelta(hours=ttl_hours)
        self._cache: dict[Any, CacheEntry] = {}
        self._access_order: list[Any] = []
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: Any) -> pd.DataFrame | None:
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None

            # Check TTL
            if datetime.now(timezone.utc) - entry.created_at > self.ttl:
                self._evict(key)
                self.misses += 1
                return None

            # Update LRU
            self._access_order.remove(key)
            self._access_order.append(key)
            entry.hits += 1
            self.hits += 1
            return entry.data

    async def set(self, key: Any, value: pd.DataFrame):
        async with self._lock:
            # Evict expired first
            self._evict_expired()

            # Evict LRU if at capacity
            while len(self._cache) >= self.max_size:
                self._evict(self._access_order[0])

            self._cache[key] = CacheEntry(data=value)
            self._access_order.append(key)

    def _evict(self, key: Any):
        self._cache.pop(key, None)
        if key in self._access_order:
            self._access_order.remove(key)

    def _evict_expired(self):
        now = datetime.now(timezone.utc)
        expired = [
            k for k, v in self._cache.items()
            if now - v.created_at > self.ttl
        ]
        for k in expired:
            self._evict(k)

    async def clear(self):
        async with self._lock:
            self._cache.clear()
            self._access_order.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0,
        }