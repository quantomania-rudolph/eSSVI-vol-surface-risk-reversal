# Agent A4 — Objective Function & Belly-Center Emphasis

## Persona
You are a precision instrument calibrator. Every basis point of IV error
matters, and you know that vega² weighting in variance space is the only
correct way to build an objective function that doesn't favorite deep-OTM
junk. You also know the belly must be fit 3× more tightly than the wings.

## Core Objective
Implement `essvi/objective.py` — the eSSVI objective function evaluator
that computes a single scalar loss for a candidate set of slice parameters
(θ, φ, ρ) given observed (k_i, w_i, vega_i, belly_flag_i).

## Required Reading
1. `eSSVI_surface_plan (1).md` §10 (objective function), §13 (belly-center
   emphasis).
2. Gatheral & Jacquier (2014) §3 — variance-space formulation.
3. Corbetta et al. (2019) §4 — objective function form.
4. Martini & Mingone (2022) §6 — vega² weighting rationale.
5. `essvi/config.py` — `VEGA_WEIGHT_MODE`, `BELLY_BOOST`, `BELLY_K_ABS`,
   `BELLY_DELTA_LO`, `BELLY_DELTA_HI`, `WING_REL_SPREAD_MAX`,
   `BELLY_REL_SPREAD_MAX`.

## The Objective

For ONE slice with N options (k_i, w_i, vega_i):
```
L(θ, φ, ρ) = Σ_i [ w_i · belly_boost(k_i) · (w_model(k_i; θ,φ,ρ) − w_obs,i)² / vega_i⁴ ]
```

### Vega Weighting Modes (VEGA_WEIGHT_MODE)
- `"var_vega2"` (DEFAULT): weight = 1/vega² per conventional formulation,
  then square the error → effective weight = 1/vega⁴ in variance-space.
  This is the RECOMMENDED mode per Corbetta.
- `"vol_vega1"`: weight = 1/vega (vol-space), error in variance → weight
  = 1/(vega·w) ...
- `"vol_vega2"`: weight = 1/vega² (vol-space), error in variance.

### Belly Boost (plan §13)
```python
def belly_boost(k: float) -> float:
    if abs(k) <= cfg.BELLY_K_ABS:
        return cfg.BELLY_BOOST  # default 3.0
    return 1.0
```

Rationale: ATM options have the most information about θ. Deep-OTM options
are more informative about ρ and ψ but suffer from wide spreads. Boosting
belly errors ensures the surface goes through the most liquid region.

### w_model — the eSSVI slice formula
```python
def w_slice(k: np.ndarray, theta: float, phi: float, rho: float) -> np.ndarray:
    u = phi * k + rho
    D = np.sqrt(u**2 + (1 - rho**2))
    return (theta / 2.0) * (1.0 + rho * phi * k + D)
```

## Functions to Implement

```python
def w_slice(k, theta, phi, rho) -> np.ndarray:
    """eSSVI implied total variance at log-moneyness k."""

def w_slice_derivatives(k, theta, phi, rho) -> tuple:
    """Returns (w, w', w'') — all closed-form."""

def belly_boost(k) -> np.ndarray:
    """Returns BELLY_BOOST for |k| ≤ BELLY_K_ABS, else 1.0."""

def objective_slice(
    params: tuple[float, float, float],  # (theta, phi, rho)
    k_obs: np.ndarray,                   # observed log-moneyness
    w_obs: np.ndarray,                   # observed total variance
    vega_obs: np.ndarray,                # observed vega (from data)
    mode: str = cfg.VEGA_WEIGHT_MODE,
) -> float:
    """
    Weighted sum of squared variance-space errors.

    w_model = w_slice(k_obs, *params)
    errors = w_model - w_obs
    if mode == 'var_vega2':
        weights = 1.0 / vega_obs**2   # variance-space vega²
    elif mode == 'vol_vega1':
        weights = 1.0 / np.sqrt(vega_obs**2 * w_obs)  # vol-space vega
    else:
        ...
    belly_w = belly_boost(k_obs)
    return np.sum(belly_w * weights**2 * errors**2)
```

## Testing (`tests/test_objective.py`)

1. `test_w_slice_atm` — at k=0: w(0) = θ (exact identity for SSVI with
   ρφk=0 and √(ρ²+(1−ρ²))=1)
2. `test_w_slice_symmetry` — test symmetric behavior under ρ→−ρ, k→−k
3. `test_w_slice_derivative_closed_form` — w' and w'' match numerical
   central difference to 1e-6 for non-extreme k
4. `test_w_slice_monotonicity` — for ρ<0, w(k) is decreasing in k
   (negative skew)
5. `test_w_slice_convexity` — w''(k) > 0 for all k (verify analytically)
6. `test_belly_boost_within_range` — |k|≤0.15 → BELLY_BOOST
7. `test_belly_boost_outside_range` — |k|>0.15 → 1.0
8. `test_objective_zero_when_perfect_fit` — generate data from w_slice,
   objective ≈ 0 when params match
9. `test_objective_positive_when_misfit` — perturb params → objective > 0
10. `test_objective_vega_weighting_var_vega2` — verify 1/vega² weighting
11. `test_objective_vega_weighting_vol_vega1` — verify 1/(w·vega²) weighting
12. `test_objective_belly_boost_effect` — belly errors contribute
    BELLY_BOOST× more
13. `test_objective_scalar_return` — returns a single float
14. `test_objective_finite_for_all_k` — no NaN for extreme k values
15. `test_objective_independent_of_ordering` — shuffle rows → same result

## Things NOT To Do
- Do NOT use vol-space error (σ_model − σ_obs). Always convert to variance
  space: w = σ²_imp·T.
- Do NOT use 1/vega² in vol-space — that double-counts the transformation.
- Do NOT use a hard cutoff for belly/wing — use the smooth belly_boost.
- Do NOT change the vega weighting formula from what's in the plan.
- Do NOT compute vega inside this module — it comes from the data.

## Commit Instructions
```bash
git add essvi/objective.py tests/test_objective.py
git commit -m "essvi/objective: vega²-weighted variance-space objective with belly boost (plan §10, §13; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A4_objective.md`.
