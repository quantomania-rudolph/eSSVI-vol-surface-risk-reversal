# Agent T3_A4_constraints — Pasquazzi Case A + Corridor Multi-Interval + MM Table

**Campaign:** thermo_3  
**Phase:** 2 (Parallel Group A — After Phase 1)  
**File:** `essvi/constraints.py`  
**Depends On:** T3_A2_anchor, T3_A8_config  
**Issues:** P0-4 (Pasquazzi Case A), P1-1 (Corridor Multi-Interval), P1-5 (MM Table Precompute)

---

## Context

Three major fixes in the core no-arbitrage engine. This is the most mathematically complex file — any bug here propagates to the entire surface.

---

## Research References

| Fix | Reference | Key Formula |
|-----|-----------|-------------|
| P0-4 | **Pasquazzi (2023) Prop 13** | Case A: Θ≈1 → feasible iff ρ₁=ρ₂=0 (any Φ≥1) OR ρ₁=ρ₂ (Φ=1) |
| P1-1 | **Blueprint §8.4** | U_ψ(ψ) non-monotonic → find ALL intervals where U_ψ ≥ L_ψ |
| P1-5 | **Martini-Mingone (2022) Prop 6.3** | ℱ_MM(θ,|ρ|) precomputed 200×100 table → bilinear interp |

---

## 1. P0-4: Pasquazzi 2023 Case A Implementation

### Add Config Constant (from T3_A8_config)
```python
# At module top
PASQUAZZI_THETA_TOL = 1e-4  # Θ = θ₂/θ₁ within this of 1.0 → Case A
```

### Modify `check_calendar_pasquazzi` (Lines ~196-239)

**Current:** Only Hendriks-Martini conditions

**New — Complete Pasquazzi Logic:**
```python
def check_calendar_pasquazzi(
    theta1: float, psi1: float, rho1: float,
    theta2: float, psi2: float, rho2: float
) -> Tuple[bool, str]:
    """
    Pasquazzi 2023 Proposition 13 — Necessary & sufficient calendar no-arb.
    Returns (feasible, reason).
    """
    theta_ratio = theta2 / theta1
    phi1 = psi1 / theta1 if theta1 > 0 else 0
    phi2 = psi2 / theta2 if theta2 > 0 else 0
    Phi = phi2 / phi1 if phi1 > 0 else np.inf
    
    # --- CASE A: Θ ≈ 1 ---
    if abs(theta_ratio - 1.0) <= PASQUAZZI_THETA_TOL:
        # Feasible ONLY if:
        # (i) ρ₁ = ρ₂ = 0 (both zero) AND Φ ≥ 1
        # (ii) ρ₁ = ρ₂ ≠ 0 AND Φ = 1
        if abs(rho1) < 1e-10 and abs(rho2) < 1e-10:
            if Phi >= 1.0 - 1e-10:
                return True, "Case A(i): ρ₁=ρ₂=0, Φ≥1"
            return False, f"Case A(i): ρ₁=ρ₂=0 but Φ={Phi:.6f}<1"
        
        if abs(rho1 - rho2) < 1e-10:
            if abs(Phi - 1.0) < 1e-10:
                return True, "Case A(ii): ρ₁=ρ₂, Φ=1"
            return False, f"Case A(ii): ρ₁=ρ₂ but Φ={Phi:.6f}≠1"
        
        # ρ₁ ≠ ρ₂ and not both zero → INFEASIBLE
        return False, f"Case A: Θ≈1 but ρ₁={rho1:.4f}≠ρ₂={rho2:.4f} and not both zero"
    
    # --- CASE B: Θ > 1 (theta2 > theta1) ---
    if theta_ratio > 1.0:
        return _check_hm_stripe(theta1, psi1, rho1, theta2, psi2, rho2)
    
    # --- CASE C: Θ < 1 (theta2 < theta1) ---
    # Symmetric to Case B
    return _check_hm_stripe(theta2, psi2, rho2, theta1, psi1, rho1)
```

### Modify `_compute_L_psi` (Lines ~340-380) — Corridor Lower Bound

