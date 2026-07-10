"""Tests for BoundedCache (P2B async cache safety)."""

import pytest
from datetime import datetime, timezone

from dataingestion.cache import BoundedCache


class TestBoundedCache:
    @pytest.mark.asyncio
    async def test_ttl_eviction(self):
        """Cache entry older than TTL is not returned."""
        cache = BoundedCache(max_size=10, ttl_hours=0)
        # Manually backdate the entry so TTL expiry triggers
        from dataingestion.cache import CacheEntry
        import pandas as pd
        cache._cache["key1"] = CacheEntry(data=pd.DataFrame(), created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        cache._access_order.append("key1")
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_lru_eviction_when_full(self):
        """Oldest entry evicted when max_size reached."""
        cache = BoundedCache(max_size=2, ttl_hours=24)
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")
        assert await cache.get("key1") is None
        assert await cache.get("key2") == "value2"
        assert await cache.get("key3") == "value3"

    @pytest.mark.asyncio
    async def test_hit_stats(self):
        """Cache hit/miss stats are accurate."""
        cache = BoundedCache(max_size=5, ttl_hours=24)
        await cache.set("key1", "value1")
        await cache.get("key1")
        await cache.get("key2")
        stats = cache.stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1
        assert stats["size"] == 1

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        """Missing key returns None."""
        cache = BoundedCache(max_size=5, ttl_hours=24)
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_clear_resets_all(self):
        """Clear removes all entries and resets stats."""
        cache = BoundedCache(max_size=5, ttl_hours=24)
        await cache.set("key1", "value1")
        await cache.get("key1")
        await cache.clear()
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["size"] == 0
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_existing_key(self):
        """Overwriting an existing key refreshes its value and LRU position."""
        cache = BoundedCache(max_size=5, ttl_hours=24)
        await cache.set("key1", "old")
        await cache.set("key1", "new")
        result = await cache.get("key1")
        assert result == "new"