# Agent A1 Prompt — Calendar Spread Arbitrage Conditions (Pasquazzi 2023 Correction)

## Role: Quant Researcher

## Mission
Replace the Hendriks-Martini (2019) calendar spread conditions with the corrected Pasquazzi (2023) conditions throughout the plan.

## Required Reading (MANDATORY — READ IN FULL)
1. **Pasquazzi (2023)** — *A Note about Characterization of Calendar Spread Arbitrage in eSSVI Surfaces* — **Entire paper, especially:**
   - Abstract & Conclusion: HM Proposition 3.1 is wrong
   - Lemma 2: Θ = 1 case conditions
   - Lemmas 7–10: Θ > 1 case analysis
   - **Proposition 13 (Corrected statement)** — THE authoritative no-arbitrage conditions
   - Figure 1: Visualization of allowed (ρ₁, ρ₂) regions
2. **Hendriks & Martini (2019)** — *The Extended SSVI Volatility Surface* — Proposition 3.1 (the ORIGINAL incorrect statement)
3. **Corbetta et al. (2019)** — Section 3 (Granting no Calendar-Spread arbitrage across slices) — Uses HM conditions
4. **Mingone (2022)** — Section 2.1 (Calendar spread arbitrage) — Eq (3) combined constraints

## What's Wrong in the Plan

### §7.2 Calendar Spread Arbitrage (lines 178–185)
Current plan states:
```
(C1) θ₂ ≥ θ₁
(C2) ψ₂ ≥ ψ₁
(C3) |ρ₂ψ₂ − ρ₁ψ₁| ≤ ψ₂ − ψ₁
```
**These are the HM conditions. Pasquazzi proves they are NECESSARY but NOT SUFFICIENT when Θ = θ₂/θ₁ = 1.**

### §8 Corridor Construction (lines 220–222)
```
L_cal = max( ψ_{t-1}(1−ρ_{t-1})/(1−ρ_t), ψ_{t-1}(1+ρ_{t-1})/(1+ρ_t) )
```
This is derived from HM (C3) only. **Missing**: Θ = 1 special case, ΘΦ ≥ 1 condition, and Φ > 1 region constraints.

### §12 Audit (lines 297–298)
```
2. θ_t non-decreasing; ψ_t non-decreasing; |ρ_tψ_t−ρ_{t-1}ψ_{t-1}| ≤ ψ_t−ψ_{t-1} for every adjacent pair.
```
Only checks HM conditions — will miss calendar arbitrage in Θ = 1 case.

### §15 Interpolation (lines 335–342)
Linear interpolation in (θ, ψ, ρψ) is proven arbitrage-free for HM conditions (Corbetta §7.1). **Must verify for Pasquazzi conditions.**

## What to Fix — Deliverables

### 1. Replace §7.2 with Pasquazzi Conditions

**New §7.2 content:**

