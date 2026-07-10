# Agent T3_A9_tests — Test Suite Rewrite (Validate CORRECT Math)

**Campaign:** thermo_3  
**Phase:** 3 (Parallel Group C — Can start after Phase 1)  
**Files:** `tests/test_*.py` (multiple)  
**Depends On:** All fixes from T3_A1 through T3_A8  
**Issues:** All tests currently validate BUGGY behavior — must rewrite

---

## Context

**All 158 tests currently pass** — but they test the WRONG math:
- `test_anchor.py` tests `compute_theta_star` (inverse function)
- `test_objective.py` tests `var_vega2` with inverted weights
- `test_constraints.py` tests HM calendar, not Pasquazzi Case A
- `test_loader.py` mocks DB with computed columns pre-populated
- `test_solver.py` uses coarse rho grid from fixture

After P0/P1 fixes, these tests will FAIL. This agent rewrites them to validate **correct** math.

---

## Test Rewrite Plan

### 1. `tests/test_anchor.py` — Anchor Extraction & Theta Formula

**DELETE tests for:** `compute_theta_star`

**ADD tests for:**
```python
def test_extract_anchor_params_independent_of_rho_psi():
    """Anchor (k*, θ*) identical regardless of candidate params."""
    df = make_slice_with_belly()
    
    anchor1 = extract_anchor_params(df)
    anchor2 = extract_anchor_params(df)  # No args!
    
    assert anchor1.k_star == anchor2.k_star
    assert anchor1.theta_star == anchor2.theta_star


def test_theta_from_psi_exact_corbetta_formula():
    """θ = θ* - ρψk* + ψ²k*²(1-ρ²)/(4θ*) — exact match."""
    anchor = AnchorParams(k_star=0.05, theta_star=0.04, quality=1.0, n_belly=5)
    
    psi, rho = 0.5, -0.3
    theta = compute_theta_t(psi, rho, anchor)
    
    expected = (anchor.theta_star 
                - rho * psi * anchor.k_star
                + psi**2 * anchor.k_star**2 * (1 - rho**2) / (4 * anchor.theta_star))
    
    assert abs(theta - expected) < 1e-12


def test_slice_passes_through_atm_for_all_rho_psi():
    """w(k*) = θ* exactly for ANY (ρ, ψ) — anchor enforcement."""
    anchor = AnchorParams(k_star=0.02, theta_star=0.035, quality=1.0, n_belly=5)
    
    for rho in [-0.8, -0.3, 0.0, 0.3, 0.8]:
        for psi in [0.1, 0.3, 0.6, 1.0]:
            theta_t = compute_theta_t(psi, rho, anchor)
            phi = psi / theta_t
            
            w_at_kstar = w_slice(np.array([anchor.k_star]), theta_t, phi, rho)[0]
            
            assert abs(w_at_kstar - anchor.theta_star) < 1e-10, \
                f"Failed ρ={rho}, ψ={psi}: w(k*)={w_at_kstar}, θ*={anchor.theta_star}"
```

### 2. `tests/test_objective.py` — Variance-Space Vega Weights

**DELETE tests for:** `1/vega²` weights

