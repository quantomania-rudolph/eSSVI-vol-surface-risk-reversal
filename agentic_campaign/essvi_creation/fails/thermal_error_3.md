# Thermo-Nuclear Review — Error Report #3

**Campaign:** `agentic_campaign/essvi_creation/`
**Scope:** Full eSSVI engine (`essvi/`) vs. Blueprint (`eSSVI_surface_plan (1).md`) vs. Data Contract (`dataingestion.md`)
**Date:** 2026-07-09
**Status:** Critical findings requiring immediate fix before production

---

## Executive Summary

The eSSVI engine has **5 P0 (blocking)** and **5 P1 (correctness)** issues that invalidate calibration accuracy. Two are **mathematical inversions** (anchor, objective weights) that flip the optimization entirely. The loader cannot read the actual database schema. The calendar arbitrage logic misses the Pasquazzi 2023 Case A correction entirely. Rho grid is asymmetric.

**All 158 tests pass** — but they test the *wrong math* because test fixtures were written against the buggy implementation.

---

## P0 — Blocking Issues (Must Fix Before Any Production Use)

### P0-1: Anchor Inversion — `essvi/anchor.py:184` (and `essvi/constraints.py:24`)

**Blueprint §5 (A3) — Exact Closed-Form Solution:**
> Given anchor `(k*_t, θ*_t)` where `θ*_t = σ*²·T` from market, and candidate `(ρ_t, ψ_t)`:
> ```
> θ_t = θ*_t − ρ_t ψ_t k*_t + ψ_t² k*_t² (1 − ρ_t²) / (4 θ*_t)
> ```

**Current Code (WRONG — computes θ* from θ):**
```python
# anchor.py:176-181 — compute_theta_star
def compute_theta_star(w_star, k_star, phi, rho) -> float:
    u = phi * k_star + rho
    d = u * u + (1.0 - rho * rho)
    denom = 1.0 + rho * phi * k_star + np.sqrt(d)
    return float(2.0 * w_star / denom)  # Returns θ from θ* — BACKWARDS
```

```python
# constraints.py:24-35 — theta_from_psi
def theta_from_psi(psi, rho, k_star, theta_star) -> float:
    return (
        theta_star
        - rho * psi * k_star
        + psi * psi * k_star * k_star * (1.0 - rho * rho) / (4.0 * theta_star)
    )
```

**The Bug:** `compute_theta_star` takes `(w*, k*, φ, ρ)` and returns `θ`. But the calibration loop **has** `(ρ, ψ)` candidates and **needs** `θ(ψ)` from the anchor. The correct function `theta_from_psi` exists in `constraints.py` but is **never used** by the solver — `solver.py:111` calls `extract_anchor_params(df_slice, phi, rho)` which calls `compute_theta_star` (the inverse).

**Impact:** The solver anchors to a **different θ for every (ρ, ψ) candidate** instead of pinning the slice to the market ATM variance. The surface does not pass through the liquid belly point.

**Fix:**
1. Delete `compute_theta_star` from `anchor.py` (or rename to `theta_from_anchor_closed_form` with correct signature).
2. In `extract_anchor_params` (anchor.py:199), store only `k_star`, `w_star` (market `θ*`), `quality`, `n_belly`.
3. In `solver.py:_evaluate_at_phi` (line 111), call `constraints.theta_from_psi(psi, rho, k_star, theta_star)` — **exact closed form, no iteration**.
4. Remove `phi` parameter from `extract_anchor_params` — anchor is **independent** of φ, ρ.

---

### P0-2: Objective Weight Inversion — `essvi/objective.py:68`

**Blueprint §10 — Variance-Space Vega² Weighting (Recommended):**
> ```
> ν_var,j = ν_vol,j / (2 · σ_mkt,j · √T) = ν_vol,j / (2 · √(w_mkt,j · T))
> W_j = (ν_var,j)²
> Error(ψ_t) = Σ_j W_j (w_mkt,j − w_mod,j)²
> ```

**Current Code (WRONG — inverts weights):**
```python
# objective.py:67-68
if weight_mode == "var_vega2":
    weights = 1.0 / vega_arr**2   # INVERSE — downweights high-vega (ATM) strikes!
```

