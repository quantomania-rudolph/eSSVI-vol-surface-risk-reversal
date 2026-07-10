# Agent A6 Prompt — Interpolation/Extrapolation & Long-Term Tail Handling

## Role: Quant Developer

## Mission
Fix the long-term extrapolation (currently says "extend θ,ψ along last linear segment" — WRONG for ψ) and verify short-term extrapolation. Add explicit wing tail capping.

## Required Reading
1. **Corbetta et al. (2019)** — Section 7 (Arbitrage-free interpolation):
   - 7.1: Linear in (θ, ψ, ρψ) — proven arbitrage-free
   - 7.2 Short-term: θ_t = λθ₁, ψ_t = λψ₁, ρ_t = ρ₁ (λ = t/T₁)
   - 7.3 Long-term: θ_t = θ_N + u(t), ψ_t = ψ_N, ρ_t = ρ_N (u increasing)
2. **Mingone (2022)** — Section 5.2 (Interpolation and extrapolation):
   - 5.2.1 Short: (θ_t, ψ_t, ρ_t) = (λθ_N, λψ_N, ρ_N)
   - 5.2.2 Long: ψ_t = ψ_N, ρ_t = ρ_N, θ_t linear
3. **Roger Lee (2004)** — Theorem 3.2: Tail slope bound |σ²/|k|| ≤ 2/T

## What's Wrong

### §15 Interpolation (lines 335–342)
```
# Linear in (θ, ψ, ρψ) → ρ = (ρψ)/ψ
θ_interp(λ) = (1−λ)θ_i + λθ_{i+1}
ψ_interp(λ) = (1−λ)ψ_i + λψ_{i+1}
ρψ_interp(λ) = (1−λ)ρ_i ψ_i + λρ_{i+1} ψ_{i+1}
ρ_interp(λ) = ρψ_interp(λ) / ψ_interp(λ)
```
**This is CORRECT per Corbetta §7.1 and Mingone §5.1.**

### §15 Extrapolation (lines 343–346)
```
# Short (T < T₁): λ = T/T₁, θ=λθ₁, ψ=λψ₁, ρ=ρ₁  ← CORRECT (Corbetta 7.2)
# Long (T > T_N): hold ρ flat, extend θ,ψ along last linear segment (or flat) ← WRONG for ψ
```
**Problem**: "extend θ,ψ along last linear segment" — ψ must be held **FLAT (constant)**, not linearly extrapolated. Linear extrapolation of ψ can violate:
- ψ(1+|ρ|) ≤ 4 (butterfly)
- ψ_t ≥ ψ_{t-1} (calendar monotonicity)

Corbetta 7.3 and Mingone 5.2.2: **ψ_t = ψ_N, ρ_t = ρ_N, θ_t linear.**

### §15 Wing Extrapolation (lines 347–350)
```
# Strike extrapolation beyond min/max K: never let the tail slope exceed the Lee/butterfly cap
```
**Problem**: No concrete algorithm. Need explicit formula.

## What to Fix — Deliverables

### 1. Fix §15 Extrapolation

**New §15 content:**

```
## 15. Continuous Surface via Inter-Slice Interpolation

### 15.1 Linear Interpolation (T_i ≤ T ≤ T_{i+1})
As per Corbetta §7.1 and Mingone §5.1, linear interpolation in the transformed variables preserves no-arbitrage:

For λ ∈ [0, 1] mapping T = (1−λ)T_i + λT_{i+1}:
```
θ(λ) = (1−λ)θ_i + λθ_{i+1}
ψ(λ) = (1−λ)ψ_i + λψ_{i+1}
(ρψ)(λ) = (1−λ)ρ_i ψ_i + λρ_{i+1} ψ_{i+1}
ρ(λ) = (ρψ)(λ) / ψ(λ)   (defined since ψ > 0)
```
**Proven arbitrage-free** (butterfly + calendar) for both HM and Pasquazzi conditions.

### 15.2 Short-Term Extrapolation (T < T₁)
As per Corbetta §7.2 and Mingone §5.2.1:
```
λ = T / T₁
θ(T) = λ θ₁
ψ(T) = λ ψ₁
ρ(T) = ρ₁
```
This is the **only** valid extrapolation for T < T₁ that preserves no-arbitrage.

### 15.3 Long-Term Extrapolation (T > T_N) — CRITICAL FIX

**INCORRECT in current plan**: "extend θ,ψ along last linear segment"

**CORRECT (Corbetta §7.3, Mingone §5.2.2):**
```
ψ(T) = ψ_N          # CONSTANT — do NOT extrapolate ψ
ρ(T) = ρ_N          # CONSTANT
θ(T) = θ_N + (θ_N − θ_{N-1}) / (T_N − T_{N-1}) · (T − T_N)   # linear in θ
```
**Rationale**: 
- ψ controls curvature/asymptotes. Increasing ψ violates calendar monotonicity (ψ_t ≥ ψ_{t-1}) and butterfly bound ψ(1+|ρ|) ≤ 4.
- ρ controls skew symmetry. Extrapolating ρ can cross ρ=0 or hit ±1.
- θ is the only parameter that MUST increase (calendar level). Linear extrapolation of θ's slope is safe and proven.

**Configurable alternative (flat θ slope):**
```
θ(T) = θ_N + θ'_N · (T − T_N)  where θ'_N = (θ_N − θ_{N-1})/(T_N − T_{N-1}) or 0
```
Default: use last segment slope.

### 15.4 Strike Extrapolation (Wing Tails) — Explicit Algorithm

For |k| > K_MAX (max calibrated strike in slice):
```
# eSSVI tail slopes:
c_+ = (ψ/2) (1 + ρ)   # right tail (k → +∞)
c_- = (ψ/2) (1 − ρ)   # left tail (k → −∞)

