# Agent T3_A6_sequential — Pre-Loop C1 Check (Degeneracy Handler)

**Campaign:** thermo_3  
**Phase:** 2 (Parallel Group B — After Group A)  
**File:** `essvi/sequential.py`  
**Depends On:** T3_A5_solver, T3_A4_constraints  
**Issues:** P1-2 (Missing Pre-Loop C1 Check)

---

## Context

Blueprint §4 line 142 + §14: Before entering the ρ-grid search for a slice, check if `θ*_t ≥ θ_{t-1} - ε`. If violated (theta dropped), **immediately trigger degeneracy handler** — don't waste time searching ρ grid where corridor is empty.

Current code enters ρ-loop, finds empty corridor for all ρ, then falls through to `handle_degenerate_slice`. This is wasteful and misses the clean §14 logic.

---

## Required Changes to `essvi/sequential.py`

### 1. Add Pre-Loop C1 Check in `calibrate_surface` (Lines ~500-520)

**Current (NO CHECK):**
```python
def calibrate_surface(slices_data: List[pd.DataFrame], cfg) -> List[SliceParams]:
    slice_results = []
    prev_locked = None
    
    for i, df_slice in enumerate(slices_data):
        # Direct to solver — no pre-check
        sl = solve_single_slice(df_slice, prev_locked, cfg)
        slice_results.append(sl)
        prev_locked = sl
        ...
```

**Fixed:**
```python
def calibrate_surface(slices_data: List[pd.DataFrame], cfg) -> List[SliceParams]:
    """
    Sequential calibration with pre-loop C1 monotonicity check.
    Blueprint §4 line 142 + §14 degeneracy handling.
    """
    slice_results = []
    prev_locked = None
    
    for i, df_slice in enumerate(slices_data):
        # --- PRE-LOOP C1 CHECK (Blueprint §4 line 142) ---
        if prev_locked is not None:
            from essvi.anchor import extract_anchor_params
            anchor = extract_anchor_params(df_slice)
            theta_star = anchor.theta_star
            
            if theta_star < prev_locked.theta - cfg.THETA_MONOTONICITY_EPS:
                # DEGENERATE: theta dropped — trigger §14 handler IMMEDIATELY
                logger.warning(
                    f"Slice {i} (T={df_slice['business_t'].iloc[0]:.4f}): "
                    f"θ*={theta_star:.6f} < θ_prev={prev_locked.theta:.6f} - ε. "
                    f"Triggering degeneracy handler."
                )
                sl = handle_theta_projection(df_slice, prev_locked, cfg)
                slice_results.append(sl)
                prev_locked = sl
                continue  # Skip ρ-grid search entirely
        
        # Normal path: full ρ-grid search
        sl = solve_single_slice(df_slice, prev_locked, cfg)
        slice_results.append(sl)
        prev_locked = sl
    
    return slice_results
```

### 2. Ensure `handle_theta_projection` Implements §14 Correctly

**Blueprint §14 Degeneracy Handler:**
> When `θ*_t < θ_{t-1} - ε`:
> 1. Project θ_t = θ_{t-1} + ε (minimal increase)
> 2. Solve for (ψ_t, ρ_t) that satisfies anchor: θ*_t = θ_t - ρ_t ψ_t k*_t + ψ_t² k*_t²(1-ρ_t²)/(4θ*_t)
> 3. With calendar constraint vs prev slice

