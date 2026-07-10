# Agent A3 Prompt — Anchor Reparameterization & Exact θ Solution

## Role: Quant Researcher

## Mission
Fix the anchor reparameterization to use the exact solution instead of the first-order approximation θ_t ≈ θ*_t − ρ_t ψ_t k*_t.

## Required Reading (MANDATORY)
1. **Corbetta et al. (2019)** — **Section 2.1** (Anchored eSSVI slices with no Butterfly arbitrage) — **Equations after (58)**:
   - Exact relation: θ* = w(k*; θ, ρ, ψ) = θ/2 [1 + ρ(ψ/θ)k* + √((ψ/θ k* + ρ)² + (1−ρ²))]
   - First-order approximation: θ ≈ θ* − ρψk* (used in current plan)
   - ψ₊(ρ, k*, θ*) bound derivation
2. **Gatheral & Jacquier (2014)** — SSVI formula (4.1): w(k, θ) = θ/2 [1 + ρφk + √((φk+ρ)² + (1−ρ²))] with φ = ψ/θ
3. **Mingone (2022)** — Section 2, Eq (1): eSSVI formula with (θ, ρ, ψ)

## What's Wrong in the Plan

### §5 Anchoring (lines 130–143)
- Uses θ_t ≈ θ*_t − ρ_t ψ_t k*_t (first-order in k*)
- Says "fixed-point converges in 1–2 iterations; k*≈0 ⇒ fast"
- **Problem**: k*_t is NOT always ≈ 0. For AMD with $0.01 strike grid and F ~ $150, k step ≈ 0.000067. But if no strike exactly at F, |k*| can be 0.005–0.02. With ρ≈-0.7, ψ≈0.05, θ≈0.04: error ≈ ρψk*/θ ~ 4%.
- No exact formula provided.

### §8 Note (line 232)
"after the inner ψ_t solve, recompute θ_t (§5 fixed-point) and re-verify ψ_t ≤ U_ψ(θ_t) — because k*≈0, θ_t≈θ*_t and re-verification rarely moves the bound, but it MUST be checked (one extra iteration)"
- **Problem**: "one extra iteration" of what? Fixed-point? Not specified. The fixed-point iteration is not defined.

### §14 Calendar Degeneracy (line 327)
"If θ*_t < θ_{t-1}... (ii) if none, enforce θ_t = max(θ*_t, θ_{t-1}+ε) (project onto the calendar-admissible set) and flag `THETA_PROJECTED`"
- **Problem**: If you change θ_t, the anchor relation w(k*_t; θ_t, ρ_t, ψ_t) = θ*_t is BROKEN. You can't just project θ_t independently.

## What to Fix — Deliverables

### 1. Replace §5 with Exact Anchor Solution

**New §5 content:**
```
## 5. Anchoring Parameter Pair (k*_t, θ*_t) — Exact Solution (Corbetta 2019 §2.1)

**What:** For each maturity slice, find the market quote whose strike is closest to the forward → its log-moneyness k*_t and total implied variance θ*_t = σ*²·T_t. Most liquid, tightest point.

**Why:** Force the slice through (k*_t, θ*_t). This removes one degree of freedom (θ_t), reducing the inner problem to 1D in ψ_t for each ρ_t.

**Exact Reparameterization:**
Given (k*_t, θ*_t, ρ_t, ψ_t), the slice parameter θ_t is defined implicitly by:
```
θ*_t = w(k*_t; θ_t, ρ_t, ψ_t) = θ_t/2 [ 1 + ρ_t (ψ_t/θ_t) k*_t + √( (ψ_t/θ_t k*_t + ρ_t)² + (1 − ρ_t²) ) ]
```

Let φ_t = ψ_t/θ_t. Rearranging:
```
2θ*_t/θ_t − 1 − ρ_t φ_t k*_t = √( (φ_t k*_t + ρ_t)² + 1 − ρ_t² )
```
Square both sides (RHS ≥ 0 always):
```
(2θ*_t/θ_t − 1 − ρ_t φ_t k*_t)² = (φ_t k*_t + ρ_t)² + 1 − ρ_t²
```
Substitute φ_t = ψ_t/θ_t and multiply by θ_t²:
```
(2θ*_t − θ_t − ρ_t ψ_t k*_t)² = (ψ_t k*_t + ρ_t θ_t)² + θ_t²(1 − ρ_t²)
```
This is a **quadratic in θ_t**. Expand and solve exactly.

**Exact Quadratic Solution:**
```
A = (1 − ρ_t²) k*_t²
B = 2ρ_t k*_t (ρ_t ψ_t k*_t − 2θ*_t) + ψ_t² k*_t²
C = 4θ*_t² − 4ρ_t ψ_t k*_t θ*_t + ρ_t² ψ_t² k*_t² − ψ_t² k*_t² − ρ_t² ψ_t² k*_t²

