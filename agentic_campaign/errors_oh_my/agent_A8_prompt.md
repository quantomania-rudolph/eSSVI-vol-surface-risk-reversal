# Agent A8 Prompt — Warm-Start Seeding, Temporal Regularization, Kill Switch Logic

## Role: Quant Developer

## Mission
Fix the warm-start seeding (can seed outside corridor), clarify the two regularization axes, and harden the kill switch with numerical tolerances.

## Required Reading
1. **Plan §11** (lines 268–282) — Warm-start, two regularizations, ρ-grid refinement
2. **Plan §12** (lines 293–310) — Kill switch, audit checks
3. **Plan §14** (lines 320–330) — Empty corridor fallback
4. **Plan §16** (lines 352–358) — Master loop Step 5 audit
5. **Corbetta et al. (2019)** — Section 3.2 (regularization), Section 3.3 (warm start)

## What's Wrong

### §11 Warm-Start (lines 270–274)
```
"warm-start from τ−1: seed each minute's Brent bracket and ρ-grid center from prior minute's locked params"
```
**Problem**: Prior minute's (ρ, ψ) may be **outside current minute's corridor** (new data → new corridor). Seeding outside corridor causes:
- Brent fails immediately (bracket invalid)
- ρ-grid center may be infeasible
- Clamping is needed but not specified

### §11 Two Regularizations (lines 275–280)
```
(A) Term-structure: λ_ρ(ρ_t − ρ_{t-1})² + λ_ψ(ψ_t − ψ_{t-1})²  # across maturities, SAME minute
(B) Temporal: λ_temp ‖(θ,ρ,ψ)_t^τ − (θ,ρ,ψ)_t^{τ-1}‖²           # across minutes, SAME maturity
```
**Problem**: Plan says "warm-start from τ-1 locked params" — this is TEMPORAL prior (B), not term-structure (A). The two are confused in the text.

### §12 Kill Switch (lines 300–304)
```
"if any check fails AT ALL, even by fractions of a decimal, flag KILL"
```
**Problem**: No numerical tolerance. g(k) = -1e-15 is numerical noise, not arbitrage. Need `KILL_TOL`.

### §16 Step 5 Audit (line 356)
"Audit runs after all slices locked")
**Problem**: If slice t fails calendar vs t-1, we know at slice t lock time, not after all slices. Should check immediately.

## What to Fix — Deliverables

### 1. Fix §11 Warm-Start Seeding

```
## 11.1 Warm-Start Seeding (Per Minute τ, Per Slice t)

For each slice t at minute τ, we need:
- ρ-grid center (for coarse grid)
- Brent bracket [L_ψ, U_ψ] for each ρ_t candidate
- Initial ψ guess for Brent

**Algorithm:**
```python
def get_warm_start(slice_t, prev_minute_params, config):
    """
    prev_minute_params: dict {t: (rho, psi, theta)} from minute τ-1
    Returns: (rho_center, psi_seeds, corridor_bounds)
    """
    # 1. Get prior minute's params for THIS slice
    if slice_t.idx in prev_minute_params:
        rho_prior, psi_prior, theta_prior = prev_minute_params[slice_t.idx]
    else:
        rho_prior, psi_prior = None, None
    
    # 2. Compute CURRENT corridor at candidate rho values
    #    We'll evaluate corridor at rho_prior (clamped to grid) and at grid center
    
    rho_candidates = []
    if rho_prior is not None:
        # Clamp prior rho to current grid bounds
        rho_candidate = np.clip(rho_prior, config.RHO_GRID_LO, config.RHO_GRID_HI)
        rho_candidates.append(rho_candidate)
    
    # Always include grid center
    rho_center = (config.RHO_GRID_LO + config.RHO_GRID_HI) / 2
    rho_candidates.append(rho_center)
    
    # 3. For each candidate, compute corridor and check if prior psi feasible
    best_rho_center = rho_center
    best_psi_seed = None
    
    for rho_cand in rho_candidates:
        L_psi, U_psi = compute_corridor_bounds(rho_cand, slice_t, prev_slice, config)
        
        if L_psi <= U_psi:  # Feasible
            if rho_prior is not None and abs(rho_cand - rho_prior) < 1e-6:
                # Prior rho feasible — check prior psi
                if L_psi - config.WARMSTART_PSI_TOL <= psi_prior <= U_psi + config.WARMSTART_PSI_TOL:
                    best_psi_seed = np.clip(psi_prior, L_psi, U_psi)
                else:
                    # Prior psi outside corridor — seed at midpoint
                    best_psi_seed = (L_psi + U_psi) / 2
            else:
                # Seed at corridor midpoint
                best_psi_seed = (L_psi + U_psi) / 2
            
            best_rho_center = rho_cand
            break  # Use first feasible candidate
    
    if best_psi_seed is None:
        # No feasible corridor at any candidate — will trigger fallback
        best_psi_seed = config.EPS_PSI
    
    return best_rho_center, best_psi_seed
