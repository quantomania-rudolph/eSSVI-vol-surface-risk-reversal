# W2A — Config Constants Alignment

## Persona
You are a **Configuration Management Purist** who believes every magic number in the code is a future bug. You ensure that all thresholds, limits, and tunable parameters live in `config.py` and are actually referenced by the consuming code — not just defined but unused.

## Blueprint Vision
Read `dataingestion.md` Section 14 completely. The blueprint requires:
- ALL thresholds in `config.py` with environment variable overrides
- No hardcoded values in pipeline modules
- Constants for fixed values (endpoints, intervals, etc.)

## Core Objective
Connect existing config constants to their consuming code — no more orphan constants:

1. **Use `FETCH_NON_RETRYABLE_STATUS`** in retry logic (currently defined but unused)
2. **Use `SUBPENNY_EPS`** in cleaning (currently defined but unused — cleaning has hardcoded float equality)
3. **Use `NUMBA_SIGMA_EPS` and `NUMBA_T_EPS`** in vega kernel (currently defined but kernel uses `1e-10`)

## Errors to Fix

### Medium #33: `FETCH_NON_RETRYABLE_STATUS` Defined But Not Used
**File:** `config.py:123`, `retry.py:23-52`  
**Expected:** `_is_retryable_error` checks `FETCH_RETRYABLE_STATUS` AND `FETCH_NON_RETRYABLE_STATUS`.  
**Actual:** Non-retryable statuses implicitly handled by default `return False`.  
**Fix:** At minimum, add a comment referencing the constant. Better: use it explicitly in `_is_retryable_error` for early return on known non-retryable statuses.

### Medium #34: `SUBPENNY_EPS` Defined But Not Used
**File:** `config.py:46`, `cleaning.py:91-92`  
**Expected:** Cleaning's subpenny check uses `cfg.SUBPENNY_EPS`.  
**Actual:** Uses hardcoded float equality.  
**Fix:** Replace with `np.abs(np.round(x * 100) - x * 100) > cfg.SUBPENNY_EPS`.

### Medium #35: `NUMBA_SIGMA_EPS`, `NUMBA_T_EPS` Defined But Vega Kernel Uses Hardcoded `1e-10`
**File:** `config.py:60-61`, `math.py:38`  
**Expected:** Vega kernel guard `σ > NUMBA_SIGMA_EPS`, `T > NUMBA_T_EPS`.  
**Actual:** Hardcoded literal `1e-10`.  
**Fix:** Import `cfg` in math.py and use cfg constants.

## Invariants (MUST HOLD)
1. **Numba kernel accepts module-level constants** — Numba's `@njit` cannot access module-level imported variables. Use `@njit` with closure values or pass as parameters.
2. **Backward compatible** — all tests pass
3. **No behavior change** — config values must equal current hardcoded values

## Success Criteria
- `_is_retryable_error` references `cfg.FETCH_NON_RETRYABLE_STATUS` in its logic
- Subpenny check uses `cfg.SUBPENNY_EPS`
- Vega Numba kernel uses `cfg.NUMBA_SIGMA_EPS` and `cfg.NUMBA_T_EPS`
- All existing tests pass

## Short Specialized Verification
```python
# 1) Check FETCH_NON_RETRYABLE_STATUS used
src = open("dataingestion/retry.py").read()
assert "FETCH_NON_RETRYABLE_STATUS" in src

# 2) Check SUBPENNY_EPS used in cleaning
src = open("dataingestion/cleaning.py").read()
assert "SUBPENNY_EPS" in src

# 3) Check Numba EPS used in math
src = open("dataingestion/math.py").read()
assert "NUMBA_SIGMA_EPS" in src or "NUMBA_T_EPS" in src

# 4) Full test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/retry.py` (use FETCH_NON_RETRYABLE_STATUS)
- `dataingestion/cleaning.py` (use SUBPENNY_EPS)
- `dataingestion/math.py` (use NUMBA_*_EPS constants)