Wait — let's derive cleanly:

Let u = θ_t. Equation: (2θ* − u − ρψk*)² = (ψk* + ρu)² + u²(1−ρ²)

LHS = (2θ* − u(1 + ρ(ψ/u)k*))? No.

Let's do it step by step:
LHS = (2θ* − u − ρ(ψ/u)k* u)²? No, φ = ψ/u, so ρφk* = ρψk*/u.

LHS = (2θ* − u − ρψk*)²  [since ρφk* u = ρψk*]

RHS = (ψk* + ρu)² + u²(1−ρ²)
    = ψ²k*² + 2ρψk*u + ρ²u² + u² − ρ²u²
    = ψ²k*² + 2ρψk*u + u²

So: (2θ* − u − ρψk*)² = u² + 2ρψk*u + ψ²k*²

Let C = 2θ* − ρψk*. Then (C − u)² = u² + 2ρψk*u + ψ²k*²
C² − 2Cu + u² = u² + 2ρψk*u + ψ²k*²
C² − 2Cu = 2ρψk*u + ψ²k*²
C² − ψ²k*² = 2u(C + ρψk*)

u = (C² − ψ²k*²) / (2(C + ρψk*))
  = ( (2θ* − ρψk*)² − ψ²k*² ) / (2(2θ* − ρψk* + ρψk*))
  = ( 4θ*² − 4ρψk*θ* + ρ²ψ²k*² − ψ²k*² ) / (4θ*)
  = θ* − (ρψk*)/2 + (ψ²k*²(ρ² − 1)) / (4θ*)

Since ρ² − 1 = −(1−ρ²):
θ_t = θ*_t − (ρ_t ψ_t k*_t)/2 − (ψ_t² k*_t² (1 − ρ_t²)) / (4 θ*_t)

**This is the EXACT solution!** No iteration needed. Closed form.

**Verification:**
- If k*_t = 0: θ_t = θ*_t ✓
- If ρ_t = 0: θ_t = θ*_t − ψ_t²k*_t²/(4θ*_t) < θ*_t ✓ (symmetric smile has min at k=0)
- First-order in k*: θ_t ≈ θ*_t − (ρ_t ψ_t k*_t)/2 ... wait, the plan had θ ≈ θ* − ρψk*. Factor of 1/2 difference!

Let me re-check the Corbetta formula...

Corbetta Eq after (58): "θ = θ* − ρθφk*" — they use φ not ψ/θ? Wait, they define ψ = θφ, so ρθφk* = ρψk*. So Corbetta says θ ≈ θ* − ρψk*.

But my exact derivation gives θ = θ* − (ρψk*)/2 − ψ²k*²(1−ρ²)/(4θ*).

There's a factor of 1/2 discrepancy on the linear term. Let me re-derive more carefully.

w(k; θ, ρ, ψ) = θ/2 [ 1 + ρ(ψ/θ)k + √( (ψ/θ k + ρ)² + 1−ρ² ) ]

Set w(k*) = θ*:
θ* = θ/2 [ 1 + ρ(ψ/θ)k* + √( (ψ/θ k* + ρ)² + 1−ρ² ) ]
2θ*/θ = 1 + ρ(ψ/θ)k* + √( (ψk*/θ + ρ)² + 1−ρ² )
2θ*/θ − 1 − ρψk*/θ = √( (ψk*/θ + ρ)² + 1−ρ² )

