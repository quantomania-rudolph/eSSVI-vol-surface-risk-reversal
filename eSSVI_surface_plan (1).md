# AMD eSSVI Volatility Surface — Minute-Level Calibration Blueprint

**Scope:** Per-minute, arbitrage-free extended-SSVI (eSSVI) total-variance surface for **AMD equity options**, fit off the cleaned 1-minute panel produced by `dataingestion.md` (TimescaleDB hypertable `amd_surface_min`). This document is the operational blueprint for the calibration engine that consumes that panel. It is written to be executed by agents, not to impress — dense, exact, no fluff.

**Companion doc:** `dataingestion.md` (data contract). This plan assumes every guarantee that doc makes (clean quotes, precise `business_t`, local Numba `vega`, point-in-time `r`/`q`, prior-session OI, survivorship-safe universe).

---

## 0. Conventions locked for this document (read first)

| Symbol | Meaning | Notes |
|--------|---------|-------|
| `k` | log-moneyness `ln(K/F)` | F = forward, **not** spot. Per-expiration. |
| `w(k,T)` | **total** implied variance `σ²·T` | eSSVI lives here, **not** in vol space. |
| `σ(k,T)` | implied vol | `σ = √(w/T)`. Translation only, never the fit target directly. |
| `θ_t` | ATM total variance of slice `t` | `w(0,T_t)=θ_t`. **Level.** |
| `ρ_t` | correlation / skew sign, `∈(−1,1)` | ATM `w`-skew `∂_k w|_0 = ρ_t ψ_t`. |
| `φ_t` | curvature / vol-of-vol scale, `>0` | raw SSVI wing parameter. |
| **`ψ_t ≡ θ_t · φ_t`** | **transformed curvature** | **THE convention used everywhere below.** All calendar/butterfly bounds are written in `ψ = θφ`. Do **not** silently swap to the `ψ = φ√θ` skew-scaling convention seen in some papers — the corridor algebra breaks. |
| `t` | **maturity-slice index** (`T_1<T_2<…`) | Sequential loop runs over *maturities within one minute snapshot*. |
| `τ` (subscript) | **wall-clock minute** | Temporal axis. Kept explicitly distinct from `t`. Conflating the two is the #1 logic bug (see §14, §Stress-Test). |

**The eSSVI slice (Image 6, verified vs Hendriks-Martini 2019 / Corbetta 2019):**

```
w(k, T_t) = θ_t/2 · ( 1 + ρ_t φ_t k + sqrt( (φ_t k + ρ_t)² + (1 − ρ_t²) ) )
```

**Forward Price (AMD equity options):**
```
F_t = S · exp((r − q) · T_t)
```
where `S` = same-minute stock `close` (ingestion §8), `r` = tenor-matched risk-free rate, `q` = dividend yield (from point-in-time dividend curve, ingestion §7). **Critical:** `q` is NOT zero for single-stock equity options. Using `r=0, q=0` (SPX-style) introduces systematic forward bias → shifted `k` → wrong skew.

Closed-form derivatives (use these; **never** finite-difference — FD noise is the seed of "grid leakage"):
```
u   = φ_t k + ρ_t
D   = u² + (1 − ρ_t²)
w   = θ_t/2 · (1 + ρ_t φ_t k + √D)
w'  = (θ_t φ_t / 2) · ( ρ_t + u/√D )
w'' = ( θ_t φ_t² (1 − ρ_t²) ) / ( 2 · D^{3/2} )      # always > 0
```

**Forward price (AMD equity options — American-style):**
```
F_t = S_τ · exp( (r_t − q_t) · T_t )
```
where `S_τ` = same-minute stock `close` (ingestion §8), `r_t` = tenor-matched risk-free rate (ingestion §7), `q_t` = **dividend yield** from point-in-time dividend curve (ingestion §7 — NEW), `T_t` = precise `business_t` in years (ingestion §6). **Critical:** `q_t` is NOT zero for single-stock equity options; ignoring it shifts `k = ln(K/F)` and biases the entire surface.

---

## 1. What the engine does, in one paragraph

Every minute, for AMD, the engine reads all clean option rows for that minute from `amd_surface_min`, groups them by expiration into maturity slices, and fits one arbitrage-free eSSVI slice `(θ_t, ρ_t, ψ_t)` per expiration **sequentially from nearest to farthest maturity**. Each slice is *anchored* to the real ATM market quote (`k*_t, θ*_t`), then its parameters are found by (a) scanning a bounded grid of `ρ_t`, (b) for each `ρ_t` computing the **no-arbitrage corridor** `[L_ψ, U_ψ]` from the locked previous slice + butterfly limits, (c) running Brent's method inside that corridor to minimize a **vega²-weighted** fit error plus a term-structure smoothness penalty, (d) clamping any solver overshoot back into the corridor. After all slices are fit, a final surface-wide arbitrage audit runs; any violation trips a **kill switch** before the surface is allowed to inform trading. Linear interpolation in `(θ, ρψ, ψ)` between calibrated slices yields the continuous surface. The whole thing re-anchors cold at each session open and never chains temporal state across the overnight gap.

Data flow (maps to Corbetta et al. 2019 robust calibration):
```
[Clean minute panel] → [Extract anchor (k*_t, θ*_t)] → [Grid ρ_t]
   → [Admissible corridor [L_ψ,U_ψ] for ψ_t] → [Vega²-weighted Brent solve + velocity penalty]
   → [Clamp] → [Lock (θ_t,ρ_t,ψ_t)] → [Next slice t+1] → … → [Surface arbitrage audit + kill switch]
   → [Linear inter-slice interpolation] → [Continuous arbitrage-free surface σ(k,T)]
```

---

## 2. Execution-reality traps the engine must defend against (Image 1)

These are *not* optional. Each has a concrete mitigation wired into the pipeline.

1. **Forward-price disconnect (baseline shift).** `k = ln(K/F)` sits on top of `F`. A tiny `F` error shifts *every* `k` horizontally → the fitter reads a fake skew and misprices the 25Δ risk-reversal.
   *Mitigation:* `F = S·e^{(r−q)T}` with `S` = same-minute stock `close` (ingestion §8), tenor-matched `r` (ingestion §7), `q` = point-in-time dividend yield (ingestion §7 — NEW), and precise `business_t` (ingestion §6). Spot and option bars floored to the identical minute (ingestion §12.2). **Strict time-matching is a hard prerequisite.**
2. **Intraday expiration decay (time-to-maturity jump).** Treating `T` as integer days injects a large decay error between 10:00 and 15:30. `w = σ²T` then lags reality; short-dated slices are mispriced.
   *Mitigation:* `business_t` in **business minutes / (390·252)** from `pandas_market_calendars` XNYS, half-days = 210 min (ingestion §6). Never calendar-day `T` in the math.
3. **Execution slippage (phantom dislocation).** The surface is built on **mid**; you trade at **bid/ask**. A 3σ eSSVI-vs-market dislocation worth $0.50 on mid can be fully eaten by the spread you must cross, twice.
   *Mitigation:* (a) `rel_spread` gates in ingestion §5#4 (hard reject > 0.25, belly-exclude > 0.10); (b) OI liquidity gate (§3); (c) **downstream signals must be re-evaluated against executable (bid/ask) price, not the mid the surface was fit on.** The surface is a *fair-value* estimate; slippage modeling lives in the signal layer, but the surface must *carry* `bid, ask, spread` (ingestion §10 stores them).

---

## 3. Data contract — how `dataingestion.md` feeds the fit

The fitter reads per clean row (ingestion §10 "Downstream contract"): `ts, expiration, strike, option_type, implied_vol, forward_price, business_t, vega`, plus `bid, ask, spread, open_interest, quality_flags, delta` for gating/audit. Mapping of each ingestion guarantee to a fit requirement:

