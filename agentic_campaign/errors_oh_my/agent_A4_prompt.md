# Agent A4 Prompt — Corridor Construction & Empty Corridor Handling

## Role: Quant Developer

## Mission
Fix the corridor construction algorithm to use exact θ_t(ψ) from Agent A3, handle ψ-dependent bounds correctly, and fix the empty-corridor fallback order.

## Required Reading (MANDATORY)
1. **Corbetta et al. (2019)** — Section 3 (Implementation), Section 2.3 (Going forward calibration) — Algorithm & corridor logic
2. **Agent A3's exact θ_t(ψ) formula** — θ_t = θ*_t − ρ_t ψ_t k*_t − ψ_t² k*_t² (1 − ρ_t²) / (4 θ*_t)
3. **Agent A1's Pasquazzi calendar conditions** — for L_cal computation
4. **Agent A2's MM butterfly bounds** — for U_bf_MM computation
5. **Mingone (2022)** — Section 3 (Global parametrization) — alternative approach

## What's Wrong in the Plan

### §8 Corridor Construction (lines 216–231)
```python
# Lower bound — calendar (needs slice t-1; for t=1 there is no prior → L_cal = ε_ψ > 0)
L_cal = max( ψ_{t-1}(1−ρ_{t-1})/(1−ρ_t), ψ_{t-1}(1+ρ_{t-1})/(1+ρ_t) )   if t>1 else ε_ψ
L_ψ   = L_cal

# Upper bound — butterfly (both GJ conditions), θ_t taken at anchor θ*_t first pass
U_bf1 = 4 / (1 + |ρ_t|)
U_bf2 = 2 * sqrt( θ*_t / (1 + |ρ_t|) )
U_ψ   = min(U_bf1, U_bf2) − ε_ψ        # strict interior; ε_ψ ~ 1e-6

# Feasibility
if L_ψ > U_ψ:  ρ_t is infeasible (tight-squeeze) → skip
```

**Problems:**
1. **U_bf2 uses θ*_t (anchor), not fitted θ_t(ψ)** — but θ_t depends on ψ! The bound U_bf2(ψ) = 2√(θ_t(ψ)/(1+|ρ|)) is ψ-dependent.
2. **L_cal uses HM formula only** — must use Pasquazzi conditions from Agent A1.
3. **Corridor is [L_ψ, U_ψ] with constant bounds** — but bounds depend on ψ. Must solve L_ψ(ψ) ≤ U_ψ(ψ) for ψ.
4. **Note (line 232)** says "recompute θ_t and re-verify" — but this should be done **inside** the corridor construction, not as a post-hoc check.

### §14 Empty Corridor Handling (lines 329–330)
```
(a) widen ρ-grid to full [−0.99,0.90] ignoring Δρ_max for this slice only
(b) if still empty, carry previous minute's slice params with STALE_SLICE flag
(c) if at open with no prior, drop slice and KILL
```
**Problem**: Order is wrong. If corridor empty because θ*_t < θ_{t-1} (calendar level violation), widening ρ-grid **won't help** — the level violation is independent of ρ. Must check calendar level degeneracy FIRST.

### §8 Note (3) (line 232)
"Calendar level (C1) is checked twice: as a fast precondition θ*_t ≥ θ_{t-1} before the ρ loop... and exactly on the fitted θ_t in the §12 audit"
- The precondition should **trigger degeneracy handling** (§14), not just skip to next slice.
- The audit KILL is too late — should prevent fitting entirely.

## What to Fix — Deliverables

### 1. Rewrite §8 Corridor Algorithm Completely

**New §8 content:**

