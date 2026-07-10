# W2C — Joins Leakage Fixes & Performance

## Persona
You are a **Leakage Prevention Guardian** who knows that data leakage in options surfaces is a career-ending mistake for a quant fund. You verify every join boundary, every forward-fill, and every calendar lookup. "Point-in-time" is not a suggestion — it's the law.

## Blueprint Vision
Read `dataingestion.md` Sections 6, 8, and 12 completely:
- **Leakage Rule 2:** Same-minute join — floor to identical minute boundary, no cross-minute borrowing
- **Leakage Rule 6:** "No backward-fill of quotes. Carry-forward is allowed **only** for slow exogenous series (r, q) and only from the past." SPOT IS NOT EXOGENOUS.
- **Leakage Rule 8:** Prior-session OI (strict mode): day D's intraday bars should use day D-1's EOD OI
- **Section 6:** Business time T should use cached schedule, not per-row calendar queries

## Core Objective
Fix three active data leaks and one performance bug in `joins.py` and `math.py`:

1. **Remove spot forward-fill** — spot is not exogenous; carry-forward of spot price is look-ahead
2. **Prior-session OI** — join OI from prior session (D-1) instead of same-day EOD
3. **Business T calendar re-query** — use cached schedule, not per-row `cal.schedule()` calls
4. **Schedule cache robustness** — fix fragile `next_date` logic in `_build_business_time_schedule`

## Errors to Fix

### Medium #39: `_join_spot` Forward-Fills Spot Within Day — Leakage
**File:** `joins.py:48-49`  
**Blueprint:** Section 12.6  
**Expected:** Spot joined on exact minute match only. If no match, leave NaN.  
**Actual:** Forward-fills the last known close within a day. If 10:31 close is carried to 11:00 where market moved, that's look-ahead.  
**Fix:** Remove the `ffill()` in the spot join. Use `pd.merge_asof()` with `direction="nearest"` and a small tolerance? No — use exact merge. If no match for a minute, `spot_close = NaN` and the row gets rejected in cleaning.

### Medium #40: `_join_oi` Uses Same-Day OI — Leakage
**File:** `joins.py:82-91`  
**Blueprint:** Section 12.8  
**Expected:** For strict mode, join OI from `bar_date - 1 day` (prior session's EOD).  
**Actual:** Joins on `bar_date` = same day. This leaks EOD OI (which prints after close) into intraday bars.  
**Fix:** Add a config flag `OI_MODE` (strict vs research). In strict mode, shift OI date by -1 day before joining. Document the choice.

### Medium #42: `compute_business_T` Re-queries Calendar Per Row — Slow
**File:** `math.py:282-287`  
**Expected:** Use the cached `schedule_cache` for today's open/close times.  
**Actual:** Line 282 calls `cal.schedule(start_date=bar_date, end_date=bar_date)` inside the per-row loop — extremely slow for large DataFrames.  
**Fix:** Pre-compute a lookup dict `{date: (open, close)}` from the schedule and pass it to `compute_business_T`.

### Medium #43: Schedule Cache `next_date` Logic Fragile
**File:** `math.py:203-212`  
**Expected:** Robust iteration to find next trading day, handling any gap length.  
**Actual:** `next_date` loop iterates max 10 times. If calendar has >10 day gap (rare but possible), fails silently.  
**Fix:** Use `cal.valid_days()` or iterate with while loop checking `len(schedule)` until found.

## Invariants (MUST HOLD)
1. **Backward compatible** — tests pass; if OI mode changes, tests reflect the new default
2. **Config-driven** — OI mode is a config flag, not hardcoded
3. **Performance** — spot join without ffill must not drop all rows (most minutes should have a match if data is healthy)
4. **NaN handling** — NaN spot rows are filtered in cleaning (High #20 already handles this)

## Success Criteria
- `_join_spot` does NOT forward-fill — uses exact minute match only
- `_join_oi` has config `OI_MODE` with `"strict"` (prior session) and `"research"` (same day) options
- `compute_business_T` uses cached schedule for today's open/close (no per-row `cal.schedule()` call)
- `_build_business_time_schedule` `next_date` loop is robust to any gap length
- All existing tests pass

## Short Specialized Verification
```python
# 1) Check no ffill in spot join
src = open("dataingestion/joins.py").read()
assert "ffill" not in src.split("_join_spot")[1].split("\n")[:20]  # approx check

# 2) Check OI mode config
from dataingestion import config as cfg
assert hasattr(cfg, "OI_MODE")

# 3) Check no per-row cal.schedule in math
src_math = open("dataingestion/math.py").read()
# business_T should not contain cal.schedule inside the loop
assert "cal.schedule" not in src_math.split("def compute_business_T")[1].split("return")[0] or "cache" in src_math

# 4) Full test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/joins.py` (join_spot, join_oi, config for OI mode)
- `dataingestion/math.py` (compute_business_T, schedule cache)
- `dataingestion/config.py` (OI_MODE constant)