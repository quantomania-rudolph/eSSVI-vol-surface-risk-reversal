# W1D — Forward Price, q Assertion, and Vega Corrections

## Persona
You are a **Derivatives Pricing Quant** who implements Black-Scholes with surgical precision. You know that `q=0` for AMD must be asserted against actual dividend data, that vega in the forward (Black-76) convention requires the exact formula `e^{-rT}·F·φ(d1)·√T`, and that every computed column must document its units.

## Blueprint Vision
Read `dataingestion.md` Sections 7, 8, 9 completely:
- **Section 7**: `q = 0` for AMD, but MUST assert no ex-dates in window. Build the dividend infrastructure even if it's a no-op today.
- **Section 8**: Forward `F = S·e^{(r-q)T}`. Spot = OHLC close (not underlying_price). `log_moneyness k = ln(K/F)`.
- **Section 9**: Vega in Black-76 convention. Numba `@njit(fastmath=False)`. Guards: σ>0.005, T>0, S/K/F>0. **Document units** — per 1.0 vol move or per vol-point.

## Core Objective
Add dividend infrastructure guardrails, and fix vega documentation/parallelism:

1. **q=0 dividend assertion** — fetch dividend calendar and assert no ex-dates in [bar_date, expiration] for AMD
2. **Vega computational parallelism** — add `parallel=True` to Numba jit
3. **Vega units documentation** — add column comment specifying units (per 1.0 vol move)

## Errors to Fix

### High #21: Forward Price: `q` Hardcoded to 0 But No Dividend Assertion
**File:** `math.py:72-73`, `orchestrator.py` (where q is set)  
**Blueprint:** Section 7 — "Hard-code q=0 for AMD and **assert no ex-dates land in [bar_date, expiration]**"  
**Expected:** At runtime, assert no AMD dividend ex-dates fall within the bar's [bar_date, expiration] range.  
**Actual:** `q = np.zeros(len(df))` with zero assertion. Silent if AMD somehow starts paying dividends.  
**Fix:** Add a fetch step (or config-powered no-op for now) that checks a dividends table. For immediate fix: add a documented assertion in the `compute_forward` function or at the point where `q=0` is assigned. Document that this is a no-op for AMD but must be enabled for other tickers.

### High #22: Vega: `parallel=True` Not Set, Units Undocumented
**File:** `math.py:22, 45`  
**Blueprint:** Section 9  
**Expected:** `@njit(fastmath=False, parallel=True)` for large panel performance. Column doc: "vega per 1.0 vol move (decimal σ)".  
**Actual:** `@njit(fastmath=False)` only. No unit documentation.  
**Fix:** Add `parallel=True` to decorator. Add column metadata or comment in the code explaining vega is ∂Price/∂σ for a 1.00 vol move.

### High #23: Vega Output Units: Per 1.0 Vol Move Not Documented in Column
**File:** `math.py:45`, `db_writer.py:107-131`  
**Blueprint:** Section 9  
**Expected:** Stored column `vega` has metadata: `"∂Price/∂σ for 1.00 vol move (decimal σ)".`  
**Actual:** No metadata. Downstream consumers don't know if they need to multiply/divide by 100.  
**Fix:** Add SQL comment on the column: `COMMENT ON COLUMN amd_surface_min.vega IS '∂Price/∂σ for a full 1.00 vol move (σ in decimals)'`. Add constant in config `VEGA_UNITS = "per_1.0_vol"`.

## Invariants (MUST HOLD)
1. **q=0 for AMD** is still q=0 — the assertion only warns/errors if dividends unexpectedly appear
2. **Vega math unchanged** — `parallel=True` must produce same results (non-deterministic reduction? Numba guarantees deterministic with parallel=True for array ops)
3. **Backward compatible** — all tests pass

## Success Criteria
- `compute_forward()` or the caller includes an assertion about dividend ex-dates (even if it's a no-op that always passes)
- `@njit(fastmath=False, parallel=True)` on the vega kernel
- SQL `COMMENT ON COLUMN ... vega IS ...` exists in the schema
- All existing tests pass

## Short Specialized Verification
```python
# 1) Check njit decorator
src = open("dataingestion/math.py").read()
assert "parallel=True" in src
assert "fastmath=False" in src

# 2) Check dividend assertion
assert "dividend" in open("dataingestion/orchestrator.py").read().lower() or "assert" in open("dataingestion/math.py").read()

# 3) Check vega units documented
src_db = open("dataingestion/db_writer.py").read()
assert "COMMENT" in src_db or "vega" in src_db  # check for vega column documentation

# 4) Test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/test_cleaning.py dataingestion/test_orchestrator.py", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/math.py` (parallel=True, assertion for q)
- `dataingestion/orchestrator.py` (dividend assertion plumbing)
- `dataingestion/db_writer.py` (vega column comment in schema)
- `dataingestion/config.py` (VEGA_UNITS constant)