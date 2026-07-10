# Thermo-Nuclear Fix Campaign — thermo_3

**Campaign:** `agentic_campaign/essvi_creation/fails/thermo_3/`  
**Source:** `thermal_error_3.md` findings (27 issues → 10 P0/P1 fixes)  
**Goal:** Fix all P0 blocking + P1 correctness issues in the eSSVI engine  
**Run Order:** Sequential by dependency; parallel where independent  
**Completion Criteria:** All P0/P1 fixes committed with passing tests; `pytest essvi/ -v` green

---

## Research Knowledge Base (Required Reading for All Agents)

### Primary Mathematical References

| # | Paper | Role in Fixes |
|---|-------|---------------|
| 1 | **Pasquazzi (2023)** — *Correction to HM Proposition 3.1*, Prop 13 | **P0-4**: Calendar Case A (Θ≈1, ρ₁≠ρ₂ → infeasible) |
| 2 | **Martini & Mingone (2022)** — *No Arbitrage SVI*, Prop 6.3 | **P0-1, P1-5**: Exact butterfly boundary ℱ_MM(θ,|ρ|); precompute table |
| 3 | **Corbetta et al. (2019)** — *Robust calibration...*, arXiv 1804.04924 | **P0-1, P1-2**: Anchor reparam θ_t = θ*_t − ρψk* + ψ²k*²(1−ρ²)/(4θ*_t); sequential algorithm |
| 4 | **Gatheral & Jacquier (2014)** — *Arbitrage-free SVI*, arXiv 1204.0646 | **P1-3, P1-4**: SSVI form, Lee bound w(k)/|k| ≤ 2, tail slope cap |
| 5 | **Lee (2004)** — *Moment Formula*, Roper (2010) | **P0-4, P1-3**: Asymptotic wing bounds, vertical-spread |

### Key Mathematical Formulas (Locked Convention)

```python
# eSSVI slice (THE convention - locked)
w(k, T_t) = θ_t/2 * (1 + ρ_t φ_t k + sqrt((φ_t k + ρ_t)^2 + (1 - ρ_t^2)))
ψ_t = θ_t * φ_t   # NOT ψ = φ*sqrt(θ)

# Anchor closed-form (Blueprint §5, Corbetta §3.2)
θ_t = θ*_t - ρ_t ψ_t k*_t + ψ_t^2 k*_t^2 (1 - ρ_t^2) / (4 θ*_t)

# Variance-space vega² weights (Blueprint §10)
ν_var = ν_vol / (2 * σ_mkt * sqrt(T)) = ν_vol / (2 * sqrt(w_mkt * T))
W = ν_var^2

# Pasquazzi Calendar (Blueprint §7.2, §8.2)
Θ = θ₂/θ₁
Case A (Θ ≈ 1): feasible iff ρ₁=ρ₂=0 (any Φ≥1) OR ρ₁=ρ₂ (Φ=1) — ELSE infeasible
Case B (Θ > 1): Hendriks-Martini stripe (ρ ≤ ρ_HM) + Φ ≥ 1
Case C (Θ < 1): symmetric to B

# Corridor (Blueprint §8)
L_ψ = max(0, (θ* - θ_prev)/k*_adj)  # simplified; full formula in §8.2
U_ψ(ψ) from Φ ≤ 1 + calendar feasibility

# Tail extrapolation (Blueprint §15.4)
c_+ = min((ψ/2)(1+ρ), TAIL_SLOPE_CAP)   # TAIL_SLOPE_CAP = 2 (Lee)
c_- = min((ψ/2)(1-ρ), TAIL_SLOPE_CAP)
w(k) = w(K_MAX) + c_± * (k - K_MAX) for |k| > K_MAX

# Long extrapolation (Blueprint §15.3)
θ(T) = θ_N + (θ_N - θ_{N-1})/(T_N - T_{N-1}) * (T - T_N)  for T > T_N
ψ(T) = ψ_N, ρ(T) = ρ_N  (FLAT)
```

### Data Contract (from `dataingestion.md` & `dataingestion/joins.py`)

**DB Table `amd_surface_min` (actual columns):**
```sql
ts, underlying, expiration, strike, option_type, spot_price, forward_price,
implied_vol, option_mid, spread, vega, bid, ask, delta,
r, q, business_t, dte_calendar, log_moneyness,
open_interest, quality_flags, ingest_run_id, underlying_timestamp
```

