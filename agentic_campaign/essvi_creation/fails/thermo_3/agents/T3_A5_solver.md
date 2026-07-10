# Agent T3_A5_solver — Solver Wire-Up + Rho Grid Fix

**Campaign:** thermo_3  
**Phase:** 2 (Parallel Group B — After Group A)  
**File:** `essvi/solver.py`  
**Depends On:** T3_A2_anchor, T3_A3_objective, T3_A4_constraints  
**Issues:** P0-1 (call site), P0-5 (rho grid — config fixed by T3_A8)

---

## Context

The solver's `_evaluate_at_phi` currently:
1. Calls `extract_anchor_params(df_slice, phi, rho)` with φ, ρ — **WRONG** (anchor independent of candidates)
2. Uses returned `theta_star` which was computed by inverted `compute_theta_star`
3. Needs to: extract anchor ONCE, then compute `theta_t = theta_from_psi(psi, rho, k*, θ*)` per candidate

Also ensure rho grid uses symmetric bounds from config.

---

## Required Changes to `essvi/solver.py`

### 1. Fix `_evaluate_at_phi` (Lines ~80-150)

**Current (BUGGY):**
```python
def _evaluate_at_phi(phi: float, rho: float, df_slice: pd.DataFrame,
                     prev_slice: Optional[SliceParams], cfg) -> Optional[SliceResult]:
    anchor = extract_anchor_params(df_slice, phi, rho)  # WRONG: passes phi, rho
    theta_t = anchor.theta_star  # This varies with phi, rho!
    ...
```

**Fixed:**
```python
def _evaluate_at_phi(phi: float, rho: float, df_slice: pd.DataFrame,
                     prev_slice: Optional[SliceParams], cfg) -> Optional[SliceResult]:
    """
    Evaluate objective at given (φ, ρ) for one slice.
    
    Anchor extracted ONCE (no φ, ρ dependence).
    For each ψ candidate, θ computed via exact closed form.
    """
    # 1. Extract anchor ONCE — no phi, rho
    from essvi.anchor import extract_anchor_params
    anchor = extract_anchor_params(df_slice)
    
    k_star = anchor.k_star
    theta_star = anchor.theta_star
    
    # 2. Corridor lower bound L_ψ
    from essvi.constraints import compute_L_psi
    L_psi = compute_L_psi(rho, prev_slice, k_star, theta_star)
    if L_psi is None:
        return None  # No feasible ψ for this ρ
    
    # 3. Find ALL feasible ψ intervals (multi-interval!)
    from essvi.constraints import find_feasible_psi_intervals
    intervals = find_feasible_psi_intervals(rho, prev_slice, k_star, theta_star, L_psi)
    
    if not intervals:
        return None
    
    # 4. Slice data
    k_arr = df_slice["log_moneyness"].values
    w_arr = df_slice["implied_vol"].values**2 * df_slice["business_t"].values
    vega_arr = df_slice["vega"].values
    T = float(df_slice["business_t"].iloc[0])
    
    # 5. Previous params for regularization
    prev_psi = prev_slice.psi if prev_slice else None
    prev_theta = prev_slice.theta if prev_slice else None
    
    # 6. Search each interval for best ψ
    best_obj = np.inf
    best_psi = None
    best_theta = None
    
    for psi_lo, psi_hi in intervals:
        # Brent minimize on this interval
        from scipy.optimize import minimize_scalar
        
        def obj_func(psi):
            return objective_slice(
                psi, rho, k_arr, w_arr, vega_arr, T,
                theta_star, k_star,
                weight_mode=cfg.VEGA_WEIGHT_MODE,
                lambda_spatial=cfg.LAMBDA_SPATIAL,
                lambda_temporal=cfg.LAMBDA_TEMPORAL,
                prev_psi=prev_psi,
                prev_theta=prev_theta
            )
        
        result = minimize_scalar(obj_func, bounds=(psi_lo, psi_hi), method='bounded')
        
        if result.fun < best_obj:
            best_obj = result.fun
            best_psi = result.x
            # Compute θ from best ψ
            from essvi.constraints import theta_from_psi
            best_theta = theta_from_psi(best_psi, rho, k_star, theta_star)
    
    if best_psi is None:
        return None
    
    # 7. Return slice result
    phi_computed = best_psi / best_theta if best_theta > 0 else 0.0
    
    return SliceResult(
        theta=best_theta,
        psi=best_psi,
        rho=rho,
        phi=phi_computed,
        objective=best_obj,
        anchor_k_star=k_star,
        anchor_theta_star=theta_star,
        anchor_quality=anchor.quality,
        n_strikes=len(k_arr)
    )
```

### 2. Fix `solve_single_slice` — Rho Grid Uses Config (Lines ~200-250)

**Current:**
```python
def solve_single_slice(df_slice, prev_slice, cfg):
    rho_grid = build_rho_grid(prev_slice.rho if prev_slice else None)  # No step!
```

