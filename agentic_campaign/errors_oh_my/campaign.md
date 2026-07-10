# eSSVI Surface Plan — Error Remediation Campaign

**Target Files:** `eSSVI_surface_plan (1).md`, `config.py` (to be created)

**Campaign Goal:** Systematically fix all identified errors, edge cases, and omissions in the eSSVI calibration blueprint by distributing work across specialized agents.

---

## Agent Architecture

| Agent | Role | Focus Area | Parallel? |
|-------|------|------------|-----------|
| **A1** | Quant Researcher | Calendar Spread Arbitrage Conditions (Pasquazzi 2023 correction) | ✅ Yes |
| **A2** | Quant Researcher | Butterfly Arbitrage Bounds (GJ vs MM conditions) | ✅ Yes |
| **A3** | Quant Researcher | Anchor Reparameterization & Exact θ Solution | ✅ Yes |
| **A4** | Quant Developer | Corridor Construction (L_cal, U_bf1, U_bf2) & Empty Corridor Handling | ✅ Yes |
| **A5** | Quant Developer | Objective Function & Weighting (vega² variance-space vs vega vol-space) | ✅ Yes |
| **A6** | Quant Developer | Interpolation/Extrapolation & Long-Term Tail Handling | ✅ Yes |
| **A7** | Quant Researcher | Short Maturity Edge Cases (7 DTE) & Overnight Gap Handling | ✅ Yes |
| **A8** | Quant Developer | Warm-Start Seeding, Temporal Regularization, Kill Switch Logic | ✅ Yes |
| **A9** | Quant Researcher | Config Parameters & Validation (config.py) | ✅ Yes |
| **A10** | Integration Lead | Cross-Agent Consistency Review & Plan Document Update | ❌ Sequential (last) |

---

## Required Reading for ALL Agents

Every agent **MUST** read these papers before starting. The key sections are flagged:

### 1. Corbetta et al. (2019) — *Robust calibration and arbitrage-free interpolation of SSVI slices*
- **Sections 2.1–2.3**: Anchor reparameterization, butterfly bounds (B1, B2), calendar conditions, corridor construction
- **Section 3**: Algorithm (ρ-grid + Brent), data consistency, robustness
- **Section 5**: Arbitrage-free interpolation (linear in θ, ψ, ρψ), short/long extrapolation
- **Key formulas**: ψ₊(ρ, k*, θ*), ψ₋(ρ), ψ̂ = (θ* − θ)/ρk*

### 2. Hendriks & Martini (2019) — *The Extended SSVI Volatility Surface*
- **Proposition 3.1 / 3.5**: Original calendar spread conditions (has error corrected by Pasquazzi)
- **Section 3**: eSSVI formulation, Θ = θ₂/θ₁, Φ = φ₂/φ₁

### 3. Pasquazzi (2023) — *A Note about Characterization of Calendar Spread Arbitrage in eSSVI Surfaces* **CRITICAL**
- **Abstract & Conclusion**: Original HM Proposition 3.1 is wrong
- **Lemma 2**: Θ = 1 case requires ρ₁ = ρ₂ = 0 and Φ ≥ 1, OR ρ₁ = ρ₂Φ and ρ₂² ≥ ρ₁²
- **Lemmas 7–10**: Θ > 1 case analysis
- **Proposition 13 (Corrected)**: **Necessary & sufficient conditions for no calendar spread arbitrage**
- **Key correction**: Conditions (2) and (3) from HM are **not sufficient** when Θ = 1

### 4. Gatheral & Jacquier (2014) — *Arbitrage-free SVI volatility surfaces*
- **Theorem 4.2**: Sufficient butterfly conditions (B1: θφ(1+|ρ|) < 4, B2: θφ²(1+|ρ|) ≤ 4)
- **Section 2.2**: Durrleman function g(k) ≥ 0, butterfly arbitrage ⇔ negative density

### 5. Martini & Mingone (2022) — *No Arbitrage SVI* / *Explicit no arbitrage domain for sub-SVIs via reparametrization*
- **Proposition 6.3**: **Necessary AND sufficient** butterfly conditions (MM conditions)
- **Equation (2)**: ψ² ≤ inf_{l > l₂(|ρ|)} [4θ√(1−ρ²)h² / (θ√(1−ρ²)g² − g₂)]
- **Key insight**: GJ conditions are sufficient but NOT necessary; MM conditions are exact

### 6. Mingone (2022) — *No arbitrage global parametrization for the eSSVI volatility surface*
- **Section 2.3 (Eq 3)**: Combined calendar + butterfly constraints
- **Section 3**: Global parametrization (ρᵢ, aᵢ, cᵢ) ∈ (−1,1)ᴺ × (0,∞)ᴺ × (0,1)ᴺ
- **Section 5**: Interpolation/extrapolation proofs

### 7. Roger Lee (2004) — *The Moment Formula for Implied Volatility at Extreme Strikes*
- **Theorem 3.2**: limsup σ²(k)/|k| ≤ 2βᵣ/T, βᵣ ∈ [0,2]
- **eSSVI implication**: Tail slope = (ψ/2)(1±ρ) ≤ 2 ⇔ ψ(1+|ρ|) ≤ 4 (exactly B1)

