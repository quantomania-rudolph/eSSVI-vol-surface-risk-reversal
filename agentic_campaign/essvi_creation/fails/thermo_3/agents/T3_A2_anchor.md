# Agent T3_A2_anchor — Anchor Inversion Fix

**Campaign:** thermo_3  
**Phase:** 1 (Sequential — After T3_A1_loader)  
**File:** `essvi/anchor.py`  
**Depends On:** T3_A8_config, T3_A1_loader  
**Issues:** P0-1 (Anchor Inversion — Core Math Bug)

---

## Context

**THIS IS THE MOST CRITICAL MATHEMATICAL FIX.** The anchor function is inverted — it computes θ* from θ instead of θ from (ψ, ρ). This breaks the entire calibration: every (ρ, ψ) candidate gets a different anchor θ, so the surface never passes through the market ATM point.

---

## Research: Exact Anchor Formula (Blueprint §5, Corbetta 2019 §3.2)

**Locked Convention (eSSVI):**
```
w(k, T) = θ/2 * (1 + ρ φ k + sqrt((φ k + ρ)² + (1 - ρ²)))
ψ = θ · φ
```

**Anchor Point:** `(k*_t, θ*_t)` where `θ*_t = σ*² · T` from market ATM option.

**Exact Closed-Form (Corbetta Eq 3.12, Blueprint §5):**
```
θ_t = θ*_t - ρ_t ψ_t k*_t + ψ_t² k*_t² (1 - ρ_t²) / (4 θ*_t)
```

**This is THE formula.** Given market anchor `(k*_t, θ*_t)` and candidate `(ρ_t, ψ_t)`, compute `θ_t` **exactly** — no iteration, no root-finding.

---

## Current Bug in `essvi/anchor.py`

**Lines ~176-181 — `compute_theta_star` (WRONG — inverse function):**
```python
def compute_theta_star(w_star, k_star, phi, rho) -> float:
    """
    WRONG: Computes θ* from w*, k*, φ, ρ.
    But w* = θ*_t IS the market anchor! We don't solve for it.
    """
    u = phi * k_star + rho
    d = u * u + (1.0 - rho * rho)
    denom = 1.0 + rho * phi * k_star + np.sqrt(d)
    return float(2.0 * w_star / denom)  # Returns θ from θ* — INVERTED
```

**Lines ~199-220 — `extract_anchor_params` (CALLS the wrong function):**
```python
def extract_anchor_params(df_slice, phi, rho) -> AnchorParams:
    # ...
    k_star = select_belly_strike(df_slice)  # finds k*
    w_star = select_atm_variance(df_slice)  # finds θ* = σ*²T
    
    # BUG: Calls inverse function with φ, ρ — returns DIFFERENT θ for each candidate!
    theta_star = compute_theta_star(w_star, k_star, phi, rho)
    
    return AnchorParams(k_star=k_star, theta_star=theta_star, ...)
```

**Correct Function EXISTS in `constraints.py:24-35` but is NEVER USED:**
```python
# constraints.py — CORRECT, but unused by solver
def theta_from_psi(psi, rho, k_star, theta_star) -> float:
    return (
        theta_star
        - rho * psi * k_star
        + psi * psi * k_star * k_star * (1.0 - rho * rho) / (4.0 * theta_star)
    )
```

---

## Required Changes to `essvi/anchor.py`

### 1. Remove/Replace `compute_theta_star` (Lines ~176-181)

**DELETE this function entirely.** It is the inverse of what we need.

### 2. Rewrite `extract_anchor_params` (Lines ~199-220)

**New Signature — NO φ, ρ parameters:**
```python
@dataclass(frozen=True)
class AnchorParams:
    k_star: float          # Belly strike (log-moneyness)
    theta_star: float      # Market ATM total variance θ*_t = σ*²T
    quality: float         # Belly quality metric
    n_belly: int           # Number of strikes in belly region
    # NO theta, phi, rho here — those are per-candidate
```