```python
def handle_theta_projection(df_slice: pd.DataFrame, prev_locked: SliceParams, cfg) -> SliceParams:
    """
    Blueprint §14: Theta projection for degenerate slices.
    """
    from essvi.anchor import extract_anchor_params
    from essvi.constraints import theta_from_psi
    
    anchor = extract_anchor_params(df_slice)
    k_star = anchor.k_star
    theta_star = anchor.theta_star
    
    # 1. Project θ_t = θ_{t-1} + ε
    theta_projected = prev_locked.theta + cfg.THETA_MONOTONICITY_EPS
    
    # 2. Search over ρ for feasible (ψ, ρ) that hits anchor
    best_result = None
    best_obj = np.inf
    
    rho_grid = build_rho_grid(
        rho_prev=prev_locked.rho,
        step=cfg.RHO_GRID_STEP,
        lo=cfg.RHO_GRID_LO,
        hi=cfg.RHO_GRID_HI
    )
    
    for rho in rho_grid:
        # Solve for ψ from anchor equation:
        # θ* = θ - ρ ψ k* + ψ² k*² (1-ρ²) / (4 θ*)
        # This is quadratic in ψ: A ψ² + B ψ + C = 0
        A = k_star * k_star * (1 - rho * rho) / (4 * theta_star)
        B = -rho * k_star
        C = theta_projected - theta_star
        
        disc = B * B - 4 * A * C
        if disc < 0:
            continue
        
        # Two roots — pick the one that makes calendar sense
        psi_1 = (-B + np.sqrt(disc)) / (2 * A)
        psi_2 = (-B - np.sqrt(disc)) / (2 * A)
        
        for psi in [psi_1, psi_2]:
            if psi <= 0:
                continue
            
            # Check calendar feasibility vs prev_locked
            from essvi.constraints import check_calendar_pasquazzi
            feasible, _ = check_calendar_pasquazzi(
                prev_locked.theta, prev_locked.psi, prev_locked.rho,
                theta_projected, psi, rho
            )
            
            if not feasible:
                continue
            
            # Check butterfly (always true for eSSVI but verify)
            from essvi.constraints import compute_f_MM
            f_MM = compute_f_MM(theta_projected, abs(rho))
            # ... existing butterfly check ...
            
            # Evaluate objective
            obj = objective_slice(
                psi, rho, 
                df_slice["log_moneyness"].values,
                df_slice["implied_vol"].values**2 * df_slice["business_t"].values,
                df_slice["vega"].values,
                float(df_slice["business_t"].iloc[0]),
                theta_star, k_star,
                weight_mode=cfg.VEGA_WEIGHT_MODE,
                lambda_spatial=cfg.LAMBDA_SPATIAL,
                lambda_temporal=cfg.LAMBDA_TEMPORAL,
                prev_psi=prev_locked.psi,
                prev_theta=prev_locked.theta
            )
            
            if obj < best_obj:
                best_obj = obj
                best_result = SliceParams(
                    theta=theta_projected,
                    psi=psi,
                    rho=rho,
                    phi=psi / theta_projected,
                    k_star=k_star,
                    theta_star=theta_star,
                    objective=obj,
                    anchor_quality=anchor.quality,
                    n_belly=anchor.n_belly,
                    degenerate=True  # Flag
                )
    
    if best_result is None:
        # Fallback: flat psi, rho = prev
        best_result = SliceParams(
            theta=theta_projected,
            psi=prev_locked.psi,
            rho=prev_locked.rho,
            phi=prev_locked.phi,
            k_star=k_star,
            theta_star=theta_star,
            objective=np.inf,
            anchor_quality=anchor.quality,
            n_belly=anchor.n_belly,
            degenerate=True
        )
    
    return best_result
```

### 3. Add `degenerate` Flag to `SliceParams`

```python
@dataclass
class SliceParams:
    theta: float
    psi: float
    rho: float
    phi: float
    k_star: float
    theta_star: float
    objective: float
    anchor_quality: float
    n_belly: int
    degenerate: bool = False  # NEW
```

---

## Tests Required (`tests/test_sequential.py`)

### Test 1: Pre-Loop Check Triggers Before Rho Grid
```python
def test_pre_loop_c1_check_triggers():
    """Theta drop triggers degeneracy handler WITHOUT entering rho grid."""
    call_count = 0
    
    def counting_solve_single_slice(df, prev, cfg):
        nonlocal call_count
        call_count += 1
        return make_slice_params()
    
    # Create slices where theta drops
    slices = [
        make_slice(theta_star=0.04),   # t=0
        make_slice(theta_star=0.035),  # t=1: drops! (ε=0.001, 0.035 < 0.04-0.001)
        make_slice(theta_star=0.045),  # t=2: recovers
    ]
    
    with patch('essvi.sequential.solve_single_slice', counting_solve_single_slice):
        with patch('essvi.sequential.handle_theta_projection') as mock_degen:
            mock_degen.return_value = make_slice_params(degenerate=True)
            
            results = calibrate_surface(slices, cfg)
    
    # solve_single_slice should be called ONCE (for first slice only)
    assert call_count == 1, f"solve_single_slice called {call_count} times, expected 1"
    # handle_theta_projection called for second slice
    assert mock_degen.call_count == 1
    # Third slice normal
    assert mock_degen.call_count == 1
```

### Test 2: Normal Case Still Runs Full Grid
```python
def test_normal_case_runs_full_rho_grid():
    """Monotonic theta runs normal solver."""
    slices = [
        make_slice(theta_star=0.04),
        make_slice(theta_star=0.045),  # Increases
        make_slice(theta_star=0.05),
    ]
    
    with patch('essvi.sequential.solve_single_slice') as mock_solve:
        mock_solve.side_effect = [make_slice_params() for _ in range(3)]
        
        results = calibrate_surface(slices, cfg)
    
    assert mock_solve.call_count == 3
```

### Test 3: Degenerate Flag Set
```python
def test_degenerate_flag_set():
    """Degenerate slice has degenerate=True flag."""
    slices = [
        make_slice(theta_star=0.04),
        make_slice(theta_star=0.035),  # Drop
    ]
    
    with patch('essvi.sequential.handle_theta_projection') as mock_degen:
        mock_degen.return_value = make_slice_params(degenerate=True)
        
        results = calibrate_surface(slices, cfg)
    
    assert results[1].degenerate is True
    assert results[0].degenerate is False
```

---

## Integration Check

```bash
pytest tests/test_sequential.py -v -x
```

---

## Commit

```bash
git add essvi/sequential.py tests/test_sequential.py
git commit -m "sequential: fix P1-2 pre-loop C1 check; trigger §14 degeneracy before rho grid (thermo_3 T3_A6_sequential; tests pass)"
```

---

## Failure Protocol

If stuck after 3 attempts:
1. Write `fails/T3_A6_sequential_<test>.md`
2. Include: theta values, prev_locked state, whether handle_theta_projection called