**Loader must compute (NOT expect from DB):**
- `mid_price` = (bid + ask) / 2
- `rel_spread` = (ask - bid) / mid_price
- `log_moneyness` = log(strike / forward_price)  [computed in math.py]
- `session_phase`, `parity_skew`, `anchor_k_star`, `anchor_theta_star`, `anchor_quality`
- `slice_strike_count`, `OTM`, `belly_flag`

---

## Campaign Structure

```
thermo_3/
├── campaign.md          # This file
├── agents/
│   ├── T3_A1_loader.md      # Fix loader DB contract mismatch (P0-3)
│   ├── T3_A2_anchor.md      # Fix anchor inversion + extract_anchor_params (P0-1)
│   ├── T3_A3_objective.md   # Fix var_vega2 weight inversion (P0-2)
│   ├── T3_A4_constraints.md # Fix Pasquazzi Case A + corridor multi-interval + MM table (P0-4, P1-1, P1-5)
│   ├── T3_A5_solver.md      # Wire anchor fix + fix rho grid asymmetry (P0-1 call site, P0-5)
│   ├── T3_A6_sequential.md  # Add pre-loop C1 check (P1-2)
│   ├── T3_A7_surface.md     # Add tail caps + long extrapolation fix (P1-3, P1-4)
│   ├── T3_A8_config.md      # Fix config values (P0-5, P2-1, P2-2, P2-4, P2-5)
│   └── T3_A9_tests.md       # Rewrite affected tests to validate CORRECT math
└── fails/                 # Failure logs if any agent stalls
```

---

## Agent Run Order & Parallelization

### Phase 1: Foundation (Sequential — Must Complete First)
| Agent | Depends On | Duration Est. | Parallel? |
|-------|------------|---------------|-----------|
| **T3_A8_config** | — | 15 min | No — fixes config values used by all |
| **T3_A1_loader** | T3_A8_config | 30 min | No — unblocks data flow |
| **T3_A2_anchor** | T3_A8_config | 30 min | No — core math fix |

### Phase 2: Core Engine (Parallelizable After Phase 1)
| Agent | Depends On | Duration Est. | Parallel Group |
|-------|------------|---------------|----------------|
| **T3_A3_objective** | T3_A2_anchor | 20 min | **Group A** |
| **T3_A4_constraints** | T3_A2_anchor, T3_A8_config | 45 min | **Group A** |
| **T3_A5_solver** | T3_A2_anchor, T3_A3_objective, T3_A4_constraints | 20 min | **Group B** (after Group A) |
| **T3_A6_sequential** | T3_A5_solver, T3_A4_constraints | 20 min | **Group B** |

### Phase 3: Surface & Integration (Parallelizable After Phase 2)
| Agent | Depends On | Duration Est. | Parallel Group |
|-------|------------|---------------|----------------|
| **T3_A7_surface** | T3_A5_solver, T3_A4_constraints | 30 min | **Group C** |
| **T3_A9_tests** | All above | 45 min | **Group C** (can start after Phase 1 done) |

### Execution Commands

```bash
# Phase 1 - Sequential
cd agentic_campaign/essvi_creation/fails/thermo_3
python -m agent T3_A8_config
python -m agent T3_A1_loader
python -m agent T3_A2_anchor

# Phase 2 - Parallel Group A (can run simultaneously after Phase 1)
# Terminal 1:
python -m agent T3_A3_objective
# Terminal 2:
python -m agent T3_A4_constraints

# Phase 2 - Group B (after Group A done)
# Terminal 1:
python -m agent T3_A5_solver
# Terminal 2:
python -m agent T3_A6_sequential

# Phase 3 - Parallel Group C (after Phase 2 done)
# Terminal 1:
python -m agent T3_A7_surface
# Terminal 2:
python -m agent T3_A9_tests

# Final validation
pytest essvi/ -v --tb=short -q
python -c "from essvi.config import validate; validate()"
python -c "from essvi.runtime import calibrate_minute; print('OK')"
```

---

## Agent Template (Each Agent Receives This Context)

### Common Instructions for All Agents