```
## 8. Constructing the No-Arbitrage Corridor [L_ψ, U_ψ] for Given ρ_t

For each candidate ρ_t in the ρ-grid, we compute the feasible ψ interval using the EXACT fitted θ_t(ψ) and the FULL Pasquazzi calendar conditions.

### 8.1 Exact θ_t(ψ) from Anchor (Agent A3)
```
θ_t(ψ) = θ*_t − ρ_t ψ k*_t − ψ² k*_t² (1 − ρ_t²) / (4 θ*_t)
```
Require θ_t(ψ) > 0 for validity.

### 8.2 Lower Bound L_ψ(ψ) — Calendar Arbitrage (Pasquazzi)

For t = 1 (first slice): L_ψ = ε_ψ.

For t > 1: Given locked previous slice (θ_prev, ρ_prev, ψ_prev), find minimum ψ such that NO calendar arbitrage with current slice (θ_t(ψ), ρ_t, ψ).

Define:
```
Θ(ψ) = θ_t(ψ) / θ_prev
Φ(ψ) = (ψ / θ_t(ψ)) / (ψ_prev / θ_prev) = ψ θ_prev / (ψ_prev θ_t(ψ))
```

The Pasquazzi conditions give the feasible ψ region. The lower bound L_ψ is the **infimum** of this region.

**Algorithm for L_ψ (per ρ_t):**
```
def compute_L_psi(rho_t, prev_slice, k_star, theta_star):
    theta_prev, rho_prev, psi_prev = prev_slice
    
    # Define theta_t(psi)
    def theta_t(psi):
        return theta_star - rho_t * psi * k_star - psi*psi * k_star*k_star * (1 - rho_t*rho_t) / (4 * theta_star)
    
    def Theta(psi): return theta_t(psi) / theta_prev
    def Phi(psi): return psi * theta_prev / (psi_prev * theta_t(psi))
    
    # Check if any psi > 0 satisfies Pasquazzi
    # We need to find the minimum psi where conditions hold
    
    # The Pasquazzi feasible region depends on Theta, Phi:
    # Case A: Theta ≈ 1
    # Case B: Theta > 1, Phi ≤ 1
    # Case C: Theta > 1, Phi > 1
    
    # In practice, for sequential calibration we ENFORCE Case B (Phi ≤ 1)
    # by requiring psi/psi_prev ≤ theta_t(psi)/theta_prev
    # This gives: psi * theta_prev ≤ psi_prev * theta_t(psi)
    
    # So the calendar lower bound from Case B is:
    # psi ≥ psi_prev * max( (1-rho_prev)/(1-rho_t), (1+rho_prev)/(1+rho_t) )
    # AND psi * theta_prev ≤ psi_prev * theta_t(psi)
    
    # The second condition is psi * theta_prev ≤ psi_prev * (theta_star - rho_t psi k_star - ...)
    # This is a quadratic in psi. Solve for the minimum psi satisfying it.
    
    # Combined L_psi = max( L_cal_HM, L_theta_monotone, epsilon_psi )
```

**Simplified practical approach (matching Corbetta/Mingone sequential):**
Since Corbetta uses the HM-derived L_cal (which is Case B), we keep it but add the θ-monotonicity constraint:

```python
def corridor_bounds(rho_t, prev_slice, k_star, theta_star, config):
    if prev_slice is None:
        return EPS_PSI, compute_U_psi(rho_t, None, k_star, theta_star, config)
    
    theta_prev, rho_prev, psi_prev = prev_slice
    
    # --- Lower bound L_psi(psi) ---
    # HM calendar skew condition (Case B, valid when Phi <= 1)
    L_cal_skew = max(
        psi_prev * (1 - rho_prev) / (1 - rho_t) if rho_t < 1 else float('inf'),
        psi_prev * (1 + rho_prev) / (1 + rho_t) if rho_t > -1 else float('inf')
    )
    
    # Theta monotonicity: theta_t(psi) >= theta_prev
    # theta_star - rho_t psi k_star - psi^2 k_star^2 (1-rho_t^2)/(4 theta_star) >= theta_prev
    # This is a quadratic inequality in psi. Solve for minimum psi.
    a = k_star*k_star * (1 - rho_t*rho_t) / (4 * theta_star)
    b = rho_t * k_star
    c = theta_prev - theta_star
    # a psi^2 + b psi + c <= 0  (since we want theta_t >= theta_prev)
    # Actually: theta_t(psi) >= theta_prev
    # theta_star - b psi - a psi^2 >= theta_prev
    # a psi^2 + b psi + (theta_prev - theta_star) <= 0
    # For a > 0 (which it is), this holds between the two roots.
    # We need psi >= lower_root (since psi > 0).
    disc = b*b - 4*a*c
    if disc < 0:
        # No real root - theta_t(psi) < theta_prev for all psi
        return None, None  # Infeasible
    psi_root_low = (-b - math.sqrt(disc)) / (2*a)
    L_theta_mono = max(psi_root_low, EPS_PSI)
    
    # Combined lower bound
    L_psi = max(L_cal_skew, L_theta_mono, EPS_PSI)
    
    # --- Upper bound U_psi(psi) ---
    # Butterfly bounds depend on theta_t(psi)
    def U_psi_of_psi(psi):
        theta = theta_star - rho_t * psi * k_star - psi*psi * k_star*k_star * (1 - rho_t*rho_t) / (4 * theta_star)
        if theta <= 0:
            return -1  # Infeasible
        
        U_bf1 = 4 / (1 + abs(rho_t))
        
        if config.BUTTERFLY_BOUND_MODE == "gj_conservative":
            U_bf2 = 2 * math.sqrt(theta / (1 + abs(rho_t)))
            return min(U_bf1, U_bf2) - config.CORRIDOR_EPS
        elif config.BUTTERFLY_BOUND_MODE == "mm_exact":
            U_bf_MM = math.sqrt(mm_butterfly_bound(theta, abs(rho_t)))
            return min(U_bf1, U_bf_MM) - config.CORRIDOR_EPS
        else:  # both
            U_bf2 = 2 * math.sqrt(theta / (1 + abs(rho_t)))
            U_bf_MM = math.sqrt(mm_butterfly_bound(theta, abs(rho_t)))
            return min(U_bf1, U_bf2, U_bf_MM) - config.CORRIDOR_EPS
    
    # The feasible psi interval is where L_psi <= U_psi(psi)
    # Since U_psi(psi) is decreasing in psi (theta_t decreases with psi), 
    # the feasible region is psi in [L_psi, psi_max] where psi_max solves L_psi = U_psi(psi_max)
    
    # Find psi_max by root-finding: f(psi) = U_psi(psi) - L_psi = 0
    def f(psi):
        U = U_psi_of_psi(psi)
        if U < 0:
            return -L_psi - 1  # negative
        return U - L_psi
    
    # Bracket: f(L_psi) >= 0 (by definition L_psi <= U_psi(L_psi) if feasible)
    # f(psi) decreases, find where it crosses zero
    try:
        psi_max = brentq(f, L_psi, L_psi * 100, xtol=config.BRENT_XTOL)
    except ValueError:
        # f doesn't cross zero - check if f(L_psi) >= 0
        if f(L_psi) >= 0:
            psi_max = L_psi * 100  # effectively unbounded above (capped by U_bf1)
        else:
            return None, None  # Infeasible
    
    return L_psi, psi_max