Square:
(2θ*/θ − 1 − ρψk*/θ)² = (ψk*/θ + ρ)² + 1−ρ²

Multiply by θ²:
(2θ* − θ − ρψk*)² = (ψk* + ρθ)² + θ²(1−ρ²)
                   = ψ²k*² + 2ρψk*θ + ρ²θ² + θ² − ρ²θ²
                   = θ² + 2ρψk*θ + ψ²k*²

LHS = (2θ* − ρψk*)² − 2(2θ* − ρψk*)θ + θ²

So:
(2θ* − ρψk*)² − 2(2θ* − ρψk*)θ + θ² = θ² + 2ρψk*θ + ψ²k*²
(2θ* − ρψk*)² − ψ²k*² = 2θ [ (2θ* − ρψk*) + ρψk* ]
                      = 2θ [ 2θ* ]

θ = [ (2θ* − ρψk*)² − ψ²k*² ] / (4θ*)

Expand numerator:
4θ*² − 4ρψk*θ* + ρ²ψ²k*² − ψ²k*²
= 4θ*² − 4ρψk*θ* + ψ²k*²(ρ² − 1)
= 4θ*² − 4ρψk*θ* − ψ²k*²(1 − ρ²)

So:
θ = θ* − ρψk* − ψ²k*²(1−ρ²)/(4θ*)

Ah! The linear term is −ρψk*, not −(ρψk*)/2. I made an algebra error earlier (missed factor of 2 in C).

**Correct Exact Solution:**
```
θ_t = θ*_t − ρ_t ψ_t k*_t − (ψ_t² k*_t² (1 − ρ_t²)) / (4 θ*_t)
```

This matches Corbetta's first-order approximation θ ≈ θ* − ρψk* plus a negative quadratic correction term.

**Algorithm (Exact, No Iteration):**
For each (ρ_t, ψ_t) candidate:
1. Compute θ_t exactly using the formula above
2. Verify θ_t > 0 (if not, candidate invalid)
3. Compute w_eSSVI(k; θ_t, ρ_t, ψ_t) for all strikes
4. Evaluate objective
```

### 2. Update §8 Corridor to Use Exact θ_t(ψ)

In the corridor construction, U_bf2 and calendar lower bound depend on θ_t. Now θ_t is an **explicit function of ψ_t** (and ρ_t, k*_t, θ*_t):
```
θ_t(ψ_t) = θ*_t − ρ_t ψ_t k*_t − ψ_t² k*_t² (1 − ρ_t²) / (4 θ*_t)
```

This makes U_bf2(ψ) and L_cal(ψ) explicit functions of ψ — no iteration needed.

### 3. Update §14 Calendar Degeneracy Handling

When θ*_t < θ_{t-1} (overnight gap):
- The anchor (k*_t, θ*_t) itself violates calendar level condition
- **Cannot** just project θ_t — must either:
  a) Choose a different anchor strike k' with θ' ≥ θ_{t-1} + ε (if exists in belly)
  b) If no such strike: **relax the anchor constraint** — allow θ_t > θ*_t by adding slack variable, or drop the slice
- Document the exact procedure

### 4. Add to config.py

```python
ANCHOR_SOLVE_METHOD = "exact_closed_form"  # "exact_closed_form" | "fixed_point" (deprecated)
ANCHOR_THETA_POSITIVE_TOL = 1e-12
```

## Output Format

Update `eSSVI_surface_plan (1).md` in place — modify sections 5, 8, 14. Add config. Mark changes with `<<A3_CHANGE>>`.

## Validation

- [ ] Exact formula matches Corbetta's first-order + quadratic correction
- [ ] For k*=0: θ = θ* exactly
- [ ] For ρ=0: θ = θ* − ψ²k*²/(4θ*) < θ* (correct, symmetric smile minimum at k=0)
- [ ] Corbetta SPX 2018-01-08: compute exact θ for each slice, verify w(k*) = θ*
- [ ] No iteration needed in inner loop — speed improvement
- [ ] θ_t(ψ) used in corridor bounds correctly
- [ ] Calendar degeneracy procedure specified and testable