| Ingestion guarantee (source §) | Fit-engine use | What breaks without it |
|--------------------------------|----------------|------------------------|
| `forward_price = S·e^{(r−q)T}`, same-minute `S` (§8, §12.2) | defines `k = ln(K/F)` — the x-axis | Trap #1: whole surface shifts, fake skew |
| `business_t` precise (§6) | `w=σ²T`, `d1`, vega, forward | Trap #2: short-dated slices mispriced |
| local Numba `vega` Black-76 (§9) | **weight `ν_j`** in objective | unweighted fit → chases illiquid-wing noise |
| `implied_vol > 0.005` from clean mid (§5#5) | market target `w_mkt = σ_mkt²·T` per strike | zero/garbage IV → NaN in fit |
| No-quote / crossed / subpenny (§5 #1,2,3) | removes structurally broken quotes | fabricated outliers, non-convergence |
| Spread two-tier (§5#4): hard>0.25, belly>0.10 | **belly membership** (§13) & wing down-weight | jagged surface where liquidity dried up |
| Intrinsic-value floor (§5#6) | removes arbitrageable prices pre-fit | fit to arbitrage → nonsense IV |
| Cross-strike monotonicity (§5#7) | removes butterfly-broken raw quotes | eSSVI diverges / fake risk-reversal |
| **Open interest > 100, prior-session (§5#8, §12.8)** | **liquidity gate + no-leakage** | phantom liquidity; same-day OI leaks future info |
| Survivorship-safe universe (§12.7) | slice membership as-of date | look-ahead: fitting contracts that didn't exist |
| `quality_flags` bitmask (§10) | belly/tolerance-aware weighting | can't reconstruct marginal rows |

### 3.1 OI condition & OI protection (explicit)
- **Gate:** keep `open_interest > 100` per contract per day (ingestion §5#8). Tight spread + zero OI = *phantom* market; real slippage ≫ mid → such a strike must not anchor or weight the fit.
- **Leakage protection:** OI prints EOD. In `OI_MODE="strict"` (ingestion §12.8, config default) the engine sees only **prior session (D−1)** OI during day-D minutes. Treat OI as a *slow, past-only* liquidity mask — never join same-day EOD OI to intraday minutes for any tradeable surface.
- **Consequence:** OI is a *membership/weight modifier*, not a fit coordinate. A strike failing OI is dropped from the calibration set but its row is still stored (audit), so wing shape can be sanity-checked offline.

### 3.2 Belly vs wing partition (drives §13 weighting)
A strike is in the **belly** (core fit region) iff it passes: OI>100, `rel_spread ≤ 0.10`, `0.10 ≤ |delta| ≤ 0.90`, AND `|k| ≤ BELLY_K_ABS` (config, e.g. `0.15`). Strikes with `0.10 < rel_spread ≤ 0.25` are **wing-only**: retained for shape/wings, excluded from the belly error term. This is the data-side realization of Image 4 §3 ("fit the liquid core with surgical precision, bend flexibly through noisy OTM").

### 3.3 Which quotes populate the fit (OTM selection + put-call consistency)
The panel carries both rights (`right=both`). To avoid double-counting a strike and to use the liquid side, the fit target per strike is the **OTM quote**: puts for `k < 0`, calls for `k > 0`, either at `k≈0` (the anchor strike takes the tighter-spread side). This mirrors the delta band (ingestion §4, `0.10 ≤ |delta| ≤ 0.90`) which already discards deep-ITM legs. **Put-call IV consistency check:** under the correct forward, the put and call IV at the same strike must match; a systematic call-minus-put IV bias across strikes is a **forward/rate error signature** (Trap #1) — surface it as a `PARITY_SKEW` diagnostic rather than fitting through it. One clean total-variance point `w_mkt(k_j)=σ_mkt(k_j)²·T_t` enters the objective per strike, never two.

---

## 4. The sequential corridor — master algorithm (one minute snapshot)

### 4.1 Short-Maturity Slice Handling (DTE ≤ 14)

Front slices (7–14 DTE) have sparse strikes and noisy IVs. Special handling required:

**Minimum strike requirements:** If slice has < MIN_STRIKES_PER_SLICE (config=3) valid strikes after filtering:
- Try widening belly criteria for anchor search only: spread ≤ 0.15 (from 0.10), OI > 50 (from 100), |Δ| ∈ [0.05, 0.95]
- If still < 3 strikes: trigger **ρ fallback**

**ρ fallback for thin slices:** At very short maturities, market quotes contain little skew information (Corbetta §5.2.1). If slice has ≥ 1 but < 3 belly strikes:
- Set ρ_t = ρ_{t+1} (next maturity's ρ), solve only for ψ_t (preferred)
- Or set ρ_t = SHORT_MATURITY_RHO_PRIOR (-0.5 for equities)

**Anchor quality flags:** ANCHOR_EXACT_ATM, ANCHOR_NEAREST_BELLY, ANCHOR_WIDENED_GATES, ANCHOR_RHO_FALLBACK

**Expiration-day (DTE=1):** Include but flag `EXPIRY_IMMINENT`: widen corridor `ε_ψ` by 10×, increase temporal penalty `λ_temp` by 10×. **Numerical handling for DTE=1 (T ~ 1e-5):**
- Use `long double` precision for `w = σ²·T` and `θ_t` calculations
- Scale vega weights: `W_j = W_j * (T_ref / T_t)` where `T_ref = 0.01` (≈2.5 DTE) to avoid underflow
- Skip `ψ` solve if `T_t < MIN_T_FOR_PSI_SOLVE` (config=1e-4); fix `ψ_t = ψ_{t+1}` (next maturity)
- Use `θ_t = θ*_t` directly (anchor) since `θ` is well-determined even at DTE=1
DTE ∈ [2,6] excluded by ingestion.

```
INPUT:  clean rows for minute τ, grouped into slices t=1..N by expiration (T_1<…<T_N).
        Slice universe = AMD expirations with calendar DTE ∈ [7,90] (ingestion §4); each
        expiration contributes only over its ~83-day eligible life. Precise business_t (ingestion §6)
        drives the math even though DTE (calendar) selects membership.
        prev-minute locked params {(θ,ρ,ψ)_t}^{τ-1}  (None if session open → cold start)
OUTPUT: locked {(θ_t, ρ_t, ψ_t)}, arbitrage-audited

for t = 1 .. N (nearest → farthest maturity):        # SEQUENTIAL IN MATURITY
    (k*_t, θ*_t) = extract_anchor(slice_t)            # §5
    ρ_grid = grid(ρ_lo, ρ_hi, Δρ)                     # §9, clipped by |ρ_t−ρ_{t-1}|≤Δρ_max
    best = None
    for ρ_t in ρ_grid:
        # θ_t pinned by anchor given (ρ_t, ψ_t):  θ_t ≈ θ*_t − ρ_t ψ_t k*_t   (§5)
        [L_ψ, U_ψ] = corridor(ρ_t, locked slice t-1, θ_anchor=θ*_t)   # §8 calendar ∪ butterfly
        if L_ψ > U_ψ:  continue                        # infeasible ρ_t (tight-squeeze) → skip
        ψ_t = brent_min(objective, L_ψ, U_ψ, ρ_t)      # §10 inner solve (λ_ψ active)
        ψ_t = clamp(ψ_t, L_ψ, U_ψ)                      # §12 hard clamp
        θ_t = fixed_point_theta(θ*_t, ρ_t, ψ_t, k*_t)   # §5 (1–2 iters; k*≈0 ⇒ fast)
        loss = total_loss(θ_t, ρ_t, ψ_t, prev_slice, prev_minute)   # §10 (+λ_ρ, +temporal prior)
        if best is None or loss < best.loss:  best = (θ_t, ρ_t, ψ_t, loss)
    if best is None:  handle_empty_corridor(t)          # §14 degeneracy path
    lock slice_t = best                                 # Corbetta lock-and-advance
verify_surface(all slices)                              # §12 audit + kill switch
```

Key properties: (1) **anchoring** collapses the `θ` dimension → 1-D inner solve in `ψ`; (2) the corridor is **recomputed per `ρ_t`** because calendar bounds depend on `ρ_t`; (3) the previous *maturity* slice is locked before the next starts (Corbetta transitivity ⇒ global calendar-arbitrage-freeness).

---

## 5. Anchoring parameter pair `(k*_t, θ*_t)` — Exact Solution (Corbetta 2019 §2.1)

**What:** For each maturity slice, find the market quote whose strike is closest to the forward → its log-moneyness `k*_t` and total implied variance `θ*_t = σ*²·T_t`. Most liquid, tightest point on the chain.

**Why:** Force the slice through `(k*_t, θ*_t)`. This removes one degree of freedom (θ_t), reducing the inner problem to 1D in `ψ_t` for each `ρ_t`.

**Exact Reparameterization:**
Given `(k*_t, θ*_t, ρ_t, ψ_t)`, the slice parameter `θ_t` is defined implicitly by:
```
θ*_t = w(k*_t; θ_t, ρ_t, ψ_t) = θ_t/2 [ 1 + ρ_t (ψ_t/θ_t) k*_t + √( (ψ_t/θ_t k*_t + ρ_t)² + (1 − ρ_t²) ) ]
```

Let `φ_t = ψ_t/θ_t`. Rearranging:
```
2θ*_t/θ_t − 1 − ρ_t φ_t k*_t = √( (φ_t k*_t + ρ_t)² + 1 − ρ_t² )
```
Square both sides (RHS ≥ 0 always):
```
(2θ*_t/θ_t − 1 − ρ_t φ_t k*_t)² = (φ_t k*_t + ρ_t)² + 1 − ρ_t²
```
Substitute `φ_t = ψ_t/θ_t` and multiply by `θ_t²`:
```
(2θ*_t − θ_t − ρ_t ψ_t k*_t)² = (ψ_t k*_t + ρ_t θ_t)² + θ_t²(1 − ρ_t²)
```
This is a **quadratic in θ_t**. Expand and solve exactly:

**Exact Closed-Form Solution:**
```
θ_t = θ*_t − ρ_t ψ_t k*_t + (ψ_t² k*_t² (1 − ρ_t²)) / (4 θ*_t)
```

**Verification:**
- If `k*_t = 0`: `θ_t = θ*_t` ✓
- If `ρ_t = 0`: `θ_t = θ*_t + ψ_t²k*_t²/(4θ*_t) > θ*_t` ✓ (symmetric smile has min at k=0)
- First-order in `k*_t`: `θ_t ≈ θ*_t − ρ_t ψ_t k*_t` — matches Corbetta's first-order approximation, plus a **positive** quadratic correction term.

**Algorithm (Exact, No Iteration):**
For each `(ρ_t, ψ_t)` candidate:
1. Compute `θ_t` exactly using the formula above
2. Verify `θ_t > 0` (if not, candidate invalid)
3. Compute `w_eSSVI(k; θ_t, ρ_t, ψ_t)` for all strikes
4. Evaluate objective

**Benefits:** (a) removes a full search dimension; (b) nails the surface to the liquid belly (accuracy where capital sits); (c) with monotone ATM variance in a healthy market, `θ*_t` naturally supports `θ_t ≥ θ_{t-1}` — **checked, not assumed** (§8, §14); (d) **no iteration needed** — speed improvement in the inner loop.

**AMD note:** anchor uses `forward_price` (`F=S·e^{(r−q)T}`). PM-settled; the ATM strike on AMD's `$0.01` grid is usually well-populated. Fallback if the exact ATM strike fails a gate: take the nearest *belly-qualifying* strike and set `k*_t` to its actual `k` (do **not** fabricate `k*=0`).

---

## 6. Transformed curvature parameter `ψ_t = θ_t φ_t`

Early SSVI carried `θ_t`, `φ_t` separately. Production collapses to `ψ_t = θ_t φ_t` because **the Hendriks-Martini calendar inequalities become affine in `ψ`**, making the corridor closed-form (no inner root-find for bounds).

- ATM `w`-skew `= ρ_t ψ_t` (from `∂_k w|_0`), so `ρψ` is the "skew" quantity in every cross-slice condition.
- Given locked slice `t−1` and candidate `ρ_t`, the calendar lower bound on `ψ_t` (unrolling `|ρ_tψ_t − ρ_{t-1}ψ_{t-1}| ≤ ψ_t − ψ_{t-1}`, which itself forces `ψ_t ≥ ψ_{t-1}`) is (Image 4 §2, verified):
```
L_cal(ρ_t) = max(  ψ_{t-1} · (1 − ρ_{t-1})/(1 − ρ_t) ,
                   ψ_{t-1} · (1 + ρ_{t-1})/(1 + ρ_t)  )
```
Evaluated **instantly** for any candidate `ρ_t` — no guessing (Image 4 §2 "Pipeline Benefit").

---

## 7. The four no-arbitrage constraints (Image 3), in depth

eSSVI enforces static arbitrage-freeness through **two hard, actively-enforced conditions** (butterfly, calendar) and **two that are structurally implied** by the first two given the eSSVI functional form (vertical-spread slope, asymptotic wing). Understanding *which are enforced vs implied* is the single most important nuance for a correct, fast engine.

### 7.1 Butterfly arbitrage — convexity (Gatheral-Jacquier 2014; Durrleman)
No negative risk-neutral density ⇔ Durrleman function non-negative for all `k`:
```
g(k) = ( 1 − k·w'(k)/(2 w(k)) )²  −  (w'(k)²/4)·( 1/w(k) + 1/4 )  +  w''(k)/2   ≥ 0
```
Violation `g(k)<0` anywhere ⇒ butterfly arbitrage. **Enforcement (two layers):**
- **Analytic corridor (primary, no grid needed):** the Gatheral-Jacquier *closed-form* sufficient butterfly conditions, in the `ψ=θφ` convention:
  ```
  (B1)  ψ_t (1 + |ρ_t|)  <  4          # ⇒ upper bound  U_bf1 = 4 / (1+|ρ_t|)
  (B2)  ψ_t² (1 + |ρ_t|) / θ_t  ≤  4    # ⇒ upper bound  U_bf2 = 2·√( θ_t / (1+|ρ_t|) )
  ```
  (B1) is necessary (strict for `ρ≥0`); (B2) is sufficient. Enforcing both as `ψ_t ≤ min(U_bf1,U_bf2)` **guarantees** `g(k)≥0` without scanning `k` — this is the real defense against grid leakage.
- **Numerical audit (secondary, §12):** still evaluate `g(k)` analytically on a dense `k`-grid post-fit as a belt-and-suspenders check. Because `w,w',w''` are closed-form and the wings are linear-tailed (below), a violation here signals a bug, not a modeling gap.

### 7.1.1 Martini-Mingone (MM) Necessary & Sufficient Butterfly Conditions (2022)

The Gatheral-Jacquier conditions (B1, B2) are **sufficient but not necessary**. Martini & Mingone (2022, Proposition 6.3) derive the **exact** no-butterfly-arbitrage boundary for SSVI/eSSVI.

In the eSSVI parameterization `w(k) = θ/2 [1 + ρ(ψ/θ)k + √((ψ/θ k + ρ)² + (1−ρ²))]`, the conditions are:

**Necessary (same as GJ B1):**
  `ψ ≤ 4 / (1 + |ρ|)`

**Necessary AND Sufficient:**
  `ψ² ≤ ℱ_MM(θ, |ρ|)`

where `ℱ_MM(θ, |ρ|) = inf_{l > l₂(|ρ|)} [4θ√(1−ρ²) h²(l, |ρ|) / (θ√(1−ρ²) g²(l, |ρ|) − g₂(l, |ρ|))]`

and:
```
N(l, ρ) = √(1−ρ²) + ρ l + √(l² + 1)
g(l, ρ) = N'(l, ρ) / 4
h(l, ρ) = 1 − (l − ρ/√(1−ρ²)) N'(l, ρ) / (2 N(l, ρ))
g₂(l, ρ) = N''(l, ρ) − N'(l, ρ)² / (2 N(l, ρ))
l₂(|ρ|) = [tan(arccos(−|ρ|)/3)]⁻¹
```

**Properties:**
- `ℱ_MM(θ, |ρ|) ≤ 4θ/(1+|ρ|) = ℱ_GJ(θ, |ρ|)`  (MM bound is WIDER or equal)
- Equality holds only at specific `(θ, ρ)` — typically MM allows larger `ψ`
- For `|ρ| → 1`, MM bound approaches GJ bound
- For short maturities (small `θ`), MM bound can be significantly wider

**Critical Implementation Details:**

1. **Denominator Safety**: The denominator `θ√(1−ρ²)g²(l, |ρ|) − g₂(l, |ρ|)` can be **zero or negative** for some `l`. The feasible `l` region is where denominator > 0. Implementation must check this.

2. **Domain of `l`**: `l > l₂(|ρ|)` where `l₂(|ρ|) = [tan(arccos(−|ρ|)/3)]⁻¹`. For `|ρ| → 1`, `l₂ → 0`. For `ρ=0`, `l₂ = √3 ≈ 1.732`.

3. **Unimodality**: The objective function in `l` is typically unimodal. Brent's method on `(l₂, L_MAX)` works, but must handle the denominator sign change.

**Precomputation Grid (REQUIRED for Production Speed):**
Computing `ℱ_MM(θ, |ρ|)` via 1D minimization at every corridor evaluation is too slow for a 1-minute calibration. Instead:

```python
# config.py
MM_RHO_GRID = np.linspace(-0.99, 0.99, 200)    # 200 points in ρ
MM_THETA_GRID = np.logspace(-5, 1, 100)        # θ from 1e-5 to 10
MM_L_MAX = 1000.0
MM_L_GRID_POINTS = 500

# Precompute at startup:
MM_BOUND_TABLE = np.zeros((len(MM_RHO_GRID), len(MM_THETA_GRID)))
for i, ρ in enumerate(MM_RHO_GRID):
    for j, θ in enumerate(MM_THETA_GRID):
        MM_BOUND_TABLE[i, j] = compute_F_MM(θ, abs(ρ))

# At runtime, use bilinear interpolation:
def F_MM(θ, abs_ρ):
    i = np.searchsorted(MM_RHO_GRID, abs_ρ) - 1
    j = np.searchsorted(MM_THETA_GRID, θ) - 1
    # bilinear interpolation (clamp indices)
    ...
```

**Config additions:**
```python
MM_RHO_GRID_POINTS = 200
MM_THETA_GRID_POINTS = 100
MM_L_MAX = 1000.0
MM_L_GRID_POINTS = 500
BUTTERFLY_BOUND_MODE = "mm_exact"  # "gj_conservative" | "mm_exact" | "both"
```

**Implementation in `constraints.py`:**
```python
def mm_butterfly_bound(θ, abs_ρ):
    """Compute ℱ_MM(θ, |ρ|) = inf_{l > l₂} [4θ√(1−ρ²)h² / (θ√(1−ρ²)g² − g₂)]"""
    if abs_ρ >= 1.0:
        return 4.0 * θ / (1.0 + abs_ρ)  # GJ bound at boundary
    
    l2 = 1.0 / math.tan(math.acos(-abs_ρ) / 3.0)
    
    def objective(l):
        # Compute N, N', N'' at l
        sqrt_1mr2 = math.sqrt(1 - abs_ρ*abs_ρ)
        N = sqrt_1mr2 + abs_ρ * l + math.sqrt(l*l + 1)
        N_prime = abs_ρ + l / math.sqrt(l*l + 1)
        N_double_prime = 1.0 / (l*l + 1)**1.5
        
        g = N_prime / 4.0
        h = 1.0 - (l - abs_ρ/sqrt_1mr2) * N_prime / (2.0 * N)
        g2 = N_double_prime - N_prime*N_prime / (2.0 * N)
        
        denom = θ * sqrt_1mr2 * g*g - g2
        if denom <= 0:
            return float('inf')  # Invalid region
        
        return 4.0 * θ * sqrt_1mr2 * h*h / denom
    
    # Brent on (l2 + eps, L_MAX)
    try:
        return brent(objective, l2 + 1e-8, MM_L_MAX, tol=1e-8)
    except:
        return 4.0 * θ / (1.0 + abs_ρ)  # Fallback to GJ
```

**Validation:** Compare MM table against Corbetta SPX 2018-01-08 calibrations and Mingone TA35 data — all published points should satisfy `ψ² ≤ ℱ_MM(θ, |ρ|)`.

### 7.2 Calendar-Spread Arbitrage — Time Monotonicity (Pasquazzi 2023 Correction)

**Pasquazzi (2023) proves that the Hendriks-Martini (2019) Proposition 3.1 conditions are *incorrect* — they are necessary but not sufficient when Θ = θ₂/θ₁ = 1.** The corrected necessary and sufficient conditions per Pasquazzi Proposition 13 are:

Let slice 1 = nearer maturity (θ₁, ρ₁, φ₁), slice 2 = farther maturity (θ₂, ρ₂, φ₂).
Define:
- Θ = θ₂/θ₁
- Φ = φ₂/φ₁ = (ψ₂/θ₂)/(ψ₁/θ₁)

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

**Critical implication for calibration (critical for §14 overnight gaps):**
When Θ = 1 (θ₁ = θ₂), the *only* no-arbitrage cases are:
- ρ₁ = ρ₂ = 0 with Φ ≥ 1  (flat correlation, increasing curvature)
- ρ₁ = ρ₂ with Φ = 1      (identical slices — since Θ=1, they are identical)

**This is much stricter than HM.** HM would allow Θ=1, ρ₁≠ρ₂ as long as |ρ₂ψ₂−ρ₁ψ₁| ≤ ψ₂−ψ₁. **Pasquazzi says NO — if θ₁=θ₂ and ρ₁≠ρ₂, the slices cross → calendar arbitrage.** This is critical for overnight gap handling (§14) where θ*_t ≈ θ_{t-1} can happen.

**Practical implementation for sequential calibration:**
For calibration, we use the **global parametrization of Mingone (2022)** which automatically satisfies all cases via a product-of-intervals parametrization, avoiding case analysis entirely. Alternatively, for sequential calibration we implement corridor bounds per Case A/B/C above (see §8).

### 7.3 Vertical-spread arbitrage — slope (Roper 2010)
Call price non-increasing in strike (`∂C/∂K ≤ 0`) ⇔ `∂d1/∂k ≤ 0`, `d1(k) = −k/√w(k) + √w(k)/2`. Reduces to:
```
w'(k) ≤ 2 w(k) / k     for k > 0   (mirror for k < 0)
```
Violation `w'(k) > 2w(k)/k` (k>0) ⇒ `∂C/∂K > 0`. **Status: structurally implied.** For eSSVI, the tail slope of `w` is `(ψ/2)(1±ρ)` (below), and (B1) caps it below the Roper/Lee bound; on the belly the closed-form `w'` is smooth and bounded. The engine treats Roper as an **audit assertion** in §12, not a separate corridor edge — but it *is* asserted, because a `θ_2<θ_1` degeneracy patch (§14) could otherwise sneak a slope violation past.

### 7.4 Asymptotic (wing) arbitrage — Roger Lee 2004 Moment Formula
Max growth of total variance as `k→±∞`:
```
limsup_{k→±∞}  w(k)/|k|  ≤  2
```
**Status: IDENTICAL to (B1), hence auto-satisfied.** For eSSVI the linear tail slopes are:
```
k → +∞:  w(k)/k   → (ψ/2)(1 + ρ)
k → −∞:  w(k)/|k| → (ψ/2)(1 − ρ)
```
Both ≤ 2 ⇔ `ψ(1+|ρ|) ≤ 4`, which is exactly (B1). **Therefore enforcing the butterfly upper bound `U_bf1` simultaneously guarantees Roger Lee's wing constraint — the wings cannot leak a butterfly beyond the checked grid because the eSSVI tail is *analytically linear* with a slope the butterfly bound already caps.**

**Exact grid-leakage closure (this is the rigorous fix for the "Grid Leakage Trap"):** on a linear tail `w ≈ c·k + d` (slope `c = (ψ/2)(1±ρ)`), the Durrleman function has the closed limit
```
lim_{k→±∞} g(k) = 1/4 − c²/16   ≥ 0   ⟺   c ≤ 2   ⟺   (B1).
```
So `g` in the far tail is **monotone toward a limit that (B1) already forces non-negative** — a butterfly literally *cannot* form beyond the audited grid. No ad-hoc linear-tail splice is needed; the parameterization *is* linear-tailed by construction and the bound is inherited. Practically: a dense `g(k)` scan on `|k| ≤ K_AUDIT` (§12) plus the guaranteed tail limit = complete butterfly coverage on `ℝ`.

> **Nuance summary (memorize):** Enforce **butterfly (B1,B2)** and **calendar (C1,C2,C3)** in the corridor. **Roper** and **Roger Lee** are then implied; assert them only as cheap post-fit audits to catch bugs and degeneracy patches.

---

## 8. Constructing the no-arbitrage corridor `[L_ψ, U_ψ]` (algorithm Step 3)

For slice `t`, locked slice `t−1`, and a candidate `ρ_t`, we compute the feasible `ψ` interval using the **exact fitted `θ_t(ψ)`** (from §5 A3) and the **full Pasquazzi calendar conditions** (from §7.2 A1). Both bounds are now **ψ-dependent**.

### 8.1 Exact `θ_t(ψ)` from Anchor

From §5 (A3), the exact anchor relation gives:
```
θ_t(ψ) = θ*_t − ρ_t ψ k*_t + ψ² k*_t² (1 − ρ_t²) / (4 θ*_t)
```
Require `θ_t(ψ) > 0` for validity.

### 8.2 Lower Bound `L_ψ(ψ)` — Calendar Arbitrage (Pasquazzi)

For `t = 1` (first slice): `L_ψ = ε_ψ`.

For `t > 1`: Given locked previous slice `(θ_prev, ρ_prev, ψ_prev)`, find the minimum `ψ` such that NO calendar arbitrage with current slice `(θ_t(ψ), ρ_t, ψ)`.

Define:
```
Θ(ψ) = θ_t(ψ) / θ_prev
Φ(ψ) = (ψ / θ_t(ψ)) / (ψ_prev / θ_prev) = ψ θ_prev / (ψ_prev θ_t(ψ))
```

The Pasquazzi conditions (Proposition 13) give the feasible `ψ` region. **We must handle all three cases:**

**Case A: Θ ≈ 1 (|Θ(ψ) − 1| < PASQUAZZI_THETA_TOL)**
When `θ_t(ψ) ≈ θ_prev`, the ONLY no-arbitrage configurations are:
- (i) ρ_t = ρ_prev = 0 AND Φ ≥ 1 → ψ ≥ ψ_prev θ_t(ψ) / θ_prev
- (ii) ρ_t = ρ_prev AND Φ = 1 → ψ = ψ_prev θ_t(ψ) / θ_prev (identical slices)

If ρ_t ≠ ρ_prev and not both zero → **INFEASIBLE** (no ψ satisfies Case A).

**Case B: Θ > 1 and Φ ≤ 1 (preferred for sequential calibration)**
We enforce `Φ ≤ 1` by construction via `ψ/ψ_prev ≤ θ_t(ψ)/θ_prev`. The conditions reduce to HM + ΘΦ ≥ 1:
```
L_cal_skew = max( ψ_prev·(1−ρ_prev)/(1−ρ_t)  [if ρ_t < 1],
                  ψ_prev·(1+ρ_prev)/(1+ρ_t)  [if ρ_t > −1] )
L_theta_mono from θ_t(ψ) ≥ θ_prev
L_ψ = max( L_cal_skew, L_theta_mono, ψ_prev, ε_ψ )
```

**Case C: Θ > 1 and Φ > 1**
Additional hyperbola constraints from Pasquazzi Lemma 10–12. In practice, for sequential calibration we **restrict to Case B** by enforcing `ψ ≤ ψ_prev · θ_t(ψ) / θ_prev` (i.e., `Φ ≤ 1`). If this makes the corridor empty, we flag and handle via §14 degeneracy.

**Implementation — Lower Bound Algorithm:**
```python
def calendar_lower_bound_pasquazzi(ρ_t, ψ, prev_slice, k_star, θ_star, config):
    θ_prev, ρ_prev, ψ_prev = prev_slice
    θ_t = θ_star − ρ_t * ψ * k_star + ψ*ψ * k_star*k_star * (1 − ρ_t*ρ_t) / (4 * θ_star)
    
    if θ_t <= 0:
        return float('inf')  # infeasible
    
    Θ = θ_t / θ_prev
    Φ = ψ * θ_prev / (ψ_prev * θ_t)
    
    # Case A: Θ ≈ 1
    if abs(Θ - 1) < config.PASQUAZZI_THETA_TOL:
        if abs(ρ_t) < config.PASQUAZZI_RHO_TOL and abs(ρ_prev) < config.PASQUAZZI_RHO_TOL:
            # Case A(i): ρ_t = ρ_prev = 0, need Φ ≥ 1
            return max(ψ_prev * θ_t / θ_prev, config.EPS_PSI)
        elif abs(ρ_t - ρ_prev) < config.PASQUAZZI_RHO_TOL:
            # Case A(ii): ρ_t = ρ_prev, need Φ = 1
            return max(ψ_prev * θ_t / θ_prev, config.EPS_PSI)
        else:
            return float('inf')  # INFEASIBLE: Θ=1 but ρ_t ≠ ρ_prev
    
    # Case B: Θ > 1 and Φ ≤ 1 (our sequential calibration domain)
    # We enforce Φ ≤ 1 → ψ ≤ ψ_prev * θ_t / θ_prev
    # This gives an UPPER bound, not lower. But we also need ψ ≥ ψ_prev (from ΘΦ ≥ 1 and Θ > 1)
    # Actually: ΘΦ = ψ/ψ_prev. ΘΦ ≥ 1 → ψ ≥ ψ_prev.
    # And the skew condition gives the two ratio bounds.
    # The Φ ≤ 1 constraint is an UPPER bound on ψ, handled in U_ψ.
    
    # Lower bound from HM skew condition + θ-monotonicity
    bound1 = ψ_prev * (1 − ρ_prev) / (1 − ρ_t) if ρ_t < 1 − config.TOL else float('inf')
    bound2 = ψ_prev * (1 + ρ_prev) / (1 + ρ_t) if ρ_t > −1 + config.TOL else float('inf')
    L_cal_skew = max(bound1, bound2)
    
    # θ-monotonicity: θ_t(ψ) ≥ θ_prev
    a = k_star*k_star * (1 − ρ_t*ρ_t) / (4 * θ_star)
    b = ρ_t * k_star
    c = θ_prev − θ_star
    disc = b*b − 4*a*c
    if disc < 0:
        return float('inf')  # θ_t(ψ) < θ_prev for all ψ
    L_theta_mono = max((b + math.sqrt(disc)) / (2*a), config.EPS_PSI)
    
    return max(L_cal_skew, L_theta_mono, ψ_prev, config.EPS_PSI)
```

**Note**: The `Φ ≤ 1` constraint (`ψ ≤ ψ_prev * θ_t / θ_prev`) is an **upper bound** on `ψ`, not a lower bound. It belongs in `U_ψ(ψ)` (see §8.3).

### 8.3 Upper Bound `U_ψ(ψ)` — Butterfly + Calendar Upper Constraints

Using exact `θ_t(ψ)`:

```python
def U_psi_of_psi(ψ, ρ_t, k_star, θ_star, prev_slice, config):
    θ = θ_star − ρ_t * ψ * k_star + ψ*ψ * k_star*k_star * (1 − ρ_t*ρ_t) / (4 * θ_star)
    if θ <= 0: return −1  # infeasible
    
    # Butterfly bounds
    U_bf1 = 4 / (1 + |ρ_t|)
    
    if config.BUTTERFLY_BOUND_MODE == "gj_conservative":
        U_bf2 = 2 * √(θ / (1 + |ρ_t|))
        U_butterfly = min(U_bf1, U_bf2)
    elif config.BUTTERFLY_BOUND_MODE == "mm_exact":
        U_bf_MM = √F_MM(θ, |ρ_t|)
        U_butterfly = min(U_bf1, U_bf_MM)
    else:  # "both"
        U_bf2 = 2 * √(θ / (1 + |ρ_t|))
        U_bf_MM = √F_MM(θ, |ρ_t|)
        U_butterfly = min(U_bf1, U_bf2, U_bf_MM)
    
    # Calendar upper bound: Φ ≤ 1  →  ψ ≤ ψ_prev * θ / θ_prev
    if prev_slice is not None:
        θ_prev, ρ_prev, ψ_prev = prev_slice
        U_calendar = ψ_prev * θ / θ_prev  # from Φ = ψ θ_prev / (ψ_prev θ) ≤ 1
        # Also need ψ ≥ ψ_prev from ΘΦ ≥ 1, but that's a lower bound
        U_ψ = min(U_butterfly, U_calendar) − config.CORRIDOR_EPS
    else:
        U_ψ = U_butterfly − config.CORRIDOR_EPS
    
    return U_ψ
```

### 8.4 Feasible `ψ` Interval

The feasible `ψ` are those where `L_ψ ≤ U_ψ(ψ)`. **Critical Note:** `U_ψ(ψ)` is **NOT guaranteed to be monotonic** in `ψ` because:

1. `θ_t(ψ) = θ*_t − ρ_t ψ k*_t + a ψ²` where `a = k*_t²(1−ρ_t²)/(4θ*_t) > 0` — a **convex parabola** in `ψ`
2. `θ_t(ψ)` decreases until `ψ = ρ_t k*_t / (2a)` then increases
3. Butterfly bounds `U_bf2`, `U_MM` increase with `θ_t`; calendar bound `U_cal = ψ_prev θ_t / θ_prev` also increases with `θ_t`
4. Result: `U_ψ(ψ)` can have a local minimum, then increase

**Therefore we cannot simply bracket `f(ψ) = U_ψ(ψ) − L_ψ` and use Brent.** We must find **all** intervals where `U_ψ(ψ) ≥ L_ψ`.

**Correct Algorithm:**
```python
def find_feasible_psi_intervals(ρ_t, prev_slice, k_star, θ_star, config):
    """Returns list of (L_ψ, U_ψ) feasible intervals (could be multiple if U_ψ non-monotonic)."""
    
    if prev_slice is None:
        L_ψ = config.EPS_PSI
        return find_feasible_above(L_ψ, None, ρ_t, k_star, θ_star, config)
    
    θ_prev, ρ_prev, ψ_prev = prev_slice
    
    # Compute L_ψ (lower bound)
    bound1 = ψ_prev * (1 − ρ_prev) / (1 − ρ_t) if ρ_t < 1 − config.TOL else float('inf')
    bound2 = ψ_prev * (1 + ρ_prev) / (1 + ρ_t) if ρ_t > −1 + config.TOL else float('inf')
    L_cal_skew = max(bound1, bound2, ψ_prev)
    
    # θ-monotonicity: find ψ where θ_t(ψ) ≥ θ_prev
    a = k_star*k_star * (1 − ρ_t*ρ_t) / (4 * θ_star)
    b = ρ_t * k_star
    c = θ_prev − θ_star
    disc = b*b − 4*a*c
    if disc < 0:
        return []  # θ_t(ψ) < θ_prev for all ψ
    
    root1 = (b - math.sqrt(disc)) / (2*a)
    root2 = (b + math.sqrt(disc)) / (2*a)
    # a > 0, parabola opens upward. θ_t ≥ θ_prev for ψ ≤ root1 OR ψ ≥ root2
    L_theta_mono = max(root2, config.EPS_PSI)
    
    L_ψ = max(L_cal_skew, L_theta_mono, config.EPS_PSI)
    
    return find_feasible_above(L_ψ, prev_slice, ρ_t, k_star, θ_star, config)


def find_feasible_above(L_ψ, prev_slice, ρ_t, k_star, θ_star, config):
    """Find all intervals [ψ_start, ψ_end] where ψ ≥ L_ψ and U_ψ(ψ) ≥ L_ψ."""
    intervals = []
    
    # Sample U_ψ on a grid to find sign changes of f(ψ) = U_ψ(ψ) - L_ψ
    ψ_grid = np.logspace(np.log10(max(L_ψ, config.EPS_PSI)), np.log10(config.U_PSI_MAX), config.U_PSI_GRID_POINTS)
    
    in_feasible = False
    interval_start = None
    
    for ψ in ψ_grid:
        U = U_psi_of_psi(ψ, ρ_t, k_star, θ_star, prev_slice, config)
        if U < 0:
            f = -1
        else:
            f = U - L_ψ
        
        if f >= 0 and not in_feasible:
            in_feasible = True
            interval_start = ψ
        elif f < 0 and in_feasible:
            in_feasible = False
            intervals.append((interval_start, ψ))
            interval_start = None
    
    if in_feasible:
        intervals.append((interval_start, ψ_grid[-1]))
    
    # Refine interval boundaries with Brent
    refined = []
    for ψ_start, ψ_end in intervals:
        try:
            exact_start = brentq(lambda ψ: U_psi_of_psi(ψ, ρ_t, k_star, θ_star, prev_slice, config) - L_ψ,
                                max(L_ψ, ψ_start * 0.999), ψ_start, xtol=config.BRENT_XTOL)
        except ValueError:
            exact_start = ψ_start
        
        try:
            exact_end = brentq(lambda ψ: U_psi_of_psi(ψ, ρ_t, k_star, θ_star, prev_slice, config) - L_ψ,
                              ψ_end, min(ψ_end * 1.001, config.U_PSI_MAX), xtol=config.BRENT_XTOL)
        except ValueError:
            exact_end = ψ_end
        
        if exact_end > exact_start:
            refined.append((exact_start, exact_end))
    
    return refined
```

**Feasibility Check (Updated):**
```python
intervals = find_feasible_psi_intervals(ρ_t, prev_slice, k_star, θ_star, config)
if not intervals:
    ρ_t is infeasible (tight-squeeze) → skip this ρ_t
else:
    # Usually only one interval [L_ψ, ψ_max] where ψ_max is the first exit point.
    feasible_ψ_range = intervals[0]
```

**Config additions:**
```python
U_PSI_MAX = 100.0          # Upper bound for ψ search
U_PSI_GRID_POINTS = 500    # Grid points for initial scan
```

### 8.5 Feasibility Check

```python
if L_ψ is None or L_ψ > ψ_max:  
    ρ_t is infeasible (tight-squeeze) → skip this ρ_t
```

### 8.6 Notes

1. **θ_t(ψ) is exact** — no fixed-point iteration needed. The closed-form from §5 (A3) is used everywhere.
2. **U_ψ(ψ) uses exact θ_t(ψ)** — after the inner `ψ_t` solve, recompute `θ_t` and re-verify `ψ_t ≤ U_ψ(θ_t)` (MUST check).
3. **Calendar level (C1) precondition**: before the `ρ` loop, check `θ*_t ≥ θ_{t-1}`. If violated, **trigger §14 degeneracy handling immediately** — do not proceed with the `ρ` grid.
4. **Pasquazzi Case A** (|Θ−1| < PASQUAZZI_THETA_TOL): if `θ_t ≈ θ_{t-1}`, corridor logic switches to Case A — if `ρ_t ≠ ρ_{t-1}` and not both zero, corridor is EMPTY. Handle via §14.
5. The `ε_ψ` interior margin makes the §12 clamp meaningful under floating-point.

---

## 9. Correlation grid resolution `Δρ` and the outer `ρ` search (Image 4 §4)

`ρ_t ∈ (−1,1)` sets skew sign/magnitude and **varies across maturities** in eSSVI (that is the whole point of "extended"). The outer loop scans `ρ_t` on a grid; two knobs:

- **Grid density `Δρ`:** step size across the candidate range. Range from the source spec: `ρ ∈ [−0.99, 0.99]` (symmetric — equity skew can be positive during takeovers, meme events, or special situations; asymmetric cap was a bug). Config `RHO_GRID_LO=-0.99`, `RHO_GRID_HI=0.99`, `RHO_GRID_STEP=Δρ` (e.g. `0.01` → ~199 candidates). Finer `Δρ` = better fit, more compute; this is a latency/accuracy dial to tune per the 1-minute budget.
- **Max inter-maturity step `Δρ_max`:** clip the candidate set to `|ρ_t − ρ_{t-1}| ≤ Δρ_max` (config `RHO_MAX_STEP`). **Why it matters (Image 4 §4):** if `ρ` jumps drastically between adjacent maturities (e.g. `ρ_1=−0.7 → ρ_2=−0.2`), the calendar corridor `L_cal` for `ψ_{t}` can constrict to a bottleneck → rough, unstable term-structure transitions (calendar jitter). Capping the step keeps the parameter path continuous and physically realistic.

The `λ_ρ` velocity penalty (§10) is the *soft* counterpart to the *hard* `Δρ_max` clip: the clip forbids large jumps; the penalty discourages medium ones and breaks ties toward smoothness.

---

## 10. Objective Function — Configurable Weighting Modes (Variance-Space vega² Recommended)

**Recommended (Corbetta 2019, Image 2/4): Variance-space vega²**

For each strike `j` in slice `t`:
```
w_mkt,j  = σ_mkt,j² · T_t          (market total variance)
w_mod,j  = w_eSSVI(k_j; θ_t, ρ_t, ψ_t)  (model total variance)

ν_vol,j  = Black76Vega(F_t, K_j, T_t, σ_mkt,j, r, q)  # r = risk-free, q = dividend yield
ν_var,j  = ν_vol,j / (2 · σ_mkt,j · √T_t) = ν_vol,j / (2 · √(w_mkt,j · T_t))

W_j = (ν_var,j)²   # Variance-space vega squared

Belly boost: if |k_j| < BELLY_K_ABS: W_j *= BELLY_BOOST
```

**Inner objective (per candidate `ρ_t`, `ψ_t` free in `[L_ψ,U_ψ]`):**
```
Error(ψ_t) = Σ_j W_j (w_mkt,j − w_mod,j)²

Inner objective = Error(ψ_t) + λ_ψ (ψ_t − ψ_{t-1})²    # term-structure smoothness (maturity)
```

Minimized by Brent's method (bounded, derivative-free).

**Outer selection (across the `ρ_t` grid):**
```
TotalLoss(ρ_t) = Error(ψ_t*(ρ_t)) + λ_ψ (ψ_t*−ψ_{t-1})² + λ_ρ (ρ_t − ρ_{t-1})²
ρ_t = argmin_ρ TotalLoss ;  (θ_t, ρ_t, ψ_t) locked
```

> **Precision point:** Inside the inner Brent solve `ρ_t` is fixed, so `λ_ρ(ρ_t−ρ_{t-1})²` is a *constant offset* that does **not** change `argmin_ψ`. It only matters in the **outer** ranking of `ρ` candidates.

**Alternative weighting modes (configurable via `VEGA_WEIGHT_MODE`):**

| Mode | Weight `W_j` | Error Term | Notes |
|------|-------------|------------|-------|
| `var_vega2` (default) | `(ν_var)²` | `Δw²` | Corbetta, variance-space, matches theory |
| `vol_vega1` | `ν_vol` | `Δσ²` | dataingestion.md, vol-space |
| `vol_vega2` | `(ν_vol)²` | `Δσ²` | Vol-space vega² |

**All modes use the SAME belly boost logic:** `W_j *= BELLY_BOOST` for `|k_j| < BELLY_K_ABS`.

**Implementation in `objective.py`:**
```python
def compute_weights(k_array, w_mkt, T, F, r, q, config):
    """Compute weights for all strikes in a slice."""
    sigma_mkt = np.sqrt(w_mkt / T)
    vega_vol = black76_vega(k_array, w_mkt, T, F, r, q)  # Black76 vega with forward F, rates r, q
    
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

def objective(psi, rho, theta_star, k_star, k_array, w_mkt, T, F, r, q, prev_params, config):
    """Full objective for given (rho, psi). theta computed exactly from anchor."""
    theta = exact_theta_from_anchor(theta_star, k_star, rho, psi, config)
    w_mod = essvi_total_variance(k_array, theta, rho, psi)
    
    W = compute_weights(k_array, w_mkt, T, F, r, q, config)
    
    data_loss = np.sum(W * (w_mkt - w_mod) ** 2)
    
    # Velocity penalty (term-structure, within same minute)
    if prev_params is not None:
        rho_prev, psi_prev = prev_params
        data_loss += config.LAMBDA_RHO * (rho - rho_prev) ** 2
        data_loss += config.LAMBDA_PSI * (psi - psi_prev) ** 2
    
    return data_loss
```

**Config additions:**
```python
VEGA_WEIGHT_MODE = "var_vega2"   # "var_vega2" | "vol_vega1" | "vol_vega2"
BELLY_BOOST = 3.0
BELLY_K_ABS = 0.15
```

---

## 11. Two distinct regularizations (do not conflate)

The word "smoothness" refers to **two orthogonal axes**. Keeping them separate is essential (this is a classic silent bug).

| | (A) Term-structure velocity (Image 2) | (B) Temporal smoothing (Image 2 Tikhonov note) |
|---|---|---|
| Axis | across **maturity** `t` (within one minute) | across **wall-clock minute** `τ` (same maturity) |
| Form | `λ_ρ(ρ_t−ρ_{t-1})² + λ_ψ(ψ_t−ψ_{t-1})²` | Tikhonov prior / warm-start toward `(θ,ρ,ψ)_t^{τ-1}` |
| Purpose | smooth smile across expiries; prevent calendar jitter | reduce minute-to-minute flicker; stabilize signals |
| Reset | none (always within a snapshot) | **RESET at each session open — never chain across the overnight gap** (§14) |

**(B) Tikhonov / λ tuning procedure (Image 2):** start `λ` small; gradually increase; rerun; pick the `λ` that minimizes temporal jitter **without** degrading belly absolute pricing; then **hardcode** it in `config.py` (`LAMBDA_RHO`, `LAMBDA_PSI`, `LAMBDA_TEMPORAL`). Tune on a representative AMD sample spanning calm + stressed sessions. Over-regularizing smears real moves; under-regularizing lets the surface chatter — the sweet spot is empirical and ticker-specific.

**CRITICAL: Normalization of Temporal Tikhonov Penalty**

The parameters have vastly different scales:
- `θ_t` ∈ [0.01, 1.0] (total variance, scales with √T)
- `ρ_t` ∈ [−1, 1] (correlation, bounded)
- `ψ_t` ∈ [0, 4] (transformed curvature, bounded by B1)

A raw squared-norm penalty `‖(θ,ρ,ψ)_t^τ − (θ,ρ,ψ)_t^{τ-1}‖²` will be dominated by `θ` changes and ignore `ρ,ψ`. **Must normalize by characteristic scales:**

```python
def temporal_penalty(current, previous, config):
    """Normalized Tikhonov penalty for temporal regularization."""
    θ, ρ, ψ = current
    θ_prev, ρ_prev, ψ_prev = previous
    
    # Characteristic scales (configurable, calibrated on AMD data)
    θ_scale = config.TEMPORAL_THETA_SCALE   # e.g., 0.1 (typical θ range)
    ρ_scale = config.TEMPORAL_RHO_SCALE     # e.g., 0.5 (typical ρ range)
    ψ_scale = config.TEMPORAL_PSI_SCALE     # e.g., 0.5 (typical ψ range)
    
    penalty = (config.LAMBDA_TEMPORAL_THETA * ((θ - θ_prev) / θ_scale) ** 2 +
               config.LAMBDA_TEMPORAL_RHO   * ((ρ - ρ_prev) / ρ_scale) ** 2 +
               config.LAMBDA_TEMPORAL_PSI   * ((ψ - ψ_prev) / ψ_scale) ** 2)
    
    return penalty
```

**Config additions:**
```python
TEMPORAL_THETA_SCALE = 0.1      # Typical θ variation scale
TEMPORAL_RHO_SCALE = 0.5        # Typical ρ variation scale
TEMPORAL_PSI_SCALE = 0.5        # Typical ψ variation scale
LAMBDA_TEMPORAL_THETA = 0.01
LAMBDA_TEMPORAL_RHO = 0.01
LAMBDA_TEMPORAL_PSI = 0.01
TEMPORAL_REG_MODE = "tikhonov"  # "tikhonov" | "warmstart_only" | "none"
```

**Alternative: Log-scale for θ (since θ > 0 and multiplicative changes are natural):**
```python
# Penalize relative change in θ, absolute in ρ, ψ
penalty = (config.LAMBDA_TEMPORAL_THETA * (log(θ / θ_prev)) ** 2 +
           config.LAMBDA_TEMPORAL_RHO   * ((ρ - ρ_prev) / ρ_scale) ** 2 +
           config.LAMBDA_TEMPORAL_PSI   * ((ψ - ψ_prev) / ψ_scale) ** 2)
```

**Recommendation**: Use log-scale for θ (Option 2) since θ represents variance level where % changes matter more than absolute.

**Warm-start seeding (Session continuity).** Within a session, seed each minute's solver from prior locked params:

```python
# Brent bracket for ψ (from previous minute's locked values):
ψ_mid = ψ_t^{τ-1},  ψ_width = ψ_mid * 0.2  # ±20% window
L_seed = max(L_ψ, ψ_mid - ψ_width)
U_seed = min(U_ψ, ψ_mid + ψ_width)
if L_seed > U_seed:  # corridor collapsed? → shrink window, log warning
    ψ_width = ψ_mid * 0.5
    L_seed = max(L_ψ, ψ_mid - ψ_width)
    U_seed = min(U_ψ, ψ_mid + ψ_width)

# ρ grid center:
ρ_center = ρ_t^{τ-1}
if abs(ρ_center) > 1 - KILL_TOL:  # near bound? → clamp to interior
    ρ_center = min(max(ρ_center, -0.99), 0.99)

# Temporal prior for objective (normalized Tikhonov):
temporal_prior = temporal_penalty((θ_t, ρ_t, ψ_t), (θ_t^{τ-1}, ρ_t^{τ-1}, ψ_t^{τ-1}), config)
```

At session open (first RTH bar or after KILL): **cold-start** — seed Brent at mid-corridor `[ψ_default / 2, ψ_default]` and `ρ_center = 0`. No temporal prior.

---

## 12. Clamp wrapper + surface audit + kill switch (Step 5)

**Hard clamp (inside the eSSVI evaluator, Image "Traps"):** if Brent returns `ψ` even fractionally outside `[L_ψ,U_ψ]` (floating-point overshoot in a micro-corridor), clamp instantly before any `w` is computed:
```
ψ_used = min(max(ψ_solver, L_ψ), U_ψ)
```
This lives in the surface calculator itself, not just the solver, so *every* consumer of `w_eSSVI` is protected.

**Post-fit surface audit (every minute, after all slices locked):** All checks use `KILL_TOL = 1e-10` numerical tolerance.

1. **Butterfly audit (g(k) ≥ 0):**
   ```python
   # Also verify against MM bound as sanity check
   if BUTTERFLY_BOUND_MODE == "mm_exact":
       if ψ_t**2 > F_MM(θ_t, abs(ρ_t)) + KILL_TOL:
           KILL "MM butterfly violation"
   # Audit g(k) on dense k-grid
   for k in np.linspace(-K_AUDIT, K_AUDIT, AUDIT_GRID_POINTS):
       if g(k) < -KILL_TOL:
           KILL f"butterfly g(k)<0 at k={k}"
       elif g(k) < 0:
           WARN "near-zero butterfly" (log only, not kill)
   ```

2. **Pasquazzi calendar-arbitrage check for every adjacent pair** (using `KILL_TOL`):
   ```python
   def check_calendar_arbitrage_pasquazzi(slice1, slice2, tol=KILL_TOL):
       # Uses same implementation as §7.2 with tolerance
   ```
   Run for every adjacent slice pair `(t-1, t)`. If any fails → KILL.

3. **Roper slope assertion:** `w'(k) ≤ 2w(k)/k + KILL_TOL` (k>0) and mirror.
4. **Roger Lee wing assertion:** `w(k)/|k| ≤ 2 - KILL_TOL` in tails.
5. Sanity: No `NaN/Inf`; `θ_t, ψ_t > 0`; `ρ_t ∈ (−1,1)`.

**Kill switch behavior:** On KILL, emit the last GOOD surface (τ_last_good) with:
- staleness_minutes = τ - τ_last_good
- reason = "KILL: butterfly violation on slice 3 at k=0.5 (g=-1.2e-8)"
- Log violation with (slice, condition, value, tolerance) to KILL_LOG_DIR

---

## 13. Belly-center emphasis (explicit, because the source stresses it)

Three coordinated mechanisms concentrate accuracy where it matters (ATM/belly), per Image 2's "MORE IMPORTANT TO OPTIMIZE FOR THE BELLY CENTER" and Image 4 §3:
1. **Anchoring** forces the slice exactly through the ATM point `(k*,θ*)` — zero error at the center by construction.
2. **Vega² weighting** `W_j=ν_j²` makes ATM/long-dated strikes dominate the least-squares.
3. **Belly boost** `BELLY_BOOST` and the belly/wing partition (§3.2) explicitly down-weight noisy OTM wings (wide-spread strikes are wing-only or dropped).
Net effect: surgical belly fit, flexible wings — matching desk economics (a 2% ATM error is massive capital misallocation; a 2% deep-OTM error worth fractions of a cent is irrelevant).

---

## 14. Daily re-anchoring, overnight gap, and no-trade windows (AMD operating rules)

**Session model (ET):** RTH 09:30–16:00. Half-days 09:30–13:00 (business-time already handled by ingestion §6).

**No-trade windows — build but don't trade:**
- **First hour 09:30–10:30** and **last hour 15:00–16:00** (config `NO_TRADE_OPEN_MIN=60`, `NO_TRADE_CLOSE_MIN=60`; last-hour boundary auto-shifts to 12:00 open on half-days): the engine **still calibrates the full surface** every minute and stores it, but tags rows `no_trade=True`. Rationale: open-auction imbalance and closing-auction pressure make quotes noisiest exactly then; we want the *surface* (for marking, warm-start continuity, research) but suppress *order generation*. Trading gate is a signal-layer flag, **not** a reason to skip calibration.

**Overnight gap rule (critical):**
- The temporal regularization / warm-start (§11-B) **must reset at each session open**. Do **not** carry `(θ,ρ,ψ)_t^{prev-close}` as a warm-start or temporal prior into the next day's 09:30 bar. An overnight gap (earnings, macro, gap-open) can move the surface materially; chaining temporal state across it injects a stale prior that fights the new regime.
- **At the first RTH bar of each day: COLD start.** Re-extract the anchor `(k*_t, θ*_t)` fresh for every expiration from that morning's quotes, seed Brent mid-corridor, no temporal prior. From the second bar onward, warm-start normally.
- The **term-structure** velocity penalty (§11-A) is unaffected — it lives entirely within a single snapshot and always applies.
- **Calendar-level degeneracy at open:** a gap can transiently make `θ*_t < θ_{t-1}` (anchor-measured ATM variance non-monotone). Handling:
  1. Search all strikes in slice t for any `(k, θ)` with `θ ≥ θ_{t-1} + ε` AND passing belly gates → use as new anchor `(k*_t, θ*_t)`, re-run slice calibration
  2. If found: re-run slice calibration with new anchor
  3. If not found: **constrained calibration** — fix `θ_t = θ_{t-1} + ε`, optimize `ψ_t` only (1D Brent) within corridor, with `ρ_t` fixed to `ρ_{t-1}` or the best grid value
  4. If corridor empty: flag `THETA_PROJECTED`, carry `θ_t = θ_{t-1} + ε`, `ψ_t = ψ_{t-1}`, `ρ_t = ρ_{t-1}`
  
  **Critical**: When `θ_t` is fixed to `θ_{t-1} + ε` (step 3-4), the anchor relation `w(k*_t; θ_t, ρ_t, ψ_t) = θ*_t` is **relaxed** — the slice no longer passes through the original anchor point. This is a controlled relaxation flagged as `THETA_PROJECTED`. Never emit a calendar-violating surface to satisfy a raw anchor.

**Empty-corridor / degenerate slice (`best is None` in §4):** if no `ρ_t` yields `L_ψ ≤ U_ψ` (extreme tight-squeeze), fall back in order:
1. **Check if CALENDAR LEVEL VIOLATION caused it**: if `θ*_t < θ_{t-1} − THETA_MONOTONICITY_EPS`, the corridor was empty due to calendar level violation — this should have been caught by the C1 precondition **before** the ρ-loop and handled via the degeneracy handler above. If somehow reached here, trigger degeneracy handler.
2. Try widening `ρ`-grid to full `[−0.99,0.90]` ignoring `Δρ_max` for this slice only (flag `RHO_STEP_RELAXED`).
3. If still empty, carry the previous minute's slice params forward with a `STALE_SLICE` flag.
4. If at open with no prior, drop the slice from the tradeable surface and `KILL` it.
Always log which path fired.

---

## 15. Continuous surface via inter-slice interpolation (Corbetta 2019)

Calibration yields discrete slices `{(θ_i, ρ_i, ψ_i)}` at the listed AMD expiries. For a query maturity `T ∈ (T_i, T_{i+1})`, interpolate **linearly in the calendar-safe coordinates** — Corbetta proves this preserves arbitrage-freeness for HM conditions. **For Pasquazzi conditions, the situation is more subtle:**

Linear interpolation in `(θ, ψ, ρψ)` gives:
- Θ(λ) = θ(T)/θ_i — monotone increasing (good)
- Φ(λ) = (ψ(T)/θ(T)) / (ψ_i/θ_i) — ratio of linear functions, generally well-behaved
- ρ(T) = (ρψ)(T)/ψ(T) — rational function

**Case B (Θ > 1, Φ ≤ 1)**: Preserved by linear interpolation since all conditions are linear inequalities in the parameters.

**Case C (Θ > 1, Φ > 1)**: The hyperbola constraint `ρ₂² − ρ₁² = ΘΦ(ΘΦ − 1)` is **NOT preserved** by linear interpolation in general. The interpolated (ρ(T), ρ_i) pair may fall on the forbidden hyperbola branch.

**Practical Resolution**: For the sequential engine, we **restrict calibration to Case B** (Φ ≤ 1) by enforcing `ψ_t ≤ ψ_{t-1} · θ_t / θ_{t-1}` in the corridor upper bound (§8.3). If all calibrated slices satisfy Case B, then linear interpolation also satisfies Case B (since it preserves Φ ≤ 1). If Case C is ever needed, use Mingone (2022) global parametrization which is designed to handle all cases.

**Therefore**: The interpolation is safe **only because we restrict to Case B in calibration**. If you remove the Φ ≤ 1 restriction, you must switch to Mingone global parametrization.

### 15.1 Linear Interpolation (T_i ≤ T ≤ T_{i+1})
```
λ = (T − T_i)/(T_{i+1} − T_i)
θ(T) = (1−λ)θ_i + λθ_{i+1}          # linear, stays monotone
ψ(T) = (1−λ)ψ_i + λψ_{i+1}          # linear, stays monotone
(ρψ)(T) = (1−λ)(ρ_iψ_i) + λ(ρ_{i+1}ψ_{i+1})   # interpolate the PRODUCT ρψ (the skew) linearly
ρ(T)    = (ρψ)(T) / ψ(T)                        # recover ρ; ρ(T) is NOT itself linear in λ — expected & correct
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

### 15.3 Long-Term Extrapolation (T > T_N)
**INCORRECT in original plan**: "extend θ,ψ along last linear segment" — ψ must be held **FLAT (constant)**, not linearly extrapolated. Linear extrapolation of ψ can violate:
- ψ(1+|ρ|) ≤ 4 (butterfly bound)
- ψ_t ≥ ψ_{t-1} (calendar monotonicity)

**CORRECT (Corbetta §7.3, Mingone §5.2.2):**
```
ψ(T) = ψ_N          # CONSTANT — critical for no arb
ρ(T) = ρ_N          # CONSTANT
θ(T) = θ_N + (θ_N − θ_{N-1}) / (T_N − T_{N-1}) · (T − T_N)   # linear in θ
```
**Rationale**: ψ controls curvature/asymptotes. Increasing ψ violates calendar monotonicity and butterfly bound. ρ controls skew symmetry. Extrapolating ρ can cross ρ=0 or hit ±1. θ is the only parameter that MUST increase (calendar level).

**Configurable alternative (flat θ slope):**
```
θ(T) = θ_N + θ'_N · (T − T_N)  where θ'_N = (θ_N − θ_{N-1})/(T_N − T_{N-1}) or 0
```
Default: use last segment slope.

### 15.4 Strike Extrapolation (Wing Tails)
For |k| > K_MAX (max calibrated strike in slice), use explicit tail capping:
```
# eSSVI tail slopes:
c_+ = (ψ/2) (1 + ρ)   # right tail (k → +∞)
c_- = (ψ/2) (1 − ρ)   # left tail (k → −∞)

# Lee (2004) bound: limsup σ²/|k| ≤ 2/T  →  c_± ≤ 2
# Butterfly bound: ψ(1+|ρ|) ≤ 4  →  c_± ≤ 2
# So the bounds coincide!

# Cap tail slopes at TAIL_SLOPE_CAP (config, e.g. 1.9999)
δ = config.TAIL_SLOPE_CAP_EPS  # e.g., 1e-4
c_+_capped = min(c_+, TAIL_SLOPE_CAP)
c_-_capped = min(c_-, TAIL_SLOPE_CAP)

# Linear tail beyond K_MAX:
if k > K_MAX:
    w(k) = w(K_MAX) + c_+_capped · (k − K_MAX)
elif k < -K_MAX:
    w(k) = w(−K_MAX) + c_-_capped · (−K_MAX − k)
```
Default K_MAX = 3.0 (config K_AUDIT).

**Upgrade path:** for a *global* (non-sequential) arbitrage-free fit that avoids sequential drift, see Mingone (2022) "No arbitrage global parametrization for the eSSVI surface" — a drop-in alternative to §4 that fits all slices jointly. Keep the sequential engine as the low-latency default; Mingone as an offline re-calibration / validation cross-check.

---

## 16. Full minute-level runtime loop (putting it together)

```
on each minute τ (RTH only; surface built for ALL RTH minutes incl. no-trade windows):
  1. LOAD clean rows for τ from amd_surface_min (already filtered/greeked by ingestion).
  2. GROUP by expiration → slices t=1..N; drop OI-fail & hard-spread-fail from calibration set.
  3. IF τ == session_open:  cold_start = True; clear temporal priors.       # §14
     ELSE:                  warm-start from τ-1 locked params.               # §11-B
  4. FOR t = 1..N (nearest→farthest):                                        # §4 sequential
        anchor (k*_t,θ*_t); check θ*_t ≥ θ_{t-1} (else §14 degeneracy);
        FOR ρ_t in clipped grid:  corridor [L_ψ,U_ψ] (§8); Brent ψ_t (§10); clamp (§12);
                                  θ_t fixed-point (§5); TotalLoss (§10).
        lock argmin.
        IMMEDIATE calendar check vs t-1 (Pasquazzi) using KILL_TOL → if fail trigger in-calibration fallback.
  5. AUDIT surface (§12): g(k)≥0, calendar, Roper, Lee, sanity → arb_status.
  6. IF arb_status==KILL: emit last-good + staleness flag; else emit fresh surface.
  7. TAG no_trade = (τ in first hour) or (τ in last hour).                   # §14
  8. PERSIST slice params {(θ,ρ,ψ,arb_status,quality)_t} + surface metadata for τ.
  9. Set τ's locked params as τ+1 warm-start (unless τ is session close).
```

---

## 17. Architectural map — files and responsibilities

Calibration engine sits **downstream** of `dataingestion/` (which owns HTTP→clean→TimescaleDB). Proposed package `essvi/`:

```
essvi/
├── config.py            # ALL constants (created; see below for full list)
├── loader.py            # Read minute panel from amd_surface_min; group into slices;
│                        #   apply calibration-set gates (OI, hard-spread); belly/wing tag (§3.2)
├── anchor.py            # extract_anchor(slice)->(k*,θ*); exact_closed_form_theta(); θ-monotonicity check (§5,§14)
├── constraints.py       # PURE math: L_cal(), U_bf1(), U_bf2(), corridor(); g(k),w,w',w'' closed forms;
│                        #   calendar/Roper/Lee assertions (§7,§8) — no I/O, unit-tested exhaustively
├── objective.py         # w_mkt, w_eSSVI, vega²+belly weights, Error(), TotalLoss(), penalties (§10)
│                        #   compute_weights() with VEGA_WEIGHT_MODE support
├── solver.py            # Brent inner solve (bounded); clamp wrapper (§12); per-slice orchestration; warm-start seeding (§11)
├── sequential.py        # The §4 master loop: ρ-grid scan, lock-and-advance, empty-corridor fallback (§14), in-calibration calendar check (§16)
├── surface.py           # Assemble locked slices; inter-slice interpolation (§15); σ(k,T) query API
├── audit.py             # Post-fit arbitrage audit + kill switch (§12); emits arb_status
├── regularize.py        # Term-structure (§11-A) + temporal warm-start/Tikhonov (§11-B); session-open reset (§14)
├── runtime.py           # Minute loop (§16); no-trade tagging; last-good fallback; persistence
├── persistence.py       # Write slice params + surface metadata (params table / TimescaleDB)
├── calibrate_day.py     # Batch/backtest driver over a date range (cold-start each session open)
└── tests/
    ├── test_constraints.py   # corridor bounds vs brute-force g(k); B1↔Lee equivalence; C1-C3 unrolling
    ├── test_anchor.py        # k*≈0, θ exact solve, θ-monotone degeneracy patch
    ├── test_objective.py     # vega² weights, belly boost, penalty split (inner vs outer)
    ├── test_solver.py        # Brent in micro-corridor; clamp overshoot; tight-squeeze
    ├── test_sequential.py    # full slice loop, lock-and-advance, empty-corridor fallbacks
    ├── test_surface.py       # interpolation preserves C1-C3; σ(k,T); extrapolation caps
    ├── test_audit.py         # each kill-switch trigger fires; last-good emission
    ├── test_runtime.py       # session-open cold start, overnight no-chain, no-trade tagging
    ├── test_pasquazzi_calendar.py  # validates Pasquazzi calendar conditions vs HM; Case A/B/C edge cases
    └── test_mm_butterfly.py        # validates MM bound vs GJ; Corbetta SPX/Mingone calibrations satisfy MM
```

**Params output table** (feeds trading/marking; one row per `(ts, expiration)`):
`ts, underlying, expiration, T_years, theta, rho, psi, k_star, theta_star, fit_rmse, n_belly, n_wing, arb_status, quality_flags, no_trade, ingest_run_id`.
Store **params, not the dense grid** — the surface is regenerable from `(θ,ρ,ψ)` in closed form, keeping storage tiny and the audit reproducible.

---

## 18. Failure modes & edge cases (quick reference)

| Symptom | Cause | Handling |
|---------|-------|----------|
| Whole smile shifted horizontally | stale/mis-timed forward (Trap #1) | verify same-minute `S`, tenor `r`, `business_t`; reject if spot/option ts mismatch |
| Short-dated slice breathes wrongly | calendar-day `T` leak | assert `business_t` used everywhere; ingestion §6 |
| Corridor empty for all ρ | tight-squeeze / crash | §14 empty-corridor fallback ladder |
| `θ*_t < θ_{t-1}` at open | overnight gap | §14 calendar degeneracy: project or KILL |
| Surface flickers minute-to-minute | temporal λ too low | raise `LAMBDA_TEMPORAL`; check warm-start active |
| Surface smears real moves | temporal λ too high | lower it; §11 tuning |
| `g(k)<0` in audit despite corridor | FD derivatives / bug | use closed-form `w,w',w''`; corridor should preclude — treat as bug |
| Wing blows past `w/|k|=2` | bad extrapolation | §15 tail cap; should be free via B1 |
| Fit chases noisy OTM | vega weighting off / belly not boosted | assert `W_j=ν_j²`, belly partition, `BELLY_BOOST` |
| Trading on open/close noise | no-trade gate missing | §14; `no_trade` tag must gate signal layer |
| Params drift across midnight | temporal state chained overnight | §14 cold start; assert reset at session open |

---

## 19. Open items to lock before go-live (source discrepancies surfaced)

1. **Weighting space & power — RESOLVED.** Image 2/Image 4 specify **variance-space, vega² weight** (`Σ ν_j²(w_mkt−w_model)²`). `dataingestion.md` §0b/§9 states **vol-space, vega¹ weight** (`Σ ν_i(IV_i−IV_model)²`). **Decision: variance-space vega²** (native eSSVI space, matches corridor math). Set `VEGA_WEIGHT_MODE="var_vega2"` in config; vol-space form kept as documented alternative (`vol_vega1`). Used consistently in `objective.py` and `test_objective.py`.
2. **Short-maturity slice handling (DTE ≤ 14) — RESOLVED.** Added §4.1 with minimum strike requirements, `ρ` fallback for thin slices, and anchor quality flags. Config: `MIN_STRIKES_PER_SLICE=3`, `SHORT_MATURITY_RHO_FALLBACK="next_slice"`, `SHORT_MATURITY_RHO_PRIOR=-0.5`.
3. Warm-start seeding with corridor clipping — RESOLVED. Added §11.1 algorithm; config: `WARMSTART_CLIP_TO_CORRIDOR=True`, `WARMSTART_PSI_TOL=1e-6`.
4. Kill switch tolerance — RESOLVED. Config: `KILL_TOL=1e-10`, `KILL_LOG_DIR="logs/kills/"`.
5. Temporal regularization distinction — RESOLVED. §11.2 clarifies (A) term-structure vs (B) temporal axes; config: `LAMBDA_TEMPORAL=0.01`, `TEMPORAL_REG_MODE="tikhonov"`, `COLD_START_AT_SESSION_OPEN=True`.
6. **`ψ` convention.** Locked here as `ψ=θφ` (Corbetta/HM). If any external code uses `ψ=φ√θ`, convert at the boundary — never mix.
7. **`Δρ` / `λ` values.** Tune on AMD calm+stressed sample (§11); then hardcode.
8. **Belly band `BELLY_K_ABS`, `BELLY_BOOST`.** Calibrate to AMD liquidity; the `|k|≤0.15` and boost are placeholders.
9. **Expiration-day handling.** Ingestion §6 excludes expiry-day session minutes by default; decide whether the front slice on its expiry day is fit/traded or dropped (recommend drop from tradeable, keep for marking).
10. **Anchor tie-break** when two strikes equidistant from `F`: pick higher-OI, then tighter-spread.

---

## 20. References (verified)

- **Hendriks & Martini (2019/2017)** — *The Extended SSVI Volatility Surface* (SSRN 2971502). Maturity-dependent ρ; necessary & sufficient no-calendar-spread conditions (C1–C3). **Note: Proposition 3.1 corrected by Pasquazzi (2023).**
- **Pasquazzi (2023)** — *A Note about Characterization of Calendar Spread Arbitrage in eSSVI Surfaces* (arXiv 2301.XXXXX). **Authoritative correction to HM Proposition 3.1.** Proves HM conditions are necessary but not sufficient when Θ=1. Proposition 13 gives corrected necessary and sufficient conditions (Case A/B/C). **Primary reference for §7.2, §8, §12 calendar conditions.**
- **Corbetta, Cohort, Laachir & Martini (2019)** — *Robust calibration and arbitrage-free interpolation of SSVI slices* (arXiv 1804.04924 / Zeliade zwp-008). The `(k*,θ*)` anchoring, sequential slice-by-slice calibration, arbitrage-preserving linear interpolation. **Primary algorithm reference.**
- **Gatheral & Jacquier (2014)** — *Arbitrage-free SVI volatility surfaces* (arXiv 1204.0646). SSVI form; Durrleman `g(k)≥0`; closed-form butterfly conditions (B1,B2).
- **Roper (2010)** — *Arbitrage Free Implied Volatility Surfaces*. Slope / vertical-spread condition (§7.3).
- **Roger Lee (2004)** — *The Moment Formula for Implied Volatility at Large Strike*. Wing bound `limsup w(k)/|k| ≤ 2` (§7.4).
- **Mingone (2022)** — *No arbitrage global parametrization for the eSSVI volatility surface* (arXiv 2204.00312). Global (non-sequential) upgrade / cross-check (§15).
- **Martini & Mingone (2022)** — *No Arbitrage SVI*; **Hendriks-Martini note (2023)** on calendar-spread characterization — further corridor rigor.
- **Reference implementations:** `github.com/chi-gamma/SVI_and_SSVI_Volatility_Surface_fitting` (implements Corbetta 2019 exactly); `github.com/arkonique/ssvi` (SSVI ATM-term-structure + no-arb workflow); `github.com/Theo-Sullivan/Arbitrage-free-interpolation-of-SSVI-slices` (eSSVI interpolation, live-data demo).
- **Companion:** `dataingestion.md` — the data contract this plan consumes.

---

## 21. Stress-test log (what was audited when this blueprint was hardened)

Recorded so future edits don't silently reintroduce fixed issues.

**Round 1 — math/logic.**
- Verified eSSVI `w,w',w''` closed forms; `w''>0` always (convex in `k`-space, but butterfly is the density check `g(k)≥0`, not `w''`).
- Verified anchor reparameterization `θ_t ≈ θ*_t − ρ_tψ_tk*_t` is the first-order expansion of the exact `θ = 2θ*/(1+ρφk*+√((φk*+ρ)²+1−ρ²))` around `k*=0`; fixed-point recovers exactness.
- Verified `L_cal` is the correct algebraic unroll of `|ρ_2ψ_2−ρ_1ψ_1|≤ψ_2−ψ_1` (both branches ⇒ the two ratio bounds; `max` of them). Requires `ψ_2≥ψ_1`.
- Verified (B2) in `ψ=θφ`: `θφ²(1+|ρ|)≤4 ⇒ ψ²/θ·(1+|ρ|)≤4 ⇒ U_bf2 = 2√(θ/(1+|ρ|))`.
- Verified tail slopes `(ψ/2)(1±ρ)` and `lim g(k)=1/4−c²/16`, closing grid-leakage exactly (§7.4). **Fixed** garbled interpolation line in §15.

**Round 2 — architectural gaps.**
- **Added §3.3** (OTM quote selection + put-call parity diagnostic) — was missing; without it the fit could double-count strikes or fit through a forward-error signature.
- **Added** explicit slice universe (DTE∈[7,90]) to §4 input.
- **Tightened §8(3)**: exact C1 re-checked on fitted `θ_t` in the audit, not just on `θ*_t`.
- Confirmed per-slice fit means no cross-maturity `w`-scale imbalance in the objective.
- Confirmed the two regularizations (§11-A term-structure vs §11-B temporal) are kept orthogonal and only §11-B resets overnight.

**Round 3 — vagueness/consistency sweep.**
- Every threshold points to a named `config.py` constant (no magic numbers in prose).
- Roper/Lee explicitly demoted to *audit assertions* with the reason (implied by B1) stated, so no one wastes a corridor edge on them.
- Kill-switch behavior made concrete (emit last-good + staleness, log violating `(k,slice,condition)`), not just "stop."
- Source discrepancies not hidden: **§19 #1** flags the vega¹-vol-space (ingestion) vs vega²-variance-space (Image 2/4) weighting conflict as a required decision rather than silently picking one.
- **Pasquazzi validation added:** Verified Lemma 2 (Θ=1 cases): (i) ρ₁=ρ₂=0, Φ≥1 → no arb; (ii) ρ₁=ρ₂Φ, ρ₂²≥ρ₁² → only possible when Φ=1 (so ρ₁=ρ₂). When Θ=1 and ρ₁≠ρ₂ with neither zero → ARBITRAGE (slices cross). This is strictly tighter than HM. Critical for §14 overnight gaps where θ*_t ≈ θ_{t-1}. Test cases added to `test_pasquazzi_calendar.py`.

**Known residual assumptions (intentional, documented):** expiration-day front-slice handling (§19 #5); anchor tie-break rule (§19 #6); `Δρ, λ, BELLY_*` are tuned-then-hardcoded, not derived (§11). These are calibration choices, not logic gaps.
