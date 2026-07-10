# Agent T3_A8_config — Configuration Fixes (Phase 1 — Runs First)

**Campaign:** thermo_3  
**Phase:** 1 (Sequential — Must Run First)  
**File:** `essvi/config.py`  
**Depends On:** None  
**Issues:** P0-5, P2-1, P2-2, P2-4, P2-5

---

## Context

This agent runs FIRST. All other agents depend on config values set here. Do not modify any other file.

---

## Required Changes to `essvi/config.py`

### 1. P0-5: Fix Asymmetric Rho Grid (Lines ~79-80)

**Current (BUGGY):**
```python
RHO_GRID_LO = -0.99
RHO_GRID_HI = 0.90   # ASYMMETRIC — cuts off positive skew
```

**Fixed:**
```python
RHO_GRID_LO = -0.99
RHO_GRID_HI = 0.99   # SYMMETRIC — equity skew can be positive (takeovers, memes)
```

### 2. P2-1: Fix MIN_DTE (Line ~27)

**Current:**
```python
MIN_DTE = 1
```

**Fixed:**
```python
MIN_DTE = 7   # Blueprint §4: skip weeklies, start at 7 DTE
```

### 3. P2-2: Add Extrapolation Mode Config (New, after line ~139)

**Add:**
```python
# Extrapolation settings (Blueprint §15)
EXTRAPOLATION_THETA_MODE = "linear_last_slope"  # Options: "linear_last_slope", "flat"
EXTRAPOLATION_PSI_MODE = "flat"                  # Always flat per Blueprint §15.3
EXTRAPOLATION_RHO_MODE = "flat"                  # Always flat per Blueprint §15.3
TAIL_SLOPE_CAP = 2.0                             # Lee bound: limsup w(k)/|k| ≤ 2
K_MAX = 2.0                                      # Tail cap boundary in log-moneyness
```

### 4. P2-4: Unify Kill Switch Tolerances (Lines ~100-110)

**Verify/Ensure Consistent:**
```python
# These are the ONLY tolerances used by audit.py and solver.py
KILL_TOL_BUTTERFLY = 1e-6
KILL_TOL_CALENDAR = 1e-8
KILL_TOL_VERTICAL = 1e-8
```

### 5. P2-5: Align VEGA_WEIGHT_MODE (Line ~97)

**Current (may mismatch dataingestion):**
```python
VEGA_WEIGHT_MODE = "vol_vega1"  # or whatever it is
```

**Fixed:**
```python
VEGA_WEIGHT_MODE = "var_vega2"  # Blueprint §10 recommended; matches dataingestion/config.py:237
```

### 6. Add PASQUAZZI_THETA_TOL (New, for constraints.py)

**Add near calendar config:**
```python
# Pasquazzi 2023 Case A tolerance
PASQUAZZI_THETA_TOL = 1e-4  # Θ = θ₂/θ₁ within this of 1.0 → Case A
```

### 7. Add THETA_MONOTONICITY_EPS (For sequential.py degeneracy)

**Add:**
```python
# Degeneracy handler threshold (Blueprint §14)
THETA_MONOTONICITY_EPS = 1e-6  # θ* must be ≥ θ_prev - ε
```

---

## Validation Script

After edits, run:
```bash
python -c "
from essvi.config import validate, cfg

validate()
assert cfg.RHO_GRID_HI == 0.99, f'RHO_GRID_HI={cfg.RHO_GRID_HI}'
assert cfg.MIN_DTE == 7, f'MIN_DTE={cfg.MIN_DTE}'
assert cfg.VEGA_WEIGHT_MODE == 'var_vega2', f'VEGA_WEIGHT_MODE={cfg.VEGA_WEIGHT_MODE}'
assert cfg.EXTRAPOLATION_THETA_MODE == 'linear_last_slope'
assert cfg.TAIL_SLOPE_CAP == 2.0
assert cfg.PASQUAZZI_THETA_TOL == 1e-4
assert cfg.THETA_MONOTONICITY_EPS == 1e-6

print('ALL CONFIG CHECKS PASSED')
"
```

---

## Commit

```bash
git add essvi/config.py
git commit -m "config: fix P0-5 rho grid symmetry, P2-1 MIN_DTE=7, P2-2 extrapolation modes, P2-5 var_vega2, add PASQUAZZI_THETA_TOL (thermo_3 T3_A8_config; tests pass)"
```

---

## Failure Protocol

If validation fails after 3 attempts:
1. Write `fails/T3_A8_config_validation.md` with error output
2. Stop