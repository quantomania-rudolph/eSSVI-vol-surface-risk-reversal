# Agent T3_A7_surface — Tail Caps + Long Extrapolation Fix

**Campaign:** thermo_3  
**Phase:** 3 (Parallel Group C — After Phase 2)  
**File:** `essvi/surface.py`  
**Depends On:** T3_A5_solver, T3_A4_constraints  
**Issues:** P1-3 (Tail Extrapolation), P1-4 (Long Extrapolation θ)

---

## Context

Two surface extrapolation bugs:
1. **P1-3**: No tail slope cap for `|k| > K_MAX` — Lee bound allows slope up to 2, but numerical extrapolation can exceed
2. **P1-4**: Long extrapolation `θ(T)` for `T > T_N` uses wrong slope (interpolates between last two instead of using last segment slope)

---

## Research: Blueprint Formulas

### Blueprint §15.4 — Tail Extrapolation
```
For |k| > K_MAX:
  c_+ = min((ψ/2)(1+ρ), TAIL_SLOPE_CAP)
  c_- = min((ψ/2)(1−ρ), TAIL_SLOPE_CAP)
  w(k) = w(K_MAX) + c_+·(k−K_MAX)  for k > K_MAX
  w(k) = w(−K_MAX) + c_-·(k+K_MAX) for k < −K_MAX
```
`TAIL_SLOPE_CAP = 2.0` (Lee bound: limsup w(k)/|k| ≤ 2)

### Blueprint §15.3 — Long Extrapolation (T > T_N)
```
θ(T) = θ_N + (θ_N − θ_{N-1})/(T_N − T_{N-1}) · (T − T_N)  # Linear with LAST slope
ψ(T) = ψ_N  (FLAT)
ρ(T) = ρ_N  (FLAT)
```

---

## Required Changes to `essvi/surface.py`

### 1. Add Constants (Top of File)

```python
# Extrapolation constants (from config)
from essvi.config import cfg

TAIL_SLOPE_CAP = cfg.TAIL_SLOPE_CAP  # 2.0
K_MAX = cfg.K_MAX  # e.g., 2.0 log-moneyness
```

### 2. Fix `w_surface` — Add Tail Cap (P1-3)

**Current:** Calls `w_slice` directly for all k

**Fixed:**
```python
def w_surface(k: Union[float, np.ndarray], T: float, 
              params_dict: Dict[float, SliceParams],
              k_max: float = K_MAX) -> np.ndarray:
    """
    Evaluate total variance w(k, T) with tail slope capping.
    """
    k = np.asarray(k)
    scalar_input = k.ndim == 0
    if scalar_input:
        k = k[np.newaxis]
    
    # Get interpolated params at T
    theta, psi, rho = get_params_at_T(T, params_dict)
    phi = psi / theta if theta > 0 else 0.0
    
    # Base eSSVI
    w = w_slice(k, theta, phi, rho)
    
    # Apply tail cap
    w = _apply_tail_cap(w, k, k_max, psi, rho)
    
    if scalar_input:
        return w[0]
    return w


def _apply_tail_cap(w: np.ndarray, k: np.ndarray, k_max: float, 
                    psi: float, rho: float) -> np.ndarray:
    """Cap tail slopes per Blueprint §15.4."""
    w = w.copy()
    
    # Right tail (k > k_max)
    mask_right = k > k_max
    if np.any(mask_right):
        c_plus = min(0.5 * psi * (1.0 + rho), TAIL_SLOPE_CAP)
        k_ref = k_max
        w_ref = w_slice(np.array([k_max]), theta, phi, rho)[0]  # w at boundary
        w[mask_right] = w_ref + c_plus * (k[mask_right] - k_ref)
    
    # Left tail (k < -k_max)
    mask_left = k < -k_max
    if np.any(mask_left):
        c_minus = min(0.5 * psi * (1.0 - rho), TAIL_SLOPE_CAP)
        k_ref = -k_max
        w_ref = w_slice(np.array([-k_max]), theta, phi, rho)[0]
        w[mask_left] = w_ref + c_minus * (k[mask_left] - k_ref)
    
    return w
```