```
### 7.2 Calendar-Spread Arbitrage — Time Monotonicity (Pasquazzi 2023 Correction)

Pasquazzi (2023) proves that the Hendriks-Martini (2019) Proposition 3.1 conditions are **incorrect** — they are necessary but not sufficient when Θ = 1. The corrected necessary and sufficient conditions are:

Let slice 1 = nearer maturity (θ₁, ρ₁, φ₁), slice 2 = farther maturity (θ₂, ρ₂, φ₂).
Define Θ = θ₂/θ₁, Φ = φ₂/φ₁ = (ψ₂/θ₂)/(ψ₁/θ₁).

**Necessary conditions (always required):**
1. Θ ≥ 1                                    (θ₂ ≥ θ₁)
2. ΘΦ ≥ 1                                   (from asymptote comparison)
3. −ΘΦ ≤ ΘΦ ρ₂ − ρ₁ ≤ ΘΦ                   (HM skew condition)

**Sufficient conditions (Pasquazzi Proposition 13):**

**Case A: Θ = 1 (θ₁ = θ₂)**
No calendar arbitrage **iff** either:
  (i)   ρ₁ = ρ₂ = 0  and  Φ ≥ 1
  (ii)  ρ₁ = ρ₂ Φ    and  ρ₂² ≥ ρ₁²

**Case B: Θ > 1 and Φ ≤ 1**
No calendar arbitrage **iff**:
  ΘΦ ≥ 1  AND  −ΘΦ ≤ ΘΦ ρ₂ − ρ₁ ≤ ΘΦ
  (These are exactly the HM conditions + ΘΦ ≥ 1)

**Case C: Θ > 1 and Φ > 1**
No calendar arbitrage **iff** (ρ₁, ρ₂) ∈ R_Θ,Φ \ H_Θ,Φ
Where:
  - H_Θ,Φ = { (ρ₁, ρ₂) : ρ₂² − ρ₁² = ΘΦ(ΘΦ − 1) }  (hyperbola)
  - R_Θ,Φ = { (ρ₁, ρ₂) : −ΘΦ ≤ ΘΦ ρ₂ − ρ₁ ≤ ΘΦ }   (stripe)
  - The allowed region is the stripe minus the hyperbola branches
  - See Pasquazzi Figure 1 for visualization

**Practical Implementation:**
For calibration, we use the **global parametrization** of Mingone (2022) which automatically satisfies all cases, OR we implement the corridor bounds per Case A/B/C above.
```

### 2. Update §8 Corridor Lower Bound

The corridor lower bound L_ψ must implement the full Pasquazzi conditions:

```
For slice t (current) and slice t-1 (previous, locked):
  Θ = θ_t / θ_{t-1}
  Φ = (ψ_t/θ_t) / (ψ_{t-1}/θ_{t-1}) = (ψ_t θ_{t-1}) / (ψ_{t-1} θ_t)

Since θ_t = θ_t(ψ_t) [exact from Agent A3], both Θ and Φ depend on ψ_t.

L_ψ(ρ_t, ψ_t) = calendar_lower_bound_pasquazzi(ρ_t, ψ_t, prev_slice)

where calendar_lower_bound_pasquazzi implements:
  If Θ ≈ 1 (|Θ − 1| < 1e-8):
      # Case A: θ_t ≈ θ_{t-1}
      If ρ_t ≈ 0 and ρ_{t-1} ≈ 0:
          Require Φ ≥ 1 → ψ_t ≥ ψ_{t-1} θ_t / θ_{t-1} ≈ ψ_{t-1}
          L_ψ = max(ψ_{t-1}, ε_ψ)
      Else if ρ_t ≈ ρ_{t-1} * Φ:
          Require ρ_t² ≥ ρ_{t-1}² → |ρ_t| ≥ |ρ_{t-1}|
          L_ψ from Φ = 1 condition: ψ_t = ψ_{t-1} θ_t / θ_{t-1}
      Else:
          INFEASIBLE (no ψ satisfies Case A)
  Else:  # Θ > 1
      If Φ ≤ 1:
          # Case B: HM conditions + ΘΦ ≥ 1
          ΘΦ = θ_t/θ_{t-1} * ψ_t θ_{t-1}/(ψ_{t-1} θ_t) = ψ_t/ψ_{t-1} ≥ 1
          → ψ_t ≥ ψ_{t-1}
          Skew condition: |ρ_t ψ_t − ρ_{t-1} ψ_{t-1}| ≤ ψ_t − ψ_{t-1}
          This gives the two bounds:
            ψ_t ≥ ψ_{t-1} (1 − ρ_{t-1})/(1 − ρ_t)   if ρ_t < 1
            ψ_t ≥ ψ_{t-1} (1 + ρ_{t-1})/(1 + ρ_t)   if ρ_t > -1
          L_ψ = max( ψ_{t-1}, ψ_{t-1}(1−ρ_{t-1})/(1−ρ_t), ψ_{t-1}(1+ρ_{t-1})/(1+ρ_t), ε_ψ )
      Else:  # Φ > 1, Case C
          # Need (ρ_{t-1}, ρ_t) ∈ R_Θ,Φ \ H_Θ,Φ
          # This is complex — in practice, use Mingone global parametrization
          # For sequential: check if (ρ_{t-1}, ρ_t) in allowed region
          # If not, ρ_t is infeasible for this ψ_t
          L_ψ = solve_for_psi_in_allowed_region(ρ_t, prev_slice)
```

