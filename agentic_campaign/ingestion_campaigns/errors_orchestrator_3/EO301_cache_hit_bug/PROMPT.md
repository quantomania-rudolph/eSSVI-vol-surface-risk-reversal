# EO301: Cache Hit Bug Fix

## Persona

You are a **senior Python engineer** who knows that `pd.DataFrame() or pd.DataFrame()` is a nonsensical expression that always evaluates to an empty DataFrame because empty DataFrames are falsy in Python. This bug silently masks cache miss behavior and could hide real issues if cached data is ever legitimately an empty DataFrame.

## Core Objective

**Fix the cache miss return logic in `_get_rates` and `_get_stock_ohlc_cached` to properly return an empty DataFrame only on cache miss, not via a broken boolean expression.**

## Current Buggy Code (Lines 209-210, 233-234)

```python
# _get_rates (line 209-210)
result = cache.get(cache_key)
return result if result is not None else pd.DataFrame() or pd.DataFrame()

# _get_stock_ohlc_cached (line 233-234)
result = cache.get(cache_key)
return result if result is not None else pd.DataFrame() or pd.DataFrame()
```

**Problem**: `pd.DataFrame() or pd.DataFrame()` evaluates to the **second** empty DataFrame because the first is falsy. The `or` is completely redundant and confusing.

## Required Fix

```python
# _get_rates
result = cache.get(cache_key)
return result if result is not None else pd.DataFrame()

# _get_stock_ohlc_cached
result = cache.get(cache_key)
return result if result is not None else pd.DataFrame()
```

## Invariants

- ✅ Cache hits return the cached DataFrame unchanged
- ✅ Cache misses return a fresh empty DataFrame
- ✅ No behavioral change — only removes the broken `or pd.DataFrame()`

## Success Criteria

### Functional
1. Both functions return cached data on hit
2. Both functions return empty DataFrame on miss
3. No `or pd.DataFrame()` anywhere in the file

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v -k "cache"
python -m pytest dataingestion/test_orchestrator.py::TestIntegration::test_cache_lifecycle_with_ohlc_fetch -v
```

## Verification Agent

After fix, run:
```bash
# Verify no broken expressions remain
grep -n "or pd.DataFrame()" dataingestion/orchestrator.py
# Should return nothing
```