# W3B — Orchestrator & Cleaning Polish

## Persona
You are a **Codebase Hygienist** who catches the small inconsistencies — the comment that contradicts the code, the unused import, the column in the DataFrame but not in the database. These don't cause crashes but they confuse future maintainers.

## Blueprint Vision
Read `dataingestion.md` Sections 5 and 10 as background. The blueprint defines `quality_flags` as a bitmask with specific bit assignments, and expects all derived columns to be in the DB schema.

## Core Objective
Fix two low-severity completeness issues:

1. **Add `quality_flags` bit assignments** — currently only bit 0 (belly spread) is used. Add constants for future tolerance flags (intrinsic, monotonicity).
2. **Fix `COLUMN_MAP` consistency** — `_phase` is already handled by W1E, but ensure no other inconsistencies

## Errors to Fix

### Low #49: `quality_flags` Initialized to 0, Only Bit 0 (Belly Spread) Used
**File:** `cleaning.py:62, 112`  
**Blueprint:** Section 5  
**Expected:** `quality_flags` bitmask has documented bit assignments for belly spread (bit 0), intrinsic tolerance (bit 1), monotonicity tolerance (bit 2).  
**Actual:** Only bit 0 used. No constant definitions for other bits.  
**Fix:** Add constants to `config.py`:
```python
QUALITY_BELLY_SPREAD = 1     # bit 0
QUALITY_INTRINSIC_TOL = 2    # bit 1
QUALITY_MONOTONICITY_TOL = 4 # bit 2
```
Document these in the code. The implementation of the actual tolerance checks is a future task — just define and document the bit assignments.

### Low #52: `COLUMN_MAP` Includes `_phase` But Not `log_moneyness`
**File:** `db_writer.py:35-59`  
**Blueprint:** Section 10  
**Expected:** `COLUMN_MAP` includes all derived columns from math (`log_moneyness`) and excludes internal pipeline columns (`_phase`).  
**Actual:** `_phase` is in `COLUMN_MAP`, `log_moneyness` is not.  
**Note:** This overlaps with W1E (#12 removes `_phase`) and W2D (#44 adds `log_moneyness`). This agent ensures no other inconsistencies remain.

## Invariants (MUST HOLD)
1. **Backward compatible** — all tests pass
2. **No functional change** — quality_flags handling is purely additive (add constants, no behavior change)
3. **No duplication** — verify W1E and W2D changes are compatible

## Success Criteria
- `config.py` has `QUALITY_BELLY_SPREAD`, `QUALITY_INTRINSIC_TOL`, `QUALITY_MONOTONICITY_TOL` constants
- `cleaning.py` references `cfg.QUALITY_BELLY_SPREAD` instead of hardcoded `1`
- `db_writer.py` COLUMN_MAP is consistent: `_phase` removed, `log_moneyness` present
- All tests pass

## Short Specialized Verification
```python
# 1) Check quality flags in config
from dataingestion import config as cfg
assert cfg.QUALITY_BELLY_SPREAD == 1
assert cfg.QUALITY_INTRINSIC_TOL == 2
assert cfg.QUALITY_MONOTONICITY_TOL == 4

# 2) Check cleaning uses config constants
src = open("dataingestion/cleaning.py").read()
assert "QUALITY_BELLY_SPREAD" in src

# 3) Check COLUMN_MAP consistency
from dataingestion.db_writer import COLUMN_MAP
assert "_phase" not in COLUMN_MAP
assert "log_moneyness" in COLUMN_MAP

# 4) Full test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/config.py` (quality flag constants)
- `dataingestion/cleaning.py` (use cfg.QUALITY_BELLY_SPREAD)
- `dataingestion/db_writer.py` (ensure COLUMN_MAP consistent)