### 8. Roper (2010) — *Arbitrage Free Implied Volatility Surfaces*
- **Theorem 2.9**: Vertical spread / slope condition
- **eSSVI implication**: Implied by B1 + eSSVI structure (verified in plan §7.3–7.4)

---

## Agent Prompts

### Agent A1 — Calendar Spread Arbitrage Conditions (Pasquazzi 2023 Correction)

**Role:** Quant Researcher  
**Task:** Fix the calendar spread arbitrage conditions in the plan. The current plan uses Hendriks-Martini (2019) Proposition 3.1 which Pasquazzi (2023) proves is **incorrect**.

**What's Wrong (from plan §7.2, §8, §12, §15):**
- Plan uses HM conditions: θ₂ ≥ θ₁, ψ₂ ≥ ψ₁, |ρ₂ψ₂ − ρ₁ψ₁| ≤ ψ₂ − ψ₁
- These are **necessary but NOT sufficient** when Θ = θ₂/θ₁ = 1
- Pasquazzi shows HM conditions allow calendar arbitrage when Θ = 1 and ρ₁, ρ₂ don't satisfy special constraints
- The corridor lower bound L_cal = max(ψ₁(1−ρ₁)/(1−ρ₂), ψ₁(1+ρ₁)/(1+ρ₂)) is derived from HM and is **incomplete**

**What to Add/Remove/Fix:**
1. **Replace §7.2 Calendar Spread section** with Pasquazzi Proposition 13 corrected conditions:
   - Define Θ = θ₂/θ₁, Φ = φ₂/φ₁ = (ψ₂/θ₂)/(ψ₁/θ₁)
   - **Necessary**: Θ ≥ 1 AND ΘΦ ≥ 1 (from asymptotes) AND −ΘΦ ≤ ΘΦρ₂ − ρ₁ ≤ ΘΦ (HM condition)
   - **Sufficient (Pasquazzi Prop 13)**:
     - If Θ = 1: No calendar arbitrage **iff** (i) ρ₁ = ρ₂ = 0 and Φ ≥ 1, OR (ii) ρ₁ = ρ₂Φ and ρ₂² ≥ ρ₁²
     - If Θ > 1 and Φ ≤ 1: No calendar arbitrage **iff** ΘΦ ≥ 1 and −ΘΦ ≤ ΘΦρ₂ − ρ₁ ≤ ΘΦ
     - If Θ > 1 and Φ > 1: Additional constraints on (ρ₁, ρ₂) region (see Pasquazzi Lemma 10–12, Figure 1)
2. **Update §8 Corridor Construction**: L_cal must incorporate Pasquazzi conditions. The simple max-of-two-ratios formula is only valid for Θ > 1, Φ ≤ 1 case.
3. **Update §12 Audit**: Calendar audit must check full Pasquazzi conditions, not just HM three conditions.
4. **Update §15 Interpolation**: Verify linear interpolation in (θ, ψ, ρψ) preserves Pasquazzi conditions (Mingone §5.1 does this for HM conditions; need to check for Pasquazzi).
5. **Add to config.py**: `CALENDAR_CONDITION_VERSION = "pasquazzi_2023"` flag

**Output:** Updated `eSSVI_surface_plan (1).md` sections 7.2, 8, 12, 15; new `config.py` entries.

---

### Agent A2 — Butterfly Arbitrage Bounds (GJ vs MM Conditions)

**Role:** Quant Researcher  
**Task:** Replace the conservative Gatheral-Jacquier (GJ) sufficient butterfly bounds with the exact Martini-Mingone (MM) necessary & sufficient bounds, while keeping GJ as a fast conservative fallback.

**What's Wrong (from plan §7.1, §8, §12, §19):**
- Plan uses only GJ bounds: U_bf1 = 4/(1+|ρ|), U_bf2 = 2√(θ/(1+|ρ|))
- GJ bounds are **sufficient but NOT necessary** — they exclude valid arbitrage-free surfaces
- MM (2022) provides **exact** bounds via infimum over l > l₂(|ρ|)
- Plan §19 flags this but doesn't fix it
- The audit (§12) evaluates g(k) on a grid but the corridor (§8) uses only GJ → corridor may be tighter than reality

**What to Add/Remove/Fix:**
1. **Add §7.1.1 MM Butterfly Conditions**: Document the exact condition from Martini-Mingone Proposition 6.3:
   - ψ ≤ 4/(1+|ρ|)  (same as GJ B1 — necessary)
   - ψ² ≤ ℱ_MM(θ, |ρ|) where ℱ_MM(θ, |ρ|) = inf_{l > l₂(|ρ|)} [4θ√(1−ρ²)h²(l,|ρ|) / (θ√(1−ρ²)g²(l,|ρ|) − g₂(l,|ρ|))]
   - Define g, h, g₂, N, l₂ per MM paper (see §2.2.2 of Mingone 2022)
2. **Update §8 Corridor Upper Bound**: 
   - U_bf = min(U_bf1, U_bf2_GJ, U_bf_MM)
   - U_bf_MM = √ℱ_MM(θ, |ρ|) — requires 1D minimization over l at each corridor evaluation
   - Add config `BUTTERFLY_BOUND_MODE = "mm_exact"` | `"gj_conservative"` | `"both"`