**Fixed:**
```python
def solve_single_slice(df_slice: pd.DataFrame,  pd.DataFrame, 
                       prev_slice: Optional[SliceParams], 
                       cfg) -> SliceResult:
    """
    Solve one eSSVI slice.
    """
    # Rho grid from config (symmetric [-0.99, 0.99])
    from essvi.config import cfg as global_cfg
    rho_grid = build_rho_grid(
        rho_prev=prev_slice.rho if prev_slice else None,
        step=cfg.RHO_GRID_STEP,
        lo=cfg.RHO_GRID_LO,
        hi=cfg.RHO_GRID_HI
    )
    
    best_obj = np.inf
    best_result = None
    
    for rho in rho_grid:
        result = _evaluate_at_phi(0.0, rho, df_slice, prev_slice, cfg)  # phi not used
        if result is not None and result.objective < best_obj:
            best_obj = result.objective
            best_result = result
    
    if best_result is None:
        raise SolverError(f"No feasible solution for slice {df_slice['expiration'].iloc[0]}")
    
    return best_result
```

### 3. Ensure `build_rho_grid` Uses Symmetric Bounds

```python
def build_rho_grid(rho_prev: Optional[float], step: float, 
                   lo: float = -0.99, hi: float = 0.99) -> np.ndarray:
    """
    Build rho grid symmetric around 0.
    If rho_prev given, refine around it.
    """
    # Base grid
    grid = np.arange(lo, hi + step/2, step)
    
    # Refine around rho_prev
    if rho_prev is not None:
        fine = np.linspace(rho_prev - 3*step, rho_prev + 3*step, 13)
        fine = fine[(fine >= lo) & (fine <= hi)]
        grid = np.unique(np.concatenate([grid, fine]))
    
    return grid
```

### 4. Update `SliceResult` Dataclass

```python
@dataclass(frozen=True)
class SliceResult:
    theta: float
    psi: float
    rho: float
    phi: float
    objective: float
    anchor_k_star: float
    anchor_theta_star: float
    anchor_quality: float
    n_strikes: int
    
    def to_dict(self):
        return {
            "theta": self.theta,
            "psi": self.psi,
            "rho": self.rho,
            "phi": self.phi,
            "objective": self.objective,
            "anchor_k_star": self.anchor_k_star,
            "anchor_theta_star": self.anchor_theta_star,
            "anchor_quality": self.anchor_quality,
            "n_strikes": self.n_strikes
        }
```

---

## Tests Required (`tests/test_solver.py`)

### Test 1: Anchor Called Once Per Slice
```python
def test_anchor_called_once_per_slice():
    """extract_anchor_params called once, not per rho."""
    call_count = 0
    
    def mock_extract(df):
        nonlocal call_count
        call_count += 1
        return AnchorParams(k_star=0.0, theta_star=0.04, quality=1.0, n_belly=5)
    
    with patch('essvi.solver.extract_anchor_params', mock_extract):
        solve_single_slice(make_slice(), None, cfg)
    
    assert call_count == 1, f"Anchor extracted {call_count} times, expected 1"
```

### Test 2: Theta from Exact Closed Form
```python
def test_theta_from_exact_formula():
    """Solver uses theta_from_psi for each psi candidate."""
    df = make_slice()
    anchor = AnchorParams(k_star=0.05, theta_star=0.04, quality=1.0, n_belly=5)
    
    with patch('essvi.solver.extract_anchor_params', return_value=anchor):
        with patch('essvi.solver.theta_from_psi') as mock_theta:
            mock_theta.return_value = 0.045
            
            solve_single_slice(df, None, cfg)
            
            # Should be called for each psi evaluation in Brent
            assert mock_theta.call_count > 0
            # Check call signature
            for call in mock_theta.call_args_list:
                psi, rho, k_star, theta_star = call[0]
                assert k_star == 0.05
                assert theta_star == 0.04
```

### Test 3: Rho Grid Symmetric
```python
def test_rho_grid_symmetric():
    """Rho grid spans [-0.99, 0.99]."""
    grid = build_rho_grid(None, step=0.01, lo=-0.99, hi=0.99)
    
    assert abs(grid[0] - (-0.99)) < 1e-10
    assert abs(grid[-1] - 0.99) < 1e-10
    assert len(grid) == 199  # 0.01 step
```

### Test 4: Multi-Interval Corridor Search
```python
def test_solver_searches_all_intervals():
    """Solver searches all feasible psi intervals, not just first."""
    # Create scenario with 2 intervals
    intervals = [(0.1, 0.2), (0.5, 0.6)]
    
    with patch('essvi.solver.find_feasible_psi_intervals', return_value=intervals):
        with patch('essvi.solver.minimize_scalar') as mock_min:
            # Return different objectives
            mock_min.side_effect = [
                Mock(fun=0.1, x=0.15),  # First interval
                Mock(fun=0.05, x=0.55), # Second interval - BETTER
            ]
            
            result = solve_single_slice(make_slice(), None, cfg)
            
            # Should pick second interval (lower objective)
            assert mock_min.call_count == 2
```

---

## Integration Check

```bash
pytest tests/test_solver.py -v -x
pytest tests/test_anchor.py -v -x  # Uses solver
```

---

## Commit

```bash
git add essvi/solver.py tests/test_solver.py
git commit -m "solver: fix P0-1 anchor call site (once per slice), use exact theta_from_psi; symmetric rho grid (thermo_3 T3_A5_solver; tests pass)"
```

---

## Failure Protocol

If stuck after 3 attempts:
1. Write `fails/T3_A5_solver_<test>.md`
2. Include: anchor call count, theta values per rho, grid boundaries, interval count