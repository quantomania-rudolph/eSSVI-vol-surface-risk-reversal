# W1A — Fetcher Config Centralization & Error Handling

## Persona
You are a **ThetaData API Integration Specialist** who has the v3 API documentation memorized. You know that `greeks/first_order` is the correct endpoint (not `greeks/all`), that `interval=1m` is the required frequency, and that `annual_dividend=0` and `rate_type=sofr` must be passed as parameters. You enforce that all API parameters come from config, not hardcoded string literals.

## Blueprint Vision
Read `dataingestion.md` Sections 1, 2, 14, and 15 completely. The blueprint defines:
- **Endpoints:** v3 paths only, `greeks/first_order` (not `all`), `interest_rate/history/eod` (not the invalid `rate?tenor=90`)
- **Config centralization:** every tunable parameter in `config.py`
- **Error handling:** 429/5xx retryable, 400/401/403/404 non-retryable
- **Field crosswalk:** vega from Theta → `vega_api` column, validated on fetch

## Core Objective
Centralize all hardcoded API parameters into `config.py` and fix error-handling robustness:

1. **Column validation** — assert `vega` exists in greeks response before rename to `vega_api`
2. **Parameter centralization** — `interval=1m`, `annual_dividend=0`, `rate_type="sofr"` → config constants
3. **Retry error structure** — make `_is_retryable_error` robust to different error object types

## Errors to Fix

### High #9: No `vega`→`vega_api` Column Validation
**File:** `fetchers.py:70-71`  
**Expected:** Assert/warn if `vega` column missing before rename.  
**Actual:** Blind rename — if Theta changes response schema, `vega_api` silently missing.  
**Fix:** Add check: if `"vega" not in df.columns` → log error and return empty DataFrame (don't silently break).

### High #10: `interval=1m` Hardcoded, Not From Config
**File:** `fetchers.py:45`  
**Expected:** `interval=cfg.THETA_INTERVAL`.  
**Actual:** String literal `"1m"` hardcoded.  
**Fix:** Add `THETA_INTERVAL = "1m"` to `config.py` and use in fetcher.

### High #11: `annual_dividend=0` and `rate_type="sofr"` Hardcoded
**File:** `fetchers.py:48-49`  
**Expected:** `annual_dividend=cfg.THETA_ANNUAL_DIVIDEND`, `rate_type=cfg.THETA_RATE_TYPE`.  
**Actual:** Hardcoded string literals.  
**Fix:** Add `THETA_ANNUAL_DIVIDEND = 0`, `THETA_RATE_TYPE = "sofr"` to `config.py` and use in fetcher.

### High #29: `_is_retryable_error` Assumes `aiohttp.ClientResponseError`
**File:** `retry.py:41-45`  
**Expected:** Works with any exception type that has a `.status` attribute, with fallback.  
**Actual:** Assumes `aiohttp.ClientResponseError`. If Theta client wraps errors differently, status check fails.  
**Fix:** Use `getattr(error, "status", 0)` instead of `error.status`; or add try/except for AttributeError.

## Invariants (MUST HOLD)
1. **No behavior change** for valid Theta responses — centralized values must equal current hardcoded values
2. **All existing tests pass** — test patches may need updating for new config references
3. **Config defaults match current behavior** — `THETA_INTERVAL = "1m"`, `THETA_ANNUAL_DIVIDEND = 0`, `THETA_RATE_TYPE = "sofr"`

## Success Criteria
- `config.py` has new constants: `THETA_INTERVAL`, `THETA_ANNUAL_DIVIDEND`, `THETA_RATE_TYPE`
- `fetchers.py` uses `cfg.THETA_INTERVAL`, `cfg.THETA_ANNUAL_DIVIDEND`, `cfg.THETA_RATE_TYPE`
- `vega` column validated before rename (with error log)
- `_is_retryable_error` uses `getattr(error, "status", 0)` pattern
- All tests pass

## Short Specialized Verification
```python
# 1) Config constants exist
from dataingestion import config as cfg
assert cfg.THETA_INTERVAL == "1m"
assert cfg.THETA_ANNUAL_DIVIDEND == 0
assert cfg.THETA_RATE_TYPE == "sofr"

# 2) Fetcher uses config
src = open("dataingestion/fetchers.py").read()
# Check no hardcoded "1m" string in key positions
# Check vega validation exists

# 3) Retry error handling
from dataingestion.retry import _is_retryable_error
class FakeError:
    status = 429
assert _is_retryable_error(FakeError()) == True
class NoStatusError:
    pass
assert _is_retryable_error(NoStatusError()) == False  # no crash

# 4) Test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/test_orchestrator.py", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/config.py` (3 new constants)
- `dataingestion/fetchers.py` (use cfg.*, add vega validation)
- `dataingestion/retry.py` (getattr pattern)