**New Implementation:**
```python
def extract_anchor_params(df_slice: pd.DataFrame) -> AnchorParams:
    """
    Extract market anchor (k*_t, θ*_t) from a single expiration slice.
    
    Anchor is INDEPENDENT of (ρ, ψ) — computed ONCE per slice.
    The solver will compute θ_t(ψ, ρ) using constraints.theta_from_psi.
    """
    # 1. Filter to OTM options (or belly region)
    belly = df_slice[df_slice["belly_flag"]].copy()
    
    if len(belly) < 3:
        # Fallback: use all OTM
        belly = df_slice[df_slice["OTM"]].copy()
    
    if len(belly) == 0:
        raise AnchorExtractionError("No OTM/belly options in slice")
    
    # 2. k* = strike minimizing |log_moneyness| (closest to forward)
    k_star_idx = belly["log_moneyness"].abs().idxmin()
    k_star = float(belly.loc[k_star_idx, "log_moneyness"])
    
    # 3. θ* = σ*² · T at k* (interpolate if needed)
    # For exact ATM, use the option at k_star
    row = belly.loc[k_star_idx]
    iv = float(row["implied_vol"])
    T = float(row["business_t"])
    theta_star = iv * iv * T  # θ* = σ*² T
    
    # 4. Quality metrics
    n_belly = len(belly)
    avg_spread = float(belly["rel_spread"].mean())
    quality = 1.0 / (1.0 + avg_spread) * np.log1p(n_belly)
    
    return AnchorParams(
        k_star=k_star,
        theta_star=theta_star,
        quality=float(quality),
        n_belly=n_belly
    )
```

### 3. Add `compute_theta_t` Function (NEW — thin wrapper)

```python
def compute_theta_t(psi: float, rho: float, anchor: AnchorParams) -> float:
    """
    Compute slice parameter θ_t for given (ψ, ρ) using EXACT closed form.
    
    Delegates to constraints.theta_from_psi to ensure single source of truth.
    """
    from essvi.constraints import theta_from_psi
    return theta_from_psi(psi, rho, anchor.k_star, anchor.theta_star)
```

### 4. Update Imports & Exports

```python
__all__ = [
    "AnchorParams",
    "extract_anchor_params",
    "compute_theta_t",
    "AnchorExtractionError",
]
```

---

## Required Changes to `essvi/solver.py` (Call Site — P0-1)

**File:** `essvi/solver.py`  
**Function:** `_evaluate_at_phi` (Line ~111)

**Current (BUGGY):**
```python
def _evaluate_at_phi(phi, rho, df_slice, anchor_params, ...):
    # anchor_params comes from extract_anchor_params(df_slice, phi, rho)
    # which already computed WRONG theta_star using phi, rho
    theta_t = anchor_params.theta_star  # This is WRONG - varies with phi, rho
    ...
```

**Fixed:**
```python
def _evaluate_at_phi(phi, rho, df_slice, anchor: AnchorParams, ...):
    """
    anchor is from extract_anchor_params(df_slice) — NO phi, rho passed.
    Compute θ_t for THIS (ψ, ρ) candidate using EXACT closed form.
    """
    psi = anchor.theta_star * phi  # ψ = θ* · φ (using θ* as initial θ approx? No...)
    
    # Wait: ψ = θ_t · φ, but we don't know θ_t yet!
    # The blueprint uses ψ = θ_t · φ convention. But θ_t depends on ψ.
    # Corbetta: iterate on ψ, compute θ(ψ), then φ = ψ/θ.
    # Or: outer loop on ρ, inner on φ, with ψ = φ · θ(ψ).
    
    # Correct approach (Blueprint §9, Corbetta §3.2):
    # For fixed ρ, solve for ψ using Brent on objective.
    # At each ψ candidate: θ = theta_from_psi(ψ, ρ, k*, θ*)
    # Then φ = ψ / θ, evaluate w_slice.
    
    # So _evaluate_at_phi should really be _evaluate_at_psi
    # But keeping signature for now:
    theta_t = compute_theta_t(psi, rho, anchor)
    phi_computed = psi / theta_t if theta_t > 0 else phi
    ...
```