**Persona:** You are a quantitative finance engineer specializing in no-arbitrage volatility surface calibration. You write production-grade Python with Numba JIT, exhaustive tests, and mathematical rigor.

**Core Objective:** Fix the specific P0/P1 issue(s) assigned. Do NOT refactor unrelated code. Follow the exact formulas from the Research Knowledge Base above.

**Prohibitions:**
- Do NOT change `essvi/config.py` unless you ARE T3_A8_config
- Do NOT modify other agents' files
- Do NOT weaken test assertions
- Do NOT use finite differences where closed forms exist

**Testing Requirements:**
1. Write/fix tests in `tests/test_<module>.py`
2. Run `pytest tests/test_<module>.py -v -x` until green
3. If a test fails after 3 fixes → write `fails/T3_A{N}_<test>.md` and stop

**Commit Message Format:**
```
git add essvi/<module>.py tests/test_<module>.py
git commit -m "<module>: fix <P0-N/P1-N> <brief> (thermo_3 T3_A{N}; tests pass)"
```

---

## Agent Prompts

### T3_A8_config — Configuration Fixes (P0-5, P2-1, P2-2, P2-4, P2-5)

**File:** `essvi/config.py`  
**Depends on:** None (run first)  
**Issues to Fix:**
1. **P0-5**: `RHO_GRID_HI = 0.99` (was 0.90 — asymmetric)
2. **P2-1**: `MIN_DTE = 7` (was 1 — per Blueprint §4)
3. **P2-2**: Add `EXTRAPOLATION_THETA_MODE = "linear_last_slope"` + implement in `surface.py` (separate agent)
4. **P2-4**: Unify `KILL_TOL_BUTTERFLY`, `KILL_TOL_CALENDAR`, `KILL_TOL_VERTICAL` usage
5. **P2-5**: `VEGA_WEIGHT_MODE = "var_vega2"` (align with dataingestion/config.py)

**Validation:**
```python
from essvi.config import validate
validate()  # Must not raise
assert cfg.RHO_GRID_HI == 0.99
assert cfg.MIN_DTE == 7
assert cfg.VEGA_WEIGHT_MODE == "var_vega2"
```

---

### T3_A1_loader — Loader Contract Fix (P0-3)

**File:** `essvi/loader.py`  
**Depends on:** T3_A8_config  
**Issues to Fix:**
1. **P0-3**: `_REQUIRED_COLUMNS` must match **actual DB columns** (19 cols), not computed ones
2. Compute `mid_price`, `rel_spread`, `log_moneyness` in loader after fetch
3. Remove expectation of `session_phase`, `parity_skew`, `anchor_*`, `slice_strike_count`, `OTM`, `belly_flag` from DB
4. Add `compute_anchor_params()` call after loading slice data (imports from `anchor.py`)

**DB Columns (EXACT from `amd_surface_min`):**
```python
DB_COLUMNS = [
    "ts", "underlying", "expiration", "strike", "option_type",
    "spot_price", "forward_price", "implied_vol", "option_mid", "spread",
    "vega", "bid", "ask", "delta", "r", "q",
    "business_t", "dte_calendar", "log_moneyness",
    "open_interest", "quality_flags", "ingest_run_id", "underlying_timestamp"
]
```

**Loader Output DataFrame must have (computed):**
```python
# After fetch + compute:
"mid_price", "rel_spread", "log_moneyness",
"session_phase", "parity_skew", "belly_flag", "OTM",
"anchor_k_star", "anchor_theta_star", "anchor_quality",
"slice_strike_count"
```

**Tests:** `tests/test_loader.py` — mock DB with only DB_COLUMNS; verify computed columns exist and anchor params are correct

---

### T3_A2_anchor — Anchor Inversion Fix (P0-1)

**Files:** `essvi/anchor.py`, `essvi/constraints.py` (use `theta_from_psi`)  
**Depends on:** T3_A8_config  
**Issues to Fix:**
1. **P0-1**: `compute_theta_star` computes θ from θ* — **INVERTED**. Delete or rename.
2. **P0-1**: `extract_anchor_params(df, phi, rho)` should **NOT take phi, rho**. It extracts market anchor `(k*, θ*, quality)` only.
3. **P0-1**: Solver must call `constraints.theta_from_psi(psi, rho, k_star, theta_star)` for each (ρ, ψ) candidate — exact closed form.

