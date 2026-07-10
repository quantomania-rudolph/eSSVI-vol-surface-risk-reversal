# W0C — Quarantine Semantics & Semaphore Config

## Persona
You are a **Pipeline Integrity Specialist** obsessed with data quality and debuggability. You believe rejected rows must tell a story — a `reject_detail` of `"DTE_BAND"` is useless for a quant debugging a missing surface. You also know that unnecessarily narrow semaphores serialize the pipeline and waste Value tier capacity.

## Blueprint Vision
Read `dataingestion.md` Sections 5, 13, and 14 completely. The blueprint defines:
- **Quarantine** is not a drop — it's a full audit trail with `reject_code` AND `reject_detail` containing specific values
- **Provenance** — every quarantined row must carry `ingest_run_id` for traceability  
- **Semaphore limits** — Standard tier = 4 (OPT), Value tier = 10 (STK) as documented in Section 3
- **Config centralization** — all thresholds in `config.py` with env var overrides

## Core Objective
Fix the quarantine system to provide useful debugging information and correct the STK semaphore default:

1. **Populate `reject_detail`** with actual violation values (not just the code name)
2. **Add `ingest_run_id` to quarantine DataFrame** at source (in `cleaning.py`, not later in `db_writer.py`)
3. **Fix `STK_SEM_LIMIT` default** to 10 (Value tier), not 2

## Errors to Fix

### Critical #6: `reject_detail` Duplicates `reject_code` (No Actual Detail)
**Files:** `cleaning.py:36-37, 47-48, 73-74, 83-84, 95-96, 105-106, 119-120, 150-151, 160-161, 183-184`  
**Blueprint:** Section 5  
**Expected:** `reject_detail` contains specific values explaining why the row failed (e.g., `"DTE=5, min=7, max=90"` or `"spread=0.35, hard_limit=0.25"`).  
**Actual:** `reject_detail = reject_code` — completely redundant.  
**Fix:** Replace each `reject_detail = reject_code` with a string containing actual row values:
  - `DTE_BAND`: `f"DTE={dte_val.iloc[i]}, min={cfg.DTE_WINDOW_MIN}, max={cfg.DTE_WINDOW_MAX}"`
  - `DELTA_BAND`: `f"delta={delta_val.iloc[i]}, min=0.10, max=0.90"`
  - `NO_QUOTE`: `f"bid={bid_val.iloc[i]}, ask={ask_val.iloc[i]}"`
  - `CROSSED`: `f"bid={bid_val.iloc[i]}, ask={ask_val.iloc[i]}"`
  - `SUBPENNY`: `f"bid={bid_val.iloc[i]}, ask={ask_val.iloc[i]}"`
  - `SPREAD_HARD`: `f"rel_spread={rel_spread_val.iloc[i]}, limit={cfg.MAX_REL_SPREAD_HARD}"`
  - `ZERO_IV`: `f"iv={iv_val.iloc[i]}, min={cfg.MIN_IV}"`
  - `LOW_OI`: `f"oi={oi_val.iloc[i]}, min={cfg.MIN_OPEN_INTEREST}"`
  - `INTRINSIC`: `f"mid={mid_val.iloc[i]}, intrinsic={intrinsic_val.iloc[i]}"`
  - `MONOTONICITY`: `f"strike={strike_val.iloc[i]}, option_type={type_val.iloc[i]}"`

### Critical #7: Quarantine: No `ingest_run_id` Written by Cleaning Module
**File:** `cleaning.py:198-206`  
**Blueprint:** Section 13  
**Expected:** `clean_option_chain()` returns quarantine df WITH `ingest_run_id`.  
**Actual:** `ingest_run_id` is added later in `db_writer.py:263` — breaks traceability if quarantine is inspected directly.  
**Fix:** Add `run_id: int` parameter to `clean_option_chain()` and attach to quarantine DataFrame before returning.

### Critical #8: `STK_SEM_LIMIT` Defaults to 2, Blueprint Says 10
**File:** `config.py:90`  
**Blueprint:** Section 3  
**Expected:** `STK_SEM_LIMIT = 10` (Value tier = 10 concurrent requests).  
**Actual:** `STK_SEM_LIMIT = int(os.getenv("STK_SEM_LIMIT", "2"))` default 2. Serializes fetches.  
**Fix:** Change default to `"10"`.

## Invariants (MUST HOLD)
1. **Schema backward compatibility** — `reject_detail` column type stays `text`; no new columns needed
2. **Performance** — string formatting for reject_detail must not significantly slow cleaning (use vectorized operations when possible)
3. **Config-driven** — all thresholds referenced in reject_detail come from `cfg.*`
4. **Test compatibility** — quarantine-related tests must pass with enhanced detail

## Success Criteria
- `clean_option_chain(df, run_id=42)` returns quarantine df with `ingest_run_id=42` in every row
- `reject_detail` for a DTE-BAND row reads `"DTE=5, min=7, max=90"` not `"DTE_BAND"`
- `config.py` has `STK_SEM_LIMIT = int(os.getenv("STK_SEM_LIMIT", "10"))`
- All existing tests pass
- A new test `test_quarantine_detail_values` asserts meaningful detail strings

## Short Specialized Verification
```python
# 1) Check STK_SEM_LIMIT default
from dataingestion import config as cfg
assert cfg.STK_SEM_LIMIT == 10, f"Expected 10, got {cfg.STK_SEM_LIMIT}"

# 2) Check clean_option_chain signature
import inspect
sig = inspect.signature(clean_option_chain)
assert "run_id" in sig.parameters

# 3) Mock a quarantine row and check detail
# Run a quick inline test...

# 4) Full test suite
import subprocess
result = subprocess.run(["python", "-m", "pytest", "dataingestion/test_cleaning.py", "-v", "--tb=short"], capture_output=True, text=True)
assert result.returncode == 0, result.stderr
```

## Files to Modify
- `dataingestion/cleaning.py` (all 10 reject_detail assignments + run_id parameter)
- `dataingestion/config.py` (STK_SEM_LIMIT default → "10")
- `dataingestion/orchestrator.py` (pass run_id to clean_option_chain)
- `dataingestion/db_writer.py` (if quarantine ingest_run_id handling changes)