# Agent A5 — Temporal & Spatial Regularization

## Persona
You are a time-series regularizer who knows that unconstrained calibration
produces noisy, jumpy parameter paths that are economically meaningless.
You add just enough Tikhonov penalty to stabilize without flattening the
smile. You work in log-space for θ because θ is always positive and spans
orders of magnitude; ρ and ψ never need log-transforms.

## Core Objective
Implement `essvi/regularize.py` — the temporal and spatial (ρ-velocity,
ψ-velocity) regularization penalties that stabilize parameter evolution.

## Required Reading
1. `eSSVI_surface_plan (1).md` §11 — Regularizations (2 axes).
2. `essvi/config.py` — `TEMPORAL_REG_MODE`, `LAMBDA_TEMPORAL`, `LAMBDA_RHO`,
   `LAMBDA_PSI`, `TEMPORAL_THETA_SCALE`, `TEMPORAL_RHO_SCALE`,
   `TEMPORAL_PSI_SCALE`, `TEMPORAL_THETA_LOG`.
3. Corbetta et al. (2019) §4.2 — λ_ρ and λ_ψ penalties on ρ and ψ.

## Two Regularization Axes

### Axis 1: Term-Structure Velocity (spatial, within one minute)
Penalize large jumps in ρ_t and ψ_t between adjacent maturities:
```
R_spatial = λ_ρ · Σ_{t=2}^{N} (ρ_t − ρ_{t−1})² / s_ρ²
          + λ_ψ · Σ_{t=2}^{N} (ψ_t − ψ_{t−1})² / s_ψ²

where s_ρ = scale_ρ (0.5), s_ψ = scale_ψ (0.5)
```
This is WITHIN one calibration minute (across DTE slices), not across time.

### Axis 2: Temporal Regularization (across successive minutes)
Penalize the parameter vector at minute m from deviating from minute m−1:
```
R_temporal = λ_temp · ‖θ̄_m − θ̄_{m−1}‖² / s_θ²
           + λ_temp · ‖ρ̄_m − ρ̄_{m−1}‖² / s_ρ²
           + λ_temp · ‖ψ̄_m − ψ̄_{m−1}‖² / s_ψ²

where θ̄_m = log(θ_m) if TEMPORAL_THETA_LOG was True per plan §11,
             else θ_m itself.
```
Note: plan §11 uses vector notation ‖·‖² across all expiries for a single
minute. A5 implements the per-minute L² norm. The actual per-expiry temporal
penalty that feeds into the solver is handled by the solver's warm-start
logic (A6).

### Usage in Solver
The spatial regularization R_spatial is ADDED to the objective at the
sequential solver level (A7). The temporal regularization R_temporal is
used in two ways:
1. **Warmstart**: A6 solver uses prior-minute params as initial guess.
2. **Tikhonov penalty**: If `cfg.TEMPORAL_REG_MODE == "tikhonov"`, add
   to objective during inner B75 solve.

## Functions to Implement

```python
def spatial_reg_penalty(
    rho_array: np.ndarray,   # [N_slices]
    psi_array: np.ndarray,   # [N_slices]
    lambda_rho: float = LAMBDA_RHO,
    lambda_psi: float = LAMBDA_PSI,
    scale_rho: float = TEMPORAL_RHO_SCALE,
    scale_psi: float = TEMPORAL_PSI_SCALE,
) -> float:
    """
    Σ_i (ρ_i − ρ_{i−1})² / sρ² · λρ  +  Σ_i (ψ_i − ψ_{i−1})² / sψ² · λψ
    """

def temporal_reg_penalty(
    theta_current: np.ndarray,    # [N_slices] current minute θ
    rho_current: np.ndarray,      # [N_slices]
    psi_current: np.ndarray,      # [N_slices]
    theta_prior: np.ndarray | None,  # previous minute (None = no penalty)
    rho_prior: np.ndarray | None,
    psi_prior: np.ndarray | None,
    lambda_temp: float = LAMBDA_TEMPORAL,
    scale_theta: float = TEMPORAL_THETA_SCALE,
    scale_rho: float = TEMPORAL_RHO_SCALE,
    scale_psi: float = TEMPORAL_PSI_SCALE,
    use_log_theta: bool = TEMPORAL_THETA_LOG,
) -> float:
    """
    If prior is None → return 0.0.
    Otherwise, L² norm of difference with log(θ) if use_log_theta.
    """

def warmstart_params(
    prior_params: dict | None,
    n_slices: int,
    rho_fallback: float = -0.5,
    psi_fallback: float = 0.5,
) -> dict:
    """
    If prior_params exists → return dict with theta_0, rho_0, psi_0 arrays.
    If None → return fallback defaults (flat init).
    """
```

## Testing (`tests/test_regularize.py`)

1. `test_spatial_reg_zero_when_constant` — all ρ equal → penalty = 0
2. `test_spatial_reg_positive_when_jumps` — ρ has a jump → penalty > 0
3. `test_spatial_reg_symmetric` — two slices with equal jump → correct
4. `test_spatial_reg_scales_with_lambda` — 2× λ → 2× penalty
5. `test_temporal_reg_zero_when_no_prior` — prior=None → penalty = 0
6. `test_temporal_reg_zero_when_identical` — current = prior → 0
7. `test_temporal_reg_positive_when_different` — current ≠ prior → > 0
8. `test_temporal_reg_log_theta` — verify log space for θ
9. `test_temporal_reg_linear_theta` — when use_log_theta=False
10. `test_warmstart_returns_prior_when_available`
11. `test_warmstart_returns_fallback_when_none`
12. `test_scale_normalization` — different scale parameters normalize
    correctly
13. `test_theta_log_prevents_negative` — log(0) scenario (use clip)

## Things NOT To Do
- Do NOT include temporal penalty inside the per-slice objective — it's
  applied at the minute level, not per slice.
- Do NOT use log for ρ or ψ — they can be negative.
- Do NOT create an adaptive λ — use config constants exactly.
- Do NOT penalize θ in the spatial (across-slice) term — only ρ and ψ
  have spatial penalties (θ is free per slice).
- Do NOT hardcode the fallback values — read from config.

## Commit Instructions
```bash
git add essvi/regularize.py tests/test_regularize.py
git commit -m "essvi/regularize: spatial ρ/ψ velocity + temporal Tikhonov + warmstart (plan §11; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A5_regularize.md`.