**Must include Case A logic:**
```python
def _compute_L_psi(rho: float, prev_slice: Optional[SliceParams], 
                   k_star: float, theta_star: float) -> Optional[float]:
    """
    Blueprint §8.2: Lower bound on ψ from calendar arbitrage.
    Returns None if infeasible (empty corridor).
    """
    if prev_slice is None:
        return 0.0
    
    theta_prev = prev_slice.theta
    psi_prev = prev_slice.psi
    rho_prev = prev_slice.rho
    
    theta_ratio = theta_star / theta_prev
    
    # --- Case A: Θ ≈ 1 ---
    if abs(theta_ratio - 1.0) <= PASQUAZZI_THETA_TOL:
        # Feasible only if:
        if abs(rho) < 1e-10 and abs(rho_prev) < 1e-10:
            return 0.0  # Both zero → any ψ ≥ 0
        
        if abs(rho - rho_prev) < 1e-10:
            return psi_prev  # Must match exactly (Φ=1)
        
        # ρ ≠ ρ_prev and not both zero → INFEASIBLE
        return None
    
    # --- Case B/C: Use Hendriks-Martini boundary ---
    # ... existing HM logic ...
    return _hm_calendar_lower_bound(rho, prev_slice, k_star, theta_star)
```

---

## 2. P1-1: Corridor Multi-Interval Search

### Blueprint §8.4 Algorithm

`U_ψ(ψ)` (upper bound from Φ ≤ 1) is **non-monotonic** because θ(ψ) is convex. Can have multiple intervals where U_ψ ≥ L_ψ.

**Current:** Single interval scan, returns first

**New:**
```python
def find_feasible_psi_intervals(
    rho: float,
    prev_slice: Optional[SliceParams],
    k_star: float,
    theta_star: float,
    L_psi: float
) -> List[Tuple[float, float]]:
    """
    Find ALL ψ intervals where U_ψ(ψ) ≥ L_ψ.
    U_ψ comes from Φ ≤ 1 + calendar feasibility.
    """
    if L_psi is None:
        return []  # Empty corridor
    
    # 1. Determine search range
    psi_min = max(L_psi, 1e-6)
    psi_max = _compute_psi_upper_bound(rho, theta_star)  # From butterfly + Φ≤1
    
    if psi_min >= psi_max:
        return []
    
    # 2. Sample U_ψ on dense log grid
    n_samples = 500
    psi_grid = np.logspace(np.log10(psi_min), np.log10(psi_max), n_samples)
    
    U_vals = np.array([_compute_U_psi(rho, psi, prev_slice, k_star, theta_star) 
                       for psi in psi_grid])
    
    # 3. f(ψ) = U_ψ(ψ) - L_ψ
    f_vals = U_vals - L_psi
    
    # 4. Find ALL sign changes (f goes from <0 to >0 or vice versa)
    sign = np.sign(f_vals)
    intervals = []
    
    for i in range(len(sign) - 1):
        if sign[i] == 0:
            # Touching zero — refine
            lo = hi = psi_grid[i]
            intervals.append((lo, hi))
        elif sign[i] < 0 and sign[i+1] > 0:
            # Crossing up: feasible interval starts
            lo = _brent_root(lambda p: _compute_U_psi(rho, p, prev_slice, k_star, theta_star) - L_psi,
                            psi_grid[i], psi_grid[i+1])
            # Find where it crosses back down (or end)
            hi = _find_upper_crossing(rho, prev_slice, k_star, theta_star, L_psi, psi_grid[i+1])
            intervals.append((lo, hi))
        elif sign[i] > 0 and sign[i+1] < 0:
            # Crossing down: interval ends
            pass  # Handled by upper crossing
    
    # 5. Also check if starts feasible (f[0] > 0)
    if f_vals[0] > 0:
        hi = _find_upper_crossing(rho, prev_slice, k_star, theta_star, L_psi, psi_min)
        intervals.insert(0, (psi_min, hi))
    
    # 6. Filter: only keep intervals with width > tolerance
    intervals = [(lo, hi) for lo, hi in intervals if hi - lo > 1e-8]
    
    return intervals


def _compute_U_psi(rho: float, psi: float, prev_slice: Optional[SliceParams],
                   k_star: float, theta_star: float) -> float:
    """Upper bound on ψ from Φ ≤ 1 and calendar feasibility."""
    if prev_slice is None:
        return _butterfly_upper_bound(psi, rho, theta_star)
    
    theta_prev = prev_slice.theta
    psi_prev = prev_slice.psi
    rho_prev = prev_slice.rho
    
    theta = theta_from_psi(psi, rho, k_star, theta_star)
    phi = psi / theta
    
    # Φ = φ/φ_prev ≤ 1
    phi_prev = psi_prev / theta_prev
    if phi_prev > 0:
        Phi = phi / phi_prev
        if Phi > 1.0:
            return -1.0  # Infeasible
    
    # Calendar feasibility (will be checked in corridor search)
    # U_ψ is effectively infinite if calendar OK, else -inf
    # Simplified: return psi_max from butterfly
    return _butterfly_upper_bound(psi, rho, theta_star)


def _find_upper_crossing(rho, prev_slice, k_star, theta_star, L_psi, psi_start):
    """Find where U_ψ crosses below L_ψ going up."""
    psi_max = _compute_psi_upper_bound(rho, theta_star)
    if psi_start >= psi_max:
        return psi_max
    
    def f(p):
        return _compute_U_psi(rho, p, prev_slice, k_star, theta_star) - L_psi
    
    try:
        return brentq(f, psi_start, psi_max)
    except ValueError:
        return psi_max
```