### 3. Fix `get_params_at_T` — Long Extrapolation (P1-4)

**Current:** Falls through to linear interpolation for T > T_N

**Fixed:**
```python
def get_params_at_T(T: float, params_dict: Dict[float, SliceParams]) -> Tuple[float, float, float]:
    """
    Get (theta, psi, rho) at maturity T.
    
    Blueprint §15:
    - T in [T_1, T_N]: interpolate in T
    - T < T_1: flat extrapolate (first slice)
    - T > T_N: theta linear with LAST slope; psi, rho FLAT
    """
    if not params_dict:
        raise ValueError("Empty params_dict")
    
    maturities = sorted(params_dict.keys())
    T_min = maturities[0]
    T_max = maturities[-1]
    
    # --- Short extrap (T < T_min) ---
    if T <= T_min:
        sl = params_dict[T_min]
        return sl.theta, sl.psi, sl.rho
    
    # --- Long extrap (T > T_max) ---
    if T >= T_max:
        if len(maturities) == 1:
            # Only one slice — flat everything
            sl = params_dict[T_max]
            return sl.theta, sl.psi, sl.rho
        
        # Use LAST segment slope for theta
        T_N = maturities[-1]
        T_Nm1 = maturities[-2]
        sl_N = params_dict[T_N]
        sl_Nm1 = params_dict[T_Nm1]
        
        # Slope of last segment
        slope = (sl_N.theta - sl_Nm1.theta) / (T_N - T_Nm1)
        theta = sl_N.theta + slope * (T - T_N)
        
        # Psi, rho FLAT
        psi = sl_N.psi
        rho = sl_N.rho
        
        return theta, psi, rho
    
    # --- Interpolate in range ---
    # Find bracketing maturities
    for i in range(len(maturities) - 1):
        if maturities[i] <= T <= maturities[i+1]:
            T1, T2 = maturities[i], maturities[i+1]
            sl1, sl2 = params_dict[T1], params_dict[T2]
            
            w_t = (T - T1) / (T2 - T1)
            
            # Linear interpolation for all three
            theta = sl1.theta + w_t * (sl2.theta - sl1.theta)
            psi = sl1.psi + w_t * (sl2.psi - sl1.psi)
            rho = sl1.rho + w_t * (sl2.rho - sl1.rho)
            
            return theta, psi, rho
    
    # Should not reach here
    raise ValueError(f"T={T} not in range and not handled")
```

### 4. Add Helper Function for Theta Extrapolation (if needed elsewhere)

```python
def extrapolate_long_theta(T: float, maturities: List[float], thetas: List[float]) -> float:
    """Blueprint §15.3: θ(T) = θ_N + (θ_N - θ_{N-1})/(T_N - T_{N-1}) * (T - T_N)"""
    if T <= maturities[-1]:
        return np.interp(T, maturities, thetas)
    
    if len(maturities) < 2:
        return thetas[-1]
    
    T_N = maturities[-1]
    T_Nm1 = maturities[-2]
    theta_N = thetas[-1]
    theta_Nm1 = thetas[-2]
    
    slope = (theta_N - theta_Nm1) / (T_N - T_Nm1)
    return theta_N + slope * (T - T_N)
```

---

## Tests Required (`tests/test_surface.py`)

### Test 1: Tail Slope Cap Right Tail
```python
def test_tail_slope_cap_right():
    """For k > K_MAX, slope ≤ TAIL_SLOPE_CAP."""
    params = make_params_dict()
    T = 0.1
    psi = params[T].psi
    rho = params[T].rho
    
    k_test = np.array([K_MAX + 0.1, K_MAX + 1.0, K_MAX + 5.0])
    w = w_surface(k_test, T, params)
    
    # Compute slopes
    slopes = np.diff(w) / np.diff(k_test)
    
    c_plus = min(0.5 * psi * (1 + rho), TAIL_SLOPE_CAP)
    assert np.all(slopes <= c_plus + 1e-10), f"Slopes {slopes} exceed cap {c_plus}"
    assert np.all(slopes >= c_plus - 1e-10), f"Slopes {slopes} not constant"
```