3. **Update §12 Audit**: 
   - Primary audit: evaluate g(k) analytically on dense k-grid (already in plan — this is the ground truth)
   - Corridor bound: use MM when `BUTTERFLY_BOUND_MODE = "mm_exact"`, else GJ
4. **Add to config.py**: `BUTTERFLY_BOUND_MODE`, `MM_L_GRID_POINTS = 200`, `MM_L2_TOL = 1e-6`

**Output:** Updated `eSSVI_surface_plan (1).md` sections 7.1, 8, 12; new `config.py` entries.

---

### Agent A3 — Anchor Reparameterization & Exact θ Solution

**Role:** Quant Researcher  
**Task:** Fix the anchor reparameterization to use the exact solution instead of the first-order approximation.

**What's Wrong (from plan §5, §8, §14):**
- Plan uses θ_t ≈ θ*_t − ρ_t ψ_t k*_t (first-order expansion around k*_t = 0)
- Corbetta §2.1 gives the **exact** relation: θ*_t = w(k*_t; θ_t, ρ_t, ψ_t) = θ_t/2 [1 + ρ_t(ψ_t/θ_t)k*_t + √((ψ_t/θ_t k*_t + ρ_t)² + (1−ρ_t²))]
- The fixed-point iteration (§5) "converges in 1–2 iterations" but:
  - At short maturities, k*_t may not be ≈ 0 (few strikes near forward)
  - The approximation error feeds into U_bf2 which uses θ*_t instead of fitted θ_t
  - §8 says "recompute θ_t and re-verify ψ_t ≤ U_ψ(θ_t)" but doesn't specify the exact formula

**What to Add/Remove/Fix:**
1. **Replace §5 Anchoring** with exact solution:
   - Given (k*_t, θ*_t, ρ_t, ψ_t), solve for θ_t exactly:
     ```
     θ_t = 2θ*_t / (1 + ρ_t(ψ_t/θ_t)k*_t + √((ψ_t/θ_t k*_t + ρ_t)² + (1−ρ_t²)))
     ```
     This is implicit in θ_t. Rearrange to quadratic in √θ_t or use 1-2 Newton iterations from θ*_t.
   - Better: Use Corbetta's reparameterization directly. The anchored slice is parameterized by (ρ, ψ) with θ = θ(ρ, ψ; k*, θ*) given implicitly by w(k*; θ, ρ, ψ) = θ*. 
   - **Exact algorithm**: For each (ρ, ψ) in corridor, compute θ by solving w(k*; θ, ρ, ψ) = θ* using Brent (1D, bounded, fast).
3. **Update §8**: U_bf2 uses exact θ_t(ρ, ψ), not θ*_t.
4. **Update §10 Objective**: The inner solve is now 2D (ρ, ψ) → θ(ρ, ψ) → w(k; θ, ρ, ψ). But Corbetta's trick: for each ρ, the corridor in ψ is computed using the exact θ(ψ). The objective is still 1D in ψ per ρ.
5. **Add to config.py**: `ANCHOR_SOLVE_METHOD = "exact_brent"` | `"fixed_point"`, `ANCHOR_THETA_TOL = 1e-10`

**Output:** Updated `eSSVI_surface_plan (1).md` sections 5, 8, 10; new `config.py` entries.

---

### Agent A4 — Corridor Construction & Empty Corridor Handling

**Role:** Quant Developer  
**Task:** Fix the corridor bounds (L_ψ, U_ψ) to use exact θ(ψ), handle the θ-monotonicity constraint properly, and fix the empty-corridor fallback order.

**What's Wrong (from plan §8, §14):**
- **L_cal formula** (lines 220–221): Uses ψ_{t-1} from previous slice but the calendar condition also requires θ_t ≥ θ_{t-1} (C1). The plan checks θ*_t ≥ θ_{t-1} as precondition but the **fitted** θ_t = θ(ρ_t, ψ_t) may violate this.
- **U_bf2 formula** (line 226): Uses θ*_t (anchor) not θ_t(ρ_t, ψ_t). Since θ_t depends on ψ_t, the upper bound is ψ-dependent.
- **Empty corridor fallback** (§14 line 329): Order is (a) widen ρ-grid, (b) carry prior params, (c) drop slice. But if corridor is empty because θ*_t < θ_{t-1} (overnight gap), widening ρ-grid won't help — need to handle calendar degeneracy first.
- **No handling of Θ = 1 case** in corridor (Pasquazzi): When θ_t ≈ θ_{t-1}, the calendar conditions change form.

**What to Add/Remove/Fix:**
1. **Rewrite §8 Corridor Algorithm**:
   ```
   For each ρ_t in ρ_grid:
       # 1. Compute ψ-dependent θ_t(ψ) exactly (from A3)
       # 2. Lower bound L_ψ(ρ_t):
           L_cal = calendar_lower_bound(ρ_t, prev_slice, θ_t(ψ))  # Pasquazzi-aware
           L_ψ = max(L_cal, ε_ψ)
       # 3. Upper bound U_ψ(ρ_t):
           U_bf1 = 4 / (1 + |ρ_t|)
           U_bf2(ψ) = 2 * sqrt(θ_t(ψ) / (1 + |ρ_t|))  # ψ-dependent!
           U_bf_MM(ψ) = sqrt(F_MM(θ_t(ψ), |ρ_t|))      # if MM mode
           U_ψ(ψ) = min(U_bf1, U_bf2(ψ), U_bf_MM(ψ)) - ε_ψ
       # 4. Find feasible ψ interval: solve L_ψ ≤ U_ψ(ψ) for ψ
           This is a 1D root-find: f(ψ) = L_ψ - U_ψ(ψ) ≤ 0
   ```