**New `anchor.py` Interface:**
```python
def extract_anchor_params(df_slice: pd.DataFrame) -> AnchorParams:
    """
    Returns: k_star (float), theta_star (float), quality (float), n_belly (int)
    NO phi, NO rho — anchor is market-observed, independent of candidate params.
    """
    # 1. Find belly strikes (|k| minimal, prefer call+put pair)
    # 2. theta_star = sigma_mid^2 * T  (market ATM variance)
    # 3. quality = f(OI, spread, parity)
    # Return AnchorParams(k_star, theta_star, quality, n_belly)
```

**`constraints.theta_from_psi` (already correct at line 24-35) becomes THE function:**
```python
def theta_from_psi(psi: float, rho: float, k_star: float, theta_star: float) -> float:
    """Exact closed-form: θ = θ* - ρψk* + ψ²k*²(1-ρ²)/(4θ*)"""
    return theta_star - rho * psi * k_star + psi*psi * k_star*k_star * (1 - rho*rho) / (4 * theta_star)
```

**Tests:** `tests/test_anchor.py` — verify:
- `extract_anchor_params` returns same (k*, θ*) regardless of φ, ρ
- `theta_from_psi(psi, rho, k*, θ*)` matches Corbetta formula exactly
- Round-trip: given market (k*, θ*), compute θ(ψ,ρ), plug into w_slice → w(k*) == θ*

---

### T3_A3_objective — Objective Weight Fix (P0-2)

**File:** `essvi/objective.py`  
**Depends on:** T3_A2_anchor  
**Issues to Fix:**
1. **P0-2**: `weight_mode == "var_vega2"` uses `1/vega^2` — **INVERTED**
2. Add `T` parameter to `objective_slice` (needed for σ = sqrt(w/T))
3. Implement variance-space vega correctly

**Correct Implementation:**
```python
def objective_slice(psi, rho, k_arr, w_arr, vega_arr, T, theta_star, k_star, 
                    weight_mode="var_vega2", lambda_spatial=0, lambda_temporal=0,
                    prev_psi=None, prev_theta=None) -> float:
    # 1. Compute theta from psi, rho, anchor (exact closed form)
    theta = constraints.theta_from_psi(psi, rho, k_star, theta_star)
    
    # 2. Model total variance
    w_model = w_slice(k_arr, theta, psi, rho)  # uses psi = theta * phi
    
    # 3. Variance-space vega weights
    if weight_mode == "var_vega2":
        sigma_mkt = np.sqrt(w_arr / T)           # σ = sqrt(w/T)
        nu_var = vega_arr / (2.0 * sigma_mkt * np.sqrt(T))  # ν_var = ν_vol / (2σ√T)
        weights = nu_var ** 2
    elif weight_mode == "vol_vega1":
        weights = np.abs(vega_arr)
    else:  # uniform
        weights = np.ones_like(w_arr)
    
    # 4. Weighted SSE
    residuals = w_arr - w_model
    obj = np.sum(weights * residuals**2)
    
    # 5. Regularization (spatial + temporal)
    if lambda_spatial > 0 and prev_theta is not None:
        obj += lambda_spatial * (np.log(theta) - np.log(prev_theta))**2
    if lambda_temporal > 0 and prev_psi is not None:
        obj += lambda_temporal * (psi - prev_psi)**2
    
    return obj
```

**Tests:** `tests/test_objective.py` — verify:
- `var_vega2` weights ATM strikes HIGHER (not lower) than wings
- Gradient matches finite-difference check
- Objective is convex in ψ for fixed ρ

---

### T3_A4_constraints — Pasquazzi + Corridor + MM Table (P0-4, P1-1, P1-5)

**File:** `essvi/constraints.py`  
**Depends on:** T3_A2_anchor, T3_A8_config  
**Issues to Fix:**
1. **P0-4**: Implement **Pasquazzi 2023 Case A** in `check_calendar_pasquazzi` AND `_compute_L_psi` AND `U_psi_of_psi`
2. **P1-1**: Corridor search returns **all feasible intervals** (not just first)
3. **P1-5**: Precompute `F_MM(θ, |ρ|)` table at module load (200×100 grid)

