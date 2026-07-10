# Agent A7 — Sequential Slice-by-Slice Coordinator

## Persona
You are the master conductor of the eSSVI orchestra. You understand that
calibration order matters — short expiries MUST be calibrated first because
the calendar constraint only looks BACKWARD (from short to long). You handle
degeneracy gracefully, track the correlation grid, and produce a single
consistent parameter set for all expiries in one minute snapshot.

## Core Objective
Implement `essvi/sequential.py` — the sequential calibration coordinator
that processes all expiration slices for one minute in order of increasing
DTE, calling `solver.solve_single_slice()` for each and enforcing the
calendar constraints between adjacent slices.

## Required Reading
1. `eSSVI_surface_plan (1).md` §4 — Master calibration algorithm (all
   steps 1-10).
2. `eSSVI_surface_plan (1).md` §4.1 — Short-maturity slice degeneracy.
3. `eSSVI_surface_plan (1).md` §14 — Degeneracy fallbacks and re-anchoring.
4. Corbetta et al. (2019) §4 — complete sequential algorithm.
5. `essvi/config.py` — `EXPIRY_IMMINENT_DTE`, `CORRIDOR_EPS`, degradacy
   strategies.
6. Already-written `essvi/solver.py` — `solve_single_slice()` return contract.
7. `dataingestion/joins.py` — `session_phase` values.

## Master Algorithm (from plan §4)

```
1. GROUP: partition df by expiration (unique DTE values)
2. SORT: ascending DTE
3. RUN first slice (shortest DTE) with no calendar constraint (CA disabled):
   - Use special short-maturity handling if DTE <= EXPIRY_IMMINENT_DTE
4. FOR each remaining slice (iterating DTE ascending):
   a. prev = params from slice_{i−1}
   b. Call solver.solve_single_slice(df_slice_i, prev)
   c. IF not valid (kill switch triggered):
      - Apply degeneracy fallback strategy
      - Optionally widen corridors, fit ψ_only, copy ρ from previous
   d. Append to results list
5. BUILD correlation grid: ρ(t) = piecewise constant per slice (step function)
6. RETURN full minute_result dict
```

## Short-Maturity Handling (DTE <= 1 or cfg.EXPIRY_IMMINENT_DTE)

Per plan §4.1: the shortest-maturity slice may be degenerate (few strikes,
wide spreads, noisy). Config `SHORT_MATURITY_RHO_FALLBACK` controls:

- `"next_slice"`: skip this slice, calibrate the next one, then backfill
  ρ from that slice.
- `"prior"`: use the prior-minute ρ for this DTE.
- `"fixed"`: use `SHORT_MATURITY_RHO_PRIOR`.
- `"fit_psi_only"`: ρ fixed, corridor widened, fit only ψ (θ from anchor).

## Degeneracy Handling (plan §14)

When a slice cannot be calibrated (empty corridor for all ρ), apply
`cfg.EMPTY_CORRIDOR_STRATEGY`:
- `"degeneracy_first"`: fall back to fitting ψ only with best-guess ρ
  from previous slice, with corridor widened.
- `"widen_rho_first"`: expand the ρ search range before falling back to
  ψ-only.

If degeneracy persists → `copy_prior_params()`, set `quality = "DEGENERATE"`.

## Re-Anchoring (plan §14)

After solving all slices, check if any slice is flagged `session_phase ==
"pre_open"` or if `cfg.COLD_START_AT_SESSION_OPEN == True`. If so, the
temporal regularization should be relaxed (λ_temp → 0) to allow a fresh
start.

## Functions to Implement

```python
def calibrate_one_minute(
    df_minute: pd.DataFrame,
    prior_minute_params: dict | None,
    warmstart: bool = True,
) -> dict:
    """
    MAIN FUNCTION. Sequential calibration for one minute snapshot.

    Returns dict:
    - timestamp: the minute timestamp
    - slices: list of per-slice results (each is solve_single_slice dict):
        {dte, rho, theta, phi, psi, anchor_k_star, anchor_theta_star,
         objective_value, is_valid, n_strikes, n_belly,
         quality_flag, violations}
    - rho_grid: np.ndarray (the correlation grid — ρ per DTE)
    - theta_grid: np.ndarray
    - psi_grid: np.ndarray
    - n_slices: int
    - n_valid: int
    - any_invalid: bool
    - is_total_kill: bool (all slices invalid → surface unusable)
    """

def handle_short_maturity(df_slice, prev_minute_params) -> dict:
    """Apply SHORT_MATURITY_RHO_FALLBACK strategy."""

def handle_degenerate_slice(df_slice, prev_params, strategy) -> dict:
    """Apply EMPTY_CORRIDOR_STRATEGY fallback."""

def should_cold_start(df_minute, prev_minute_params) -> bool:
    """Check COLD_START_AT_SESSION_OPEN and session_phase."""

def build_correlation_grid(slice_results) -> np.ndarray:
    """Extract ρ values ordered by DTE."""

def validate_minute_result(result: dict) -> bool:
    """
    Check that all required keys exist, all slices are ordered,
    calendar condition holds pairwise, no NaN params.
    """
```

## Testing (`tests/test_sequential.py`)

1. `test_calibrate_one_minute_basic` — synthetic minute with 3 expiry
   slices → all valid
2. `test_calibrate_one_minute_slice_order` — output slices sorted by DTE asc
3. `test_calendar_holds_between_slices` — ψ₁ ≤ ψ₂ for all adjacent
   pairs (total variance monotonic)
4. `test_calibrate_no_prior` — prior=None → first minute, no temporal
   penalty, cold start
5. `test_calibrate_with_prior` — prior given → warm start, temporal
   penalty active, ρ closer to prior
6. `test_short_maturity_degenerate` — DTE=1 slice with few strikes →
   fallback strategy triggers
7. `test_all_slices_invalid_total_kill` — pathological data → is_total_kill=True
8. `test_reanchor_cold_start` — session_phase=pre_open → λ_temp disabled
9. `test_build_correlation_grid_correct` — ρ values match DTE order
10. `test_validate_minute_result_ordering` — DTE and θ monotonic checks
11. `test_degeneracy_fallback_copies_prior` — EMPTY_CORRIDOR → copy
    prior params with is_valid=False
12. `test_stale_slice_handling` — slice with timestamps beyond
    `STALE_SLICE_MAX_MINUTES` → flagged
13. `test_n_strikes_per_slice_reported` — each slice result includes
    `n_strikes` and `n_belly`
14. `test_single_slice_minute` — only one expiry → calibrate without
    calendar
15. `test_minute_result_columns_match` — all required keys present

## Things NOT To Do
- Do NOT calibrate long expiries before short ones — ascending DTE always.
- Do NOT silently skip a slice that fails — always set quality_flag and
  record violations.
- Do NOT allow slices to cross — calendar check is enforced at the
  sequential level too.
- Do NOT apply temporal regularization to a cold-start calibration.
- Do NOT return slices with NaN params.

## Commit Instructions
```bash
git add essvi/sequential.py tests/test_sequential.py
git commit -m "essvi/sequential: forward DTE-ascending coordinator with degeneracy fallback + cold start (plan §4, §14; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A7_sequential.md`.
