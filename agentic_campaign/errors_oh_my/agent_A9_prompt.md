# Agent A9 Prompt — Config Parameters & Validation (config.py)

## Role: Quant Researcher + Developer

## Mission
Create the complete `config.py` with all parameters referenced in the plan, organized by category, with validation.

## Required Reading
- All agent prompts A1–A8 (they define config parameters)
- Plan §17 (Config list)
- Plan §19 (Open items that become config)

## What to Create

### `config.py` — Complete Configuration Module

```python
"""
eSSVI Calibration Engine Configuration

All parameters for the eSSVI volatility surface calibration.
References: eSSVI_surface_plan.md sections 4-17, Agent prompts A1-A8.

Categories:
- Corridor & Arbitrage Bounds
- Rho Grid & Outer Search
- Anchor & Theta Solve
- Objective Function & Weighting
- Regularization
- Solver Settings
- Interpolation & Extrapolation
- Session & Time Handling
- Degeneracy & Fallbacks
- Audit Grid
"""

# ============================================================
# CORRIDOR & ARBITRAGE BOUNDS
# ============================================================
CALENDAR_CONDITION_VERSION = "pasquazzi_2023"     # "hendriks_martini_2019" | "pasquazzi_2023"
BUTTERFLY_BOUND_MODE = "mm_exact"                 # "gj_conservative" | "mm_exact" | "both"
CORRIDOR_EPS = 1e-6
THETA_MONOTONICITY_EPS = 1e-8
KILL_TOL = 1e-10

# GJ Butterfly Bounds (used when BUTTERFLY_BOUND_MODE = "gj_conservative")
U_BF1_FACTOR = 4.0       # ψ(1+|ρ|) < 4
U_BF2_FACTOR = 2.0       # ψ²(1+|ρ|)/θ ≤ 4

# MM Butterfly Bound Parameters
MM_L_GRID_POINTS = 200
MM_L2_TOL = 1e-6
MM_L_MAX = 1000.0

# ============================================================
# RHO GRID & OUTER SEARCH
# ============================================================
RHO_GRID_LO = -0.99
RHO_GRID_HI = 0.90
RHO_GRID_STEP = 0.01              # Δρ for coarse grid
RHO_MAX_STEP = 0.15               # Δρ_max between adjacent maturities
RHO_GRID_REFINE_FACTOR = 3        # refinement factor for stage 2 (§4 step 4)

# ============================================================
# ANCHOR & THETA SOLVE
# ============================================================
ANCHOR_SOLVE_METHOD = "exact_closed_form"  # "exact_closed_form" | "fixed_point" (deprecated)
ANCHOR_THETA_TOL = 1e-10
ANCHOR_K_STAR_TOL = 1e-8
MIN_STRIKES_PER_SLICE = 3
SHORT_MATURITY_RHO_FALLBACK = "next_slice"  # "next_slice" | "prior" | "fixed" | "fit_psi_only"
SHORT_MATURITY_RHO_PRIOR = -0.5

# ============================================================
# OBJECTIVE FUNCTION & WEIGHTING
# ============================================================
VEGA_WEIGHT_MODE = "var_vega2"      # "var_vega2" | "vol_vega1" | "vol_vega2"
BELLY_BOOST = 3.0
BELLY_K_ABS = 0.15
BELLY_DELTA_LO = 0.10
BELLY_DELTA_HI = 0.90
WING_REL_SPREAD_MAX = 0.25
BELLY_REL_SPREAD_MAX = 0.10
BELLY_OI_MIN = 100

# Relaxed belly gates for anchor search
RELAXED_BELLY_REL_SPREAD_MAX = 0.15
RELAXED_BELLY_OI_MIN = 50
RELAXED_BELLY_DELTA_LO = 0.05
RELAXED_BELLY_DELTA_HI = 0.95

# ============================================================
# REGULARIZATION
# ============================================================
LAMBDA_RHO = 0.1            # term-structure ρ velocity penalty
LAMBDA_PSI = 0.1            # term-structure ψ velocity penalty
LAMBDA_TEMPORAL = 0.01      # temporal Tikhonov penalty
TEMPORAL_REG_MODE = "tikhonov"  # "tikhonov" | "warmstart_only" | "none"

# ============================================================
# SOLVER SETTINGS
# ============================================================
BRENT_XTOL = 1e-8
BRENT_MAX_ITER = 100
BRENT_BRACKET_EXPAND = 1.5

# ============================================================
# INTERPOLATION & EXTRAPOLATION
# ============================================================
EXTRAPOLATION_PSI_MODE = "flat"         # "flat" | "linear" (linear is WRONG)
EXTRAPOLATION_RHO_MODE = "flat"         # "flat" | "linear"
EXTRAPOLATION_THETA_MODE = "linear"     # "linear" | "flat"
TAIL_SLOPE_CAP = 1.9999                 # < 2.0 for Lee/B1 margin
TAIL_SLOPE_CAP_EPS = 1e-4
SHORT_EXTRAP_MODE = "corbetta"          # "corbetta" | "flat"
K_AUDIT = 3.0                           # max |k| for audit grid

# ============================================================
# SESSION & TIME HANDLING
# ============================================================
NO_TRADE_OPEN_MIN = 60
NO_TRADE_CLOSE_MIN = 60
SESSION_OPEN_HOUR = 9
SESSION_OPEN_MIN = 30
SESSION_CLOSE_HOUR = 16
SESSION_CLOSE_MIN = 0
COLD_START_AT_SESSION_OPEN = True

# ============================================================
# DEGENERACY & FALLBACKS
# ============================================================
EMPTY_CORRIDOR_STRATEGY = "degeneracy_first"  # "degeneracy_first" | "widen_rho_first"
THETA_PROJECTION_EPS = 1e-6
EXPIRY_IMMINENT_DTE = 1
EXPIRY_IMMINENT_CORRIDOR_WIDEN = 10.0
EXPIRY_IMMINENT_LAMBDA_TEMPORAL_MULT = 10.0
STALE_SLICE_MAX_MINUTES = 5

# Warm-Start
WARMSTART_CLIP_TO_CORRIDOR = True
WARMSTART_PSI_TOL = 1e-6
WARMSTART_RHO_TOL = 1e-6

# ============================================================
# AUDIT GRID
# ============================================================
AUDIT_GRID_POINTS = 400

# ============================================================
# VALIDATION
# ============================================================
def validate() -> bool:
    """Validate all config parameters. Returns True if valid, raises AssertionError otherwise."""
    # Corridor & Bounds
    assert CALENDAR_CONDITION_VERSION in ("hendriks_martini_2019", "pasquazzi_2023"), \
        f"Invalid CALENDAR_CONDITION_VERSION: {CALENDAR_CONDITION_VERSION}"
    assert BUTTERFLY_BOUND_MODE in ("gj_conservative", "mm_exact", "both"), \
        f"Invalid BUTTERFLY_BOUND_MODE: {BUTTERFLY_BOUND_MODE}"
    assert CORRIDOR_EPS > 0, "CORRIDOR_EPS must be positive"
    assert THETA_MONOTONICITY_EPS > 0, "THETA_MONOTONICITY_EPS must be positive"
    assert KILL_TOL >= 0, "KILL_TOL must be non-negative"
    assert MM_L_GRID_POINTS > 0, "MM_L_GRID_POINTS must be positive"
    assert MM_L2_TOL > 0, "MM_L2_TOL must be positive"
    
    # Rho Grid
    assert RHO_GRID_LO < RHO_GRID_HI, "RHO_GRID_LO must be < RHO_GRID_HI"
    assert -1 < RHO_GRID_LO < 1, "RHO_GRID_LO must be in (-1, 1)"
    assert -1 < RHO_GRID_HI < 1, "RHO_GRID_HI must be in (-1, 1)"
    assert RHO_GRID_STEP > 0, "RHO_GRID_STEP must be positive"
    assert RHO_MAX_STEP > 0, "RHO_MAX_STEP must be positive"
    assert RHO_GRID_REFINE_FACTOR >= 1, "RHO_GRID_REFINE_FACTOR must be >= 1"
    
    # Anchor
    assert ANCHOR_SOLVE_METHOD in ("exact_closed_form", "fixed_point"), \
        f"Invalid ANCHOR_SOLVE_METHOD: {ANCHOR_SOLVE_METHOD}"
    assert ANCHOR_THETA_TOL > 0, "ANCHOR_THETA_TOL must be positive"
    assert MIN_STRIKES_PER_SLICE > 0, "MIN_STRIKES_PER_SLICE must be positive"
    assert SHORT_MATURITY_RHO_FALLBACK in ("next_slice", "prior", "fixed", "fit_psi_only"), \
        f"Invalid SHORT_MATURITY_RHO_FALLBACK: {SHORT_MATURITY_RHO_FALLBACK}"
    assert -1 < SHORT_MATURITY_RHO_PRIOR < 1, "SHORT_MATURITY_RHO_PRIOR must be in (-1, 1)"
    
    # Objective
    assert VEGA_WEIGHT_MODE in ("var_vega2", "vol_vega1", "vol_vega2"), \
        f"Invalid VEGA_WEIGHT_MODE: {VEGA_WEIGHT_MODE}"
    assert BELLY_BOOST > 0, "BELLY_BOOST must be positive"
    assert BELLY_K_ABS > 0, "BELLY_K_ABS must be positive"
    assert 0 <= BELLY_DELTA_LO < BELLY_DELTA_HI <= 1, "Invalid belly delta bounds"
    assert 0 < WING_REL_SPREAD_MAX <= 1, "WING_REL_SPREAD_MAX must be in (0, 1]"
    assert 0 < BELLY_REL_SPREAD_MAX <= WING_REL_SPREAD_MAX, "BELLY_REL_SPREAD_MAX <= WING_REL_SPREAD_MAX"
    assert BELLY_OI_MIN >= 0, "BELLY_OI_MIN must be non-negative"
    
    # Regularization
    assert LAMBDA_RHO >= 0, "LAMBDA_RHO must be non-negative"
    assert LAMBDA_PSI >= 0, "LAMBDA_PSI must be non-negative"
    assert LAMBDA_TEMPORAL >= 0, "LAMBDA_TEMPORAL must be non-negative"
    assert TEMPORAL_REG_MODE in ("tikhonov", "warmstart_only", "none"), \
        f"Invalid TEMPORAL_REG_MODE: {TEMPORAL_REG_MODE}"
    
    # Solver
    assert BRENT_XTOL > 0, "BRENT_XTOL must be positive"
    assert BRENT_MAX_ITER > 0, "BRENT_MAX_ITER must be positive"
    assert BRENT_BRACKET_EXPAND > 1, "BRENT_BRACKET_EXPAND must be > 1"
    
    # Extrapolation
    assert EXTRAPOLATION_PSI_MODE in ("flat", "linear"), \
        f"Invalid EXTRAPOLATION_PSI_MODE: {EXTRAPOLATION_PSI_MODE}"
    assert EXTRAPOLATION_RHO_MODE in ("flat", "linear"), \
        f"Invalid EXTRAPOLATION_RHO_MODE: {EXTRAPOLATION_RHO_MODE}"
    assert EXTRAPOLATION_THETA_MODE in ("linear", "flat"), \
        f"Invalid EXTRAPOLATION_THETA_MODE: {EXTRAPOLATION_THETA_MODE}"
    assert TAIL_SLOPE_CAP < 2.0, "TAIL_SLOPE_CAP must be < 2.0 (Lee bound)"
    assert TAIL_SLOPE_CAP_EPS > 0, "TAIL_SLOPE_CAP_EPS must be positive"
    assert SHORT_EXTRAP_MODE in ("corbetta", "flat"), \
        f"Invalid SHORT_EXTRAP_MODE: {SHORT_EXTRAP_MODE}"
    assert K_AUDIT > 0, "K_AUDIT must be positive"
    
    # Session
    assert 0 <= SESSION_OPEN_HOUR < 24, "Invalid SESSION_OPEN_HOUR"
    assert 0 <= SESSION_OPEN_MIN < 60, "Invalid SESSION_OPEN_MIN"
    assert 0 <= SESSION_CLOSE_HOUR < 24, "Invalid SESSION_CLOSE_HOUR"
    assert 0 <= SESSION_CLOSE_MIN < 60, "Invalid SESSION_CLOSE_MIN"
    assert NO_TRADE_OPEN_MIN >= 0, "NO_TRADE_OPEN_MIN must be non-negative"
    assert NO_TRADE_CLOSE_MIN >= 0, "NO_TRADE_CLOSE_MIN must be non-negative"
    
    # Degeneracy
    assert EMPTY_CORRIDOR_STRATEGY in ("degeneracy_first", "widen_rho_first"), \
        f"Invalid EMPTY_CORRIDOR_STRATEGY: {EMPTY_CORRIDOR_STRATEGY}"
    assert THETA_PROJECTION_EPS > 0, "THETA_PROJECTION_EPS must be positive"
    assert EXPIRY_IMMINENT_DTE >= 1, "EXPIRY_IMMINENT_DTE must be >= 1"
    assert EXPIRY_IMMINENT_CORRIDOR_WIDEN > 1, "EXPIRY_IMMINENT_CORRIDOR_WIDEN must be > 1"
    assert EXPIRY_IMMINENT_LAMBDA_TEMPORAL_MULT > 1, "EXPIRY_IMMINENT_LAMBDA_TEMPORAL_MULT must be > 1"
    assert STALE_SLICE_MAX_MINUTES >= 0, "STALE_SLICE_MAX_MINUTES must be non-negative"
    
    # Warm-Start
    assert WARMSTART_PSI_TOL >= 0, "WARMSTART_PSI_TOL must be non-negative"
    assert WARMSTART_RHO_TOL >= 0, "WARMSTART_RHO_TOL must be non-negative"
    
    # Audit
    assert AUDIT_GRID_POINTS > 0, "AUDIT_GRID_POINTS must be positive"
    
    print("✓ config.py validation passed")
    return True

# Run validation on import
if __name__ == "__main__":
    validate()

# Convenience: get all config as dict
def as_dict() -> dict:
    """Return all config parameters as a dictionary."""
    return {k: v for k, v in globals().items() if k.isupper() and not k.startswith('_')}

def get_config_summary() -> str:
    """Return a formatted summary of key config values."""
    lines = [
        "eSSVI Config Summary",
        "=" * 40,
        f"Calendar: {CALENDAR_CONDITION_VERSION}",
        f"Butterfly: {BUTTERFLY_BOUND_MODE}",
        f"Vega Weight: {VEGA_WEIGHT_MODE}",
        f"Rho Grid: [{RHO_GRID_LO}, {RHO_GRID_HI}] step={RHO_GRID_STEP}",
        f"Lambda: ρ={LAMBDA_RHO}, ψ={LAMBDA_PSI}, temp={LAMBDA_TEMPORAL} ({TEMPORAL_REG_MODE})",
        f"Extrapolation: ψ={EXTRAPOLATION_PSI_MODE}, ρ={EXTRAPOLATION_RHO_MODE}, θ={EXTRAPOLATION_THETA_MODE}",
        f"Tail Cap: {TAIL_SLOPE_CAP}",
        f"Kill Tol: {KILL_TOL}",
        f"Session: {SESSION_OPEN_HOUR}:{SESSION_OPEN_MIN:02d} - {SESSION_CLOSE_HOUR}:{SESSION_CLOSE_MIN:02d}",
    ]
    return "\n".join(lines)
```

## Output Format

Create `config.py` in the `essvi/` package directory (or project root). Mark with `<<A9_CHANGE>>`.

## Validation

- [ ] All parameters from A1–A8 prompts present
- [ ] Validation function catches invalid values
- [ ] `as_dict()` and `get_config_summary()` work
- [ ] No syntax errors
- [ ] Importable: `from essvi.config import validate; validate()`