# EO303: Schedule Cache Date Range Fix

## Persona

You are a **quantitative engineer** who knows that `business_t` (time to expiration in business years) is the single most critical input to Black-Scholes. If the schedule cache doesn't cover the full range of timestamps in the data, `compute_business_T` will fall back to on-demand calendar calls, defeating the entire purpose of the cache.

## Core Objective

**Fix the schedule cache build range to cover the full backfill window including DTE extensions.**

## Current Buggy Code (Lines 611-615)

```python
max_exp = max(expirations) if expirations else end_date
schedule_cache = _build_business_time_schedule(
    cal,
    pd.Timestamp(start_date - dt.timedelta(days=5), tz="US/Eastern"),
    pd.Timestamp(max_exp + dt.timedelta(days=5), tz="US/Eastern"),
)
```

**Problem**: 
- Backfill processes chunks from `start_date` to `end_date`
- DTE window extends `DTE_WINDOW_MAX=90` days **before** each expiration
- Earliest timestamp needed = `start_date - 90 days - 5 days buffer`
- Latest timestamp needed = `max(expiration) + 5 days buffer` (already correct)
- Current code only goes back `start_date - 5 days`, missing up to 90 days of early DTE data

## Required Fix

```python
from dataingestion.config import DTE_WINDOW_MAX

# Earliest bar we might need: start_date minus max DTE window minus buffer
earliest_needed = start_date - dt.timedelta(days=DTE_WINDOW_MAX + 5)
# Latest bar: max expiration plus buffer (already correct)
latest_needed = max_exp + dt.timedelta(days=5)

schedule_cache = _build_business_time_schedule(
    cal,
    pd.Timestamp(earliest_needed, tz="US/Eastern"),
    pd.Timestamp(latest_needed, tz="US/Eastern"),
)
```

## Invariants

- ✅ Schedule covers ALL timestamps that `compute_business_T` will encounter
- ✅ No on-demand calendar calls inside `compute_business_T` (cache hits only)
- ✅ Uses `DTE_WINDOW_MAX` from config (not hardcoded 90)
- ✅ Buffer of 5 days on each side for holidays/half-days

## Success Criteria

### Functional
1. `compute_business_T` never triggers calendar calls for timestamps in backfill range
2. Schedule cache built once per backfill
3. Range = `[start_date - DTE_WINDOW_MAX - 5d, max_expiration + 5d]`

### Testing
```bash
python -m pytest dataingestion/test_math.py -v -k "schedule"
# Add test verifying cache covers full DTE range
```

## Verification Agent

Add test in `test_math.py`:
```python
def test_schedule_cache_covers_full_dte_range():
    """Schedule cache start_date = backfill_start - DTE_WINDOW_MAX - 5d."""
    # Mock backfill with start_date, verify schedule range
```