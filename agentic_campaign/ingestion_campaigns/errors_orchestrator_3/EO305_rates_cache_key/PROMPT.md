# EO305: Rates Cache Key Ignores Rate Symbol Fix

## Persona

You are a **systems engineer** who knows that cache keys must uniquely identify the data they store. If the rates cache key doesn't include the rate symbol, switching from SOFR to TREASURY or adding multi-rate support will return stale SOFR data for TREASURY queries.

## Core Objective

**Add the rate symbol to the rates cache key in `_get_rates` to prevent cross-contamination between different rate sources.**

## Current Buggy Code (Line 328)

```python
async def _get_rates(client, start_date, end_date, cache):
    cache_key = (start_date, end_date)  # BUG: Missing rate symbol!
    ...
```

## Required Fix

```python
async def _get_rates(
    client: AsyncThetaClient,
    start_date: dt.date,
    end_date: dt.date,
    cache: BoundedCache,
    rate_symbol: str = "SOFR",  # Add parameter with default for backward compat
) -> pd.DataFrame:
    cache_key = (rate_symbol, start_date, end_date)  # Include symbol in key
    ...
```

Also update the call site in `run_backfill`:
```python
rates_df = await _get_rates(client, start_date, end_date, rates_cache, rate_symbol="SOFR")
```

## Invariants

- ✅ Cache key uniquely identifies rate source + date range
- ✅ Default "SOFR" maintains backward compatibility
- ✅ No behavioral change for current single-rate usage
- ✅ Future multi-rate support works correctly

## Success Criteria

### Functional
1. Rates cache key includes rate symbol
2. Default parameter = "SOFR" 
3. All tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v -k "rate"
```

## Verification Agent

```bash
# Verify cache key includes symbol
grep -n "cache_key.*rate_symbol" dataingestion/orchestrator.py
```