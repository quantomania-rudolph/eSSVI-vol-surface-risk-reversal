# EH-05: Orchestrator Integration Fixes

## Persona

You are a **senior systems engineer** specializing in complex async data pipelines, financial backfill orchestration, and idempotent workflow design. You understand the critical importance of join order, semaphore discipline, watermark resume, and zero data leakage.

## Mission

**Fix the orchestrator (`dataingestion/orchestrator.py`) to correctly join OHLC spot, OI, and rates BEFORE cleaning, use async fetcher variants, and ensure all pipeline invariants hold.**

## Current State Analysis

**File:** `dataingestion/orchestrator.py` (399 lines)

**Critical Gaps Identified in Audit:**

### 1. Join Order — OI and Rates Must Join BEFORE Cleaning
**Current code (lines 251-263):**
```python
# 2. Join spot and OI
opt_df = _join_spot(opt_df, stk_df)
opt_df = _join_oi(opt_df, oi_df)

# 3. Clean
clean_df, quar_df = clean_option_chain(opt_df)

# 4. Math (skip if empty)
if not clean_df.empty:
    clean_df = compute_business_T(clean_df, cal)
    clean_df = _attach_rates(clean_df, rates_df)  # RATES ATTACHED AFTER CLEANING!
    clean_df = compute_forward(clean_df)
    clean_df = compute_vega(clean_df)
```

**Problem:** `_attach_rates` happens AFTER cleaning, but cleaning doesn't use rates. However, the **OI join happens before cleaning** — this is correct! But wait — the audit said cleaning will reject all rows for LOW_OI because fetchers output NA. Let me re-check...

Actually, looking at the current orchestrator: OI IS joined before cleaning (line 253). Good. But rates are attached AFTER cleaning (line 261). This is fine since cleaning doesn't need rates. The audit flagged this as a gap but the orchestrator actually does join OI before cleaning. The issue is that the OI fetch might be returning empty/NA.

### 2. Sync Fetcher Wrappers Block Event Loop
**Current code (lines 229-244):**
```python
async def _fetch_opt():
    async with OPT_SEM:
        return fetch_option_greeks_first_order(...)  # SYNC WRAPPER!

async def _fetch_oi():
    async with OPT_SEM:
        return fetch_option_open_interest(...)  # SYNC WRAPPER!

async def _fetch_stk():
    return await _get_stock_ohlc_cached(...)  # This one is async

opt_df, oi_df, stk_df = await asyncio.gather(_fetch_opt(), _fetch_oi(), _fetch_stk())
```

**Problem:** `fetch_option_greeks_first_order` and `fetch_option_open_interest` are sync wrappers that call `asyncio.run()` internally — they create new event loops and block! The `async with OPT_SEM` is ineffective because the semaphore is acquired, then the sync function runs in a separate loop.

**Fix:** Use the new async variants from EH-01:
```python
async def _fetch_opt():
    async with OPT_SEM:
        return await async_fetch_option_greeks_first_order(...)

async def _fetch_oi():
    async with OPT_SEM:
        return await async_fetch_option_open_interest(...)
```

### 3. Rates Fetch Uses Sync Wrapper
**Line 95:** `_RATES_DF = fetch_interest_rate_eod(client, "SOFR", start_date, end_date)` — sync wrapper!

### 4. Expirations Fetch Uses Sync Wrapper
**Line 318:** `expirations = fetch_option_list_expirations(client, "AMD")` — sync wrapper!

### 5. Schedule Not Cached for Business Time
`compute_business_T` called per-chunk with `cal` but no schedule cache passed. EH-02 adds schedule cache support — orchestrator should build once and pass.

### 6. Connection Management in Loop
Lines 368-386: Acquires/releases connection per chunk. Could batch but current approach is fine for correctness.

## Required Changes

### 1. Import Async Fetcher Variants
```python
from dataingestion.fetchers import (
    async_fetch_option_greeks_first_order,
    async_fetch_stock_ohlc,
    async_fetch_option_open_interest,
    async_fetch_interest_rate_eod,
    async_fetch_option_list_expirations,
    # Keep sync versions for backward compat if needed
)
```

### 2. Use Async Variants in `_process_chunk`
```python
async def _fetch_opt():
    async with OPT_SEM:
        return await async_fetch_option_greeks_first_order(
            client, "AMD", exp, chunk_start, chunk_end
        )

async def _fetch_oi():
    async with OPT_SEM:
        return await async_fetch_option_open_interest(
            client, "AMD", exp, chunk_start, chunk_end
        )

async def _fetch_stk():
    async with STK_SEM:
        return await async_fetch_stock_ohlc(client, "AMD", chunk_start, chunk_end)
```

### 3. Use Async Variants for Rates and Expirations
```python
# Line 344: rates
rates_df = await _get_rates_async(client, start_date, end_date)

# Line 318: expirations
expirations = await async_fetch_option_list_expirations(client, "AMD")
```

### 4. Build and Pass Schedule Cache
```python
# After getting calendar (line 308)
cal = await _get_calendar()
# Build schedule cache for full range
schedule_cache = math._build_business_time_schedule(cal, start_date, end_date)

# Pass to _process_chunk
clean_df = compute_business_T(clean_df, cal, schedule_cache=schedule_cache)
```

### 5. Update `_get_rates` to Use Async Fetcher
```python
async def _get_rates_async(client, start_date, end_date):
    global _RATES_DF
    if _RATES_DF is not None:
        return _RATES_DF
    
    async with STK_SEM:
        _RATES_DF = await async_fetch_interest_rate_eod(client, "SOFR", start_date, end_date)
    
    # ... same processing ...
    return _RATES_DF
```

### 6. Update `_get_stock_ohlc_cached` to Use Async Fetcher
```python
async def _get_stock_ohlc_cached(client, symbol, chunk_start, chunk_end):
    cache_key = (chunk_start, chunk_end)
    if cache_key in _OHLC_CACHE:
        return _OHLC_CACHE[cache_key]
    
    async with STK_SEM:
        df = await async_fetch_stock_ohlc(client, symbol, chunk_start, chunk_end)
    
    # ... same processing ...
    return _OHLC_CACHE.get(cache_key, pd.DataFrame())
```

## Invariants (Must Preserve)

- ✅ Heartbeat called first (line 302)
- ✅ Schema initialized (line 305)
- ✅ Chunks ≤ 30 days (`_month_chunks`)
- ✅ Watermark checked before each chunk (line 222)
- ✅ Pipeline order: fetch → join spot → join OI → clean → business_T → attach rates → forward → vega → load
- ✅ Empty DataFrames skip cleanly (line 247-247)
- ✅ DB errors don't crash (try/except line 267)
- ✅ Dual semaphores: OPT_SEM=4, STK_SEM=2 (lines 65-66)
- ✅ No raw HTTP in orchestrator (uses fetchers)
- ✅ No raw SQL outside db_writer
- ✅ Resume works via `get_completed_chunks`

## Acceptance Criteria

### Functional
1. All fetcher calls use async variants (no `asyncio.run()` inside async functions)
2. Semaphores correctly limit concurrent Theta requests
3. OI joined before cleaning (already correct, verify)
4. Rates attached before forward/vega (already correct, verify)
5. Schedule cache built once and passed to `compute_business_T`
6. All existing orchestrator tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v    # all 19 tests pass
```

### New Tests to Verify
- Concurrent fetch timing test (verify semaphores work)
- Resume test (watermark skip)
- Join order verification (spot → OI → clean → rates → math)

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` with all fixes
2. **Verification** all tests pass