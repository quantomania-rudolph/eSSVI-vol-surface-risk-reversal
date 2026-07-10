# EH-02: Math Business Time Optimization

## Persona

You are a **quantitative engineer** specializing in high-performance financial time-series computations. You understand business time conventions, Numba JIT optimization, and the critical importance of correct time-to-expiry calculations for volatility surface fitting.

## Mission

**Optimize `compute_business_T` in `dataingestion/math.py` from O(n × d) to O(n) using prefix sums, and add schedule caching for the orchestrator.**

## Current State Analysis

**File:** `dataingestion/math.py` (238 lines)

**Current Implementation (lines 54-141):**
```python
def compute_business_T(df: pd.DataFrame, cal) -> pd.DataFrame:
    # ... builds date_to_session dict from cal.schedule() ...
    # ... for EACH row, calls _cum_between(bar_date, exp_date) which ITERATES all sorted_dates ...
    for i in range(len(df)):
        # ...
        between_minutes = _cum_between(bar_date, exp_date)  # O(d) per row!
        business_t_arr[i] = (minutes_remaining + between_minutes) / (390.0 * 252.0)
```

**Problems:**
1. **O(n × d) complexity** — for each of n rows, iterates up to d trading days between bar and expiry
2. **Schedule rebuilt per call** — `cal.schedule()` called every `compute_business_T` invocation
3. **No caching** — orchestrator calls this per-chunk; same schedule fetched repeatedly

## Required Changes

### 1. Prefix-Sum Optimization (Core)

Pre-compute cumulative session minutes for O(1) lookup:

```python
def _build_schedule_prefix(cal, min_date, max_date):
    """Build date -> cumulative business minutes from epoch."""
    schedule = cal.schedule(start_date=min_date, end_date=max_date)
    # ... compute prefix sums ...
    return prefix_dict, session_minutes_dict
```

Then in `compute_business_T`:
```python
# O(1) per row instead of O(d)
between_minutes = prefix[exp_date] - prefix[bar_date] - session_minutes[bar_date] - session_minutes[exp_date]
# Adjust for partial day (minutes_remaining_today)
```

### 2. Schedule Caching

Add module-level cache or accept pre-built schedule:

```python
_SCHEDULE_CACHE: dict[tuple, tuple] = {}  # (min_date, max_date) -> (prefix, session_dict)

def compute_business_T(df: pd.DataFrame, cal, schedule_cache=None) -> pd.DataFrame:
    # If schedule_cache provided (from orchestrator), use it
    # Else build and cache
```

### 3. Orchestrator Integration

Orchestrator should:
1. Build schedule once for full backfill range
2. Pass pre-built schedule/prefix to `compute_business_T`
3. Avoid rebuilding per chunk

## Invariants (Must Preserve)

- ✅ `business_t` in years (float64), positive, monotonic decreasing with later timestamp
- ✅ Formula: `T_years = (minutes_remaining_today + sum_session_minutes_between) / (390 * 252)`
- ✅ Uses `pandas_market_calendars` XNYS schedule (handles half-days, holidays)
- ✅ Excludes today and expiration day from "between" sum
- ✅ No future leakage — depends only on bar timestamp and fixed expiration
- ✅ `_phase` set to `"math"` by `compute_vega` (not here)
- ✅ All existing tests pass

## Acceptance Criteria

### Functional
1. `compute_business_T` runs in O(n) time for n rows (verified by benchmark)
2. Schedule built once per backfill, not per chunk
3. Results identical to current implementation (within floating point tolerance)
4. All existing tests pass

### Performance
```python
# Benchmark: 100K rows should complete in < 500ms (vs current ~5-10s)
```

### Testing
```bash
python -m pytest dataingestion/test_math.py -v           # all 17 tests pass
python -m pytest dataingestion/test_math_perf.py -v      # new performance test
```

## Implementation Guide

### New Helper Functions

```python
def _build_business_time_schedule(cal, start_date: dt.date, end_date: dt.date) -> dict:
    """
    Build prefix-sum lookup for O(1) business time queries.
    
    Returns:
        dict with keys:
        - 'prefix_minutes': dict[date] -> cumulative minutes from start_date to date (exclusive)
        - 'session_minutes': dict[date] -> session minutes for that date
        - 'tz': timezone (US/Eastern)
    """
    schedule = cal.schedule(start_date=start_date, end_date=end_date)
    # ... compute prefix sums ...
    return {'prefix_minutes': prefix, 'session_minutes': session_minutes, 'tz': tz}

def compute_business_T(
    df: pd.DataFrame,
    cal,
    schedule_cache: dict | None = None,
) -> pd.DataFrame:
    """
    Add business_t column using prefix-sum lookup (O(1) per row).
    
    Args:
        df: Clean DataFrame with timestamp (tz-aware) and expiration columns
        cal: pandas_market_calendars calendar (used only if schedule_cache not provided)
        schedule_cache: Pre-built cache from _build_business_time_schedule
    """
    if schedule_cache is None:
        # Build on-demand (backward compat)
        min_date = df["timestamp"].min().date()
        max_date = df["expiration"].max().date()
        schedule_cache = _build_business_time_schedule(cal, min_date, max_date)
    
    # Vectorized O(n) computation using prefix sums
    # ...
    return df
```

### Prefix Sum Logic

```
For each row with bar_date B and exp_date E:
    
    minutes_remaining_today = max(0, session_close_B - bar_ts) if B in session else 0
    
    # Full days strictly between B and E:
    # prefix[E] - prefix[B] gives minutes from B (inclusive) to E (exclusive)
    # But we want strictly between, so:
    between = prefix[E] - prefix[B] - session_minutes[B] - session_minutes[E]
    
    business_t = (minutes_remaining_today + between) / (390 * 252)
```

Handle edge cases:
- B == E: between = 0, only minutes_remaining_today
- B not in schedule (holiday/weekend): minutes_remaining = 0, session_minutes[B] = 0
- E not in schedule: session_minutes[E] = 0

## Deliverables

1. **Modified** `dataingestion/math.py` with optimized `compute_business_T` + new helper functions
2. **New** `dataingestion/test_math_perf.py` with performance benchmarks
3. **Verification** that all existing tests pass and results are numerically identical