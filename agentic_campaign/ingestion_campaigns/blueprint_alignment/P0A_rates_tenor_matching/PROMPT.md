# W0A — Rate Symbol Tenor Matching & Decimal Conversion

## Persona
You are a **Rates Infrastructure Specialist** with deep expertise in options pricing theory and interest rate mechanics. You understand that every basis point error in `r` propagates through the forward `F = S·e^{(r-q)T}` and into vega, directly biasing the eSSVI surface fit.

## Blueprint Vision
Read `dataingestion.md` Sections 1, 7, and 8 completely. The blueprint defines:
- **Endpoint:** `/v3/interest_rate/history/eod?symbol={SOFR|TREASURY_M1|TREASURY_M3}`
- **Rate is PERCENT** → must convert to decimal (`r = rate / 100`)
- **Tenor-match by DTE:** `TREASURY_M1` for short DTE, `TREASURY_M3` for longer, or linear interpolation across SOFR/M1/M3
- **Compounding:** simple→cc conversion is available as a config switch
- The entire pipeline depends on `r` being in **decimal continuous-compounding form**

## Core Objective
Implement proper interest rate handling in the ingestion pipeline:  
1. **Tenor matching** — select appropriate rate symbol(s) based on DTE of each option  
2. **Percent→decimal conversion** — move the `/100` division into `_get_rates()` so the fetcher returns ready-to-use `r` values  
3. **Config-driven** — all rate symbols and the compounding switch must come from `config.py`

## Errors to Fix

### Critical #1: Rate Symbol Hardcoded to SOFR Only — No Tenor Matching
**File:** `orchestrator.py:149`, `fetchers.py:150`  
**Blueprint:** Section 7  
**Expected:** Pipeline fetches multiple rate symbols (SOFR, TREASURY_M1, TREASURY_M3) and selects/interpolates based on DTE.  
**Actual:** Only `SOFR` fetched. One rate for all DTEs [7,90] misprices short vs long maturities.  
**Fix:** Add DTE-aware rate symbol selection in `_get_rates()`. Options:
  - DTE ≤ 30: use SOFR or TREASURY_M1
  - DTE 31–60: interpolate SOFR/M1
  - DTE 61–90: interpolate M1/M3 or use M3  
  Add rate symbols to `config.py` as `RATE_SYMBOLS_SHORT`, `RATE_SYMBOLS_MEDIUM`, `RATE_SYMBOLS_LONG`.

### Critical #2: Rate Percent→Decimal Conversion Missing in Fetcher
**File:** `fetchers.py:150-169`  
**Blueprint:** Section 7  
**Expected:** Fetcher returns `r` column in decimal form (`4.50% → 0.045`).  
**Actual:** Raw `rate` column (percent) returned. Conversion happens only at `orchestrator.py:183`. Other callers get percent.  
**Fix:** Move `rate/100` conversion into `_get_rates()` or a helper. Rename column to `r`. Ensure downstream code (joins, math) receives decimal.

## Invariants (MUST HOLD)
1. **Backward compatible** — all existing tests must pass; no signature changes to public functions
2. **NaN propagation** — missing rates stay `NaN`, never become `0.0` (Critical #7 guarantee)
3. **Config-driven** — no new hardcoded strings; all rate symbols in `config.py`
4. **No fetch duplication** — multiple rate symbols fetched in parallel, not sequentially

## Success Criteria
- `async_fetch_interest_rate_eod()` returns `r` as decimal (0.0–0.10 range for normal rates)
- `_get_rates()` accepts optional `dtc_bucket` parameter and returns the appropriate rate series
- `config.py` has `RATE_SYMBOLS_SHORT`, `RATE_SYMBOLS_MEDIUM`, `RATE_SYMBOLS_LONG` constants
- All existing tests pass unchanged (backward compatible)
- The `test_orchestrator` test suite reports 0 failures

## Short Specialized Verification
```python
# 1) Rate is decimal, not percent
from dataingestion import config as cfg
assert 0 < cfg.RATE_SYMBOLS_SHORT  # at least one symbol

# 2) Fetcher returns decimal
from dataingestion.fetchers import async_fetch_interest_rate_eod
# Mock a response: would verify column renamed to "r" and values divided by 100

# 3) Orchestrator passes rate symbol down
import inspect
sig = inspect.signature(run_backfill)
assert "rate_symbol" in sig.parameters  # already exists, verify it's used in _get_rates

# 4) No percent→decimal in orchestrator (moved to fetcher)
src = open("dataingestion/orchestrator.py").read()
assert "/ 100" not in src.split("_get_rates")[1].split("\n")[0:20]

# 5) Full test suite
import subprocess
result = subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short"], capture_output=True, text=True)
assert result.returncode == 0, result.stderr
```

## Files to Modify
- `dataingestion/fetchers.py` (rate fetch → decimal conversion)
- `dataingestion/config.py` (add RATE_SYMBOLS_* constants)
- `dataingestion/orchestrator.py` (DTE-aware rate selection in `_get_rates`)
- `dataingestion/joins.py` (if rate join logic needs update for multiple symbols)