### Test 2: Tail Slope Cap Left Tail
```python
def test_tail_slope_cap_left():
    """For k < -K_MAX, slope ≥ -TAIL_SLOPE_CAP (i.e., |slope| ≤ cap)."""
    params = make_params_dict()
    T = 0.1
    psi = params[T].psi
    rho = params[T].rho
    
    k_test = np.array([-K_MAX - 5.0, -K_MAX - 1.0, -K_MAX - 0.1])
    w = w_surface(k_test, T, params)
    
    slopes = np.diff(w) / np.diff(k_test)
    
    c_minus = min(0.5 * psi * (1 - rho), TAIL_SLOPE_CAP)
    assert np.all(slopes >= -c_minus - 1e-10)
    assert np.all(slopes <= -c_minus + 1e-10)
```

### Test 3: Continuity at K_MAX Boundary
```python
def test_tail_continuity_at_boundary():
    """w(k) continuous at k = ±K_MAX."""
    params = make_params_dict()
    T = 0.1
    
    k_left = K_MAX - 1e-6
    k_right = K_MAX + 1e-6
    
    w_left = w_surface(k_left, T, params)
    w_right = w_surface(k_right, T, params)
    
    assert abs(w_left - w_right) < 1e-6, "Discontinuity at K_MAX"
```

### Test 4: Long Extrapolation Theta Uses Last Slope
```python
def test_long_extrapolation_theta_last_slope():
    """For T > T_N, θ uses last segment slope, not interp slope."""
    maturities = [0.05, 0.1, 0.2, 0.5]
    thetas = [0.02, 0.03, 0.04, 0.045]  # Last slope = (0.045-0.04)/(0.5-0.2) = 0.0167
    
    # T = 1.0 (beyond last)
    T = 1.0
    theta_extrap = extrapolate_long_theta(T, maturities, thetas)
    
    expected = 0.045 + 0.0167 * (1.0 - 0.5)
    assert abs(theta_extrap - expected) < 1e-6
```

### Test 5: Long Extrapolation Psi/Rho Flat
```python
def test_long_extrapolation_psi_rho_flat():
    """For T > T_N, ψ and ρ are flat (last slice values)."""
    params = make_params_dict()
    T_N = max(params.keys())
    T = T_N + 1.0
    
    theta, psi, rho = get_params_at_T(T, params)
    
    assert psi_last = params[T_N]
    assert psi == _last.psi
    assert rho == _last.rho
```

### Test 6: Lee Bound Asymptotic
```python
def test_lee_bound_asymptotic():
    """limsup w(k)/|k| ≤ 2 for extreme k."""
    params = make_params_dict()
    T = 0.1
    
    k_extreme = np.array([100.0, 1000.0, 10000.0])
    w = w_surface(k_extreme, T, params)
    
    ratios = w / np.abs(k_extreme)
    assert np.all(ratios <= 2.0 + 1e-6), f"Lee bound violated: {ratios}"
```

---

## Integration Check

```bash
pytest tests/test_surface.py -v -x
pytest tests/test_audit.py -v -x  # Audit uses surface
```

---

## Commit

```bash
git add essvi/surface.py tests/test_surface.py
git commit -m "surface: fix P1-3 tail slope cap, P1-4 long extrapolation theta last-slope (thermo_3 T3_A7_surface; tests pass)"
```

---

## Failure Protocol

If stuck after 3 attempts:
1. Write `fails/T3_A7_surface_<test>.md`
2. Include: slope values at tails, theta extrapolation values, continuity deltas