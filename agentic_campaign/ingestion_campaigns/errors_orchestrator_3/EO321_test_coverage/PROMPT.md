# EO321: Test Coverage for Edge Cases

## Persona

You are a **test architect** who knows that 77 passing tests don't guarantee correctness — they only test the happy path and known scenarios. Edge cases are where bugs live.

## Core Objective

**Add missing unit tests for edge cases identified in the thermo-nuclear audit.**

## Missing Test Coverage

### 1. `_month_chunks` Edge Cases (in `test_chunking.py`)
- Exact month boundary (start=1st, end=last day of month)
- Leap year February (2024-02-29)
- Single-day range (start=end)
- Range spanning year boundary (Dec 15 - Jan 15)

### 2. `_dte_window` Edge Cases (in `test_chunking.py`)
- Expiration on weekend/holiday
- DTE window spanning holiday
- DTE min/max at boundaries

### 3. `BoundedCache` Behavior (in `test_cache.py`)
- TTL eviction (mock time)
- LRU eviction when max_size reached
- Hit/miss stats accuracy
- Concurrent access safety (if applicable)

### 4. `fetch_with_retry` Edge Cases (in `test_retry.py`)
- Semaphore release during backoff (already tested for OPT_SEM, add STK_SEM)
- Jitter bounds verification
- Retry after connection error vs timeout

### 5. `_join_spot` Edge Cases (in `test_joins.py`)
- Forward-fill across day boundary (last bar of day, first bar of next)
- Multiple spot values per minute (should use last)
- Empty stock DataFrame with non-empty option DataFrame

### 6. `_attach_rates` Edge Cases (in `test_joins.py`)
- Missing rates for some dates (NaN propagation)
- Rates DataFrame with duplicate dates
- Empty rates DataFrame

### 7. Real Calendar Integration (in `test_math.py`)
- `pandas_market_calendars` with actual XNYS calendar
- Half-day sessions (Thanksgiving, Christmas Eve)
- Holiday handling

## Invariants

- ✅ All new tests pass
- ✅ Tests are fast (no real I/O)
- ✅ Tests use mocks appropriately

## Success Criteria

```bash
python -m pytest dataingestion/test_*.py -v
# All 77+ new tests pass
```