**ADD tests for:**
```python
def test_var_vega2_weights_atm_heavy():
    """Variance-space vega² weights ATM HIGHER than wings."""
    k_arr = np.array([-0.5, -0.1, 0.0, 0.1, 0.5])
    T = 0.1
    sigma = np.array([0.4, 0.35, 0.3, 0.35, 0.4])  # Smile: ATM lowest σ
    w_arr = sigma**2 * T
    vega_arr = np.array([0.05, 0.08, 0.10, 0.08, 0.05])  # ATM highest vega
    
    weights = _compute_weights(w_arr, vega_arr, T, "var_vega2")
    
    # ATM (index 2) should have highest weight
    assert weights[2] == pytest.approx(max(weights), rel=1e-6)
    # Wings should be lower
    assert weights[0] < weights[2]
    assert weights[4] < weights[2]


def test_var_vega2_formula_correct():
    """ν_var = ν_vol / (2σ√T) = ν_vol / (2√(wT))."""
    w = 0.04  # σ²T = 0.04, T=0.1 → σ=0.632
    T = 0.1
    vega_vol = 0.1
    
    sigma = np.sqrt(w / T)
    nu_var = vega_vol / (2 * sigma * np.sqrt(T))
    expected_weight = nu_var**2
    
    weight = _compute_weights(np.array([w]), np.array([vega_vol]), T, "var_vega2")[0]
    
    assert weight == pytest.approx(expected_weight, rel=1e-10)


def test_objective_convex_in_psi():
    """For fixed ρ, objective convex in ψ."""
    anchor = make_anchor()
    rho = -0.3
    k_arr, w_arr, vega_arr, T = make_slice_data()
    
    psi_vals = np.linspace(0.1, 1.0, 20)
    objs = [objective_slice(psi, rho, k_arr, w_arr, vega_arr, T,
                            anchor.theta_star, anchor.k_star)
            for psi in psi_vals]
    
    # Second differences should be positive (convex)
    second_diff = np.diff(objs, 2)
    assert np.all(second_diff > -1e-10), "Objective not convex in ψ"
```

### 3. `tests/test_constraints.py` — Pasquazzi Case A + Multi-Interval + MM Table

**DELETE tests for:** Old HM-only calendar

**ADD tests for:**
```python
# --- Pasquazzi Case A ---
def test_pasquazzi_case_A_feasible_rho_zero():
    """Case A(i): Θ≈1, ρ₁=ρ₂=0, Φ≥1 → feasible."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, 0.0, 0.04001, 0.25, 0.0)
    assert feasible
    assert "Case A(i)" in reason


def test_pasquazzi_case_A_feasible_equal_rho():
    """Case A(ii): Θ≈1, ρ₁=ρ₂≠0, Φ=1 → feasible."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, -0.3, 0.04001, 0.20005, -0.3)
    assert feasible
    assert "Case A(ii)" in reason


def test_pasquazzi_case_A_infeasible_rho_mismatch():
    """Case A: Θ≈1, ρ₁≠ρ₂, not both zero → INFEASIBLE."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, -0.3, 0.04001, 0.25, 0.2)
    assert not feasible
    assert "INFEASIBLE" in reason


# --- Corridor Multi-Interval ---
def test_corridor_returns_multiple_intervals():
    """U_ψ dips below L_ψ then above → two intervals."""
    prev = make_prev_slice_with_valley()
    intervals = find_feasible_psi_intervals(-0.3, prev, 0.05, 0.04, L_psi=0.1)
    
    assert len(intervals) >= 2
    for lo, hi in intervals:
        assert hi > lo + 1e-8


# --- MM Table ---
def test_mm_table_speed():
    """Table lookup >> Brent."""
    import time
    
    thetas = np.logspace(-6, 0, 20)
    rhos = np.linspace(0, 0.99, 20)
    
    t0 = time.perf_counter()
    for t, r in zip(thetas, rhos):
        compute_f_MM(t, r)
    t_table = time.perf_counter() - t0
    
    t0 = time.perf_counter()
    for t, r in zip(thetas[:2], rhos[:2]):
        _compute_f_MM_brent(t, r)
    t_brent = (time.perf_counter() - t0) * 100  # Extrapolate
    
    assert t_table * 50 < t_brent, f"Table {t_table:.4f}s not 50x faster than Brent {t_brent:.4f}s"


def test_mm_table_accuracy():
    """Table matches Brent within 1e-6."""
    for theta in [1e-4, 1e-3, 0.01, 0.1, 0.5]:
        for rho in [0.0, 0.3, 0.7, 0.95]:
            table = compute_f_MM(theta, rho)
            brent = _compute_f_MM_brent(theta, rho)
            assert abs(table - brent) < 1e-6
```