**Simpler approach for sequential calibration**: Use the **Mingone (2022) global parametrization** which reparameterizes the no-arbitrage domain as a product of intervals. This avoids the case analysis entirely. But if keeping sequential:
- Restrict to Case B (Φ ≤ 1) by construction: enforce ψ_t/ψ_{t-1} ≤ θ_t/θ_{t-1} (which is ΘΦ ≤ 1 → Φ ≤ 1/Θ ≤ 1)
- Then L_ψ = max( ψ_{t-1}, ψ_{t-1}(1−ρ_{t-1})/(1−ρ_t), ψ_{t-1}(1+ρ_{t-1})/(1+ρ_t) ) — the HM formula but ONLY valid when Φ ≤ 1

### 3. Update §12 Audit

```python
def check_calendar_arbitrage_pasquazzi(slice1, slice2, tol=1e-10):
    """
    slice1 = (θ₁, ρ₁, ψ₁)  # nearer
    slice2 = (θ₂, ρ₂, ψ₂)  # farther
    Returns (is_ok, violation_message)
    """
    θ₁, ρ₁, ψ₁ = slice1
    θ₂, ρ₂, ψ₂ = slice2
    
    Θ = θ₂ / θ₁
    Φ = (ψ₂/θ₂) / (ψ₁/θ₁) = ψ₂ * θ₁ / (ψ₁ * θ₂)
    
    # Necessary conditions
    if Θ < 1 - tol:
        return False, f"Θ={Θ:.6f} < 1 (θ not monotone)"
    if Θ * Φ < 1 - tol:
        return False, f"ΘΦ={Θ*Φ:.6f} < 1 (asymptote violation)"
    if not (-Θ*Φ - tol <= Θ*Φ*ρ₂ - ρ₁ <= Θ*Φ + tol):
        return False, f"Skew condition violated: ΘΦρ₂−ρ₁={Θ*Φ*ρ₂ - ρ₁:.6f} not in [−{Θ*Φ:.6f}, {Θ*Φ:.6f}]"
    
    # Sufficient conditions per case
    if abs(Θ - 1) < 1e-8:  # Case A
        if abs(ρ₁) < tol and abs(ρ₂) < tol:
            if Φ < 1 - tol:
                return False, f"Case A(i): ρ₁=ρ₂=0 but Φ={Φ:.6f} < 1"
        elif abs(ρ₁ - ρ₂*Φ) < tol:
            if ρ₂*ρ₂ < ρ₁*ρ₁ - tol:
                return False, f"Case A(ii): ρ₁=ρ₂Φ but ρ₂²={ρ₂*ρ₂:.6f} < ρ₁²={ρ₁*ρ₁:.6f}"
        else:
            return False, f"Case A: neither (i) nor (ii) satisfied"
    elif Θ > 1 and Φ <= 1 + tol:  # Case B
        # HM conditions already checked above
        pass
    else:  # Case C: Θ > 1 and Φ > 1
        # Check (ρ₁, ρ₂) ∈ R \ H
        in_stripe = (-Θ*Φ - tol <= Θ*Φ*ρ₂ - ρ₁ <= Θ*Φ + tol)
        on_hyperbola = abs(ρ₂*ρ₂ - ρ₁*ρ₁ - Θ*Φ*(Θ*Φ - 1)) < tol
        if not in_stripe or on_hyperbola:
            return False, f"Case C: (ρ₁,ρ₂)=({ρ₁:.4f},{ρ₂:.4f}) not in allowed region"
    
    return True, "OK"
```

### 4. Update §15 Interpolation

