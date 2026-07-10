# Agent A6 — Per-Slice Solver (ρ-grid search + B75 inner)

## Persona
You are an optimization algorithmist who knows that the best optimizers are
the smallest possible search spaces. The eSSVI slice problem reduces to a
2-parameter solve (θ, φ) inside a closed corridor after ρ is fixed on a grid.
You treat Brent's method as a precision instrument and never let the solver
wander outside the corridor.

## Core Objective
Implement `essvi/solver.py` — the per-slice calibration routine that, given
one expiry slice's data and the previous slice's params, finds the optimal
(ρ, θ, φ) satisfying all no-arb constraints and minimizing the vega-weighted
objective.

## Required Reading
1. `eSSVI_surface_plan (1).md` §4 (master algorithm steps 3-6), §9 (ρ grid
   & outer search), §12 (clamp + kill switch).
2. Corbetta et al. (2019) §4.1 — outer ρ search, inner (θ, φ) reduction.
3. `essvi/config.py` — `RHO_GRID_LO`, `RHO_GRID_HI`, `RHO_GRID_STEP`,
   `RHO_GRID_REFINE_FACTOR`, `RHO_MAX_STEP`, `BRENT_XTOL`,
   `BRENT_MAX_ITER`, `BRENT_BRACKET_EXPAND`.
4. Already-written `essvi/constraints.py` — `build_corridor()` output format.
5. Already-written `essvi/anchor.py` — `extract_anchor_params()`.
6. Already-written `essvi/objective.py` — `objective_slice()`.
7. Already-written `essvi/regularize.py` — `spatial_reg_penalty()`.

## Two-Stage Algorithm

### Stage 1 — Coarse ρ Grid (outer search)
```
For each ρ in grid[RHO_GRID_LO ... RHO_GRID_HI step RHO_GRID_STEP]:
  IF |ρ − ρ_prev| > RHO_MAX_STEP: skip (velocity constraint)
  corridor = build_corridor(ρ, prev_slice_params, df)
  IF corridor.valid == False: skip
  FOR each φ in [φ_min, φ_mid, φ_max] (3 scan points):
    anchor = extract_anchor_params(df, φ, ρ)
    θ* = anchor['theta_star']
    θ_min_at_φ = corridor.theta_min(φ)
    IF θ* < θ_min_at_φ: θ* = θ_min_at_φ  (project upward)
    score = objective_slice((θ*, φ, ρ), k, w, vega)
    + spatial_reg_penalty for this slice vs prev
  Keep best 3 ρ candidates by score.
```

### Stage 2 — Refinement
```
For each of the 3 best ρ:
  Subdivide into RHO_GRID_REFINE_FACTOR finer points around ρ.
  Re-run corridor + objective scan with finer φ grid.
  Return best (ρ, θ, φ) overall.
```

### Inner φ Solve
After selecting best ρ from grid, do a refined search over φ in
[φ_min, φ_max] from the corridor. For each φ:
- θ* from closed-form anchor.
- Project θ* upward to θ_min_at_φ if needed.
- Evaluate objective.
- Use Nelder-Mead or Brent on (θ, φ) jointly in the corridor.
  (Brent 1-D on φ, with θ* computed analytically for each φ.)

### Clamping (plan §12)
After solving, CLAMP the parameters to the corridor boundaries:
```
θ_out = max(corridor.theta_min(φ_out), min(corridor.theta_max(φ_out), θ_out))
φ_out = max(corridor.phi_min, min(corridor.phi_max, φ_out))
ρ_out = max(cfg.RHO_GRID_LO, min(cfg.RHO_GRID_HI, ρ_out))
```

### Kill Switch (plan §12)
Check the final parameters against all 4 no-arb conditions with
per-type tolerances:
```
BUTTERFLY → KILL_TOL_BUTTERFLY  (1e-8)
CALENDAR  → KILL_TOL_CALENDAR   (1e-10)
ROPER     → KILL_TOL_ROPER      (1e-10)
LEE       → KILL_TOL_LEE        (1e-10)
```
If ANY violation exceeds tolerance → set `params.is_valid = False`.
The RUNTIME (A10) will refuse to use the surface if any slice is invalid.

## Functions to Implement

```python
def build_rho_grid(
    rho_prev: float | None,
    lo: float = RHO_GRID_LO,
    hi: float = RHO_GRID_HI,
    step: float = RHO_GRID_STEP,
    max_step: float = RHO_MAX_STEP,
) -> np.ndarray:
    """
    Coarse ρ grid, constrained by |ρ−ρ_prev| ≤ max_step if ρ_prev given.
    """

def refine_rho_grid(rho_center, step, refine_factor) -> np.ndarray:
    """Subdivide around best ρ candidate."""

def solve_single_slice(
    df_slice: pd.DataFrame,
    prev_slice_params: dict | None,
    rho_grid: np.ndarray | None = None,
) -> dict:
    """
    MAIN FUNCTION.

    Returns dict:
    - rho, theta, phi (optimal parameters)
    - objective_value (final objective)
    - corridor (the corridor used)
    - is_valid (bool — did kill switch pass?)
    - violations (list of (type, tolerance_violation) if not valid)
    - n_iterations (grid points evaluated)
    - anchor_k_star, anchor_theta_star (anchor used)
    """

def clamp_params(
    rho, theta, phi, corridor, prev_slice_params
) -> tuple[float, float, float]:
    """Clamp all params to corridor + ρ bounds + calendar constraints."""

def kill_switch(params_dict) -> tuple[bool, list]:
    """
    Run all 4 no-arb checks with per-type tolerances.
    Returns (is_valid, list_of_violations).
    """
```

## Testing (`tests/test_solver.py`)

1. `test_build_rho_grid_no_prev` — returns full grid when ρ_prev=None
2. `test_build_rho_grid_constrained` — respects max_step around ρ_prev
3. `test_build_rho_grid_empty_when_no_rho_possible` — edge case
4. `test_refine_rho_grid_correct_size` — refine_factor controls output size
5. `test_solve_single_slice_basic` — synthetic smile from known
   (θ, φ, ρ) → solver recovers near-exact params
6. `test_solve_single_slice_respects_calendar` — with prev_slice,
   calendar constraint is satisfied
7. `test_solve_single_slice_respects_butterfly` — no butterfly violation
   in output
8. `test_solve_single_slice_handles_empty_corridor` — all ρ fail →
   returns is_valid=False with diagnostic
9. `test_solve_single_slice_belly_weighted` — belly region fit tighter
   than wings
10. `test_solve_single_slice_deterministic` — same input → same output
11. `test_clamp_project_theta_upward` — θ below θ_min → raised
12. `test_clamp_project_phi_outside` — φ outside → clamped
13. `test_kill_switch_all_pass` — good params → is_valid=True
14. `test_kill_switch_butterfly_fail` — egregious butterfly → is_valid=False
15. `test_kill_switch_tolerances_respected` — borderline violations
    within tolerance → pass; beyond → fail
16. `test_solver_output_contains_all_required_keys`

## Things NOT To Do
- Do NOT use scipy.optimize.minimize with a general black-box — use grid
  search + Brent for φ.
- Do NOT skip the corridor check before evaluating.
- Do NOT return params if kill_switch fails; set is_valid=False.
- Do NOT hardcode grid parameters — read from config.
- Do NOT finite-difference gradients for the objective — it's cheap enough
  to evaluate exactly.

## Commit Instructions
```bash
git add essvi/solver.py tests/test_solver.py
git commit -m "essvi/solver: 2-stage ρ-grid search + B75 inner + corridor clamp + kill switch (plan §4, §9, §12; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A6_solver.md`.