**Impact:** The fit **chases noisy OTM wings** and **ignores the liquid belly**. ATM strikes (high vega) get near-zero weight; deep OTM (low vega) get huge weight. Total variance error is minimized where it matters least.

**Fix:** 
```python
if weight_mode == "var_vega2":
    # variance-space vega: ν_var = ν_vol / (2 * σ * sqrt(T))
    sigma_mkt = np.sqrt(w_arr / T)  # w = σ²T → σ = sqrt(w/T)
    nu_var = vega_arr / (2.0 * sigma_mkt * np.sqrt(T))
    weights = nu_var ** 2
```

Also add `T` parameter to `objective_slice` (currently missing — `T` needed for variance-space conversion).

---

### P0-3: Loader Contract Mismatch — `essvi/loader.py:21-48`

**Database Schema (`dataingestion.md:296-311`):**
```sql
CREATE TABLE amd_surface_min (
  ts timestamptz NOT NULL,
  underlying text NOT NULL,
  expiration date NOT NULL,
  strike numeric(12,4) NOT NULL,
  option_type char(1) NOT NULL,
  spot_price double precision,
  forward_price double precision,
  implied_vol double precision,
  option_mid double precision,
  spread double precision,
  vega double precision,
  bid double precision, ask double precision, delta double precision,
  r double precision, q double precision,
  business_t double precision, dte_calendar int, log_moneyness double precision,
  open_interest int, quality_flags int, ingest_run_id bigint,
  underlying_timestamp timestamptz,
  UNIQUE (underlying, expiration, strike, option_type, ts)
);
```

**Loader Required Columns (28 cols, line 21-48):**
```python
_REQUIRED_COLUMNS = (
    "timestamp", "root", "expiration", "strike", "right", "bid", "ask",
    "mid_price", "rel_spread", "oi", "spot_price", "forward_price",
    "r", "q", "business_t", "log_moneyness", "vega",
    "delta_black76", "session_phase", "parity_skew",
    "anchor_k_star", "anchor_theta_star", "anchor_quality",
    "slice_strike_count", "OTM", "belly_flag",
)
```

**Mismatch:**
| DB Column | Loader Expects | Status |
|-----------|----------------|--------|
| `underlying` | `root` | rename OK |
| `option_type` | `right` | rename OK |
| `open_interest` | `oi` | rename OK |
| `delta` | `delta_black76` | rename OK |
| `dte_calendar` | `dte` | rename OK |
| `option_mid` | `mid_price` | **COMPUTED** — not in DB |
| `spread` | `rel_spread` | **COMPUTED** — not in DB |
| `log_moneyness` | `log_moneyness` | **NOT IN DB** — computed in math.py |
| `session_phase` | `session_phase` | **NOT IN DB** |
| `parity_skew` | `parity_skew` | **NOT IN DB** |
| `anchor_*` | `anchor_*` | **NOT IN DB** — computed by anchors.py |
| `slice_strike_count` | `slice_strike_count` | **NOT IN DB** |
| `OTM`, `belly_flag` | `OTM`, `belly_flag` | **NOT IN DB** |

**Impact:** `loader.py:80-83` raises `MissingColumnError` on every call — **engine cannot load data**.

**Fix:** `_REQUIRED_COLUMNS` should only contain **actual DB columns** (19 cols). All computed columns (`mid_price`, `rel_spread`, `log_moneyness`, `belly_flag`, `OTM`, `anchor_*`, `session_phase`, `parity_skew`, `slice_strike_count`) must be **computed in loader** after fetch, or loaded from a **materialized view** that includes them.

---

### P0-4: Pasquazzi 2023 Case A Missing — `essvi/constraints.py:196`

**Blueprint §7.2 / §8.2 — Pasquazzi Proposition 13:**
> When `Θ = θ₂/θ₁ ≈ 1` (within tolerance), the **only** no-arbitrage configurations are:
> - (i) `ρ₁ = ρ₂ = 0` and `Φ ≥ 1`
> - (ii) `ρ₁ = ρ₂` and `Φ = 1` (identical slices)
> 
> **If `ρ₁ ≠ ρ₂` and not both zero → INFEASIBLE (calendar arbitrage).**

