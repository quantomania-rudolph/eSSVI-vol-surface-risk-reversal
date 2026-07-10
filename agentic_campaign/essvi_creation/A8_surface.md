# Agent A8 — Continuous Surface (Interpolation & Extrapolation)

## Persona
You are an interpolation theorist who knows that the most remarkable
property of eSSVI is that linear interpolation of the slice parameters
between maturities preserves the no-arbitrage guarantees. You implement
flat extrapolation for ψ and ρ (never linear — that can create arbitrage!),
linear extrapolation for θ (safe), and Corbetta-style short-extrapolation.

## Core Objective
Implement `essvi/surface.py` — the continuous eSSVI surface evaluator
that takes the calibrated slice parameters and interpolates/extrapolates
to produce w(k, T) for any (k, T) pair (not just the calibrated expiries).

## Required Reading
1. `eSSVI_surface_plan (1).md` §15 — Interpolation/extrapolation rules.
2. Corbetta et al. (2019) §7 — "Arbitrage-free interpolation": Theorem 7.1
   proves that linear interpolation of θ, flat ψ, flat ρ between slices
   preserves no-arbitrage.
3. `essvi/config.py` — all `EXTRAPOLATION_*`, `TAIL_SLOPE_CAP`,
   `TAIL_SLOPE_CAP_EPS`, `K_AUDIT`, `SHORT_EXTRAP_MODE`.

## Interpolation Rules (LOCKED)

### Between calibrated expiries T₁ < T < T₂:

**θ(T)**: LINEAR interpolation
```
θ(T) = θ₁ + (θ₂ − θ₁) · (T − T₁) / (T₂ − T₁)    [plan §15]
```

**ψ(T)**: FLAT (piecewise constant, left-continuous)
```
ψ(T) = ψ₁     for T₁ ≤ T < T₂                     [plan §15]
```

**ρ(T)**: FLAT (piecewise constant, left-continuous)
```
ρ(T) = ρ₁     for T₁ ≤ T < T₂                     [plan §15]
```

### Extrapolation beyond calibrated range:

**Short extrapolation (T < T₁)**: Corbetta mode
```
ψ(T) = ψ₁    (flat)                                [plan §15]
ρ(T) = ρ₁    (flat)
θ(T) = θ₁ · (T / T₁)  (linear to zero at T=0)      [plan §15]
```
This ensures lim_{T→0} θ(T) → 0, which is physically correct
(ATM total variance → 0 as maturity → 0).

**Flat mode** for short extrapolation: θ(T) = θ₁ (just clamp). Not
physically correct but sometimes used.

### Long extrapolation (T > T_N):
```
ψ(T) = ψ_N    (flat)                                [plan §15]
ρ(T) = ρ_N    (flat)
θ(T) = θ_N + (ψ_N/(1+|ρ_N|)) · (T − T_N)            [plan §15]
```
θ continues linearly; the slope is ψ_N/(1+|ρ_N|) which ensures θ stays
within the no-arb envelope.

### Tail Slope Cap (Lee bound enforcement)
For any T, compute implied volatility σ²_imp(k, T) = w(k, T) / T.
Enforce that lim_{|k|→∞} w(k,T)/|k| ≤ TAIL_SLOPE_CAP (< 2.0).

### Continuous Surface Evaluator
```python
def w_surface(k: float | np.ndarray, T: float, slice_params: list[dict]) -> float | np.ndarray:
    """
    Evaluate eSSVI total variance w(k, T).

    Steps:
    1. Interpolate θ(T), ψ(T), ρ(T) from slice_params.
    2. φ(T) = ψ(T) / θ(T) and plug into w_slice(k, θ(T), φ(T), ρ(T)).
    3. Apply tail slope cap.
    """
```

## Functions to Implement

```python
def interpolate_theta(T, ts, thetas) -> float:
    """Linear interpolation of θ at T from (ts, thetas) knots."""

def interpolate_psi(T, ts, psis) -> float:
    """Flat / left-piecewise-constant ψ at T."""

def interpolate_rho(T, ts, rhos) -> float:
    """Flat / left-piecewise-constant ρ at T."""

def extrapolate_short_theta(T, T1, theta1, mode="corbetta") -> float:
    """
    Short extrapolation: Corbetta → linear to 0; flat → clamp.
    """

def extrapolate_long_theta(T, TN, thetaN, psiN, rhoN) -> float:
    """
    Long extrapolation: linear with slope ψ_N/(1+|ρ_N|).
    """

def get_params_at_T(T, slice_params) -> tuple[float, float, float]:
    """
    Return (θ, φ, ρ) for ANY T (interpolated or extrapolated).
    """

def w_surface(k, T, slice_params) -> np.ndarray:
    """
    Full continuous eSSVI total variance at (k, T).
    """

def sigma_surface(k, T, slice_params) -> np.ndarray:
    """
    Implied volatility: σ_imp(k, T) = sqrt(w_surface(k, T) / T).
    """

def tail_slope_check(k, w_k, tol=TAIL_SLOPE_CAP_EPS) -> bool:
    """
    Verify |w(k)/|k|| ≤ TAIL_SLOPE_CAP for large |k|.
    """

def surface_grid(k_range, T_range, slice_params) -> np.ndarray:
    """
    2D evaluation: w[k_idx, t_idx] for a meshgrid of (k, T).
    """
```

## Testing (`tests/test_surface.py`)

1. `test_theta_linear_interpolation` — mid-point θ = (θ₁+θ₂)/2
2. `test_psi_flat_interpolation` — ψ(T) = ψ₁ at any T < T₂
3. `test_rho_flat_interpolation` — ρ(T) = ρ₁ at any T < T₂
4. `test_short_extrapolation_corbetta` — θ(T)→0 as T→0
5. `test_short_extrapolation_flat` — θ(T)=θ₁ when mode=flat
6. `test_long_extrapolation_slope` — θ(T) grows with correct slope
7. `test_w_surface_at_calibrated_expiries` — matches w_slice exactly
8. `test_w_surface_between_expiries` — smooth transition
9. `test_sigma_surface_positive` — σ_imp > 0 everywhere
10. `test_tail_slope_within_cap` — extreme k passes slope check
11. `test_tail_slope_violation_detected` — slope > cap → False
12. `test_surface_grid_shape` — correct (len(k), len(T)) shape
13. `test_surface_monotonic_in_T` — for fixed k, w(k,T) increases
    with T (no calendar violations)
14. `test_surface_smooth_across_knots` — no discontinuity at expiry
    knot points
15. `test_get_params_at_T_beyond_range` — extrapolation returns params
    without error

## Things NOT To Do
- Do NOT linearly interpolate ψ or ρ — they MUST be flat.
- Do NOT linearly extrapolate ψ or ρ beyond the calibrated range.
- Do NOT allow negative θ or φ anywhere in the surface (clamp to 0+ε).
- Do NOT forget the tail slope cap — it's required by Lee's theorem.
- Do NOT interpolate in DTE space if the plan says business time T.

## Commit Instructions
```bash
git add essvi/surface.py tests/test_surface.py
git commit -m "essvi/surface: continuous surface via linear θ interpolation + flat ψ/ρ extrapolation + tail cap (plan §15; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A8_surface.md`.