### 4. `tests/test_loader.py` — Mock Only DB Columns

**DELETE tests for:** Mock with computed columns pre-filled

**ADD tests for:**
```python
def test_loader_computes_mid_price():
    """mid_price = (bid + ask) / 2."""
    df = mock_db_rows(1)[0]
    df["bid"] = 2.4
    df["ask"] = 2.6
    df["option_mid"] = 2.5  # DB has this
    
    loaded = load_minute_slice(mock_conn, ts, "AMD")
    
    assert "mid_price" in loaded.columns
    assert loaded["mid_price"].iloc[0] == 2.5


def test_loader_computes_rel_spread():
    """rel_spread = spread / mid_price."""
    df = mock_db_rows(1)[0]
    df["spread"] = 0.2
    df["option_mid"] = 2.5
    
    loaded = load_minute_slice(mock_conn, ts, "AMD")
    
    assert "rel_spread" in loaded.columns
    assert loaded["rel_spread"].iloc[0] == pytest.approx(0.2 / 2.5)


def test_loader_computes_anchor_params_per_slice():
    """anchor_k_star, anchor_theta_star, anchor_quality computed per expiration."""
    # Multiple strikes per expiration
    rows = mock_db_rows(10)
    for i, row in enumerate(rows):
        row["expiration"] = date(2024, 1, 19) if i < 5 else date(2024, 1, 26)
        row["strike"] = 140 + i
        row["implied_vol"] = 0.35 + 0.01 * i
        row["business_t"] = 0.05
    
    loaded = load_minute_slice(mock_conn, ts, "AMD")
    
    for exp in loaded["expiration"].unique():
        slice_df = loaded[loaded["expiration"] == exp]
        
        assert "anchor_k_star" in slice_df.columns
        assert "anchor_theta_star" in slice_df.columns
        assert "anchor_quality" in slice_df.columns
        assert slice_df["anchor_k_star"].nunique() == 1  # Same per slice
        assert slice_df["anchor_theta_star"].nunique() == 1


def test_loader_db_columns_only():
    """_REQUIRED_DB_COLUMNS matches actual DB schema (no computed)."""
    from essvi.loader import _REQUIRED_DB_COLUMNS
    
    expected_db = {
        "ts", "underlying", "expiration", "strike", "option_type",
        "spot_price", "forward_price", "implied_vol", "option_mid", "spread",
        "vega", "bid", "ask", "delta",
        "r", "q", "business_t", "dte_calendar", "log_moneyness",
        "open_interest", "quality_flags", "ingest_run_id", "underlying_timestamp"
    }
    
    assert set(_REQUIRED_DB_COLUMNS) == expected_db
```

### 5. `tests/test_solver.py` — Anchor Called Once, Fine Grid

**DELETE tests for:** Coarse rho grid from fixture

**ADD tests for:**
```python
def test_solver_extracts_anchor_once():
    """extract_anchor_params called once per slice."""
    call_count = 0
    
    def counting_extract(df):
        nonlocal call_count
        call_count += 1
        return AnchorParams(k_star=0.0, theta_star=0.04, quality=1.0, n_belly=5)
    
    with patch('essvi.solver.extract_anchor_params', counting_extract):
        solve_single_slice(make_slice(), None, cfg)
    
    assert call_count == 1


def test_solver_uses_fine_rho_grid():
    """Explicit step=0.01 for precision tests."""
    grid = build_rho_grid(None, step=0.01, lo=-0.99, hi=0.99)
    assert len(grid) == 199
    assert abs(grid[0] + 0.99) < 1e-10
    assert abs(grid[-1] - 0.99) < 1e-10


def test_solver_theta_from_psi_exact():
    """Solver computes θ via theta_from_psi for each ψ."""
    anchor = AnchorParams(k_star=0.05, theta_star=0.04, quality=1.0, n_belly=5)
    
    with patch('essvi.solver.extract_anchor_params', return_value=anchor):
        with patch('essvi.solver.theta_from_psi') as mock_theta:
            mock_theta.return_value = 0.045
            
            solve_single_slice(make_slice(), None, cfg)
            
            assert mock_theta.call_count > 0
            for call in mock_theta.call_args_list:
                psi, rho, k_star, theta_star = call[0]
                assert k_star == 0.05
                assert theta_star == 0.04
```

