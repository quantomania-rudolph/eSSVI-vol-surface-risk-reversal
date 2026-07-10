# EH209: Global Cache Management

## Persona

You are a **memory management engineer** who knows that unbounded global caches in a long-running backfill are memory leaks waiting to happen. A 7-year backfill with 1-minute bars could accumulate millions of rows in `_OHLC_CACHE` and `_RATES_DF` — and if `run_backfill()` is called multiple times, caches persist across runs.

## Mission

**Add cache lifecycle management to `orchestrator.py`: clear caches at start, bound cache size, and provide cache statistics for monitoring.**

## Current State (UNBOUNDED, LEAKY)

```python
# Lines 68-70: Module-level globals (persist across runs!)
_OHLC_CACHE: dict[tuple[dt.date, dt.date], pd.DataFrame] = {}
_RATES_DF: pd.DataFrame | None = None
```

**Problems:**
1. Caches never cleared — memory grows with each chunk
2. Caches persist across multiple `run_backfill()` calls
3. No size limit — could OOM on large backfills
4. No visibility into cache hit/miss rates
5. `_RATES_DF` never refreshed if backfill spans rate changes

## Required Changes

### 1. Cache Configuration (Add to EH-06 config)

```python
# dataingestion/config.py
OHLC_CACHE_MAX_CHUNKS = 50  # Max chunks to cache
OHLC_CACHE_TTL_HOURS = 24   # Auto-evict old entries
RATES_CACHE_TTL_HOURS = 6   # Rates change daily
```

### 2. Cache Management Class

```python
# In orchestrator.py (or new cache module)
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from dataingestion.config import OHLC_CACHE_MAX_CHUNKS, OHLC_CACHE_TTL_HOURS

@dataclass
class CacheEntry:
    data: pd.DataFrame
    created_at: datetime = field(default_factory=datetime.utcnow)
    hits: int = 0

class BoundedCache:
    """LRU cache with TTL and max size."""
    
    def __init__(self, max_size: int, ttl_hours: int):
        self.max_size = max_size
        self.ttl = timedelta(hours=ttl_hours)
        self._cache: dict[Any, CacheEntry] = {}
        self._access_order: list[Any] = []
        self.hits = 0
        self.misses = 0
    
    def get(self, key: Any) -> pd.DataFrame | None:
        entry = self._cache.get(key)
        if entry is None:
            self.misses += 1
            return None
        
        # Check TTL
        if datetime.utcnow() - entry.created_at > self.ttl:
            self._evict(key)
            self.misses += 1
            return None
        
        # Update LRU
        self._access_order.remove(key)
        self._access_order.append(key)
        entry.hits += 1
        self.hits += 1
        return entry.data
    
    def set(self, key: Any, value: pd.DataFrame):
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
        now = datetime.utcnow()
        expired = [
            k for k, v in self._cache.items()
            if now - v.created_at > self.ttl
        ]
        for k in expired:
            self._evict(k)
    
    def clear(self):
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
```

### 3. Replace Global Caches with BoundedCache Instances

```python
# In run_backfill() - create fresh caches per run
ohlc_cache = BoundedCache(
    max_size=OHLC_CACHE_MAX_CHUNKS,
    ttl_hours=OHLC_CACHE_TTL_HOURS
)
rates_cache = BoundedCache(
    max_size=1,  # Only one rates DF
    ttl_hours=RATES_CACHE_TTL_HOURS
)

# Pass caches to helper functions
async def _get_stock_ohlc_cached(client, symbol, chunk_start, chunk_end, cache: BoundedCache):
    cache_key = (chunk_start, chunk_end)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    async with STK_SEM:
        df = await async_fetch_stock_ohlc(client, symbol, chunk_start, chunk_end)
    
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.floor("min")
        df = df.rename(columns={"close": "spot_close"})
        cache.set(cache_key, df[["timestamp", "spot_close"]].copy())
    
    return cache.get(cache_key) or pd.DataFrame()

async def _get_rates_cached(client, start_date, end_date, cache: BoundedCache):
    cache_key = (start_date, end_date)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    async with STK_SEM:
        df = await async_fetch_interest_rate_eod(client, "SOFR", start_date, end_date)
    
    if not df.empty:
        df["rate"] = df["rate"].astype(float) / 100.0
        df = df.rename(columns={"created": "date", "rate": "r"})
        df["date"] = pd.to_datetime(df["date"]).dt.date
        cache.set(cache_key, df)
    
    return cache.get(cache_key) or pd.DataFrame()
```

### 4. Log Cache Stats

```python
async def run_backfill(...):
    # ... create caches ...
    
    log.info("backfill_started", extra={
        "ohlc_cache_max": OHLC_CACHE_MAX_CHUNKS,
        "rates_cache_ttl_hours": RATES_CACHE_TTL_HOURS,
    })
    
    # ... processing ...
    
    # Log cache stats at end
    log.info("backfill_completed", extra={
        "ohlc_cache_stats": ohlc_cache.stats(),
        "rates_cache_stats": rates_cache.stats(),
    })
```

### 5. Remove Module-Level Globals

```python
# DELETE these lines (68-70):
# _OHLC_CACHE: dict[tuple[dt.date, dt.date], pd.DataFrame] = {}
# _RATES_DF: pd.DataFrame | None = None
```

## Invariants (Must Preserve)

- ✅ Cache hit/miss rates logged for monitoring
- ✅ Memory bounded — max `OHLC_CACHE_MAX_CHUNKS` entries
- ✅ TTL eviction prevents stale data
- ✅ Fresh caches per `run_backfill()` call
- ✅ Same cache behavior for hits (returns cached data)
- ✅ All tests pass

## Acceptance Criteria

### Functional
1. No module-level global caches
2. `BoundedCache` used for OHLC and rates
3. Cache size bounded by config
4. TTL eviction works
5. Cache stats logged at backfill end
6. All orchestrator tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
```

### New Test in `test_orchestrator.py`
```python
class TestCacheManagement:
    def test_cache_bounded_by_config(self, patched_orchestrator):
        """OHLC cache evicts LRU when max size reached."""
        # Configure small cache, process many chunks
        # Verify cache size never exceeds max
    
    def test_cache_ttl_eviction(self, patched_orchestrator):
        """Expired cache entries evicted."""
        # Mock time, verify old entries evicted
    
    def test_fresh_cache_per_run(self, patched_orchestrator):
        """Two run_backfill() calls have independent caches."""
        await run_backfill(...)
        await run_backfill(...)
        # Second run should not see first run's cache
    
    def test_cache_stats_logged(self, patched_orchestrator):
        """Cache hit/miss stats in completion log."""
        # Capture log output, verify stats present
```

## Dependencies

- **EH-06 MUST BE COMPLETE** — cache config constants
- **EH201, EH204 SHOULD BE COMPLETE** — async fetchers + client lifecycle

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` — bounded caches with stats
2. **Verification** all tests pass