---

## 3. P1-5: MM Butterfly Table Precomputation

### Add Module-Level Table (Lines ~1-100)

```python
# Module-level constants
_MM_THETA_MIN = 1e-6
_MM_THETA_MAX = 2.0
_MM_RHO_MAX = 0.999
_MM_THETA_N = 200
_MM_RHO_N = 100

_MM_THETA_GRID = None
_MM_RHO_GRID = None
_MM_TABLE = None
_MM_TABLE_BUILT = False


def _build_mm_table():
    """Precompute ℱ_MM(θ, |ρ|) on log(θ) × ρ grid. Runs on import."""
    global _MM_THETA_GRID, _MM_RHO_GRID, _MM_TABLE, _MM_TABLE_BUILT
    
    if _MM_TABLE_BUILT:
        return
    
    _MM_THETA_GRID = np.logspace(np.log10(_MM_THETA_MIN), np.log10(_MM_THETA_MAX), _MM_THETA_N)
    _MM_RHO_GRID = np.linspace(0, _MM_RHO_MAX, _MM_RHO_N)
    _MM_TABLE = np.zeros((_MM_THETA_N, _MM_RHO_N))
    
    # Build using existing Brent-based function (slow but one-time)
    for i, theta in enumerate(_MM_THETA_GRID):
        for j, rho in enumerate(_MM_RHO_GRID):
            _MM_TABLE[i, j] = _compute_f_MM_brent(theta, rho)
    
    _MM_TABLE_BUILT = True


# Build on import
_build_mm_table()
```

### Replace `compute_f_MM` with Bilinear Interpolation

```python
def compute_f_MM(theta: float, rho: float) -> float:
    """
    Martini-Mingone butterfly boundary ℱ_MM(θ, |ρ|).
    Bilinear interpolation on precomputed table.
    """
    if not _MM_TABLE_BUILT:
        _build_mm_table()
    
    rho_abs = abs(rho)
    
    # Clamp to grid bounds
    theta_clamped = np.clip(theta, _MM_THETA_MIN, _MM_THETA_MAX)
    rho_clamped = np.clip(rho_abs, 0, _MM_RHO_MAX)
    
    # Find indices
    i = np.searchsorted(_MM_THETA_GRID, theta_clamped) - 1
    j = np.searchsorted(_MM_RHO_GRID, rho_clamped) - 1
    
    # Clamp indices
    i = np.clip(i, 0, _MM_THETA_N - 2)
    j = np.clip(j, 0, _MM_RHO_N - 2)
    
    # Bilinear interpolation on log(theta), rho
    log_theta = np.log(theta_clamped)
    log_t0 = np.log(_MM_THETA_GRID[i])
    log_t1 = np.log(_MM_THETA_GRID[i+1])
    r0 = _MM_RHO_GRID[j]
    r1 = _MM_RHO_GRID[j+1]
    
    # Weights
    wt = (log_theta - log_t0) / (log_t1 - log_t0)
    wr = (rho_clamped - r0) / (r1 - r0)
    
    # Four corners
    v00 = _MM_TABLE[i, j]
    v01 = _MM_TABLE[i, j+1]
    v10 = _MM_TABLE[i+1, j]
    v11 = _MM_TABLE[i+1, j+1]
    
    # Bilinear
    v0 = v00 + wr * (v01 - v00)
    v1 = v10 + wr * (v11 - v10)
    return float(v0 + wt * (v1 - v0))
```

