# EH-03: Cleaning Fixes

## Persona

You are a **financial data quality engineer** specializing in option market microstructure, arbitrage detection, and robust data cleaning pipelines. You understand penny-pilot rules, spread dynamics, and the critical balance between filtering noise and preserving signal.

## Mission

**Fix the identified issues in `dataingestion/cleaning.py` and extract all thresholds to the new config module.**

## Current State Analysis

**File:** `dataingestion/cleaning.py` (207 lines)

**Issues to Fix:**

### 1. Subpenny Check — Floating Point Bug (Line 91-92)
```python
# CURRENT — BROKEN for floating point
bid_penny = np.round(result["bid"].values * 100) == result["bid"].values * 100
ask_penny = np.round(result["ask"].values * 100) == result["ask"].values * 100
```
**Problem:** `0.01 * 100 = 1.0000000000000001` → fails equality check
**Fix:** Use epsilon comparison

### 2. Hardcoded Thresholds (Multiple Lines)
All thresholds should come from `dataingestion.config`:
- DTE band: 7, 90 (lines 34, 66)
- Delta band: 0.10, 0.90 (line 45)
- No-quote: bid>0, ask>0 (line 71)
- Crossed: ask > bid (line 81)
- Subpenny: penny grid (lines 91-92)
- Spread hard: 0.25 (line 103)
- Spread belly: 0.10 (line 111)
-1)
- Zero IV: 0.005 (line 117)
- Intrinsic: exact (lines 174-179)
- Monotonicity: exact (lines 145-147)
- Low OI: 100 (line 158)

### 3. OI Column Expectation
Cleaning expects `open_interest` column — orchestrator must join OI BEFORE cleaning (EH-05 handles this, but cleaning should handle missing column gracefully).

## Required Changes

### 1. Import Config
```python
from dataingestion.config import (
    MIN_DTE, MAX_DTE,
    MIN_DELTA_ABS, MAX_DELTA_ABS,
    MAX_REL_SPREAD_HARD, MAX_REL_SPREAD_BELLY,
    MIN_IV, MIN_OI,
    SUBPENNY_EPS,
)
```

### 2. Fix Subpenny Check
```python
def _on_penny_grid(prices: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    """Check if prices are on penny grid (multiple of 0.01)."""
    return np.abs(prices * 100 - np.round(prices * 100)) < eps

bid_penny = _on_penny_grid(result["bid"].values)
ask_penny = _on_penny_grid(result["ask"].values)
subpenny_ok = bid_penny & ask_penny
```

### 3. Replace All Hardcoded Thresholds
Use config constants throughout.

### 4. Handle Missing OI Gracefully
```python
# If open_interest column missing, create it as NA
if "open_interest" not in result.columns:
    result["open_interest"] = pd.NA
```

## Invariants (Must Preserve)

- ✅ All 10 checks in correct order (pre-filters first, cross-strike last)
- ✅ Pre-filter 1: DTE band [MIN_DTE, MAX_DTE] calendar days
- ✅ Pre-filter 2: Delta band |delta| ∈ [MIN_DELTA_ABS, MAX_DELTA_ABS]
- ✅ No-quote: bid > 0 AND ask > 0
- ✅ Crossed: ask > bid
- ✅ Subpenny: bid and ask on penny grid (epsilon comparison)
- ✅ Spread: hard reject > MAX_REL_SPREAD_HARD, belly flag > MAX_REL_SPREAD_BELLY
- ✅ Zero-IV: implied_vol > MIN_IV
- ✅ Monotonicity per (expiration, timestamp, option_type) sorted by strike
- ✅ Intrinsic with belly exemption
- ✅ Low OI: open_interest > MIN_OI (null → reject)
- ✅ Row accounting: clean + quarantine == input
- ✅ Belly spread flagged (bit 0) but NOT quarantined
- ✅ Quarantine carries reject_code + reject_detail
- ✅ Output columns match COLUMNS.md §II

## Acceptance Criteria

### Functional
1. Subpenny check passes for valid penny prices (0.01, 0.02, 1.05, etc.)
2. Subpenny check rejects sub-penny (0.001, 0.015, etc.)
3. All thresholds configurable via config module
4. All existing tests pass
5. No hardcoded numeric thresholds remain in cleaning.py

### Testing
```bash
python -m pytest dataingestion/test_cleaning.py -v    # all 32 tests pass
```

### New Tests to Add in test_cleaning.py
- Subpenny edge cases: 0.01, 0.009999999, 0.010000001, 1.00, 3.05
- Config override test: verify thresholds come from config
- Missing OI column handled gracefully

## Implementation Notes

### Config Constants Needed
Add to `dataingestion/config.py` (EH-06):
```python
# DTE
MIN_DTE = 7
MAX_DTE = 90

# Delta
MIN_DELTA_ABS = 0.10
MAX_DELTA_ABS = 0.90

# Spread
MAX_REL_SPREAD_HARD = 0.25
MAX_REL_SPREAD_BELLY = 0.10

# IV
MIN_IV = 0.005

# OI
MIN_OI = 100

# Subpenny
SUBPENNY_EPS = 1e-8

# Quality flags
BELLY_SPREAD_BIT = 1
```

## Deliverables

1. **Modified** `dataingestion/cleaning.py` with fixes + config imports
2. **Updated** `dataingestion/test_cleaning.py` with new edge case tests
3. **Verification** all tests pass