```

### 2. Fix §14 Empty Corridor / Degeneracy Handling

**New priority order:**
```
if no rho_t yields feasible corridor:
    # 1. Check if CALENDAR LEVEL VIOLATION (theta*_t < theta_{t-1})
    if theta_star_t < theta_prev - THETA_MONOTONICITY_EPS:
        # Handle calendar degeneracy FIRST (before widening rho grid)
        handle_calendar_degeneracy(t, theta_star_t, theta_prev)
        return
    
    # 2. Try widening rho-grid to full [-0.99, 0.90] (ignore Delta_rho_max)
    if not RHO_STEP_RELAXED:
        retry_with_full_rho_grid()
        if success: return
    
    # 3. Carry previous minute's slice params
    if prior_minute_params_available:
        carry_stale_slice()
        return
    
    # 4. At session open with no prior: drop slice, KILL surface
    kill_slice_or_surface()
```

**Calendar degeneracy handler (§14):**
```python
def handle_calendar_degeneracy(t, theta_star_t, theta_prev):
    """
    theta*_t < theta_{t-1} — overnight gap or market disorder.
    Options in priority order:
    """
    # 1. Search for alternative anchor in belly with theta >= theta_prev + eps
    alt_anchor = find_anchor_with_min_theta(slice_t, theta_prev + config.THETA_MONOTONICITY_EPS)
    if alt_anchor:
        k_star_t, theta_star_t = alt_anchor
        re_run_slice_calibration(t, k_star_t, theta_star_t)  # Restart this slice
        return
    
    # 2. No belly anchor satisfies monotonicity: project theta_t = theta_prev + eps
    #    This BREAKS the anchor constraint w(k*_t) = theta*_t.
    #    Must solve constrained: min error s.t. theta_t = theta_prev + eps
    #    This is a different optimization — flag QUALITY=THETA_PROJECTED
    theta_t = theta_prev + config.THETA_MONOTONICITY_EPS
    psi_t = solve_constrained_psi(t, theta_t, rho_t_grid)  # New constrained solve
    flag_slice(t, "THETA_PROJECTED")
    return
    
    # 3. If entire front is disordered (multiple slices), KILL those slices
    if multiple_slices_disordered:
        for s in disordered_slices:
            flag_slice(s, "KILL", arb_status="CALENDAR_DEGENERACY")
```

### 3. Add to config.py

```python
# Corridor
CORRIDOR_EPS = 1e-6
EPS_PSI = 1e-8
THETA_MONOTONICITY_EPS = 1e-8

# Empty corridor
EMPTY_CORRIDOR_STRATEGY = "degeneracy_first"  # "degeneracy_first" | "widen_rho_first"
RHO_STEP_RELAXED_FLAG = "RHO_STEP_RELAXED"
STALE_SLICE_FLAG = "STALE_SLICE"
THETA_PROJECTED_FLAG = "THETA_PROJECTED"
```

## Output Format

Update `eSSVI_surface_plan (1).md` in place — replace §8 entirely, update §14. Add config. Mark with `<<A4_CHANGE>>`.

## Validation

- [ ] Corridor bounds use exact θ_t(ψ) from Agent A3
- [ ] L_ψ includes θ-monotonicity quadratic constraint
- [ ] U_ψ(ψ) uses MM or GJ bound with exact θ_t(ψ)
- [ ] Feasible ψ interval found by root-finding on U_ψ(ψ) - L_ψ
- [ ] Empty corridor: calendar level checked FIRST
- [ ] Calendar degeneracy: alternative anchor search implemented
- [ ] Calendar degeneracy: theta projection with constrained solve documented
- [ ] No infinite loops in corridor computation
- [ ] Speed: corridor computation < 1ms per ρ_t