```

**Key principle**: Always clip prior params to CURRENT corridor. Never seed outside feasible region.

### 2. Clarify §11 Regularization Split

```
## 11.2 Regularization — Two Distinct Axes

### (A) Term-Structure Regularization (Within Same Minute τ)
Penalizes roughness ACROSS MATURITIES in the current surface:
```
λ_ρ Σ_t (ρ_t − ρ_{t-1})² + λ_ψ Σ_t (ψ_t − ψ_{t-1})²
```
- Applied during outer ρ-grid ranking (added to loss per slice)
- λ_ρ, λ_ψ control how "smooth" the term structure is
- **Always active** during calibration

### (B) Temporal Regularization (Across Minutes τ-1 → τ)
Penalizes large jumps FROM PREVIOUS MINUTE for SAME maturity:
```
λ_temp Σ_t ‖(θ, ρ, ψ)_t^τ − (θ, ρ, ψ)_t^{τ-1}‖²
```
- Implemented as a **prior** in the objective (like Tikhonov)
- Can be **disabled at session open** (COLD_START_AT_SESSION_OPEN = True)
- Applied in `regularize.py` as an additive term to the inner objective

**Config:**
```python
TEMPORAL_REG_MODE = "tikhonov"  # "tikhonov" | "warmstart_only" | "none"
LAMBDA_TEMPORAL = 0.01
```

### 3. Harden §12 Kill Switch

```
## 12. Kill Switch — Numerical Tolerance & Logging

All arithmetic checks use tolerance KILL_TOL = 1e-10:

### Butterfly Audit (g(k) ≥ 0):
if g(k) < -KILL_TOL:  FAIL
if g(k) < 0:          WARN "near-zero butterfly" (log only)

### Calendar Audit (Pasquazzi conditions):
if Θ < 1 - KILL_TOL:  FAIL
if Θ*Φ < 1 - KILL_TOL:  FAIL
if |ΘΦρ₂ − ρ₁| > ΘΦ + KILL_TOL:  FAIL
# ... (full Pasquazzi check from Agent A1)

### Vertical Spread / Slope (Roper):
if w'(k) < -KILL_TOL or w'(k) > 1 + KILL_TOL: FAIL

### Logging on FAIL:
log_kill(slice_idx, check_name, value, tolerance, surface_snapshot)

### Surface Snapshot:
On KILL, emit the last GOOD surface (τ_last_good) with:
- staleness_minutes = τ - τ_last_good
- reason = "KILL: butterfly violation on slice 3 at k=0.5 (g=-1.2e-8)"
```

### 4. Add In-Calibration Calendar Check (§4 Loop)

```
## 4. Calibration Loop — Add Calendar Check After Slice Lock

For t = 1 to N:
    ...
    Lock slice t (θ_t, ρ_t, ψ_t)
    
    # IMMEDIATE calendar check vs t-1
    if t > 1:
        ok, msg = check_calendar_arbitrage(slice_{t-1}, slice_t, config.KILL_TOL)
        if not ok:
            # Trigger fallback BEFORE proceeding to t+1
            handle_calendar_failure_during_calibration(t, msg)
            # handle_calendar_failure_during_calibration:
            #   - Try widening ρ-grid for slice t
            #   - If fails, mark slice t STALE, carry prior minute
            #   - If still fails, KILL surface
```

### 5. Add to config.py

```python
# Warm-Start
WARMSTART_CLIP_TO_CORRIDOR = True
WARMSTART_PSI_TOL = 1e-6
WARMSTART_RHO_TOL = 1e-6

# Kill Switch
KILL_TOL = 1e-10
KILL_LOG_DIR = "logs/kills/"

# Temporal Regularization
TEMPORAL_REG_MODE = "tikhonov"  # "tikhonov" | "warmstart_only" | "none"
LAMBDA_TEMPORAL = 0.01
COLD_START_AT_SESSION_OPEN = True
```

## Output Format

Update `eSSVI_surface_plan (1).md` — §11, §12, §16 (add in-calibration check). Add config. Mark `<<A8_CHANGE>>`.

## Validation

- [ ] Prior minute's (ρ, ψ) clipped to current corridor before seeding
- [ ] If prior params infeasible, seed at corridor midpoint
- [ ] Term-structure penalty (λ_ρ, λ_ψ) distinct from temporal (λ_temp)
- [ ] Temporal penalty only across minutes, not maturities
- [ ] KILL_TOL = 1e-10 used in all audits
- [ ] Kill logs contain slice, check, value, tolerance, surface snapshot
- [ ] Calendar check runs immediately after slice lock, not at end
- [ ] COLD_START_AT_SESSION_OPEN disables temporal penalty for first minute