**Current Code (Hendriks-Martini only — WRONG for Θ≈1):**
```python
# constraints.py:196-239 — check_calendar_pasquazzi
def check_calendar_pasquazzi(params1, params2) -> tuple[bool, str]:
    theta_ratio = theta2 / theta1
    # ... only implements HM stripe conditions (C1, C2, C3)
    # NO check for Θ≈1 with ρ₁≠ρ₂ infeasibility
```

**Corridor Lower Bound (§8.2) also missing Case A logic.**

**Impact:** At session open (overnight gap), `θ*_t ≈ θ_{t-1}` is common. Current code allows `ρ_t ≠ ρ_{t-1}` → **calendar arbitrage slips through**. Kill switch may catch it post-hoc, but corridor should be **empty** for those ρ candidates.

**Fix:** Add `PASQUAZZI_THETA_TOL` config (e.g., `1e-4`), implement Case A/B/C logic in:
- `check_calendar_pasquazzi` (audit)
- `_compute_L_psi` (corridor lower bound)
- `U_psi_of_psi` (corridor upper bound via `Φ ≤ 1` constraint)

---

### P0-5: Asymmetric Rho Grid — `essvi/config.py:79-80`

**Blueprint §9:** `ρ ∈ [−0.99, 0.99]` (symmetric — equity skew can be positive during takeovers/meme events).

**Current Code:**
```python
RHO_GRID_LO = -0.99
RHO_GRID_HI = 0.90   # ASYMMETRIC — cuts off positive skew region
```

**Impact:** Positive skew scenarios (rare but real) have **no candidate ρ** → solver fails or picks boundary.

**Fix:** `RHO_GRID_HI = 0.99`

---

## P1 — Correctness Issues (High Priority)

### P1-1: Corridor Multi-Interval Logic Incomplete — `essvi/constraints.py:379`

**Blueprint §8.4:** `U_ψ(ψ)` is **non-monotonic** (convex θ_t(ψ) → U_ψ can have local min). Must find **all** intervals where `U_ψ(ψ) ≥ L_ψ`.

**Current Code:** Single sign-change scan on log-spaced grid, returns first interval only.

**Fix:** Implement full algorithm from blueprint §8.4:
```python
def _find_feasible_psi_intervals(rho, prev_slice, k_star, theta_star, L_psi):
    # 1. Sample U_ψ(ψ) on dense log grid
    # 2. Detect ALL sign changes of f(ψ) = U_ψ(ψ) - L_ψ
    # 3. Refine each interval boundary with Brent
    # 4. Return list of (ψ_lo, ψ_hi) intervals
```

---

### P1-2: Pre-Loop C1 Check Missing — `essvi/sequential.py:504`

