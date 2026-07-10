# Agent A7 Prompt — Short Maturity (7 DTE) Edge Cases & Overnight Gap Handling

## Role: Quant Researcher

## Mission
Address the unique challenges of the front slice (7 DTE minimum per ingestion) and the overnight gap degeneracy handling.

## Required Reading
1. **Plan §4** — Slice universe: DTE ∈ [7, 90]
2. **Plan §5** — Anchor fallback: "if exact ATM strike fails gate: take nearest belly-qualifying strike"
3. **Plan §14** — Calendar degeneracy: overnight gap θ*_t < θ_{t-1}, "prefer nearest belly quote restoring monotonicity" (vague)
4. **Plan §19 #5** — Expiration-day handling undecided
5. **Corbetta et al. (2019)** — §5.2.1: "very short maturity where market conveys only information on θ... not on ρ and φ"
6. **dataingestion.md §5#5** — Minimum 3 strikes per slice, OI gates

## What's Wrong

### §4 Slice Universe (lines 105–108)
- DTE ∈ [7, 90]. At 7 DTE (≈0.019 yr), options have very few strikes, wide spreads, low vega.
- No special handling for "front slice is thin" — just "skip if no data".

### §5 Anchor (lines 135–138)
```
"Fallback if exact ATM strike fails gate: take nearest belly-qualifying strike...
 If no belly strike qualifies, widen gates progressively... If still none, drop slice."
```
**Problem**: At 7 DTE, belly may be empty (OI > 100, spread ≤ 0.10, |Δ| ∈ [0.10, 0.90]). "Widen gates" is vague.

### §14 Calendar Degeneracy (lines 325–330)
```
"If θ*_t < θ_{t-1}: (i) prefer nearest belly quote restoring monotonicity
 (ii) if none, enforce θ_t = max(θ*_t, θ_{t-1}+ε) (project onto calendar-admissible set)
 flag `THETA_PROJECTED`"
```
**Problems**:
- "nearest belly quote restoring monotonicity" — what if NO belly quote has θ ≥ θ_{t-1}?
- "enforce θ_t = max(θ*_t, θ_{t-1}+ε)" — breaks anchor constraint w(k*_t) = θ*_t
- No procedure for "what if θ*_t is far below θ_{t-1}?"

### §19 #5 (line 438)
"Expiration-day handling: DTE=0 slice excluded by ingestion. DTE=1 slice: include? widen gates? flag? — Decision required"

## What to Fix — Deliverables

### 1. Add §4.1 Short-Maturity Slice Handling (New Section)

```
## 4.1 Short-Maturity Slice Handling (DTE ≤ 14)

Front slices (7–14 DTE) have sparse strikes and noisy IVs. Special handling required:

### 4.1.1 Minimum Strike Requirements
```
MIN_STRIKES_PER_SLICE = 3  # config
```
If slice has < 3 valid strikes after filtering:
- Try widening belly criteria for anchor search only:
  - Spread ≤ 0.15 (from 0.10)
  - OI > 50 (from 100)
  - |Δ| ∈ [0.05, 0.95] (from [0.10, 0.90])
- If still < 3 strikes: trigger **ρ fallback**

### 4.1.2 ρ Fallback for Thin Slices
At very short maturities, market quotes contain little skew information (Corbetta §5.2.1).
If slice has ≥ 1 but < 3 belly strikes:
- **Option A (preferred)**: Set ρ_t = ρ_{t+1} (next maturity's ρ), solve only for ψ_t
- **Option B**: Set ρ_t = SHORT_MATURITY_RHO_PRIOR (−0.5 for equities), solve for ψ_t
- **Option C**: If only 1 strike (ATM), set θ_t = θ*_t exactly, ρ_t and ψ_t from prior/next slice

Flag: `SHORT_MATURITY_RHO_FALLBACK` with mode used.

### 4.1.3 Anchor Quality Flags
```
ANCHOR_EXACT_ATM          # k* = 0 exactly
ANCHOR_NEAREST_BELLY      # fell back to nearest belly
ANCHOR_WIDENED_GATES      # gates widened to find anchor
ANCHOR_RHO_FALLBACK       # ρ fixed, only ψ solved
```

### 2. Fix §14 Calendar Degeneracy Handling

```
## 14. Calendar Degeneracy Handling (Overnight Gap)