**Pasquazzi Case A Logic (Blueprint §7.2, §8.2):**
```python
PASQUAZZI_THETA_TOL = 1e-4  # config

def _case_A_feasible(theta1, theta2, rho1, rho2) -> bool:
    theta_ratio = theta2 / theta1
    if abs(theta_ratio - 1.0) > PASQUAZZI_THETA_TOL:
        return True  # Not Case A
    
    # Case A: Θ ≈ 1
    # Feasible ONLY if: (ρ1 == 0 and ρ2 == 0) OR (ρ1 == rho2)
    if abs(rho1) < 1e-10 and abs(rho2) < 1e-10:
        return True  # Both zero, any Φ ≥ 1
    if abs(rho1 - rho2) < 1e-10:
        return True  # Equal rho, Φ = 1
    return False  # ρ1 ≠ ρ2 and not both zero → INFEASIBLE
```

**Corridor Multi-Interval (Blueprint §8.4):**
```python
def find_feasible_psi_intervals(rho, prev_slice, k_star, theta_star, L_psi):
    # 1. Sample U_psi(psi) on dense log grid [psi_min, psi_max]
    # 2. f(psi) = U_psi(psi) - L_psi
    # 3. Find ALL sign changes of f
    # 4. For each interval, refine boundaries with Brent
    # 5. Return list of (psi_lo, psi_hi) where U_psi >= L_psi
```

**MM Table Precomputation (Blueprint §7.1.1):**
```python
# Module level - runs on import
_MM_THETA_GRID = np.logspace(np.log10(1e-6), np.log10(2.0), 200)
_MM_RHO_GRID = np.linspace(0, 0.999, 100)
_MM_TABLE = np.zeros((200, 100))

def _build_mm_table():
    for i, theta in enumerate(_MM_THETA_GRID):
        for j, rho in enumerate(_MM_RHO_GRID):
            _MM_TABLE[i, j] = _compute_f_MM_brent(theta, rho)  # existing slow version

_build_mm_table()

def compute_f_MM(theta, rho):
    """Bilinear interpolation on log(theta), rho"""
    li = np.searchsorted(_MM_THETA_GRID, theta)
    ri = np.searchsorted(_MM_RHO_GRID, abs(rho))
    # bilinear interp...
    return interp_val
```

**Tests:** `tests/test_constraints.py` — verify:
- Case A: Θ=1.0001, ρ1=0.3, ρ2=-0.2 → INFEASIBLE
- Case A: Θ=1.0001, ρ1=0, ρ2=0 → FEASIBLE
- Corridor returns multiple intervals when U_ψ dips below L_ψ
- `compute_f_MM` matches Brent result within 1e-6, runs 100x faster

---

### T3_A5_solver — Solver Wire-Up (P0-1 call site, P0-5)

**File:** `essvi/solver.py`  
**Depends on:** T3_A2_anchor, T3_A3_objective, T3_A4_constraints  
**Issues to Fix:**
1. **P0-1**: `_evaluate_at_phi` calls `extract_anchor_params(df, phi, rho)` — **REMOVE phi, rho args**; call `extract_anchor_params(df)` once per slice, then `constraints.theta_from_psi(psi, rho, k_star, theta_star)` inside ρ-loop
2. **P0-5**: Use `cfg.RHO_GRID_HI = 0.99` (already fixed by T3_A8_config)
3. Ensure `rho_grid = build_rho_grid(rho_prev, step=cfg.RHO_GRID_STEP)` uses config

**Key Refactor in `_evaluate_at_phi`:**
```python
def _evaluate_at_phi(phi, df_slice, rho_grid, prev_slice, cfg):
    # Extract anchor ONCE per slice (no phi, rho)
    anchor = extract_anchor_params(df_slice)
    k_star, theta_star = anchor.k_star, anchor.theta_star
    
    best_obj = np.inf
    best_result = None
    
    for rho in rho_grid:
        # Corridor for this rho
        L_psi = constraints.compute_L_psi(rho, prev_slice, k_star, theta_star)
        if L_psi is None:
            continue
        intervals = constraints.find_feasible_psi_intervals(rho, prev_slice, k_star, theta_star, L_psi)
        
        for psi_lo, psi_hi in intervals:
            # Brent on psi within [psi_lo, psi_hi]
            result = brent_minimize(
                lambda psi: objective.objective_slice(
                    psi, rho, k_arr, w_arr, vega_arr, T,
                    theta_star, k_star,
                    weight_mode=cfg.VEGA_WEIGHT_MODE,
                    lambda_spatial=cfg.LAMBDA_SPATIAL,
                    lambda_temporal=cfg.LAMBDA_TEMPORAL,
                    prev_psi=prev_psi, prev_theta=prev_theta
                ),
                psi_lo, psi_hi
            )
            if result.fun < best_obj:
                best_obj = result.fun
                best_result = (rho, result.x, theta_from_psi(result.x, rho, k_star, theta_star))
    
    return best_result
```