**Blueprint §4 Line 142 + §14:**
> Before ρ-loop: check `θ*_t ≥ θ_{t-1} + ε`. If violated → trigger §14 degeneracy handler **immediately** (don't enter ρ loop).

**Current Code:** No check. Enters ρ-loop, corridor empty for all ρ → falls through to `handle_degenerate_slice`.

**Fix:** Add at top of slice loop:
```python
if prev_locked is not None:
    theta_star = float(df_slice["anchor_theta_star"].iloc[0])
    if theta_star < prev_locked["theta"] - cfg.THETA_MONOTONICITY_EPS:
        # Trigger degeneracy handler BEFORE ρ grid
        sl = handle_theta_projection(...)
        slice_results.append(sl)
        continue
```

---

### P1-3: Tail Extrapolation Missing — `essvi/surface.py`

**Blueprint §15.4:** For `|k| > K_MAX`, use **linear tails with capped slopes**:
```
c_+ = min((ψ/2)(1+ρ), TAIL_SLOPE_CAP)
c_- = min((ψ/2)(1−ρ), TAIL_SLOPE_CAP)
w(k) = w(K_MAX) + c_+·(k−K_MAX)  for k > K_MAX
```

**Current Code:** `w_surface` calls `w_slice` directly — **no tail cap**. Lee bound (ψ(1+|ρ|)≤4) guarantees `c_± ≤ 2` asymptotically, but **numerical extrapolation beyond audit grid can exceed**.

**Fix:** Implement `_apply_tail_cap` in `w_surface` per §15.4.

---

### P1-4: Long Extrapolation θ Wrong — `essvi/surface.py:111`

**Blueprint §15.3:** For `T > T_N`:
```
θ(T) = θ_N + (θ_N − θ_{N-1})/(T_N − T_{N-1}) · (T − T_N)   # linear with LAST slope
ψ(T) = ψ_N  (FLAT — critical for no arb)
ρ(T) = ρ_N  (FLAT)
```

**Current Code:** `extrapolate_long_theta` not used; `get_params_at_T` falls through to linear interpolation for `T > ts[-1]` (interpolates θ between last two — wrong slope).

**Fix:** Implement `extrapolate_long_theta` per formula; use flat ψ/ρ.

---

### P1-5: MM Butterfly Table Not Precomputed — `essvi/constraints.py:101`

**Blueprint §7.1.1:** Precompute `F_MM(θ, |ρ|)` table at startup (200×100 grid), bilinear interpolate at runtime. Current `compute_f_MM` runs 1D Brent **every corridor evaluation** — ~500× slower.

**Impact:** Sequential calibration (190 expiries × 199 ρ × 3 refine × 80 l-grid) = **minutes per minute** vs seconds.

**Fix:** Add module-level `_build_mm_table()` called on import; `compute_f_MM` uses `np.interp` on log(θ), ρ.

---

## P2 — Config / Completeness

| # | Issue | Location | Fix |
|---|-------|----------|-----|
| P2-1 | `MIN_DTE = 1` in shared config | `core_engine/shared/calibration_config.py` | Set to `7` per blueprint §4 |
| P2-2 | `EXTRAPOLATION_THETA_MODE` not implemented | `essvi/config.py:139`, `surface.py` | Add `"linear_last_slope"` mode |
| P2-3 | `SHORT_MATURITY_RHO_FALLBACK` chain incomplete | `sequential.py:328` | Implement all 4 strategies fully |
| P2-4 | `KILL_TOL_*` usage inconsistent | `constraints.py` vs `solver.py:272` | Unify: solver uses per-type tolerances |
| P2-5 | `VEGA_WEIGHT_MODE` default mismatch | `dataingestion/config.py:237` = `vol_vega1`, `essvi/config.py:97` = `var_vega2` | Align to `var_vega2` (blueprint §10) |

---

## Test Suite Status

**All 158 tests pass** — but they were written against the buggy implementation:
- `test_anchor.py` tests `compute_theta_star` (inverse function)
- `test_objective.py` tests `var_vega2` with inverted weights
- `test_constraints.py` tests HM calendar, not Pasquazzi Case A
- `test_loader.py` mocks DB with computed columns pre-populated

**Required:** After P0 fixes, **rewrite affected tests** to validate correct math.

---

## Recommended Fix Order

1. **P0-3 (Loader)** — unblocks data flow
2. **P0-1 (Anchor)** — fixes core calibration math
3. **P0-2 (Objective)** — fixes fit priority
4. **P0-4 (Pasquazzi Case A)** — fixes calendar arb at session open
5. **P0-5 (Rho Grid)** — trivial, enables positive skew
6. **P1-1, P1-2, P1-5** — corridor correctness + speed
7. **P1-3, P1-4** — surface extrapolation
8. **P2** — config cleanup

---

## Files to Modify

| File | P0 Issues | P1 Issues |
|------|-----------|-----------|
| `essvi/loader.py` | P0-3 | — |
| `essvi/anchor.py` | P0-1 | — |
| `essvi/constraints.py` | P0-1, P0-4 | P1-1, P1-5 |
| `essvi/objective.py` | P0-2 | — |
| `essvi/solver.py` | P0-1 (call site) | — |
| `essvi/sequential.py` | — | P1-2 |
| `essvi/surface.py` | — | P1-3, P1-4 |
| `essvi/config.py` | P0-5, P2-1 | P2-2 |
| `core_engine/shared/calibration_config.py` | P2-1 | — |

---

**End of Thermal Error 3 Report**