When θ*_t < θ_{t-1} (anchor total variance below previous slice's fitted total variance):

### Algorithm:
```python
def handle_calendar_degeneracy(slice_t, prev_slice, config):
    theta_star = slice_t.theta_star
    theta_prev = prev_slice.theta
    
    if theta_star >= theta_prev - config.THETA_MONOTONICITY_EPS:
        return  # OK
    
    # 1. Search ALL strikes in slice t for any with theta >= theta_prev + eps
    #    AND passing belly quality gates (relaxed for this search)
    alt_anchor = find_anchor_with_min_theta(
        slice_t, 
        min_theta=theta_prev + config.THETA_MONOTONICITY_EPS,
        relax_gates=True
    )
    if alt_anchor:
        slice_t.k_star, slice_t.theta_star = alt_anchor
        slice_t.flags.add("ANCHOR_RELOCATED_MONOTONE")
        # Re-run calibration for this slice with new anchor
        return
    
    # 2. No anchor satisfies monotonicity
    #    Constrained calibration: fix theta_t = theta_prev + eps
    #    Solve min error over (rho_t, psi_t) with theta_fixed
    theta_t = theta_prev + config.THETA_PROJECTION_EPS
    rho_t, psi_t = solve_constrained_slice(slice_t, theta_t, config)
    slice_t.theta = theta_t
    slice_t.rho = rho_t
    slice_t.psi = psi_t
    slice_t.flags.add("THETA_PROJECTED")
    slice_t.quality = "DEGRADED"
```

### Constrained Solve (theta fixed):
Minimize Σ W_j (w_mkt,j − w_eSSVI(k_j; θ_fixed, ρ, ψ))² over (ρ, ψ)
- 2D optimization: grid search over ρ ∈ [ρ_grid_lo, ρ_grid_hi], Brent on ψ
- Corridor bounds apply with θ = θ_fixed (no anchor constraint)
- This is a DIFFERENT problem from the normal 1D-in-ψ solve

### 3. If entire front section disordered (multiple consecutive slices with θ* < θ_prev):
- Apply degeneracy handling per slice
- If > 50% of slices DEGRADED: flag `SURFACE_DEGRADED`, consider KILL

### 3. Decide §19 #5 — Expiration-Day Handling

**Decision (add to plan):**
- DTE = 0 (expiration day): **Excluded** by ingestion (DTE < 7 filter)
- DTE = 1: **Include** but flag `EXPIRY_IMMINENT`
  - Widen corridor ε_ψ by 10× (more tolerance for noisy data)
  - Increase temporal penalty λ_temp by 10× for this slice
  - Use `SHORT_MATURITY_RHO_FALLBACK = "next_slice"` (prior may be expiring)
- DTE ∈ [2, 6]: **Excluded** by ingestion (DTE < 7)

**Rationale**: DTE=1 options have unreliable IVs but provide the front anchor. Better to include with flags than drop entirely.

### 4. Add to config.py

```python
# Short Maturity
MIN_STRIKES_PER_SLICE = 3
SHORT_MATURITY_DTE = 14
SHORT_MATURITY_RHO_FALLBACK = "next_slice"  # "next_slice" | "prior" | "fixed" | "fit_psi_only"
SHORT_MATURITY_RHO_PRIOR = -0.5

# Belly gate relaxation for anchor search
BELLY_REL_SPREAD_MAX = 0.10
BELLY_OI_MIN = 100
BELLY_DELTA_LO = 0.10
BELLY_DELTA_HI = 0.90

RELAXED_BELLY_REL_SPREAD_MAX = 0.15
RELAXED_BELLY_OI_MIN = 50
RELAXED_BELLY_DELTA_LO = 0.05
RELAXED_BELLY_DELTA_HI = 0.95

# Calendar Degeneracy
THETA_PROJECTION_EPS = 1e-6
THETA_MONOTONICITY_EPS = 1e-8

# Expiry
EXPIRY_IMMINENT_DTE = 1
EXPIRY_IMMINENT_CORRIDOR_WIDEN = 10.0
EXPIRY_IMMINENT_LAMBDA_TEMPORAL_MULT = 10.0
```

## Output Format

Update `eSSVI_surface_plan (1).md` — add §4.1, update §5, §14, §19#5. Add config. Mark `<<A7_CHANGE>>`.

## Validation

- [ ] 7 DTE SPX slice: count belly strikes with standard vs relaxed gates
- [ ] 7 DTE single-stock (AMD): test ρ fallback path
- [ ] Calendar gap overnight: test anchor relocation + projection
- [ ] DTE=1 slice: test widened corridor and temporal penalty
- [ ] Flags emitted correctly: ANCHOR_RELOCATED_MONOTONE, THETA_PROJECTED, EXPIRY_IMMINENT
- [ ] No crash when slice has 1, 2, or 0 valid strikes