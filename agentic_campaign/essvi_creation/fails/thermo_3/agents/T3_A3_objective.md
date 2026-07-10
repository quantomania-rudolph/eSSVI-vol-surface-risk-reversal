# Agent T3_A3_objective — Objective Weight Fix (Variance-Space Vega²)

**Campaign:** thermo_3  
**Phase:** 2 (Parallel Group A — After Phase 1)  
**File:** `essvi/objective.py`  
**Depends On:** T3_A2_anchor  
**Issues:** P0-2 (Objective Weight Inversion)

---

## Context

**Blueprint §10 — Variance-Space Vega² Weighting (Recommended):**
```
ν_var,j = ν_vol,j / (2 · σ_mkt,j · √T) = ν_vol,j / (2 · √(w_mkt,j · T))
W_j = (ν_var,j)²
Error(ψ_t) = Σ_j W_j (w_mkt,j − w_mod,j)²
```

**Current Code (WRONG — INVERSE):**
```python
# objective.py:67-68
if weight_mode == "var_vega2":
    weights = 1.0 / vega_arr**2   # INVERSE — downweights ATM (high vega)!
```

This makes the fit chase noisy OTM wings and ignore the liquid belly.

---

## Required Changes to `essvi/objective.py`

### 1. Fix `objective_slice` Signature & Logic (Lines ~50-100)

**Current:**
```python
def objective_slice(psi, rho, k_arr, w_arr, vega_arr, theta_star, k_star,
                    weight_mode="var_vega2", lambda_spatial=0, lambda_temporal=0,
                    prev_psi=None, prev_theta=None) -> float:
    # ...
    if weight_mode == "var_vega2":
        weights = 1.0 / vega_arr**2  # WRONG
```

**Fixed:**
```python
def objective_slice(
    psi: float,
    rho: float,
    k_arr: np.ndarray,
    w_arr: np.ndarray,
    vega_arr: np.ndarray,
    T: float,                    # REQUIRED for variance-space conversion
    theta_star: float,
    k_star: float,
    weight_mode: str = "var_vega2",
    lambda_spatial: float = 0.0,
    lambda_temporal: float = 0.0,
    prev_psi: Optional[float] = None,
    prev_theta: Optional[float] = None,
) -> float:
    """
    Vega²-weighted least squares in VARIANCE space.
    
    Blueprint §10: W_j = (ν_var,j)² where ν_var = ν_vol / (2 σ √T)
    """
    from essvi.constraints import theta_from_psi
    from essvi.math import w_slice  # or local implementation
    
    # 1. Compute θ from ψ, ρ, anchor (EXACT closed form)
    theta = theta_from_psi(psi, rho, k_star, theta_star)
    
    if theta <= 0:
        return np.inf
    
    # 2. Model total variance w(k) = θ/2 * (1 + ρ φ k + sqrt((φ k + ρ)² + 1 - ρ²))
    #    where φ = ψ / θ
    phi = psi / theta
    w_model = w_slice(k_arr, theta, phi, rho)
    
    # 3. Variance-space vega weights
    if weight_mode == "var_vega2":
        # σ_mkt = sqrt(w_mkt / T)
        # ν_var = ν_vol / (2 * σ_mkt * sqrt(T)) = ν_vol / (2 * sqrt(w_mkt * T))
        sigma_mkt = np.sqrt(np.maximum(w_arr / T, 1e-12))
        nu_var = vega_arr / (2.0 * sigma_mkt * np.sqrt(T))
        weights = nu_var ** 2
    elif weight_mode == "vol_vega1":
        weights = np.abs(vega_arr)
    else:  # "uniform"
        weights = np.ones_like(w_arr)
    
    # 4. Weighted SSE
    residuals = w_arr - w_model
    obj = np.sum(weights * residuals ** 2)
    
    # 5. Spatial regularization: λ_s * (log θ - log θ_prev)²
    if lambda_spatial > 0 and prev_theta is not None and prev_theta > 0:
        obj += lambda_spatial * (np.log(theta) - np.log(prev_theta)) ** 2
    
    # 6. Temporal regularization: λ_t * (ψ - ψ_prev)²
    if lambda_temporal > 0 and prev_psi is not None:
        obj += lambda_temporal * (psi - prev_psi) ** 2
    
    return float(obj)
```

