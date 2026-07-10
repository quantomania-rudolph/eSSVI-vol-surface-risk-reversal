# Agent A2 Prompt — Butterfly Arbitrage Bounds (GJ vs MM Conditions)

## Role: Quant Researcher

## Mission
Replace the conservative Gatheral-Jacquier (GJ) sufficient butterfly bounds with the exact Martini-Mingone (MM) necessary & sufficient bounds, while keeping GJ as a fast conservative fallback.

## Required Reading (MANDATORY — read before starting)
1. **Martini & Mingone (2022)** — *No Arbitrage SVI* / *Explicit no arbitrage domain for sub-SVIs via reparametrization* (arXiv:2106.02418) — **FULL PAPER**, especially:
   - Proposition 6.3: Exact necessary & sufficient butterfly conditions
   - Equation (2): ψ² ≤ inf_{l > l₂(|ρ|)} [4θ√(1−ρ²)h² / (θ√(1−ρ²)g² − g₂)]
   - Definitions of N(l,ρ), g, h, g₂, l₂
2. **Gatheral & Jacquier (2014)** — *Arbitrage-free SVI volatility surfaces* — **Theorem 4.2** (sufficient conditions B1, B2)
3. **Mingone (2022)** — *No arbitrage global parametrization for the eSSVI volatility surface* — **Section 2.2.1** (GJ) and **Section 2.2.2** (MM)
4. **Corbetta et al. (2019)** — **Section 2.1** (No Butterfly Arbitrage) — uses GJ bounds

## What's Wrong in the Plan (Current State)

### §7.1 Butterfly Arbitrage (lines 164–176)
- Plan enforces only GJ bounds:
  ```
  (B1) ψ(1 + |ρ|) < 4          → U_bf1 = 4 / (1 + |ρ|)
  (B2) ψ²(1 + |ρ|) / θ ≤ 4     → U_bf2 = 2√(θ / (1 + |ρ|))
  U_ψ = min(U_bf1, U_bf2)
  ```
- **Problem**: GJ bounds are **sufficient but NOT necessary**. They exclude valid arbitrage-free surfaces, especially:
  - Short maturities (high curvature)
  - Extreme skews (|ρ| → 1)
  - Cases where φ(θ) has specific functional forms
- MM (2022) provides the **exact** boundary: ψ² ≤ ℱ_MM(θ, |ρ|) where ℱ_MM ≤ ℱ_GJ (strictly tighter/wider)
- The audit (§12) evaluates g(k) on a grid (ground truth) but the corridor (§8) uses only GJ → corridor may be tighter than reality, causing empty corridors falsely

### §8 Corridor (lines 224–227)
```python
U_bf1 = 4 / (1 + |ρ_t|)
U_bf2 = 2 * sqrt( θ*_t / (1 + |ρ_t|) )
U_ψ = min(U_bf1, U_bf2) - ε_ψ
```
**Problem**: Uses θ*_t (anchor) not fitted θ_t, and uses only GJ bounds.

### §19 #1 (line 430)
Flags the weighting discrepancy but also mentions: "These are *not* identical... Decision required — recommend the Image-2 variance-space vega² form". **The butterfly bound discrepancy is also flagged but not fixed.**

## What to Fix — Deliverables

### 1. Add §7.1.1 MM Butterfly Conditions (New Subsection)

**New content after §7.1:**
```
### 7.1.1 Martini-Mingone (MM) Necessary & Sufficient Butterfly Conditions (2022)

The Gatheral-Jacquier conditions (B1, B2) are **sufficient but not necessary**. Martini & Mingone (2022, Proposition 6.3) derive the **exact** no-butterfly-arbitrage boundary for SSVI/eSSVI.

In the eSSVI parameterization w(k) = θ/2 [1 + ρ(ψ/θ)k + √((ψ/θ k + ρ)² + (1−ρ²))], the conditions are:

**Necessary (same as GJ B1):**
  ψ ≤ 4 / (1 + |ρ|)

**Necessary AND Sufficient:**
  ψ² ≤ ℱ_MM(θ, |ρ|)

where ℱ_MM(θ, |ρ|) = inf_{l > l₂(|ρ|)} [4θ√(1−ρ²) h²(l, |ρ|) / (θ√(1−ρ²) g²(l, |ρ|) − g₂(l, |ρ|))]

and:
  N(l, ρ) = √(1−ρ²) + ρ l + √(l² + 1)
  g(l, ρ) = N'(l, ρ) / 4
  h(l, ρ) = 1 − (l − ρ/√(1−ρ²)) N'(l, ρ) / (2 N(l, ρ))
  g₂(l, ρ) = N''(l, ρ) − N'(l, ρ)² / (2 N(l, ρ))
  l₂(|ρ|) = [tan(arccos(−|ρ|)/3)]⁻¹

**Properties:**
- ℱ_MM(θ, |ρ|) ≤ 4θ/(1+|ρ|) = ℱ_GJ(θ, |ρ|)  (MM bound is WIDER or equal)
- Equality holds only at specific (θ, ρ) — typically MM allows larger ψ
- For |ρ| → 1, MM bound approaches GJ bound
- For short maturities (small θ), MM bound can be significantly wider

**Implementation:**
ℱ_MM is computed by 1D minimization over l ∈ (l₂(|ρ|), L_MAX). The function is unimodal; Brent's method works well.
```