2. **Fix §14 Empty Corridor Handling**:
   - Priority 1: If θ*_t < θ_{t-1} (calendar level violation), trigger **calendar degeneracy handling** (§14) BEFORE trying ρ-grid widening
   - Priority 2: If corridor empty for all ρ due to butterfly bounds too tight → widen ρ-grid to full [−0.99, 0.90]
   - Priority 3: Carry prior minute's slice with `STALE_SLICE` flag
   - Priority 4: Drop slice, `KILL` surface
3. **Add θ-monotonicity to corridor**: The condition θ_t(ψ) ≥ θ_{t-1} must be enforced as part of L_ψ (it's a lower bound on ψ).
4. **Add to config.py**: `CORRIDOR_EPS = 1e-6`, `THETA_MONOTONICITY_EPS = 1e-8`, `EMPTY_CORRIDOR_STRATEGY = "degeneracy_first"`

**Output:** Updated `eSSVI_surface_plan (1).md` sections 8, 14; new `config.py` entries.

---

### Agent A5 — Objective Function & Weighting (Variance-Space vega² vs Vol-Space vega)

**Role:** Quant Developer  
**Task:** Resolve the weighting discrepancy flagged in §19 #1 and implement the chosen scheme consistently.

**What's Wrong (from plan §10, §19, `dataingestion.md`):**
- **Plan (§10, Image 2/4)**: Variance-space, vega² weighting: Σ ν_j² (w_mkt − w_model)²
- **dataingestion.md (§0b, §9)**: Vol-space, vega¹ weighting: Σ ν_j (IV_mkt − IV_model)²
- These are **not equivalent**: ν_vol = ν_var / (2σ√T), and (w_mkt − w_model) = 2σ√T (IV_mkt − IV_model) + (IV_mkt − IV_model)² T
- Plan §19 says "Decision required — recommend variance-space vega²" but not implemented

**What to Add/Remove/Fix:**
1. **Decide and Document in config.py**:
   ```python
   VEGA_WEIGHT_MODE = "var_vega2"  # "var_vega2" | "vol_vega1" | "vol_vega2"
   # var_vega2: Σ (ν_var_j)² (w_mkt - w_model)²  [Corbetta/Image 4 - RECOMMENDED]
   # vol_vega1: Σ (ν_vol_j) (IV_mkt - IV_model)²   [dataingestion.md]
   # vol_vega2: Σ (ν_vol_j)² (IV_mkt - IV_model)²  [alternative]
   ```
2. **Update §10 Objective Function** to use the config flag:
   - If `var_vega2`: `W_j = (vega_black76_var)²` where vega_var = ∂w/∂σ = 2σT = 2√(wT)
   - If `vol_vega1`: `W_j = vega_black76_vol` where vega_vol = ∂σ/∂σ = 1 (wait — vega in vol space is ∂C/∂σ, not 1)
   - Clarify: ν_j in ingestion is Black-76 vega = ∂C/∂σ. In variance space, ∂C/∂w = (∂C/∂σ)(∂σ/∂w) = ν_j / (2σ√T)
3. **Implement both in `objective.py`** with unit tests in `test_objective.py`
4. **Add belly boost**: `BELLY_BOOST` multiplier on belly strikes (already in plan §13) — ensure it applies in both modes
5. **Update §19**: Mark as resolved with config reference

**Output:** Updated `eSSVI_surface_plan (1).md` sections 10, 19; `config.py` entries; `objective.py` stub with both implementations.

---

### Agent A6 — Interpolation/Extrapolation & Long-Term Tail Handling

**Role:** Quant Developer  
**Task:** Fix the long-term extrapolation (currently extrapolates ψ linearly — WRONG) and verify short-term extrapolation.

**What's Wrong (from plan §15, §14):**
- **§15 Long-term extrapolation**: "hold ρ flat, extend θ,ψ along the last linear segment (or flat)" — **ψ must be held CONSTANT (flat), not linearly extrapolated**. Linear extrapolation of ψ can violate ψ(1+|ρ|) ≤ 4 and calendar monotonicity ψ_t ≥ ψ_{t-1}.
- **Corbetta §7.3 / Mingone §5.2.2**: ψ_t = ψ_N (constant), ρ_t = ρ_N (constant), θ_t = θ_N + u(t) with u'(t) ≥ 0
- **Short-term extrapolation** (§15, Corbetta §7.2): θ_t = λθ₁, ψ_t = λψ₁, ρ_t = ρ₁ with λ = t/T₁ — this is correct
- **Wing extrapolation**: Plan mentions "never let the tail slope exceed the Lee/butterfly cap" but no concrete algorithm

**What to Add/Remove/Fix:**
1. **Correct §15 Long-term Extrapolation**:
   ```
   For T > T_N (last calibrated expiry):
       θ(T) = θ_N + (θ_N - θ_{N-1})/(T_N - T_{N-1}) * (T - T_N)  # or flat θ'(T_N)
       ψ(T) = ψ_N      # CONSTANT - critical for no arb
       ρ(T) = ρ_N      # CONSTANT
   ```
2. **Add Wing Tail Cap in `surface.py`**: When querying σ(k,T) for |k| > K_MAX:
   - Compute tail slope c_± = (ψ/2)(1 ± ρ)
   - Cap at c_± = min(c_±, 2 - δ) where δ = 1e-4
   - Use linear tail: w(k) = w(K_MAX) + c_± (|k| - K_MAX)
3. **Verify §15 Interpolation**: Linear in (θ, ψ, ρψ) with ρ = (ρψ)/ψ is correct per Corbetta §7.1 and Mingone §5.1. No change needed.
4. **Add to config.py**: `EXTRAPOLATION_PSI_MODE = "flat"`, `TAIL_SLOPE_CAP = 1.9999`, `SHORT_EXTRAP_MODE = "corbetta"`

**Output:** Updated `eSSVI_surface_plan (1).md` section 15; `config.py` entries; `surface.py` stub for extrapolation.

---

### Agent A7 — Short Maturity (7 DTE) Edge Cases & Overnight Gap Handling

**Role:** Quant Researcher  
**Task:** Address the unique challenges of the front slice (7 DTE minimum per ingestion) and the overnight gap degeneracy handling.

**What's Wrong (from plan §4, §5, §14, §19):**
- **§4**: Slice universe DTE ∈ [7, 90]. At 7 DTE (≈0.019 yr), options have very few strikes, wide spreads, low vega.
- **§5 Anchor**: "Fallback if exact ATM strike fails gate: take nearest belly-qualifying strike" — but at 7 DTE, belly may be empty (OI > 100, spread ≤ 0.10, |Δ| ∈ [0.10, 0.90])
- **§14 Calendar Degeneracy**: Overnight gap can make θ*_t < θ_{t-1}. The "prefer nearest belly quote restoring monotonicity" is vague — if no belly quotes, what then?
- **§19 #5**: Expiration-day handling undecided — front slice on expiry day (DTE=0) excluded by ingestion, but what about DTE=1?
- **No special handling for ρ at short maturities**: Corbetta §5.2.1 notes "very short maturity where market conveys only information on θ... not on ρ and φ"

**What to Add/Remove/Fix:**
1. **Add §4.1 Short-Maturity Slice Handling**:
   - Minimum strikes per slice: `MIN_STRIKES_PER_SLICE = 3` (config)
   - If belly strikes < 3: widen belly criteria (spread ≤ 0.15, OI > 50) for anchor search only
   - If still < 3: use **ρ fallback** — set ρ_t = ρ_{t+1} (next maturity) or ρ_t = -0.5 (equity prior), solve only for ψ_t
   - If only 1 strike (ATM): fit θ_t = θ*_t exactly, set ρ_t, ψ_t from prior/next slice with `STRONG_PRIOR` flag
2. **Fix §14 Calendar Degeneracy Handling**:
   - Algorithm for θ*_t < θ_{t-1}:
     1. Search all strikes in slice t for any (k, θ) with θ ≥ θ_{t-1} + ε AND passing belly gates → use as new anchor
     2. If found: re-run slice calibration with new (k*_t, θ*_t)
     3. If not found: **constrained calibration** — fix θ_t = θ_{t-1} + ε, optimize ψ_t only (1D Brent) within corridor
     4. If corridor empty: flag `THETA_PROJECTED`, carry θ_t = θ_{t-1} + ε, ψ_t = ψ_{t-1}, ρ_t = ρ_{t-1}
3. **Add §4.2 Expiration-Day Handling**: 
   - DTE = 0: exclude from tradeable surface (ingestion already does this)
   - DTE = 1: include but flag `EXPIRY_IMMINENT`, increase `LAMBDA_TEMPORAL` for this slice, widen corridor ε_ψ
4. **Add to config.py**: `MIN_STRIKES_PER_SLICE = 3`, `SHORT_MATURITY_RHO_FALLBACK = "next_slice"`, `SHORT_MATURITY_RHO_PRIOR = -0.5`, `EXPIRY_IMMINENT_DTE = 1`, `THETA_PROJECTION_EPS = 1e-6`

**Output:** Updated `eSSVI_surface_plan (1).md` sections 4, 5, 14; `config.py` entries.

---

### Agent A8 — Warm-Start Seeding, Temporal Regularization, Kill Switch Logic

**Role:** Quant Developer  
**Task:** Fix the warm-start seeding (can seed outside corridor), clarify the two regularization axes, and harden the kill switch.

**What's Wrong (from plan §11, §12, §14, §16):**
- **§11 Warm-start**: "seed each minute's Brent bracket and ρ-grid center from prior minute's locked params" — but prior minute's ψ may be **outside current minute's corridor** (corridor changes with new data). Seeding outside corridor causes Brent to fail or clamp immediately.
- **§11 Two Regularizations**: Correctly separated (A: term-structure across maturities; B: temporal across minutes). But §11 says "warm-start from τ-1 locked params" — this is temporal prior, not term-structure.
- **§12 Kill Switch**: "if any check fails AT ALL, even by fractions of a decimal, flag KILL" — but floating point noise can cause g(k) = -1e-15. Need tolerance.
- **§16 Step 5**: Audit runs after all slices locked. But if slice t fails calendar vs t-1, we should know **during** calibration, not after.

**What to Add/Remove/Fix:**
1. **Fix §11 Warm-Start Seeding**:
   - For each slice t at minute τ:
     - Compute current corridor [L_ψ, U_ψ] at ρ = ρ_t^{τ-1} (prior minute's ρ)
     - Seed ψ_t^0 = clip(ψ_t^{τ-1}, L_ψ, U_ψ)  **not** ψ_t^{τ-1} directly
     - Seed ρ_t^0 = clip(ρ_t^{τ-1}, ρ_grid_lo, ρ_grid_hi) — but also check |ρ_t^0 − ρ_{t-1}^τ| ≤ Δρ_max
     - If prior params infeasible: seed at corridor midpoint
2. **Clarify §11 Regularization Split**:
   - Term-structure (A): λ_ρ(ρ_t − ρ_{t-1})² + λ_ψ(ψ_t − ψ_{t-1})² — **within same minute**, across maturities. Always active.
   - Temporal (B): λ_temp ‖(θ,ρ,ψ)_t^τ − (θ,ρ,ψ)_t^{τ-1}‖² — **across minutes**, same maturity. **Reset at session open**.
   - Add config: `TEMPORAL_REG_MODE = "tikhonov"` | `"warmstart_only"` | `"none"`
3. **Harden §12 Kill Switch**:
   - Numerical tolerance: `KILL_TOL = 1e-10` for g(k), calendar conditions
   - g(k) ≥ -KILL_TOL passes; g(k) < -KILL_TOL fails
   - Calendar: |ρ₂ψ₂ − ρ₁ψ₁| ≤ ψ₂ − ψ₁ + KILL_TOL
   - Log violation with (slice, condition, value, tolerance)
   - Emit last-good surface with `staleness_minutes = τ - τ_last_good`
4. **Add In-Calibration Calendar Check**: In §4 loop, after locking slice t, immediately verify calendar vs t-1. If fails, trigger fallback before proceeding to t+1.
5. **Add to config.py**: `WARMSTART_CLIP_TO_CORRIDOR = True`, `KILL_TOL = 1e-10`, `TEMPORAL_REG_MODE = "tikhonov"`, `LAMBDA_TEMPORAL = 0.01`

**Output:** Updated `eSSVI_surface_plan (1).md` sections 11, 12, 16; `config.py` entries.

---

### Agent A9 — Config Parameters & Validation (config.py)

**Role:** Quant Researcher + Developer  
**Task:** Create the complete `config.py` with all parameters referenced in the plan, organized by category, with validation.

**What to Add/Remove/Fix:**
Create `config.py` with all parameters from the plan (§17 config.py list) plus new ones from agents A1–A8. Organize into sections:

```python
# config.py — eSSVI Calibration Engine Configuration
# All parameters referenced in eSSVI_surface_plan.md

# ============================================================
# CORRIDOR & ARBITRAGE BOUNDS
# ============================================================
CALENDAR_CONDITION_VERSION = "pasquazzi_2023"  # "hendriks_martini_2019" | "pasquazzi_2023"
BUTTERFLY_BOUND_MODE = "mm_exact"              # "gj_conservative" | "mm_exact" | "both"
CORRIDOR_EPS = 1e-6
THETA_MONOTONICITY_EPS = 1e-8
KILL_TOL = 1e-10

# GJ Butterfly Bounds (used when BUTTERFLY_BOUND_MODE = "gj_conservative")
U_BF1_FACTOR = 4.0  # ψ(1+|ρ|) < 4
U_BF2_FACTOR = 2.0  # ψ²(1+|ρ|)/θ ≤ 4

# MM Butterfly Bound Parameters
MM_L_GRID_POINTS = 200
MM_L2_TOL = 1e-6

# ============================================================
# RHO GRID & OUTER SEARCH
# ============================================================
RHO_GRID_LO = -0.99
RHO_GRID_HI = 0.90
RHO_GRID_STEP = 0.01        # Δρ
RHO_MAX_STEP = 0.15         # Δρ_max between maturities
RHO_GRID_REFINE_FACTOR = 3  # refinement factor for stage 2 (§4 step 4)

# ============================================================
# ANCHOR & THETA SOLVE
# ============================================================
ANCHOR_SOLVE_METHOD = "exact_brent"   # "exact_brent" | "fixed_point"
ANCHOR_THETA_TOL = 1e-10
ANCHOR_K_STAR_TOL = 1e-8
MIN_STRIKES_PER_SLICE = 3
SHORT_MATURITY_RHO_FALLBACK = "next_slice"  # "next_slice" | "prior" | "fixed"
SHORT_MATURITY_RHO_PRIOR = -0.5

# ============================================================
# OBJECTIVE FUNCTION & WEIGHTING
# ============================================================
VEGA_WEIGHT_MODE = "var_vega2"   # "var_vega2" | "vol_vega1" | "vol_vega2"
BELLY_BOOST = 3.0
BELLY_K_ABS = 0.15
BELLY_DELTA_LO = 0.10
BELLY_DELTA_HI = 0.90
WING_REL_SPREAD_MAX = 0.25
BELLY_REL_SPREAD_MAX = 0.10

# ============================================================
# REGULARIZATION
# ============================================================
LAMBDA_RHO = 0.1          # term-structure ρ velocity penalty
LAMBDA_PSI = 0.1          # term-structure ψ velocity penalty
LAMBDA_TEMPORAL = 0.01    # temporal Tikhonov penalty
TEMPORAL_REG_MODE = "tikhonov"  # "tikhonov" | "warmstart_only" | "none"

# ============================================================
# SOLVER SETTINGS
# ============================================================
BRENT_XTOL = 1e-8
BRENT_MAX_ITER = 100
BRENT_BRACKET_EXPAND = 1.5

# ============================================================
# INTERPOLATION & EXTRAPOLATION
# ============================================================
EXTRAPOLATION_PSI_MODE = "flat"        # "flat" | "linear" (linear is WRONG for ψ)
EXTRAPOLATION_RHO_MODE = "flat"
EXTRAPOLATION_THETA_MODE = "linear"    # "linear" | "flat"
TAIL_SLOPE_CAP = 1.9999
SHORT_EXTRAP_MODE = "corbetta"         # "corbetta" | "flat"

# ============================================================
# SESSION & TIME HANDLING
# ============================================================
NO_TRADE_OPEN_MIN = 60
NO_TRADE_CLOSE_MIN = 60
SESSION_OPEN_HOUR = 9
SESSION_OPEN_MIN = 30
SESSION_CLOSE_HOUR = 16
SESSION_CLOSE_MIN = 0
COLD_START_AT_SESSION_OPEN = True

# ============================================================
# DEGENERACY & FALLBACKS
# ============================================================
EMPTY_CORRIDOR_STRATEGY = "degeneracy_first"
THETA_PROJECTION_EPS = 1e-6
EXPIRY_IMMINENT_DTE = 1
STALE_SLICE_MAX_MINUTES = 5

# ============================================================
# AUDIT GRID
# ============================================================
K_AUDIT = 3.0
AUDIT_GRID_POINTS = 400

# ============================================================
# VALIDATION
# ============================================================
def validate():
    assert RHO_GRID_LO < RHO_GRID_HI
    assert RHO_GRID_STEP > 0
    assert RHO_MAX_STEP > 0
    assert CORRIDOR_EPS > 0
    assert KILL_TOL >= 0
    assert VEGA_WEIGHT_MODE in ("var_vega2", "vol_vega1", "vol_vega2")
    assert BUTTERFLY_BOUND_MODE in ("gj_conservative", "mm_exact", "both")
    assert CALENDAR_CONDITION_VERSION in ("hendriks_martini_2019", "pasquazzi_2023")
    assert EXTRAPOLATION_PSI_MODE in ("flat", "linear")
    assert EXTRAPOLATION_RHO_MODE in ("flat", "linear")
    assert EXTRAPOLATION_THETA_MODE in ("linear", "flat")
    assert TAIL_SLOPE_CAP < 2.0
    assert LAMBDA_RHO >= 0 and LAMBDA_PSI >= 0 and LAMBDA_TEMPORAL >= 0
    print("✓ config.py validation passed")

if __name__ == "__main__":
    validate()
```

**Output:** New file `config.py` in the `essvi/` package directory.

---

### Agent A10 — Integration Lead: Cross-Agent Consistency Review & Plan Document Update

**Role:** Integration Lead (Runs LAST, after A1–A9 complete)  
**Task:** Merge all agent changes into a single consistent `eSSVI_surface_plan (1).md`, verify cross-references, update section numbers, and produce final deliverable.

**What to Do:**
1. **Wait for A1–A9 to complete** (they run in parallel)
2. **Read all updated sections** from each agent
3. **Merge into master plan document** — resolve any conflicts (e.g., A1 and A4 both modify §8; A3 and A4 both modify §5/§8)
4. **Update section numbers, cross-references, line numbers**
5. **Verify all config.py references** in plan match Agent A9's config.py
6. **Add "Changes from Campaign" appendix** to plan documenting every fix
7. **Run validation checklist**:
   - [ ] All §19 open items resolved or moved to config
   - [ ] Calendar conditions use Pasquazzi (A1)
   - [ ] Butterfly bounds use MM exact mode (A2)
   - [ ] Anchor uses exact solve (A3)
   - [ ] Corridor uses ψ-dependent θ (A4)
   - [ ] Objective uses var_vega2 (A5)
   - [ ] Extrapolation holds ψ flat (A6)
   - [ ] Short maturity handling complete (A7)
   - [ ] Warm-start clips to corridor (A8)
   - [ ] config.py complete and validated (A9)
8. **Output**: Final `eSSVI_surface_plan (1).md` and `config.py`

---

## Run Order & Parallelization

```
PARALLEL GROUP 1 (Independent, can start immediately):
  ├── Agent A1: Calendar Spread (Pasquazzi)
  ├── Agent A2: Butterfly Bounds (MM vs GJ)
  ├── Agent A3: Anchor Exact Solve
  ├── Agent A5: Objective Weighting
  ├── Agent A6: Interpolation/Extrapolation
  └── Agent A9: Config.py Creation

PARALLEL GROUP 2 (Depend on Group 1 outputs):
  ├── Agent A4: Corridor Construction (needs A1 calendar, A2 butterfly, A3 anchor)
  ├── Agent A7: Short Maturity (needs A3 anchor, A4 corridor)
  └── Agent A8: Warm-Start/Kill Switch (needs A4 corridor, A9 config)

SEQUENTIAL FINAL:
  └── Agent A10: Integration & Final Plan Update (needs ALL above complete)
```

**Estimated Timeline:**
- Group 1: 2–3 hours parallel
- Group 2: 1–2 hours parallel (after Group 1)
- Agent A10: 1 hour (after Group 2)
- **Total: ~4–6 hours wall time with parallel agents**

---

## Deliverables Checklist

| File | Produced By | Status |
|------|-------------|--------|
| `config.py` | Agent A9 | ☐ |
| `eSSVI_surface_plan (1).md` — §7.2 Calendar (Pasquazzi) | Agent A1 | ☐ |
| `eSSVI_surface_plan (1).md` — §7.1 Butterfly (MM) | Agent A2 | ☐ |
| `eSSVI_surface_plan (1).md` — §5 Anchor Exact | Agent A3 | ☐ |
| `eSSVI_surface_plan (1).md` — §8 Corridor, §14 Fallback | Agent A4 | ☐ |
| `eSSVI_surface_plan (1).md` — §10 Objective, §19 Resolved | Agent A5 | ☐ |
| `eSSVI_surface_plan (1).md` — §15 Extrapolation | Agent A6 | ☐ |
| `eSSVI_surface_plan (1).md` — §4, §14 Short Maturity | Agent A7 | ☐ |
| `eSSVI_surface_plan (1).md` — §11, §12, §16 Warm-Start/Kill | Agent A8 | ☐ |
| **Final merged `eSSVI_surface_plan (1).md`** | Agent A10 | ☐ |
| **Campaign Log** (this file) | — | ✅ |

---

## Reference: Key Equations to Implement

### Pasquazzi Calendar Conditions (Agent A1)
```
Θ = θ₂/θ₁, Φ = (ψ₂/θ₂)/(ψ₁/θ₁)

If Θ = 1:
    No arb ⟺ (ρ₁ = ρ₂ = 0 and Φ ≥ 1) OR (ρ₁ = ρ₂Φ and ρ₂² ≥ ρ₁²)

If Θ > 1 and Φ ≤ 1:
    No arb ⟺ ΘΦ ≥ 1 and −ΘΦ ≤ ΘΦρ₂ − ρ₁ ≤ ΘΦ

If Θ > 1 and Φ > 1:
    No arb ⟺ (ρ₁, ρ₂) ∈ R_Θ,Φ \ H_Θ,Φ  (see Pasquazzi Fig 1, Lemmas 10-12)
```

### MM Butterfly Bound (Agent A2)
```
ψ ≤ 4/(1+|ρ|)                                    (necessary, = GJ B1)
ψ² ≤ ℱ_MM(θ, |ρ|) = inf_{l > l₂(|ρ|)} [4θ√(1−ρ²)h² / (θ√(1−ρ²)g² − g₂)]
where:
    N(l, ρ) = √(1−ρ²) + ρl + √(l²+1)
    g(l, ρ) = N'(l, ρ)/4
    h(l, ρ) = 1 − (l − ρ/√(1−ρ²)) N'(l, ρ)/(2N(l, ρ))
    g₂(l, ρ) = N''(l, ρ) − N'(l, ρ)²/(2N(l, ρ))
    l₂(|ρ|) = tan(arccos(−|ρ|)/3)⁻¹
```

### Exact Anchor Solve (Agent A3)
```
Given (k*, θ*, ρ, ψ), solve for θ:
θ = 2θ* / (1 + ρ(ψ/θ)k* + √((ψ/θ k* + ρ)² + (1−ρ²)))

Fixed-point iteration:
θ_{n+1} = 2θ* / (1 + ρ(ψ/θ_n)k* + √((ψ/θ_n k* + ρ)² + (1−ρ²)))
Start: θ_0 = θ*
```

### ψ-Dependent Corridor (Agent A4)
```
For each ρ_t:
    θ_t(ψ) = exact_anchor_solve(k*_t, θ*_t, ρ_t, ψ)
    L_ψ = max(calendar_lower_bound(ρ_t, prev, θ_t(ψ)), ε_ψ)
    U_bf1 = 4/(1+|ρ_t|)
    U_bf2(ψ) = 2√(θ_t(ψ)/(1+|ρ_t|))
    U_bf_MM(ψ) = √ℱ_MM(θ_t(ψ), |ρ_t|)
    U_ψ(ψ) = min(U_bf1, U_bf2(ψ), U_bf_MM(ψ)) - ε_ψ
    Feasible ψ: L_ψ ≤ U_ψ(ψ)
```

---

## Notes for Agents

- **Do not modify** `dataingestion.md` — this campaign only touches the calibration plan and config
- **All agents write to the same `eSSVI_surface_plan (1).md`** — use clear section headers; Agent A10 will merge
- **Test your changes** against the Corbetta SPX 2018-01-08 results (Table 1 in paper) and Mingone TA35 data
- **Document every config parameter** you add with units and valid ranges
- **Flag any remaining open questions** in the plan's §19 for future campaigns