# Agent A9 — Post-Calibration Audit & Kill-Switch Verifier

## Persona
You are an audit watchdog who trusts nothing. Every calibrated surface must
be verified on a fine audit grid spanning the full log-moneyness range. You
check butterfly, calendar, vertical-spread, asymptotic wing, and total
variance monotonicity. You report every violation with its exact location,
magnitude, and type so the runtime can decide whether to use or reject the
surface.

## Core Objective
Implement `essvi/audit.py` — the comprehensive post-calibration audit
that flags every arbitrage violation on a dense (k, T) grid and produces
a structured audit report.

## Required Reading
1. `eSSVI_surface_plan (1).md` §12 — Clamping, audit, kill-switch
   behavior.
2. `essvi/config.py` — `AUDIT_GRID_POINTS`, `K_AUDIT`, all `KILL_TOL_*`.
3. Already-written `essvi/constraints.py` — all 4 check functions.
4. Already-written `essvi/surface.py` — `w_surface()` and
   `sigma_surface()`.
5. Corbetta et al. (2019) §5 — audit on a fine k-grid.
6. Pasquazzi (2023) Proposition 13 — calendar condition.
7. Martini & Mingone (2022) Proposition 6.3 — butterfly exact.

## Audit Grid

```
k_audit = linspace(−K_AUDIT, +K_AUDIT, AUDIT_GRID_POINTS)
  where K_AUDIT = 3.0, AUDIT_GRID_POINTS = 400

For each calibrated expiry T_i and for each (k, T_i):
  Evaluate w(k, T_i), w'(k, T_i), w''(k, T_i) — all closed-form.
  Run the four checks.
```

## Four Audit Checks (per plan §7)

### 1. Butterfly Arbitrage Scan
```
For each slice (calibrated T_i):
  Evaluate Durrleman g(k) ≥ 0 on the fine k-grid:
    g(k) = (1 − k·w'(k)/(2·w(k)))² − w'(k)²·(1/w(k) + 1/4)/4 + w''(k)/2

  IF g(k) < −KILL_TOL_BUTTERFLY for any k:
    → flag it with (T_i, k, g(k))
```

Note: the Durrleman g-function is the definitive check. The closed-form
MM conditions are corridor constraints; g(k)≥0 is the audit reality.

### 2. Calendar-Spread Audit
```
For each pair of adjacent expiries (T_i, T_{i+1}):
  For each k in audit grid:
    w(k, T_i) ≤ w(k, T_{i+1})  ← must hold everywhere
  IF w(k, T_i) > w(k, T_{i+1}) + KILL_TOL_CALENDAR:
    → flag it
```

### 3. Vertical-Spread / Slope Audit (Roper)
```
For each slice (T_i):
  For each k in audit grid:
    |w'(k, T_i)| ≤ 2/(T_i)  ← equivalently |w'(k)|·T ≤ 2
  IF |w'(k)| > 2/T_i + KILL_TOL_ROPER:
    → flag it
```

### 4. Asymptotic Wing Audit (Lee)
```
For each slice (T_i):
  k_min = min(k_audit), k_max = max(k_audit)
  slope = max(w(k_min, T_i)/|k_min|, w(k_max, T_i)/|k_max|)
  IF slope > TAIL_SLOPE_CAP + KILL_TOL_LEE:
    → flag it
```

### 5. Total Variance Monotonicity
```
For each pair of adjacent calibrated expiries:
  θ_{i+1} ≥ θ_i   ← total ATM variance must increase
```

## Kill Switch Decision

```python
def audit_result_to_kill_switch(report: dict) -> dict:
    """
    Convert audit report to runtime decision.

    Returns dict:
    - surface_usable: bool (False if any P0 violation)
    - butterfly_violations: [(T, k, severity), ...]
    - calendar_violations: [(T_i, T_{i+1}, k, severity), ...]
    - slope_violations: [(T, k, severity), ...]
    - lee_violations: [(T, slope, cap), ...]
    - monotonicity_violations: [(T_i, T_{i+1}, (θ_i, θ_{i+1})), ...]
    - total_violations: int
    - worst_severity: float  (max violation magnitude)
    - kill_triggered: bool (True if any violation exceeds kill tolerance)
    """
```

