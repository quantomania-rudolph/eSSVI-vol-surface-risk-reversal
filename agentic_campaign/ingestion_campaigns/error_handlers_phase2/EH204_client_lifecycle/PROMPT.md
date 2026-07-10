# EH204: Client Lifecycle Management

## Persona

You are a **network systems engineer** who knows that creating and tearing down HTTP connections for every expiration in a 7-year backfill is not just slow — it triggers rate limits, wastes TCP handshakes, and violates the principle of connection pooling.

## Mission

**Refactor `orchestrator.py` to use a SINGLE `AsyncThetaClient` for the entire backfill, not one per expiration.**

## Current State (WASTEFUL)

```python
# Line 326-327: NEW CLIENT for expiration list
async with AsyncThetaClient(CFG) as client:
    expirations = fetch_option_list_expirations(client, "AMD")

# Line 343-344: NEW CLIENT for rates
async with AsyncThetaClient(CFG) as client:
    rates_df = await _get_rates(client, start_date, end_date)

# Line 375-376: NEW CLIENT PER EXPIRATION in main loop!
for exp, exp_start, exp_end in valid_expirations:
    async with AsyncThetaClient(CFG) as client:
        for chunk_start, chunk_end in chunks:
            # ... process chunk
```

**Problems:**
1. 3+ client lifecycles per backfill run
2. Each `async with` does TCP connect + auth handshake + disconnect
3. No connection reuse across chunks/expirations
4. Theta terminal sees rapid connect/disconnect = suspicious

## Required Changes

### 1. Single Client for Entire Backfill

```python
async def run_backfill(...):
    # ... heartbeat, init_schema, calendar ...
    
    # ONE client for everything
    async with AsyncThetaClient(CFG) as client:
        # 3. Get run ID (needs pool, not client)
        pool = await get_pool()
        conn = await _acquire_conn(pool)
        run_id = await next_run_id(conn)
        await _release_conn(pool, conn)
        
        # 4. Fetch expirations
        expirations = await async_fetch_option_list_expirations(client, "AMD")
        
        # 5. Pre-fetch rates
        rates_df = await _get_rates_async(client, start_date, end_date)
        
        # 6. Get completed chunks
        conn = await _acquire_conn(pool)
        completed_chunks = await get_completed_chunks(conn, "AMD")
        await _release_conn(pool, conn)
        
        # 7. Process all expirations with SAME client
        for exp, exp_start, exp_end in valid_expirations:
            chunks = _month_chunks(exp_start, exp_end)
            
            for chunk_start, chunk_end in chunks:
                conn = await _acquire_conn(pool)
                await _process_chunk(
                    client, exp, chunk_start, chunk_end,
                    conn, run_id, cal, rates_df, completed_chunks,
                    schedule_cache  # from EH202
                )
                await _release_conn(pool, conn)
```

### 2. Update `_get_rates` to Async Variant (From EH-01)

```python
async def _get_rates_async(client, start_date, end_date) -> pd.DataFrame:
    global _RATES_DF
    if _RATES_DF is not None:
        return _RATES_DF
    
    async with STK_SEM:
        _RATES_DF = await async_fetch_interest_rate_eod(client, "SOFR", start_date, end_date)
    
    if not _RATES_DF.empty:
        _RATES_DF["rate"] = _RATES_DF["rate"].astype(float) / 100.0
        _RATES_DF = _RATES_DF.rename(columns={"created": "date", "rate": "r"})
        _RATES_DF["date"] = pd.to_datetime(_RATES_DF["date"]).dt.date
    
    return _RATES_DF
```

### 3. Update `_get_stock_ohlc_cached` to Async Variant

```python
async def _get_stock_ohlc_cached(client, symbol, chunk_start, chunk_end):
    cache_key = (chunk_start, chunk_end)
    if cache_key in _OHLC_CACHE:
        return _OHLC_CACHE[cache_key]
    
    async with STK_SEM:
        df = await async_fetch_stock_ohlc(client, symbol, chunk_start, chunk_end)
    
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.floor("min")
        df = df.rename(columns={"close": "spot_close"})
        _OHLC_CACHE[cache_key] = df[["timestamp", "spot_close"]].copy()
    
    return _OHLC_CACHE.get(cache_key, pd.DataFrame())
```

### 4. Update `_process_chunk` to Use Async Fetchers

```python
async def _process_chunk(...):
    # ...
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
            return await _get_stock_ohlc_cached(client, "AMD", chunk_start, chunk_end)
    
    opt_df, oi_df, stk_df = await asyncio.gather(_fetch_opt(), _fetch_oi(), _fetch_stk())
    # ...
```

## Invariants (Must Preserve)

- ✅ Single `AsyncThetaClient` lifecycle per `run_backfill()`
- ✅ All fetcher calls use the same semantics (params, returns, errors)
- ✅ Semaphores still acquired correctly per request
- ✅ Caches still work (OHLC per-chunk, rates global)
- ✅ Watermark/resume logic unchanged
- ✅ All tests pass

## Acceptance Criteria

### Functional
1. Only ONE `AsyncThetaClient` created per backfill
2. All fetcher calls use async variants (EH-01)
3. Connection reuse visible in Theta terminal logs
4. All orchestrator tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
```

### New Test in `test_orchestrator.py`
```python
class TestClientLifecycle:
    def test_single_client_per_backfill(self, patched_orchestrator):
        """AsyncThetaClient.__aenter__ called exactly once."""
        enter_count = 0
        original_aenter = AsyncThetaClient.__aenter__
        
        async def counting_aenter(self):
            nonlocal enter_count
            enter_count += 1
            return await original_aenter(self)
        
        with patch.object(AsyncThetaClient, "__aenter__", counting_aenter):
            await run_backfill(...)
            assert enter_count == 1, f"Client entered {enter_count} times, expected 1"
```

## Dependencies

- **EH-01 MUST BE COMPLETE** — async fetcher variants required
- **EH202 SHOULD BE COMPLETE** — schedule cache integration

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` — single client lifecycle
2. **Verification** all tests pass