**Tests:** `tests/test_solver.py` — verify:
- Anchor extracted once per slice (not per ρ)
- Theta computed via `theta_from_psi` matches closed form
- Rho grid spans [-0.99, 0.99]

---

### T3_A6_sequential — Pre-Loop C1 Check (P1-2)

**File:** `essvi/sequential.py`  
**Depends on:** T3_A5_solver, T3_A4_constraints  
**Issues to Fix:**
1. **P1-2**: Add pre-loop check `θ*_t ≥ θ_{t-1} - ε` before entering ρ-grid

**Implementation:**
```python
def calibrate_surface(slices_data, cfg):
    slice_results = []
    prev_locked = None
    
    for i, df_slice in enumerate(slices_data):
        # --- PRE-LOOP C1 CHECK (Blueprint §4 line 142, §14) ---
        if prev_locked is not None:
            anchor = anchor.extract_anchor_params(df_slice)
            theta_star = anchor.theta_star
            
            if theta_star < prev_locked["theta"] - cfg.THETA_MONOTONICITY_EPS:
                # DEGENERATE: theta dropped → trigger §14 handler immediately
                sl = handle_theta_projection(df_slice, prev_locked, cfg)
                slice_results.append(sl)
                prev_locked = sl.to_dict()
                continue
        
        # Normal ρ-grid search...
        sl = solve_single_slice(df_slice, prev_locked, cfg)
        slice_results.append(sl)
        prev_locked = sl.to_dict()
    
    return slice_results
```

**Tests:** `tests/test_sequential.py` — verify:
- Monotonicity drop triggers degeneracy handler WITHOUT entering ρ-loop
- Normal case still runs full ρ-grid

---

### T3_A7_surface — Tail Caps + Long Extrapolation (P1-3, P1-4)

**File:** `essvi/surface.py`  
**Depends on:** T3_A5_solver, T3_A4_constraints  
**Issues to Fix:**
1. **P1-3**: Add tail slope cap in `w_surface` (Blueprint §15.4)
2. **P1-4**: Implement `extrapolate_long_theta` with last-segment slope (Blueprint §15.3)

**Tail Cap Implementation:**
```python
TAIL_SLOPE_CAP = 2.0  # Lee bound: limsup w(k)/|k| <= 2

def _apply_tail_cap(w_val, k, k_max, psi, rho):
    if k > k_max:
        c_plus = min(0.5 * psi * (1 + rho), TAIL_SLOPE_CAP)
        return w_val + c_plus * (k - k_max)
    elif k < -k_max:
        c_minus = min(0.5 * psi * (1 - rho), TAIL_SLOPE_CAP)
        return w_val + c_minus * (k + k_max)
    return w_val

def w_surface(k, T, params_dict, k_max=None):
    # ... interpolate slice params ...
    w = w_slice(k, theta, psi, rho)
    if k_max is not None:
        w = _apply_tail_cap(w, k, k_max, psi, rho)
    return w
```

**Long Extrapolation Implementation:**
```python
def extrapolate_long_theta(T, ts, thetas):
    """Blueprint §15.3: θ(T) = θ_N + (θ_N - θ_{N-1})/(T_N - T_{N-1}) * (T - T_N)"""
    if T <= ts[-1]:
        return np.interp(T, ts, thetas)
    # T > T_N: linear with LAST segment slope
    slope = (thetas[-1] - thetas[-2]) / (ts[-1] - ts[-2])
    return thetas[-1] + slope * (T - ts[-1])

def get_params_at_T(T, ts, params_list):
    # params_list = [(theta, psi, rho), ...] matching ts
    if T <= ts[0]:
        return params_list[0]
    if T >= ts[-1]:
        theta = extrapolate_long_theta(T, ts, [p[0] for p in params_list])
        psi = params_list[-1][1]   # FLAT
        rho = params_list[-1][2]   # FLAT
        return (theta, psi, rho)
    # Interpolate in between
    theta = np.interp(T, ts, [p[0] for p in params_list])
    psi = np.interp(T, ts, [p[1] for p in params_list])
    rho = np.interp(T, ts, [p[2] for p in params_list])
    return (theta, psi, rho)
```