### 2. Fix `w_slice` Implementation (if not in math.py)

```python
def w_slice(k: np.ndarray, theta: float, phi: float, rho: float) -> np.ndarray:
    """eSSVI total variance slice. Locked convention: ψ = θ·φ."""
    u = phi * k + rho
    D = u * u + (1.0 - rho * rho)
    sqrt_D = np.sqrt(D)
    return 0.5 * theta * (1.0 + rho * phi * k + sqrt_D)
```

### 3. Ensure Exports

```python
__all__ = ["objective_slice", "w_slice"]
```

---

## Tests Required (`tests/test_objective.py`)

### Test 1: var_vega2 Weights ATM Higher Than Wings
```python
def test_var_vega2_weights_atm_heavier():
    """Variance-space vega² gives higher weight to ATM (high vega) than wings."""
    # ATM strike
    k_atm = 0.0
    w_atm = 0.04
    vega_atm = 0.20  # High vega at ATM
    
    # Wing strike
    k_wing = 0.5
    w_wing = 0.05
    vega_wing = 0.05  # Low vega at wing
    
    T = 0.1
    
    weights = np.array([vega_atm, vega_wing]) / (2 * np.sqrt(np.array([w_atm, w_wing]) * T))
    weights = weights ** 2
    
    assert weights[0] > weights[1], "ATM should have higher weight than wing"
    assert weights[0] / weights[1] > 10, "ATM weight should be much higher"
```

### Test 2: Objective Convex in ψ
```python
def test_objective_convex_in_psi():
    """Objective should be convex in ψ for fixed ρ."""
    anchor = AnchorParams(k_star=0.02, theta_star=0.04, quality=1.0, n_belly=5)
    k_arr = np.array([-0.3, -0.1, 0.0, 0.1, 0.3])
    w_arr = np.array([0.05, 0.042, 0.04, 0.042, 0.05])
    vega_arr = np.array([0.05, 0.15, 0.20, 0.15, 0.05])
    T = 0.1
    
    rhos = [-0.5, 0.0, 0.5]
    for rho in rhos:
        psis = np.linspace(0.1, 1.0, 20)
        objs = [objective_slice(psi, rho, k_arr, w_arr, vega_arr, T, 
                                anchor.theta_star, anchor.k_star, "var_vega2")
                for psi in psis]
        
        # Check convexity: second differences >= 0
        diffs = np.diff(objs, 2)
        assert np.all(diffs >= -1e-10), f"Non-convex for ρ={rho}"
```

### Test 3: Gradient Matches Finite Difference
```python
def test_objective_gradient():
    """Analytic gradient (if implemented) matches finite difference."""
    # If you add gradient function later
    pass
```

### Test 4: T Parameter Required
```python
def test_objective_requires_T():
    """Objective slice requires T parameter for variance-space weights."""
    # Calling without T should raise TypeError
    with pytest.raises(TypeError):
        objective_slice(0.5, -0.3, k_arr, w_arr, vega_arr, 
                       theta_star=0.04, k_star=0.0)
```

---

## Integration Check

```bash
pytest tests/test_objective.py -v -x
pytest tests/test_solver.py::test_solve_single_slice_basic -v -x  # Uses objective
```

---

## Commit

```bash
git add essvi/objective.py tests/test_objective.py
git commit -m "objective: fix P0-2 var_vega2 weight inversion; add T param for variance-space vega (thermo_3 T3_A3_objective; tests pass)"
```

---

## Failure Protocol

If stuck after 3 attempts:
1. Write `fails/T3_A3_objective_<test>.md`
2. Include: weight values at ATM vs wing, objective values, gradient check