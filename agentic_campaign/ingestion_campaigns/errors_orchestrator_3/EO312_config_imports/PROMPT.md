# EO312: Config Imports - Use Module Import Pattern

## Persona

You are a **Python best practices enforcer** who knows that importing 23 individual constants is brittle, pollutes namespace, and makes config changes require editing multiple files.

## Core Objective

**Replace 23 individual `from dataingestion.config import ...` statements with `from dataingestion import config as cfg` and access via `cfg.CONSTANT_NAME`.**

## Current State (Lines 80-100)

```python
from dataingestion.config import (
    OPT_SEM_LIMIT,
    STK_SEM_LIMIT,
    MAX_CHUNK_DAYS,
    DTE_WINDOW_MIN,
    DTE_WINDOW_MAX,
    OHLC_CACHE_MAX_CHUNKS,
    OHLC_CACHE_TTL_HOURS,
    RATES_CACHE_TTL_HOURS,
    FETCH_MAX_RETRIES,
    FETCH_BASE_DELAY,
    FETCH_MAX_DELAY,
    FETCH_RETRYABLE_STATUS,
    FETCH_NON_RETRYABLE_STATUS,
    THETA_INTERVAL,
    THETA_FORMAT,
    THETA_ANNUAL_DIVIDEND,
    THETA_RATE_TYPE,
    THETA_VERSION,
)
```

## Required Fix

```python
# Top of orchestrator.py
from dataingestion import config as cfg

# Usage throughout:
OPT_SEM = asyncio.Semaphore(cfg.OPT_SEM_LIMIT)
STK_SEM = asyncio.Semaphore(cfg.STK_SEM_LIMIT)
...
ohlc_cache = BoundedCache(max_size=cfg.OHLC_CACHE_MAX_CHUNKS, ttl_hours=cfg.OHLC_CACHE_TTL_HOURS)
...
if attempt < cfg.FETCH_MAX_RETRIES:
    delay = min(cfg.FETCH_BASE_DELAY * (2 ** attempt), cfg.FETCH_MAX_DELAY)
```

## Invariants

- ✅ Single import line for all config
- ✅ No unused imports (THETA_* can be removed if not used)
- ✅ All tests pass

## Success Criteria

### Functional
1. `grep -c "from dataingestion.config import" dataingestion/orchestrator.py` returns 0
2. `grep -c "cfg\." dataingestion/orchestrator.py` > 0
3. All 77 tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
```