**Note:** The solver's inner loop structure may need adjustment to match Corbetta's algorithm (solve for ψ, not φ). But minimal fix: use `compute_theta_t` with anchor.

---

## Tests Required (`tests/test_anchor.py`)

**Rewrite existing tests to validate CORRECT math:**

### Test 1: Anchor Extraction Independence
```python
def test_anchor_independent_of_rho_psi():
    """Anchor (k*, θ*) must be identical regardless of candidate (ρ, ψ)."""
    df = make_slice_fixture()
    
    anchor1 = extract_anchor_params(df)
    anchor2 = extract_anchor_params(df)  # No args!
    
    assert anchor1.k_star == anchor2.k_star
    assert anchor1.theta_star == anchor2.theta_star
```

### Test 2: Exact Closed-Form Theta Matches Corbetta
```python
def test_theta_from_psi_exact_formula():
    """θ_t = θ* - ρψk* + ψ²k*²(1-ρ²)/(4θ*) — exact, no iteration."""
    anchor = AnchorParams(k_star=0.05, theta_star=0.04, quality=1.0, n_belly=5)
    
    # Test case from Corbetta Table 1 (or known values)
    psi = 0.5
    rho = -0.3
    
    theta_t = compute_theta_t(psi, rho, anchor)
    
    # Manual computation
    expected = (anchor.theta_star 
                - rho * psi * anchor.k_star
                + psi**2 * anchor.k_star**2 * (1 - rho**2) / (4 * anchor.theta_star))
    
    assert abs(theta_t - expected) < 1e-12
```

### Test 3: Anchor Passes Through Market ATM
```python
def test_slice_passes_through_atm():
    """For ANY (ρ, ψ), w(k*) = θ* exactly (by construction)."""
    anchor = AnchorParams(k_star=0.02, theta_star=0.035, quality=1.0, n_belly=5)
    
    for rho in [-0.8, -0.3, 0.0, 0.3, 0.8]:
        for psi in [0.1, 0.3, 0.6, 1.0]:
            theta_t = compute_theta_t(psi, rho, anchor)
            phi = psi / theta_t
            
            # Evaluate eSSVI at k*
            w_at_kstar = w_slice(anchor.k_star, theta_t, phi, rho)
            
            assert abs(w_at_kstar - anchor.theta_star) < 1e-10, \
                f"Failed for ρ={rho}, ψ={psi}: w(k*)={w_at_kstar}, θ*={anchor.theta_star}"
```

### Test 4: Old `compute_theta_star` Removed
```python
def test_compute_theta_star_removed():
    """Ensure the inverted function is gone."""
    from essvi.anchor import extract_anchor_params, compute_theta_t
    # compute_theta_star should not exist
    assert not hasattr(sys.modules['essvi.anchor'], 'compute_theta_star')
```

---

## Validation

```bash
pytest tests/test_anchor.py -v -x

# Also verify solver integration
pytest tests/test_solver.py::test_solve_single_slice_basic -v -x
```

---

## Commit

```bash
git add essvi/anchor.py essvi/solver.py tests/test_anchor.py
git commit -m "anchor: fix P0-1 inversion; extract_anchor_params now returns market (k*,θ*) independent of (ρ,ψ); solver uses exact closed-form theta_from_psi (thermo_3 T3_A2_anchor; tests pass)"
```

---

## Failure Protocol

If tests fail after 3 fixes:
1. Write `fails/T3_A2_anchor_<test>.md`
2. Include: anchor values, theta_t computed, expected vs actual, Corbetta reference
3. Stop