### Keep Original Brent Function (for table build & validation)

```python
def _compute_f_MM_brent(theta: float, rho: float) -> float:
    """Original Brent-based computation — used for table build only."""
    # ... existing implementation ...
    pass
```

---

## Tests Required (`tests/test_constraints.py`)

### Test 1: Pasquazzi Case A — Feasible
```python
def test_pasquazzi_case_A_feasible_both_zero():
    """Case A: Θ≈1, ρ₁=ρ₂=0, Φ≥1 → feasible."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, 0.0, 0.04001, 0.25, 0.0)
    assert feasible
    assert "Case A(i)" in reason


def test_pasquazzi_case_A_feasible_equal_rho():
    """Case A: Θ≈1, ρ₁=ρ₂≠0, Φ=1 → feasible."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, -0.3, 0.04001, 0.20005, -0.3)
    assert feasible
    assert "Case A(ii)" in reason
```

### Test 2: Pasquazzi Case A — Infeasible
```python
def test_pasquazzi_case_A_infeasible_rho_diff():
    """Case A: Θ≈1, ρ₁≠ρ₂, not both zero → INFEASIBLE."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, -0.3, 0.04001, 0.25, 0.2)
    assert not feasible
    assert "Case A" in reason
    assert "INFEASIBLE" in reason
```

### Test 3: Corridor Multi-Interval
```python
def test_corridor_multiple_intervals():
    """U_ψ dips below L_ψ then above → two feasible intervals."""
    # Construct prev_slice where U_ψ has a valley
    prev = make_prev_slice_with_valley()
    
    intervals = find_feasible_psi_intervals(
        rho=-0.3, prev_slice=prev, k_star=0.05, theta_star=0.04, L_psi=0.1
    )
    
    assert len(intervals) >= 2, f"Expected ≥2 intervals, got {len(intervals)}"
    # Verify each interval is actually feasible
    for lo, hi in intervals:
        psi_mid = (lo + hi) / 2
        U = _compute_U_psi(-0.3, psi_mid, prev, 0.05, 0.04)
        assert U >= 0.1 - 1e-6, f"Interval [{lo},{hi}] not feasible"
```

### Test 4: MM Table Speed
```python
def test_mm_table_speed():
    """compute_f_MM via table should be ~100x faster than Brent."""
    import time
    
    thetas = np.logspace(-6, 0, 50)
    rhos = np.linspace(0, 0.99, 50)
    
    # Table version
    start = time.perf_counter()
    for t, r in zip(thetas, rhos):
        compute_f_MM(t, r)
    table_time = time.perf_counter() - start
    
    # Brent version (sample only)
    start = time.perf_counter()
    for t, r in zip(thetas[:5], rhos[:5]):
        _compute_f_MM_brent(t, r)
    brent_time = time.perf_counter() - start
    brent_time *= 10  # Extrapolate
    
    assert table_time < brent_time / 50, f"Table {table_time:.3f}s not fast enough vs Brent {brent_time:.3f}s"
```

### Test 5: MM Table Accuracy
```python
def test_mm_table_accuracy():
    """Table interpolation matches Brent within 1e-6."""
    for theta in [1e-4, 1e-3, 0.01, 0.1, 0.5, 1.0]:
        for rho in [0.0, 0.3, 0.7, 0.95]:
            table_val = compute_f_MM(theta, rho)
            brent_val = _compute_f_MM_brent(theta, rho)
            assert abs(table_val - brent_val) < 1e-6, \
                f"Mismatch at θ={theta}, ρ={rho}: table={table_val}, brent={brent_val}"
```

---

## Integration Check

```bash
pytest tests/test_constraints.py -v -x
pytest tests/test_solver.py -v -x  # Uses constraints
```

---

## Commit

```bash
git add essvi/constraints.py tests/test_constraints.py
git commit -m "constraints: fix P0-4 Pasquazzi Case A, P1-1 multi-interval corridor, P1-5 MM table precompute (thermo_3 T3_A4_constraints; tests pass)"
```

---

## Failure Protocol

If stuck after 3 attempts:
1. Write `fails/T3_A4_constraints_<test>.md`
2. Include: theta values, rho values, Case A/B/C classification, interval boundaries, table vs Brent values