## Functions to Implement

```python
def build_audit_grid(
    n_points: int = AUDIT_GRID_POINTS,
    k_max: float = K_AUDIT,
) -> np.ndarray:
    """linspace(-k_max, +k_max, n_points)."""

def compute_durrleman_g(k, w, wp, wpp) -> np.ndarray:
    """
    Full Durrleman g-function:
    g = (1 − k*w'/(2w))² − (w')²·(w⁻¹ + 1/4)/4 + w''/2
    """

def audit_butterfly(slice_params_list, k_grid) -> list[dict]:
    """For each slice, evaluate g(k) on grid, flag negatives."""

def audit_calendar(slice_params_list, k_grid) -> list[dict]:
    """For each adjacent pair, check w₁(k) ≤ w₂(k)."""

def audit_vertical_spread(slice_params_list, k_grid) -> list[dict]:
    """Check |w'(k)|·T ≤ 2 for every (k, T)."""

def audit_lee_bound(slice_params_list) -> list[dict]:
    """Check asymptotic slope at k=±K_AUDIT."""

def audit_monotonicity(slice_params_list) -> list[dict]:
    """Check θ_{i+1} ≥ θ_i."""

def run_full_audit(minute_result: dict) -> dict:
    """
    MAIN FUNCTION. Runs all 5 audits and produces a structured report.

    Input minute_result from sequential.calibrate_one_minute.
    Returns full audit report dict.
    """

def is_surface_safe(audit_report: dict) -> bool:
    """Convenience: True iff kill_triggered is False."""
```

## Testing (`tests/test_audit.py`)

1. `test_durrleman_g_nonnegative_for_valid_params` — known-good params →
   g(k) ≥ 0 ∀ k
2. `test_durrleman_g_negative_for_arbitrageable_params` — ψ too large →
   g(k) < 0 somewhere
3. `test_butterfly_audit_flags_violation` — arbitrageable surface → flagged
4. `test_butterfly_audit_clean_for_valid` — valid surface → zero butterfly
   violations
5. `test_calendar_audit_flags_violation` — w₁ > w₂ → flagged
6. `test_calendar_audit_clean_for_monotonic` — properly calibrated → clean
7. `test_vertical_spread_audit_flags_violation` — extreme slope → flagged
8. `test_lee_bound_audit_flags_violation` — tail too steep → flagged
9. `test_monotonicity_audit_flags_violation` — θ decreasing → flagged
10. `test_full_audit_on_valid_minute_result` — complete minute_result →
    clean audit
11. `test_full_audit_report_structure` — all required keys present
12. `test_kill_switch_triggered_by_butterfly` — butterfly violation →
    kill_triggered=True
13. `test_kill_switch_not_triggered_within_tolerance` — tiny violation
    below tolerance → not triggered
14. `test_audit_grid_correct_bounds` — [-3, +3] range with 400 points
15. `test_is_surface_safe_convenience` — wrapper returns correct bool

## Things NOT To Do
- Do NOT use finite differences for w' and w'' — `w_surface` provides
  closed-form derivatives.
- Do NOT skip the audit grid in favor of checking only at slice strike
  points — the grid must be denser and wider.
- Do NOT hardcode kill tolerances — read from config.
- Do NOT return a binary pass/fail — report every violation with location
  and magnitude.
- Do NOT audit the surface BEFORE interpolation/calibration is complete.

## Commit Instructions
```bash
git add essvi/audit.py tests/test_audit.py
git commit -m "essvi/audit: full 5-check post-calibration audit on dense k-grid with kill-switch logic (plan §12; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A9_audit.md`.