### 6. `tests/test_sequential.py` — Pre-Loop C1 Check

```python
def test_pre_loop_c1_triggers_degeneracy():
    """Theta drop triggers handler BEFORE rho grid."""
    call_count = 0
    
    def counting_solve(df, prev, cfg):
        nonlocal call_count
        call_count += 1
        return make_params()
    
    slices = [
        make_slice(theta_star=0.04),
        make_slice(theta_star=0.035),  # DROP
        make_slice(theta_star=0.045),
    ]
    
    with patch('essvi.sequential.solve_single_slice', counting_solve):
        with patch('essvi.sequential.handle_theta_projection') as mock_degen:
            mock_degen.return_value = make_params(degenerate=True)
            
            calibrate_surface(slices, cfg)
    
    assert call_count == 1  # Only first slice


def test_normal_monotonic_runs_full_grid():
    """Increasing theta runs normal solver."""
    slices = [make_slice(theta_star=0.04 + i*0.005) for i in range(5)]
    
    with patch('essvi.sequential.solve_single_slice') as mock_solve:
        mock_solve.return_value = make_params()
        
        calibrate_surface(slices, cfg)
    
    assert mock_solve.call_count == 5
```

### 7. `tests/test_surface.py` — Tail Cap + Long Extrap

```python
def test_tail_slope_capped_at_2():
    """Right tail slope ≤ 2 (Lee bound)."""
    params = make_params_dict()
    T = 0.1
    
    k = np.array([K_MAX + 1, K_MAX + 10, K_MAX + 100])
    w = w_surface(k, T, params)
    
    slopes = np.diff(w) / np.diff(k)
    assert np.all(slopes <= 2.0 + 1e-10)


def test_long_extrapolation_theta_last_slope():
    """T > T_N: θ uses last segment slope."""
    maturities = [0.1, 0.2, 0.5]
    thetas = [0.03, 0.04, 0.045]  # Last slope = (0.045-0.04)/(0.5-0.2) = 0.0167
    
    theta_1yr = extrapolate_long_theta(1.0, maturities, thetas)
    expected = 0.045 + 0.0167 * (1.0 - 0.5)
    
    assert abs(theta_1yr - expected) < 1e-6


def test_long_extrapolation_psi_rho_flat():
    """T > T_N: ψ, ρ flat."""
    params = make_params_dict()
    T_N = max(params.keys())
    T = T_N + 1.0
    
    theta, psi, rho = get_params_at_T(T, params)
    
    assert psi == params[T_N].psi
    assert rho == params[T_N].rho
```

---

## Validation Commands

```bash
# Run all rewritten tests
pytest tests/test_anchor.py tests/test_objective.py tests/test_constraints.py \
       tests/test_loader.py tests/test_solver.py tests/test_sequential.py \
       tests/test_surface.py -v -x

# Full suite
pytest essvi/ -v --tb=short -q

# Config validation
python -c "from essvi.config import validate; validate()"

# Import check
python -c "from essvi.runtime import calibrate_minute; print('OK')"
```

---

## Commit

```bash
git add tests/
git commit -m "tests: rewrite all tests to validate CORRECT math (thermo_3 T3_A9_tests; tests pass)"
```

---

## Failure Protocol

If any test fails after 3 fixes:
1. Write `fails/T3_A9_tests_<test_name>.md`
2. Include: test name, expected vs actual, which fix broke it
3. Stop — human review needed