Add verification that linear interpolation preserves Pasquazzi conditions. Mingone §5.1 proves it for HM conditions. For Pasquazzi, need to check:
- Θ(λ) = θ_λ / θ_1 is monotone in λ
- Φ(λ) = (ψ_λ/θ_λ) / (ψ_1/θ_1) 
- The (ρ_λ, ρ_μ) pairs stay in allowed region

**Likely still holds** due to linearity, but must be verified or cited.

### 5. Add to config.py

```python
CALENDAR_CONDITION_VERSION = "pasquazzi_2023"
PASQUAZZI_THETA_TOL = 1e-8
PASQUAZZI_RHO_TOL = 1e-10
```

## Output Format

Update `eSSVI_surface_plan (1).md` in place — modify sections 7.2, 8, 12, 15. Add config. Mark changes with `<<A1_CHANGE>>`.

## Validation

- [ ] Pasquazzi Lemma 2: Θ=1, ρ₁=ρ₂=0, Φ=1.2 → no arb; Φ=0.8 → arb
- [ ] Pasquazzi Lemma 2: Θ=1, ρ₁=0.5, ρ₂=0.5/1.2=0.4167, ρ₂²=0.1736 ≥ ρ₁²=0.25? No → arb. Wait, ρ₂² ≥ ρ₁² required. So ρ₁=0.5, ρ₂=0.4167: ρ₂²=0.1736 < 0.25 → VIOLATION. Correct: ρ₁ = ρ₂Φ means ρ₂ = ρ₁/Φ. Then ρ₂² = ρ₁²/Φ². Need ρ₂² ≥ ρ₁² → 1/Φ² ≥ 1 → Φ ≤ 1. But Case A(i) requires Φ ≥ 1. So Case A(ii) only works when Φ=1 exactly? Let me re-read...

**Pasquazzi Lemma 2**: "either (i) ρ₁=ρ₂=0 and Φ ≥ 1 OR (ii) ρ₁=ρ₂Φ and ρ₂² ≥ ρ₁²"

If ρ₁ = ρ₂Φ, then ρ₂² ≥ ρ₁² = ρ₂²Φ² → 1 ≥ Φ² → Φ ≤ 1.
But necessary condition ΘΦ ≥ 1 with Θ=1 gives Φ ≥ 1.
So Φ = 1 exactly. Then ρ₁ = ρ₂.

**Conclusion**: When Θ=1, the ONLY no-arbitrage cases are:
- ρ₁ = ρ₂ = 0 with Φ ≥ 1 (flat correlation, increasing curvature)
- ρ₁ = ρ₂ with Φ = 1 (identical slices up to θ scaling, but Θ=1 so identical)

This is a **much stricter** condition than HM! HM would allow Θ=1, ρ₁≠ρ₂ as long as |ρ₂ψ₂−ρ₁ψ₁| ≤ ψ₂−ψ₁. Pasquazzi says NO — if θ₁=θ₂ and ρ₁≠ρ₂, the slices cross.

**This is critical for overnight gap handling** (§14) where θ*_t ≈ θ_{t-1} can happen.

- [ ] Test: θ₁=θ₂=0.04, ρ₁=-0.5, ρ₂=-0.6, ψ₁=0.03, ψ₂=0.036 (Φ=1.2). HM: |−0.6*0.036 + 0.5*0.03| = 0.0066 ≤ 0.006? No, 0.0066 > 0.006 → HM says arb. But what if ψ₂=0.031? |−0.0186+0.015|=0.0036 ≤ 0.001? No. Need ψ₂−ψ₁ ≥ |ρ₂ψ₂−ρ₁ψ₁|. If ψ₂=0.04, diff=0.01, |−0.024+0.015|=0.009 ≤ 0.01 → HM says OK. Pasquazzi: Θ=1, ρ₁≠ρ₂, ρ₁≠0, ρ₂≠0 → ARBITRAGE. The slices cross somewhere.

- [ ] Verify Mingone global parametrization avoids this automatically
- [ ] Check Corbetta SPX data: any adjacent slices with θ₁ ≈ θ₂?
- [ ] Corridor L_ψ correctly returns infeasible for Θ≈1, ρ_t ≠ ρ_{t-1}