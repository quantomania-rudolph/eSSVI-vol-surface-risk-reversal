# Agent A5 Prompt — Objective Function & Weighting (Variance-Space vega² vs Vol-Space vega¹)

## Role: Quant Developer

## Mission
Resolve the weighting discrepancy between the plan (variance-space vega²) and dataingestion.md (vol-space vega¹), implement the chosen scheme consistently.

## Required Reading
1. **Plan §10** (lines 240–255) — Objective function with variance-space vega² weighting (Image 2/4)
2. **dataingestion.md §0b, §9** — Vol-space vega¹ weighting
3. **Corbetta et al. (2019)** — Section 3.2 (Objective function): "weighted least-squares... with weights ω_i = ν_i²" where ν_i is Black-Scholes vega
4. **Plan §19 #1** (line 430) — "Decision required — recommend the Image-2 variance-space vega² form"

## What's Wrong

### Plan §10 (lines 240–255)
```
W_j = (ν_j)²   where ν_j = ∂C/∂σ (Black-76 vega)
Objective: min Σ W_j (w_mkt,j − w_eSSVI,j)²
```
"Variance-space, vega² weighted" — matches Corbetta Image 2/4.

### dataingestion.md §9
```
Weights: ν_j (vol-space vega¹) times belly boost
Objective: min Σ W_j (IV_mkt,j − IV_eSSVI,j)²
```
"Vol-space, vega¹ weighted" — DIFFERENT.

### The Mathematical Difference
Let w = σ²T (total variance), σ = √(w/T) (implied vol).

Black-76 vega: ν_vol = ∂C/∂σ
Variance-space vega: ν_var = ∂C/∂w = (∂C/∂σ) (∂σ/∂w) = ν_vol × (1/(2σ√T)) = ν_vol / (2√(wT))

Model error in variance space: Δw = w_mkt − w_model
Model error in vol space: Δσ = σ_mkt − σ_model ≈ Δw / (2σ√T) = Δw / (2√(wT))

Plan objective: Σ ν_var² Δw² = Σ (ν_vol² / (4wT)) Δw²
Ingestion objective: Σ ν_vol Δσ² = Σ ν_vol (Δw/(2√(wT)))² = Σ (ν_vol / (4wT)) Δw²

**Ratio: Plan weights are ν_vol times larger than ingestion weights.**

These are NOT equivalent. The plan's variance-space vega² is what Corbetta uses and is the RECOMMENDED approach (§19).

## What to Fix — Deliverables

### 1. Update §10 with Configurable Weighting Mode

```
## 10. Vega²-Weighted Objective + Velocity Penalty (Variance Space)

**Recommended (Corbetta 2019, Image 2/4): Variance-space vega²**

For each strike j in slice t:
  w_mkt,j  = σ_mkt,j² · T_t          (market total variance)
  w_mod,j  = w_eSSVI(k_j; θ_t, ρ_t, ψ_t)  (model total variance)
  
  ν_vol,j  = Black76Vega(F_t, K_j, T_t, σ_mkt,j, r=0)  # r=0 for SPX European
  ν_var,j  = ν_vol,j / (2 · σ_mkt,j · √T_t) = ν_vol,j / (2 · √(w_mkt,j · T_t))
  
  W_j = (ν_var,j)²   # Variance-space vega squared
  
  Belly boost: if |k_j| < BELLY_K_ABS: W_j *= BELLY_BOOST
  
Objective: min_{ρ_t, ψ_t} Σ_j W_j (w_mkt,j − w_mod,j)² + Penalty(ρ_t, ψ_t)
```

**Alternative modes (configurable):**

| Mode | Weight W_j | Error | Notes |
|------|-----------|-------|-------|
| `var_vega2` (default) | (ν_var)² | Δw² | Corbetta, variance-space, matches theory |
| `vol_vega1` | ν_vol | Δσ² | dataingestion.md, vol-space |
| `vol_vega2` | (ν_vol)² | Δσ² | Vol-space vega² |

**All modes use the SAME belly boost logic.**

### 2. Implement in `objective.py`

```python
def compute_weights(k_array, w_mkt, T, config):
    """Compute weights for all strikes in a slice."""
    sigma_mkt = np.sqrt(w_mkt / T)
    vega_vol = black76_vega(k_array, w_mkt, T)  # r=0
    
    if config.VEGA_WEIGHT_MODE == "var_vega2":
        vega_var = vega_vol / (2 * sigma_mkt * np.sqrt(T))
        W = vega_var ** 2
    elif config.VEGA_WEIGHT_MODE == "vol_vega1":
        W = vega_vol
    elif config.VEGA_WEIGHT_MODE == "vol_vega2":
        W = vega_vol ** 2
    else:
        raise ValueError(f"Unknown VEGA_WEIGHT_MODE: {config.VEGA_WEIGHT_MODE}")
    
    # Belly boost
    belly_mask = np.abs(k_array) < config.BELLY_K_ABS
    W[belly_mask] *= config.BELLY_BOOST
    
    return W

def objective(psi, rho, theta_star, k_star, k_array, w_mkt, T, prev_params, config):
    """Full objective for given (rho, psi). theta computed exactly from anchor."""
    theta = exact_theta_from_anchor(theta_star, k_star, rho, psi, config)
    w_mod = essvi_total_variance(k_array, theta, rho, psi)
    
    W = compute_weights(k_array, w_mkt, T, config)
    
    data_loss = np.sum(W * (w_mkt - w_mod) ** 2)
    
    # Velocity penalty (term-structure, within same minute)
    if prev_params is not None:
        rho_prev, psi_prev = prev_params
        data_loss += config.LAMBDA_RHO * (rho - rho_prev) ** 2
        data_loss += config.LAMBDA_PSI * (psi - psi_prev) ** 2
    
    return data_loss
```

### 3. Update config.py

```python
VEGA_WEIGHT_MODE = "var_vega2"   # "var_vega2" | "vol_vega1" | "vol_vega2"
BELLY_BOOST = 3.0
BELLY_K_ABS = 0.15
```

### 4. Update §19 — Mark as Resolved

```
#1 Weighting discrepancy — RESOLVED: variance-space vega² (config VEGA_WEIGHT_MODE="var_vega2")
```

## Output Format

Update `eSSVI_surface_plan (1).md` §10, §19. Add `objective.py` stub. Mark `<<A5_CHANGE>>`.

## Validation

- [ ] Corbetta SPX 2018-01-08: fit with var_vega2 matches Table 1 parameters
- [ ] var_vega2 vs vol_vega1: compare fitted parameters on sample data
- [ ] Belly boost correctly applied in all modes
- [ ] Config parameter documented with valid values
- [ ] No division by zero in vega_var (σ > 0 guaranteed by ingestion)