**Tests:** `tests/test_surface.py` — verify:
- Tail slope never exceeds 2.0
- Long extrapolation uses last segment slope for θ, flat ψ/ρ
- Continuity at boundary K_MAX and T_N

---

### T3_A8_config — (Already defined above as Phase 1)

---

### T3_A9_tests — Test Suite Rewrite (Validates ALL Fixes)

**Files:** `tests/test_*.py` (multiple)  
**Depends on:** All above agents (can start after Phase 1)  
**Objective:** Rewrite tests that validated **buggy behavior** to validate **correct math**

**Tests to Rewrite:**

| Test File | Old (Buggy) Assertion | New (Correct) Assertion |
|-----------|----------------------|------------------------|
| `test_anchor.py` | `compute_theta_star(w*, k*, φ, ρ)` returns θ | `extract_anchor_params` returns (k*, θ*) independent of φ,ρ; `theta_from_psi(ψ,ρ,k*,θ*)` matches Corbetta |
| `test_objective.py` | `var_vega2` weights = `1/vega²` | `var_vega2` weights ATM > wings; `ν_var = ν_vol/(2σ√T)` |
| `test_constraints.py` | Only HM calendar test | Add Pasquazzi Case A: Θ≈1, ρ₁≠ρ₂→infeasible; Θ≈1, ρ₁=ρ₂→feasible |
| `test_solver.py` | Tests use coarse rho grid (fixture) | Tests explicitly use `step=0.01`; anchor called once per slice |
| `test_loader.py` | Mocks DB with computed columns | Mocks DB with ONLY raw columns; verifies computed columns added |

**New Tests to Add:**
- `test_pasquazzi_case_A_feasible()` — ρ1=ρ2=0, Θ≈1 → feasible
- `test_pasquazzi_case_A_infeasible()` — ρ1≠ρ2, Θ≈1 → infeasible
- `test_corridor_multi_interval()` — U_ψ dips below L_ψ then above → two intervals
- `test_mm_table_speed()` — `compute_f_MM` 100x faster than Brent
- `test_tail_slope_cap()` — w'(k) ≤ 2 for |k| > K_MAX
- `test_long_extrapolation_theta()` — θ(T) uses last segment slope

**Validation Commands:**
```bash
pytest tests/test_anchor.py tests/test_objective.py tests/test_constraints.py \
       tests/test_solver.py tests/test_sequential.py tests/test_surface.py \
       tests/test_loader.py -v -x

# Full suite
pytest essvi/ -v --tb=short -q
python -c "from essvi.config import validate; validate()"
python -c "from essvi.runtime import calibrate_minute; print('OK')"
```

---

## Failure Protocol (Per Agent)

If any agent fails after 3 fix attempts:
1. Write `fails/T3_A{N}_<test_name>.md` with:
   - Agent name, test name, full failure output
   - What was tried (code snippets)
   - Why stuck (mathematical ambiguity, missing data, etc.)
2. Stop that agent, continue others if independent
3. Human review required

---

## Success Criteria (Campaign Complete)

- [ ] All P0 issues fixed (5/5)
- [ ] All P1 issues fixed (5/5)
- [ ] All P2 config issues fixed (5/5)
- [ ] `pytest essvi/ -v` → **ALL GREEN** (no xfail, no skip)
- [ ] `python -c "from essvi.config import validate; validate()"` → no error
- [ ] `python -c "from essvi.runtime import calibrate_minute; print('OK')"` → OK
- [ ] No test validates buggy math (all rewritten in T3_A9_tests)

---

**Campaign Owner:** Thermo-Nuclear Review Follow-up  
**Created:** 2026-07-09  
**Status:** Ready for Agent Execution