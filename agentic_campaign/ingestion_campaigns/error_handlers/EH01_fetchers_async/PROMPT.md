# EH-01: Fetchers Async Variants

## Persona

You are a **senior Python async engineer** specializing in high-throughput financial data ingestion. You understand the Theta Data v3 API, asyncio patterns, and the critical importance of preserving backward compatibility while enabling true parallelism.

## Mission

**Add native `async` variants to all 6 fetcher functions in `dataingestion/fetchers.py` while preserving the existing synchronous `asyncio.run()` wrappers for backward compatibility.**

The orchestrator (EH-05) needs to call fetchers concurrently within `asyncio.TaskGroup` — the current sync wrappers block the event loop.

## Current State Analysis

**File:** `dataingestion/fetchers.py` (271 lines)

**Current Pattern (all 6 functions):**
```python
def fetch_option_greeks_first_order(...) -> pd.DataFrame:
    async def _run() -> pd.DataFrame:
        # actual async logic
    return asyncio.run(_run())
```

**Problems:**
1. Each call creates/runs a new event loop — prevents true concurrency
2. Orchestrator cannot `asyncio.gather()` multiple fetches
3. Semaphore acquisition in orchestrator is ineffective (fetchers run in separate loops)

## Required Changes

### 1. Add Async Variants (Primary)

For each of the 6 functions, add an `async_` prefixed variant:

| Sync Function | Async Variant |
|---------------|---------------|
| `fetch_option_greeks_first_order` | `async_fetch_option_greeks_first_order` |
| `fetch_stock_ohlc` | `async_fetch_stock_ohlc` |
| `fetch_interest_rate_eod` | `async_fetch_interest_rate_eod` |
| `fetch_option_open_interest` | `async_fetch_option_open_interest` |
| `fetch_option_list_expirations` | `async_fetch_option_list_expirations` |
| `fetch_option_list_contracts` | `async_fetch_option_list_contracts` |

### 2. Preserve Sync Wrappers (Backward Compat)

Keep existing sync functions but implement them as thin wrappers:
```python
def fetch_option_greeks_first_order(...) -> pd.DataFrame:
    return asyncio.run(async_fetch_option_greeks_first_order(...))
```

### 3. Refactor Internal Logic

Move the actual async logic from the inner `_run()` function to the async variant. The sync wrapper becomes trivial.

### 4. Endpoint Verification (CRITICAL)

**Search and verify the latest Theta Data v3 endpoints** — especially for EOD open interest. The current code uses:
- `/v3/option/history/open_interest` — verify this returns **daily EOD OI** (not intraday)
- Check if there's a separate endpoint for intraday OI
- Confirm params: `symbol`, `expiration`, `strike`, `start_date`, `end_date`, `format=ndjson`
- Verify response columns: `date`, `open_interest`

Use the Theta Terminal running locally or docs at `docs.thetadata.us` to confirm.

## Invariants (Must Preserve)

- ✅ All 6 functions return correct columns per `COLUMNS.md` §I
- ✅ No semaphore imports in fetchers.py
- ✅ No asyncpg imports
- ✅ No heartbeat calls
- ✅ No disk writes
- ✅ Empty DataFrame on error (non-200 or empty payload)
- ✅ Correct params sent to client (interval=1m, format=ndjson, annual_dividend=0, rate_type=sofr)
- ✅ `_phase = "raw"` set on output

## Acceptance Criteria

### Functional
1. All 6 async variants exist and are callable: `await async_fetch_option_greeks_first_order(...)`
2. All 6 sync wrappers still work identically: `fetch_option_greeks_first_order(...)`
3. Orchestrator can `asyncio.gather()` multiple async fetches concurrently
4. Semaphore in orchestrator correctly limits concurrent Theta requests

### Testing
Run existing tests + new async tests:
```bash
python -m pytest dataingestion/test_fetchers.py -v           # existing 25 tests pass
python -m pytest dataingestion/test_fetchers_async.py -v     # new async tests pass
```

### New Test File: `dataingestion/test_fetchers_async.py`

Create tests that verify:
- Async variants return identical DataFrames to sync wrappers (same data, same columns)
- Multiple async fetches can run concurrently (timing test)
- Semaphore limiting works when called from orchestrator context
- No event loop creation inside async variants (they're native async)

## Implementation Notes

### Pattern for Each Function

```python
# ASYNC VARIANT (new, primary)
async def async_fetch_option_greeks_first_order(
    client: AsyncThetaClient,
    symbol: str,
    expiration: dt.date,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    status, payload = await client.get(
        "/v3/option/history/greeks/first_order",
        {
            "symbol": symbol,
            "expiration": _fmt(expiration),
            "strike": "*",
            "right": "both",
            "interval": "1m",
            "start_date": _fmt(start_date),
            "end_date": _fmt(end_date),
            "annual_dividend": 0,
            "rate_type": "sofr",
            "version": "latest",
            "format": "ndjson",
        },
        ticker=symbol,
    )
    if status != 200:
        return pd.DataFrame()
    
    df = to_dataframe(payload)
    if df.empty:
        return df
    
    # ... existing normalization logic ...
    
    df["_phase"] = "raw"
    return df


# SYNC WRAPPER (preserved, thin)
def fetch_option_greeks_first_order(
    client: AsyncThetaClient,
    symbol: str,
    expiration: dt.date,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    return asyncio.run(async_fetch_option_greeks_first_order(
        client, symbol, expiration, start_date, end_date
    ))
```

### Special Attention: Open Interest Endpoint

The current `fetch_option_open_interest` calls:
```
/v3/option/history/open_interest
```

**Verify:** Does this return daily EOD OI? The cleaning module expects `open_interest` as Int64 daily values joined by date. If the endpoint returns intraday, we need to aggregate to EOD (last value per day) or find the correct EOD endpoint.

Check Theta v3 docs for:
- `/v3/option/history/open_interest` — params: `symbol`, `expiration`, `strike`, `start_date`, `end_date`
- Is there a `/v3/option/history/open_interest/eod` or similar?

If the current endpoint is correct, ensure the response parsing extracts `date` and `open_interest` correctly.

## Deliverables

1. **Modified** `dataingestion/fetchers.py` with 6 async variants + 6 sync wrappers
2. **New** `dataingestion/test_fetchers_async.py` with async-specific tests
3. **Verification** that all existing tests still pass