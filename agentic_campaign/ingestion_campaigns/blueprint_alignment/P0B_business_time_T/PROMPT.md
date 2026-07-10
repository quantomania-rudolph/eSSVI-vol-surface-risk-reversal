# W0B — Business Time `T` Formula Corrections

## Persona
You are a **Quantitative Business Time Specialist** specializing in options time decay. You know that treating `T` as calendar days instead of business minutes injects massive decay error between 10:00 and 15:30 of the same day. You follow the blueprint's formula with surgical precision.

## Blueprint Vision
Read `dataingestion.md` Section 6 completely. The formula is:

```
T_years = ( minutes_remaining_today  +  Σ_d session_minutes(d) ) / (390 × 252)
```

- `minutes_remaining_today = max(0, session_close_today − bar_timestamp)` — **only if bar within RTH**; 0 outside RTH
- `Σ_d` runs over trading days **strictly between** bar_date and expiry (exclude today AND expiration day)
- Half-days = 210 min (Thanksgiving Friday, Christmas Eve, July 3)
- Holidays = 0 min
- No future info: T depends only on bar_ts + expiry

Read `math.py` lines 155–301 (`compute_business_T`, `_build_business_time_schedule`, the vega kernel) to understand the current implementation.

## Core Objective
Fix the business time `T` calculation to precisely match the blueprint formula:

1. **Double-exclude expiration day fix** — `prefix_exp - prefix_bar` already excludes exp day; don't subtract `session_exp` again
2. **Pre-open bar minutes fix** — bars at 09:00 (outside RTH 09:30–16:00) should get `minutes_remaining = 0`, not full session minutes
3. **Half-day validation** — add tests for known half-days (2024-11-29, 2024-12-24, 2025-07-03) with `session_minutes == 210`
4. **Calendar re-query optimization** — replace per-row calendar query with cached open/close times

## Errors to Fix

### Critical #3: Missing Half-Day / Holiday Validation
**File:** `math.py:155-301`  
**Blueprint:** Section 6  
**Expected:** Half-days = 210 min with test coverage.  
**Actual:** `pandas_market_calendars` used (should include half-days), but no explicit validation or tests.  
**Fix:** Add unit tests asserting `session_minutes == 210` for known half-days.

### Critical #4: Double-Excludes Expiration Day
**File:** `math.py:289-298`  
**Blueprint:** Section 6  
**Expected:** `between_minutes = prefix_exp - prefix_bar - session_bar` (exclude today only).  
**Actual:** `prefix_exp - prefix_bar - session_bar - session_exp` — `session_exp` is subtracted twice because prefix already excludes exp day.  
**Fix:** Remove `- session_exp` from line 296.

### Critical #5: `minutes_remaining_today` Logic Flawed
**File:** `math.py:276-287`  
**Blueprint:** Section 6  
**Expected:** `minutes_remaining = 0` when `bar_ts < open_t` (not within RTH).  
**Actual:** Pre-open bars get full session minutes.  
**Fix:** Add check: if `bar_ts < open_t`, `minutes_remaining = session_today` (full day remaining). If `bar_ts > close_t`, `minutes_remaining = 0`.

## Invariants (MUST HOLD)
1. **Point-in-time** — T depends only on bar_ts + expiry; no future info
2. **Backward compatible** — all tests pass; no signature changes
3. **Positive T** — DTE ≥ 7 guarantees T > 0
4. **T ≈ 0 for 09:31 bar at 09:30 close** — minimal decay on last minute

## Success Criteria
- `compute_business_T(09:31 bar on expiry day)` returns very small T (only the remaining minutes that day)
- `compute_business_T(pre-open 09:00 bar)` returns 0
- Specific tests pass:
  - `test_half_day_minutes` — 2024-11-29 = 210 min
  - `test_double_exclude` — verifying exp day not double-counted
  - `test_pre_open_zero` — 09:00 bar → 0 minutes_remaining
- All existing tests pass

## Short Specialized Verification
```python
# 1) Half-day is 210 min
import pandas_market_calendars as mcal
cal = mcal.get_calendar("XNYS")
sched = cal.schedule("2024-11-29", "2024-11-29")
mins = (sched.market_close - sched.market_open).dt.total_seconds().iloc[0] / 60
assert mins == 210, f"Expected 210, got {mins}"

# 2) Bar at 16:01 → minutes_remaining = 0
from dataingestion.math import compute_business_T
# Mock scenario...

# 3) Double-exclude test
# Build prefix_minutes with known dates, assert between_minutes matches expected

# 4) Full test suite
import subprocess
result = subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short", "-k", "business"], capture_output=True, text=True)
print(result.stdout)
```

## Files to Modify
- `dataingestion/math.py` (T formula corrections)
- `dataingestion/test_chunking.py` or new test file for business time tests