### 2. Update §8 Corridor Upper Bound

Replace lines 224–227:
```python
# Upper bound — butterfly (both GJ and MM conditions), θ_t taken at fitted θ_t(ψ)
U_bf1 = 4 / (1 + |ρ_t|)                                    # GJ B1 = MM necessary
U_bf2_GJ(ψ) = 2 * sqrt( θ_t(ψ) / (1 + |ρ_t|) )            # GJ B2, ψ-dependent via θ_t(ψ)
U_bf_MM(ψ) = sqrt( F_MM( θ_t(ψ), |ρ_t| ) )                # MM exact, ψ-dependent

# Select bound based on config
if BUTTERFLY_BOUND_MODE == "gj_conservative":
    U_ψ(ψ) = min(U_bf1, U_bf2_GJ(ψ)) - ε_ψ
elif BUTTERFLY_BOUND_MODE == "mm_exact":
    U_ψ(ψ) = min(U_bf1, U_bf_MM(ψ)) - ε_ψ
elif BUTTERFLY_BOUND_MODE == "both":
    U_ψ(ψ) = min(U_bf1, U_bf2_GJ(ψ), U_bf_MM(ψ)) - ε_ψ
```

**Note**: θ_t(ψ) comes from exact anchor solve (Agent A3). The corridor upper bound is now **ψ-dependent** — must solve L_ψ ≤ U_ψ(ψ) for ψ interval.

### 3. Update §12 Audit

Butterfly audit remains: evaluate g(k) analytically on dense k-grid (this is the ground truth). Add:
```python
# Also verify against MM bound as sanity check
if BUTTERFLY_BOUND_MODE == "mm_exact":
    assert ψ_t**2 <= F_MM(θ_t, |ρ_t|) + KILL_TOL, "MM butterfly violation"
```

### 4. Add MM Bound Computation to `constraints.py`

```python
def mm_butterfly_bound(theta: float, rho: float, l_grid_points: int = 200) -> float:
    """
    Compute F_MM(theta, |rho|) = inf_{l > l2(|rho|)} 4*theta*sqrt(1-rho^2)*h^2 / (theta*sqrt(1-rho^2)*g^2 - g2)
    """
    abs_rho = abs(rho)
    if abs_rho >= 1.0:
        return 0.0  # invalid
    
    # l2 = 1/tan(arccos(-|rho|)/3)
    l2 = 1.0 / math.tan(math.acos(-abs_rho) / 3.0)
    
    def integrand(l):
        sqrt_1mr2 = math.sqrt(1 - rho*rho)
        N = sqrt_1mr2 + rho*l + math.sqrt(l*l + 1)
        # N' = rho + l/sqrt(l^2+1)
        N_prime = rho + l / math.sqrt(l*l + 1)
        # N'' = 1/(l^2+1)^(3/2)
        N_double_prime = 1.0 / (l*l + 1)**1.5
        
        g = N_prime / 4.0
        h = 1.0 - (l - rho/sqrt_1mr2) * N_prime / (2.0 * N)
        g2 = N_double_prime - N_prime*N_prime / (2.0 * N)
        
        numerator = 4.0 * theta * sqrt_1mr2 * h * h
        denominator = theta * sqrt_1mr2 * g * g - g2
        
        if denominator <= 0:
            return float('inf')
        return numerator / denominator
    
    # Minimize over l in (l2, L_MAX)
    # Use Brent on log-scale for stability
    import scipy.optimize as opt
    result = opt.minimize_scalar(
        lambda log_l: integrand(math.exp(log_l)),
        bounds=(math.log(l2 * 1.0001), math.log(1000)),  # L_MAX = 1000
        method='bounded'
    )
    return result.fun
```

### 5. Add to config.py

```python
BUTTERFLY_BOUND_MODE = "mm_exact"              # "gj_conservative" | "mm_exact" | "both"
MM_L_GRID_POINTS = 200
MM_L2_TOL = 1e-6
MM_L_MAX = 1000.0
```

## Output Format

Update `eSSVI_surface_plan (1).md` **in place** — modify sections 7.1, 8, 12. Add new subsection 7.1.1. Add config entries. Mark all changes with `<<A2_CHANGE>>` comments.

## Validation

Before finishing, verify:
- [ ] ℱ_MM(θ, |ρ|) ≤ ℱ_GJ(θ, |ρ|) for all θ > 0, |ρ| < 1 (test on grid)
- [ ] Corbetta SPX 2018-01-08 parameters satisfy MM bound (Table 1: check each slice)
- [ ] Mingone TA35/NISUSD calibrations satisfy MM bound
- [ ] No-arbitrage surfaces from current plan have ψ² ≤ ℱ_MM (should pass)
- [ ] Some GJ-valid surfaces are MM-invalid? (should not happen — MM is wider)
- [ ] MM bound computation < 1ms per call (for corridor speed)
- [ ] Config parameter documented with valid values