# Lee (2004) bound: limsup σ²/|k| ≤ 2/T  →  c_± ≤ 2
# Butterfly bound: ψ(1+|ρ|) ≤ 4  →  c_± ≤ 2
# So the bounds coincide!

# Cap tail slopes at 2 − δ
δ = config.TAIL_SLOPE_CAP_EPS  # e.g., 1e-4
c_+_capped = min(c_+, 2 − δ)
c_-_capped = min(c_-, 2 − δ)

# Linear tail beyond K_MAX:
if k > K_MAX:
    w(k) = w(K_MAX) + c_+_capped · (k − K_MAX)
elif k < −K_MAX:
    w(k) = w(−K_MAX) + c_-_capped · (−K_MAX − k)

# Note: K_MAX is the max |k| in the calibrated slice.
# For production, use K_MAX = K_AUDIT = 3.0 (from §12 audit grid).
```

### 15.5 Surface Assembly Algorithm

```
def build_surface(locked_slices, config):
    # locked_slices: list of (T, θ, ρ, ψ) sorted by T
    
    # 1. Sort by T
    slices = sorted(locked_slices, key=lambda s: s.T)
    
    # 2. For each query T:
    def surface(T, k):
        if T <= slices[0].T:
            # Short extrapolation
            λ = T / slices[0].T
            θ = λ * slices[0].theta
            ψ = λ * slices[0].psi
            ρ = slices[0].rho
        elif T >= slices[-1].T:
            # Long extrapolation
            θ = extrapolate_theta_long(T, slices[-1], slices[-2], config)
            ψ = slices[-1].psi
            ρ = slices[-1].rho
        else:
            # Interpolation
            i = find_bracket(slices, T)
            λ = (T - slices[i].T) / (slices[i+1].T - slices[i].T)
            θ = (1-λ)*slices[i].theta + λ*slices[i+1].theta
            ψ = (1-λ)*slices[i].psi + λ*slices[i+1].psi
            ρψ = (1-λ)*slices[i].rho*slices[i].psi + λ*slices[i+1].rho*slices[i+1].psi
            ρ = ρψ / ψ
        
        # Strike extrapolation
        K_MAX = config.K_AUDIT  # or slice-specific max strike
        if k > K_MAX:
            c = min((ψ/2)*(1+ρ), 2 - config.TAIL_SLOPE_CAP_EPS)
            return essvi_total_variance(K_MAX, θ, ρ, ψ) + c * (k - K_MAX)
        elif k < -K_MAX:
            c = min((ψ/2)*(1-ρ), 2 - config.TAIL_SLOPE_CAP_EPS)
            return essvi_total_variance(-K_MAX, θ, ρ, ψ) + c * (-K_MAX - k)
        else:
            return essvi_total_variance(k, θ, ρ, ψ)
    
    return surface
```

### 2. Add to config.py

```python
# Interpolation/Extrapolation
EXTRAPOLATION_PSI_MODE = "flat"        # "flat" | "linear" (linear is WRONG)
EXTRAPOLATION_RHO_MODE = "flat"        # "flat" | "linear"
EXTRAPOLATION_THETA_MODE = "linear"    # "linear" | "flat"
TAIL_SLOPE_CAP_EPS = 1e-4
SHORT_EXTRAP_MODE = "corbetta"         # "corbetta" | "flat"
```

## Output Format

Update `eSSVI_surface_plan (1).md` §15. Add config. Mark `<<A6_CHANGE>>`. Provide `surface.py` stub.

## Validation

- [ ] Long extrapolation: ψ flat, ρ flat, θ linear — verify no arb with audit grid
- [ ] Short extrapolation: Corbetta scaling — verify no arb
- [ ] Tail capping: c_± ≤ 2 - 1e-4 — verify Lee bound satisfied
- [ ] Interpolation: linear in (θ, ψ, ρψ) — verify preserves Pasquazzi calendar
- [ ] Surface query at T < T₁, T > T_N, between slices — all produce valid w(k)
- [ ] Config params documented