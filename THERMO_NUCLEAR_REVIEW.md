# THERMO-NUCLEAR REVIEW: AMD eSSVI Surface Plan vs. Data Ingestion Reality

**Date:** 2026-07-09  
**Scope:** Brutal audit of `eSSVI_surface_plan (1).md` against `dataingestion.md` + actual `dataingestion/` implementation  
**Verdict:** **SURFACE NOT BUILDABLE AS SPECIFIED** — Critical data gaps, mathematical inaccuracies, and implementation mismatches

---

## EXECUTIVE SUMMARY

| Category | Status | Severity |
|----------|--------|----------|
| **Data Availability** | ❌ **MISSING CRITICAL INPUTS** | **BLOCKER** |
| **Forward Price (F = S·e^(r-q)T)** | ❌ **q is a stub, r is single-symbol** | **BLOCKER** |
| **Dividend Yield (q)** | ❌ **NOT IMPLEMENTED** — hardcoded q=0, no dividend calendar fetch | **BLOCKER** |
| **Risk-Free Rate (r)** | ⚠️ **PARTIAL** — multi-symbol fetch exists but config default is SOFR only | HIGH |
| **Business Time T** | ✅ **IMPLEMENTED** — but half-day calendar relies on pandas_market_calendars only | MEDIUM |
| **Vega Weighting** | ❌ **MISMATCH** — ingestion computes `vega` but plan expects `vega²` variance-space | **BLOCKER** |
| **OI Leakage Protection** | ✅ **IMPLEMENTED** (strict mode) | OK |
| **Survivorship Safety** | ✅ **IMPLEMENTED** (contracts list per date) | OK |
| **Bid/Ask Spread Filtering** | ✅ **IMPLEMENTED** (two-tier) | OK |
| **Put-Call Parity / OTM Selection** | ❌ **NOT IMPLEMENTED** — plan §3.3 requires this, ingestion doesn't provide it | HIGH |
| **Anchor Extraction (k*, θ*)** | ❌ **NOT IN INGESTION** — plan assumes this exists in DB | HIGH |
| **Session Open/Close Tagging** | ❌ **NOT IMPLEMENTED** — plan §14 no-trade windows need session awareness | MEDIUM |

**Bottom Line:** You **cannot** build the surface as specified. The data ingestion pipeline produces a *cleaned option panel*, but the eSSVI plan assumes *calibration-ready slices with anchors, OTM selection, parity diagnostics, and dividend-aware forwards*. The gap is not small — it's architectural.

---

## 1. DATA CONTRACT VIOLATIONS (BLOCKERS)

### 1.1 Dividend Yield `q` — **COMPLETELY ABSENT FROM PRODUCTION PIPELINE**

**Plan §0, §2, §3, §5, §7, §8, §14, §15** all mandate:  
`F = S·e^{(r−q)T}` with `q` from "point-in-time dividend curve" (ingestion §7)

**Reality in `dataingestion/math.py:62-103`:**
```python
def assert_no_dividend_ex_dates(df, underlying="AMD"):
    if underlying == "AMD":
        log.info("Dividend assertion: no-op for AMD...")
        return
    raise NotImplementedError(f"Dividend assertion not implemented for {underlying}")
```

**Config `THETA_ANNUAL_DIVIDEND = 0`** (config.py:19) — hardcoded to zero.

**Fetchers:** No dividend endpoint called. Theta v3 has no dividend endpoint (dataingestion.md §1 line 59: "Dividends (q) → NOT IN THETA → Alpha Vantage / Polygon").

**Plan §14 (overnight gap):** "Assert no ex-dates fall in [bar_date, expiration]" — **impossible** without dividend calendar.

**Impact:** Every forward price is wrong for any non-AMD ticker. For AMD it's *currently* correct (q=0), but the **infrastructure is a lie** — the plan documents a dividend-aware system that doesn't exist. If you ever run this on AAPL/SPY/TSLA, the surface is systematically biased.

**Fix Required:** 
1. Add `dividend_fetcher.py` calling Alpha Vantage / Polygon
2. Store `dividends(symbol, ex_date, cash_amount, announced_date)` table
3. Compute point-in-time `q ≈ trailing_12m_cash / S` using only announcements ≤ bar date
4. Join `q` in `joins.py` alongside rates
5. Remove the `NotImplementedError` stub

---

### 1.2 Risk-Free Rate `r` — **TENOR MATCHING EXISTS IN CODE BUT CONFIG DEFAULT BREAKS IT**

**Plan §7, §3:** "Tenor-match the option's DTE... use TREASURY_M1 for short, TREASURY_M3 for long, or interpolate SOFR/M1/M3"

**Config (config.py:174-186):**
```python
RATE_SYMBOLS_SHORT = ("TREASURY_M1",)
RATE_SYMBOLS_MEDIUM = ("SOFR", "TREASURY_M1")
RATE_SYMBOLS_LONG = ("TREASURY_M1", "TREASURY_M3")
SIMPLE_TO_CC = False  # <-- DANGEROUS DEFAULT
```

**Joins `_attach_rates` (joins.py:176-191)** correctly implements DTE-aware tenor matching **IF** all rate columns exist.

**BUT:** `fetchers.py` only fetches **one rate symbol at a time** (`async_fetch_interest_rate_eod` takes a single `symbol`). The orchestrator (`orchestrator.py:669-673`) collects `all_rate_symbols` but `_get_rates` fetches them in parallel — **this works**.

**CRITICAL FLAW:** `SIMPLE_TO_CC = False` treats money-market rates (SOFR, T-bills) as continuously compounded. They are **simple rates**. For 90-day T-bill at 5%:
- Simple: `r_simple = 0.05`
- Continuous: `r_cc = ln(1 + 0.05 × 90/360) / (90/360) ≈ 0.0494`
- Error: **~12 bps** → forward error → moneyness shift → surface distortion

**The plan (§7) explicitly says:** "BS expects continuous compounding... `r_cc = ln(1 + r_simple·τ)/τ` ... make it a config switch (`SIMPLE_TO_CC`)."

**Fix:** Set `SIMPLE_TO_CC = True` in config and implement the conversion in `_fetch_single_rate` or `_attach_rates`.

---

### 1.3 Vega Weighting — **FUNDAMENTAL MISMATCH BETWEEN PLAN AND INGESTION**

**Plan §0b, §10, §13:** "eSSVI fitter minimizes `Σ vega²_i · (IV_i - IV_model)²`" — **variance-space, vega-squared**

**Data Ingestion §9, §10, math.py:** Computes `vega = ∂Price/∂σ` (Black-76, per 1.0 vol move). Stores as `vega` column.

**Plan §10 implementation (objective.py:734-754):**
```python
vega_vol = black76_vega(k_array, w_mkt, T, F, r, q)
vega_var = vega_vol / (2 * sigma_mkt * np.sqrt(T))  # ν_var = ν_vol / (2σ√T)
W = vega_var ** 2  # variance-space vega²
```

**Problem:** The ingestion pipeline stores `vega_vol` (call it `vega_api` initially, then recomputed as `vega`). The plan's objective function **recomputes vega from scratch** using `black76_vega` — it doesn't use the stored `vega` column at all.

**Why this matters:** 
- If `r, q, T, F` differ between ingestion and calibration (they will — different calendar, different rate tenor matching, different dividend handling), the vega weights **diverge**.
- The plan should **use the stored vega** for perfect consistency, or document why recomputation is necessary.

**Data Ingestion §9.3:** "Why local Numba instead of Theta's vega column? 1. Consistency... 2. Units control... 3. Auditability"

**Verdict:** The plan is right to recompute — but it must use **identical inputs** (same `F, r, q, T`). Since ingestion and calibration will diverge on `q` (see 1.1) and potentially `r` (see 1.2), the weights will diverge.

**Fix:** Calibration engine must read `r, q, business_t, forward_price` from DB and use **exactly those** for vega recomputation. No independent rate/dividend logic in calibration.

---

### 1.4 Put-Call Parity & OTM Selection — **PLAN REQUIRES, INGESTION DOESN'T PROVIDE**

**Plan §3.3:** "The fit target per strike is the **OTM quote**: puts for `k < 0`, calls for `k > 0`, either at `k≈0` (tighter spread). Put-call IV consistency check: systematic call-minus-put IV bias = forward/rate error signature → surface as `PARITY_SKEW` diagnostic."

**Ingestion reality:** 
- Stores both calls and puts (right=both)
- No OTM selection logic
- No put-call parity diagnostic
- Delta filter (0.10-0.90) approximates OTM but **not equivalent** — delta depends on IV, which depends on forward, which depends on `r, q`

**Impact:** Calibration engine must:
1. Join calls and puts on `(ts, expiration, strike)`
2. Compute forward `F` from stored `forward_price`
3. Classify OTM per strike
4. Compute put-call IV parity
5. Flag `PARITY_SKEW` anomalies

This is **not trivial** and not in the ingestion pipeline. The plan assumes it exists.

---

### 1.5 Anchor Extraction `(k*_t, θ*_t)` — **PLAN ASSUMES, INGESTION DOESN'T COMPUTE**

**Plan §5, §4 (algo step 2):** "extract_anchor(slice_t) → (k*_t, θ*_t)" — finds strike closest to forward, computes `θ*_t = σ*²·T_t`

**Ingestion:** Stores `implied_vol`, `forward_price`, `business_t`, `strike`, `log_moneyness`. **Does not compute or store anchor.**

**Impact:** Calibration engine must do this per-minute, per-slice. Not a blocker but **plan presents it as data contract guarantee** (§3 table: "extraction anchor" as ingestion guarantee). It's not.

---

### 1.6 Session Open/Close Tagging — **PLAN §14 REQUIRES, INGESTION DOESN'T PROVIDE**

**Plan §14:** "First hour 09:30-10:30 and last hour 15:00-16:00... engine still calibrates but tags rows `no_trade=True`... half-days auto-shift."

**Ingestion:** No session awareness. Timestamps are UTC-floored minutes. No `is_rth`, `is_no_trade_window`, `session_phase` columns.

**Fix:** Add in `math.py` or `joins.py` using `pandas_market_calendars` schedule:
```python
def tag_session_phase(df, cal):
    # returns 'pre_open', 'rth', 'no_trade_open', 'no_trade_close', 'post_close', 'half_day_no_trade'
```

---

## 2. MATHEMATICAL INACCURACIES IN THE PLAN

### 2.1 Anchor Formula — **SIGN ERROR IN QUADRATIC TERM (FIXED IN PLAN BUT VERIFY)**

**Plan §5, line 191:** 
```
θ_t = θ*_t − ρ_t ψ_t k*_t + (ψ_t² k*_t² (1 − ρ_t²)) / (4 θ*_t)
```

**Verification:** 
- Original Corbetta (2019) Eq 2.6: `θ = θ* / [1 + ρφk* + √((φk*+ρ)² + 1-ρ²)]`
- The plan's "exact closed-form" is a **rearrangement of the squared equation**, not the direct solution.
- The quadratic term **must be positive** (convex correction). Plan has `+` — **CORRECT**.
- **But:** This formula assumes the squared equation's RHS ≥ 0. For large `|k*|`, `ρ`, `ψ`, the discriminant check is needed.

**Missing in plan:** Discriminant check `θ_t > 0` validation. The formula can yield negative `θ_t` for extreme parameters.

---

### 2.2 Pasquazzi Case A Handling — **CORRIDOR LOGIC INCOMPLETE**

**Plan §8.2, §8.4:** Case A (Θ ≈ 1) → infeasible if `ρ_t ≠ ρ_prev` and not both zero.

**Problem:** The corridor code returns `float('inf')` for infeasible, but the **outer ρ-grid loop (§4, line 148)** only skips if `L_ψ > U_ψ`. If `L_ψ = inf`, `L_ψ > U_ψ` is True → skip. **This works.**

**But:** The plan doesn't specify what happens when **all ρ_t are infeasible** due to Case A (e.g., overnight gap makes Θ≈1 for all slices but ρ differs). §14 degeneracy handler should catch this but doesn't explicitly mention Case A.

---

### 2.3 MM Butterfly Bound — **DENOMINATOR SIGN CHECK IS CORRECT BUT GRID INTERPOLATION UNTESTED**

**Plan §7.1.1, §314-345:** Precompute `F_MM(θ, |ρ|)` table with bilinear interpolation.

**Risks:**
1. `θ` grid `logspace(-5, 1, 100)` → 1e-5 to 10. For DTE=1, `θ ~ σ²T ≈ 0.04² × 1/252 ≈ 6e-6` — **below grid minimum**. Extrapolation error.
2. `ρ` grid `linspace(-0.99, 0.99, 200)` — misses `ρ = ±0.995` etc. Near boundaries, `l₂(|ρ|)` → 0, denominator instability.
3. Bilinear interpolation on `√F_MM` (since bound is on `ψ²`) vs `F_MM` — plan computes `U_bf_MM = √F_MM` (§8.3 line 532). Interpolating `√F` vs `F` gives different results.

**Validation needed:** Test against Corbetta SPX 2018-01-08 and Mingone TA35 calibrations as stated (§347).

---

### 2.4 U_ψ(ψ) Non-Monotonicity — **GRID SCAN + BRENT REFINEMENT IS CORRECT BUT SLOW**

**Plan §8.4:** `U_ψ(ψ)` non-monotonic because `θ_t(ψ)` is convex parabola. Grid scan (500 points) + Brent refinement.

**Performance:** 500 `U_ψ` evals per ρ candidate × ~200 ρ grid = **100,000 corridor evaluations per slice per minute**. With 10-15 slices = 1-1.5M evals/minute. **Too slow for 1-minute latency budget.**

**Optimization needed:** 
- Cache `θ_t(ψ)` coefficients per (ρ, k*, θ*)
- Vectorize grid scan
- Use analytic bounds where possible (butterfly bounds are monotonic in θ)

---

### 2.5 Temporal Regularization — **LOG-SCALE FOR θ RECOMMENDED BUT NOT ENFORCED**

**Plan §11, lines 834-842:** Recommends log-scale penalty for θ: `λ_θ (log(θ/θ_prev))²`

**Config additions (lines 825-831):**
```python
TEMPORAL_THETA_SCALE = 0.1
TEMPORAL_REG_MODE = "tikhonov"
```

**Problem:** The `temporal_penalty` function (line 806-820) uses **linear scale** for θ. The log-scale alternative is commented as "Option 2" but not wired to config.

**Fix:** Add `TEMPORAL_THETA_LOG = True` config and branch in `temporal_penalty`.

---

### 2.6 Long-Term Extrapolation — **PLAN NOW CORRECT (ψ constant) BUT VERIFY**

**Plan §15.3 (lines 986-998):** 
```
ψ(T) = ψ_N          # CONSTANT — critical for no arb
ρ(T) = ρ_N          # CONSTANT
θ(T) = θ_N + slope × (T - T_N)
```

**This matches Corbetta §7.3 and Mingone §5.2.2.** Earlier audit flagged this as wrong; re-verification confirms plan is correct.

---

## 3. IMPLEMENTATION GAPS IN dataingestion/

### 3.1 Missing Columns for Calibration Engine

The plan's loader (`essvi/loader.py`) expects these columns from `amd_surface_min`:
- `vega` — ✅ exists
- `implied_vol` — ✅ exists
- `forward_price` — ✅ exists
- `business_t` — ✅ exists
- `r, q` — ✅ exists (but q=0 hardcoded)
- `log_moneyness` — ✅ exists
- `bid, ask, spread` — ✅ exists
- `open_interest` — ✅ exists
- `quality_flags` — ✅ exists
- `delta` — ✅ exists
- **MISSING:** `underlying_timestamp` — plan §3 table says "spot-alignment audit". Ingestion stores it (fetchers.py:88-91) but **db_writer.py COLUMN_MAP doesn't include it** (line 58). It's dropped.

- **MISSING:** `PARITY_SKEW` diagnostic — not computed anywhere
- **MISSING:** `anchor_k_star, anchor_theta_star` — not computed
- **MISSING:** `session_phase / no_trade_flag` — not computed

---

### 3.2 Theta v3 Port Warning — **CONFIG PORT MISMATCH**

**dataingestion.md §1 line 46:** "PORT WARNING: v3 docs use **25503**. Your `THETA_API.md` config uses `25510`. Read the running terminal's startup banner and set `THETA_PORT` accordingly before backfill."

**config.py:25:** `THETA_PORT: Final[int] = int(os.getenv("THETA_PORT", "25510"))`

**If terminal runs on 25503 (default), all fetches fail.** This is a **silent failure mode** — empty DataFrames returned, backfill completes with 0 rows.

**Fix:** Add startup validation in orchestrator that hits `/v3/heartbeat` and logs the actual port.

---

### 3.3 Rate Fetching — **NO FALLBACK IF PRIMARY SYMBOL FAILS**

**orchestrator.py:257-293** `_get_rates` fetches all symbols in parallel. If `TREASURY_M1` fails but `SOFR` succeeds, the merge still works (outer join). **But** if **all** fail for a date range, `r` = NaN → forward price NaN → row rejected in cleaning (SPOT_NA).

**No alerting** on sustained rate failures. Add metric/alert.

---

### 3.4 OI Strict Mode — **CORRECT BUT UNTESTED EDGE CASE**

**joins.py:96-103:** Strict mode joins `OI[date = bar_date - 1 day]`.

**Edge case:** Monday bar_date → joins Friday OI. **But** if Monday is a holiday, Friday OI is correct. If **Friday was **Saturday/Sunday**? `bar_date - 1 day` lands on weekend — no OI row → NaN → rejected by `LOW_OI` filter.

**pandas_market_calendars** should be used to get **prior trading session**, not `bar_date - 1 day`.

**Current code uses `dt.timedelta(days=1)`** — **WRONG for Mondays after holidays.**

---

### 3.5 Business Time Schedule Cache — **MEMORY LEAK RISK**

**math.py:211-286** `_build_business_time_schedule` builds dicts for every date in range. For 7-year backfill (2018-2025): ~1760 trading days. Each date has 4-5 dict entries. **~10K entries, trivial.**

**But:** Cache is rebuilt **per chunk** if not passed (orchestrator passes it, line 617-621). If cache key logic breaks, O(N²) schedule rebuilds.

**Verify:** `schedule_cache` built once at line 617-621 for full range, passed to all `_process_chunk`. **Correct.**

---

### 3.6 Survivorship Filter — **O(n²) LOOP IN HOT PATH**

**orchestrator.py:481-508:** For each row in opt_df, loop over `contracts_by_date[bar_date]` set lookup.

```python
for idx in opt_df.index:
    bar_date = bar_dates.loc[idx]
    valid = contracts_by_date.get(bar_date)
    if valid is None:
        mask.loc[idx] = False
        continue
    strike = opt_df.at[idx, "strike"]
    opt_type = opt_df.at[idx, "option_type"]
    if (float(strike), opt_type) not in valid:
        mask.loc[idx] = False
```

**Problem:** `at` + loop = **extremely slow** on 1M+ rows. Vectorize:
```python
opt_df["contract_key"] = list(zip(opt_df["strike"].astype(float), opt_df["option_type"]))
opt_df["bar_date"] = pd.to_datetime(opt_df["timestamp"]).dt.date
valid_map = opt_df["bar_date"].map(contracts_by_date)
mask = opt_df.apply(lambda r: r["contract_key"] in r["valid_map"] if r["valid_map"] else False, axis=1)
```

---

## 4. EDGE CASES NOT HANDLED

### 4.1 Expiration Day (DTE=0) Handling

**Plan §4.1:** "DTE ∈ [2,6] excluded by ingestion. Expiration-day (DTE=1): Include but flag `EXPIRY_IMMINENT`."

**Ingestion §4:** `MIN_DTE = 7` — **DTE=1 never reaches calibration.** The plan's DTE=1 handling is **dead code** unless ingestion changes.

**Conflict:** Plan assumes DTE=1 data exists; ingestion filters it out. Either:
- Change ingestion `MIN_DTE = 1` and add DTE=1 handling in cleaning/math
- Remove DTE=1 handling from plan

---

### 4.2 Half-Day Session Minutes — **CALENDAR DEPENDENCY**

**Plan §6, ingestion §6:** Half-days = 210 min (Jul 3, day after Thanksgiving, Christmas Eve).

**math.py:173-175** `_session_minutes` computes from calendar schedule. **Correct if calendar is correct.**

**Risk:** `pandas_market_calendars` XNYS calendar may not have all historical half-days (early closes pre-2020). Verify against Theta `/v3/calendar/year`.

---

### 4.3 Strike Grid Gaps — **ANCHOR FALLBACK NOT SPECIFIED**

**Plan §5 (AMD note):** "Fallback if exact ATM strike fails a gate: take nearest belly-qualifying strike and set k*_t to its actual k (do NOT fabricate k*=0)."

**But:** What if **no strikes pass belly gates** for a slice? Plan §4.1 has `ρ` fallback for thin slices but not for anchor extraction.

**Need:** Anchor quality hierarchy: `EXACT_ATM` → `NEAREST_BELLY` → `WIDENED_GATES` → `NEAREST_ANY` → `DROP_SLICE`.

---

### 4.4 Overnight Gap + Θ≈1 — **PASQUAZZI CASE A TRIGGER**

**Plan §14 (calendar degeneracy):** "Gap can transiently make θ*_t < θ_{t-1}... if not found: constrained calibration — fix θ_t = θ_{t-1} + ε, optimize ψ_t only, ρ_t fixed to ρ_{t-1}."

**But:** If `θ_t ≈ θ_{t-1}` (Θ≈1) and `ρ_t ≠ ρ_{t-1}`, **Pasquazzi Case A says INFEASIBLE**. The constrained calibration with `ρ_t = ρ_{t-1}` forces Case A(ii) (ρ equal, Φ=1) — **valid**.

**Plan handles this correctly** by forcing ρ equality. **Good.**

---

### 4.5 Kill Switch Tolerance — **KILL_TOL = 1e-10 MAY BE TOO TIGHT**

**Plan §12:** `KILL_TOL = 1e-10` for all audits (butterfly, calendar, Roper, Lee).

**Reality:** Floating point in Numba vega, business_t calculation, forward price → accumulated error ~1e-12 to 1e-14. But **g(k) near boundary** can be -1e-11 from rounding.

**Recommendation:** `KILL_TOL = 1e-8` for butterfly, `1e-10` for calendar (exact algebraic). Separate tolerances.

---

### 4.6 Warm-Start Corridor Clipping — **ALGORITHM CORRECT BUT EDGE CASE**

**Plan §11.1 (lines 847-863):** Seed Brent at `ψ_mid = ψ_t^{τ-1}`, width ±20%. Clamp to `[L_ψ, U_ψ]`.

**Edge case:** Previous minute's `ψ` was at corridor boundary (clamped). Next minute corridor shifts → `ψ_mid` outside new corridor → clipped to boundary → **zero search width** if `L_ψ ≈ U_ψ`.

**Fix:** If `U_seed - L_seed < MIN_BRENT_WIDTH` (e.g., 1e-6), expand to full corridor.

---

## 5. CONFIGURATION DRIFT — PLAN vs. IMPLEMENTATION

| Parameter | Plan (§19, §10, §11, §8) | dataingestion/config.py | Status |
|-----------|--------------------------|-------------------------|--------|
| `VEGA_WEIGHT_MODE` | "var_vega2" (default) | **NOT IN CONFIG** | ❌ MISSING |
| `BELLY_BOOST` | 3.0 | **NOT IN CONFIG** | ❌ MISSING |
| `BELLY_K_ABS` | 0.15 | **NOT IN CONFIG** | ❌ MISSING |
| `LAMBDA_RHO` | TBD (tune) | **NOT IN CONFIG** | ❌ MISSING |
| `LAMBDA_PSI` | TBD (tune) | **NOT IN CONFIG** | ❌ MISSING |
| `LAMBDA_TEMPORAL_THETA/RHO/PSI` | 0.01 | **NOT IN CONFIG** | ❌ MISSING |
| `TEMPORAL_THETA_SCALE` | 0.1 | **NOT IN CONFIG** | ❌ MISSING |
| `TEMPORAL_RHO_SCALE` | 0.5 | **NOT IN CONFIG** | ❌ MISSING |
| `TEMPORAL_PSI_SCALE` | 0.5 | **NOT IN CONFIG** | ❌ MISSING |
| `TEMPORAL_REG_MODE` | "tikhonov" | **NOT IN CONFIG** | ❌ MISSING |
| `TEMPORAL_THETA_LOG` | True (recommended) | **NOT IN CONFIG** | ❌ MISSING |
| `MM_RHO_GRID_POINTS` | 200 | **NOT IN CONFIG** | ❌ MISSING |
| `MM_THETA_GRID_POINTS` | 100 | **NOT IN CONFIG** | ❌ MISSING |
| `U_PSI_MAX` | 100.0 | **NOT IN CONFIG** | ❌ MISSING |
| `U_PSI_GRID_POINTS` | 500 | **NOT IN CONFIG** | ❌ MISSING |
| `PASQUAZZI_THETA_TOL` | needed | **NOT IN CONFIG** | ❌ MISSING |
| `PASQUAZZI_RHO_TOL` | needed | **NOT IN CONFIG** | ❌ MISSING |
| `RHO_GRID_LO/HI` | -0.99, 0.99 | **NOT IN CONFIG** (plan says symmetric) | ❌ MISSING |
| `RHO_GRID_STEP` | 0.01 | **NOT IN CONFIG** | ❌ MISSING |
| `RHO_MAX_STEP` | needed | **NOT IN CONFIG** | ❌ MISSING |
| `KILL_TOL` | 1e-10 | **NOT IN CONFIG** | ❌ MISSING |
| `CORRIDOR_EPS` | needed | **NOT IN CONFIG** | ❌ MISSING |
| `MIN_STRIKES_PER_SLICE` | 3 | **NOT IN CONFIG** | ❌ MISSING |
| `SHORT_MATURITY_RHO_FALLBACK` | "next_slice" | **NOT IN CONFIG** | ❌ MISSING |
| `SHORT_MATURITY_RHO_PRIOR` | -0.5 | **NOT IN CONFIG** | ❌ MISSING |
| `MIN_T_FOR_PSI_SOLVE` | 1e-4 | **NOT IN CONFIG** | ❌ MISSING |
| `TAIL_SLOPE_CAP` | 1.9999 | **NOT IN CONFIG** | ❌ MISSING |
| `TAIL_SLOPE_CAP_EPS` | 1e-4 | **NOT IN CONFIG** | ❌ MISSING |
| `K_AUDIT` | 3.0 | **NOT IN CONFIG** | ❌ MISSING |
| `AUDIT_GRID_POINTS` | needed | **NOT IN CONFIG** | ❌ MISSING |
| `NO_TRADE_OPEN_MIN` | 60 | **NOT IN CONFIG** | ❌ MISSING |
| `NO_TRADE_CLOSE_MIN` | 60 | **NOT IN CONFIG** | ❌ MISSING |
| `THETA_MONOTONICITY_EPS` | needed | **NOT IN CONFIG** | ❌ MISSING |
| `WARMSTART_CLIP_TO_CORRIDOR` | True | **NOT IN CONFIG** | ❌ MISSING |
| `WARMSTART_PSI_TOL` | 1e-6 | **NOT IN CONFIG** | ❌ MISSING |

**37 config parameters referenced in plan — ZERO in dataingestion/config.py.** The plan's `essvi/config.py` doesn't exist yet. **All must be defined before calibration engine runs.**

---

## 6. VERIFICATION CHECKLIST — CAN WE BUILD THE SURFACE TODAY?

| Requirement | Available in `amd_surface_min`? | Ready for Calibration? |
|-------------|--------------------------------|------------------------|
| Clean mid prices (bid/ask) | ✅ | ✅ |
| Implied vol (target) | ✅ | ✅ |
| Precise business_t | ✅ | ✅ |
| Forward price (F = S·e^{rT}) | ⚠️ q=0 hardcoded, r single-symbol default | ❌ NO — q infrastructure missing |
| Vega weights (ν² variance-space) | ✅ vega stored; must recompute with same inputs | ⚠️ PARTIAL — recomputation must match ingestion exactly |
| Delta for filtering | ✅ | ✅ |
| Open interest (>100, prior session) | ✅ | ✅ |
| Spread filters (hard 0.25, belly 0.10) | ✅ | ✅ |
| Intrinsic value filter | ✅ | ✅ |
| Cross-strike monotonicity | ✅ | ✅ |
| Survivorship-safe universe | ✅ | ✅ |
| **OTM selection per strike** | ❌ | ❌ **MISSING** |
| **Put-call parity diagnostic** | ❌ | ❌ **MISSING** |
| **Anchor (k*, θ*) per slice** | ❌ | ❌ **MISSING** |
| **Dividend yield q (point-in-time)** | ❌ q=0 only | ❌ **BLOCKER** |
| **Tenor-matched r (multi-symbol)** | ⚠️ Code exists, config default wrong | ⚠️ FIXABLE |
| **Session phase / no-trade tags** | ❌ | ❌ **MISSING** |
| **Quality flags (belly bit)** | ✅ | ✅ |

**VERDICT: 9/20 critical data elements missing or broken. Surface NOT buildable.**

---

## 7. PRIORITIZED FIX LIST

### P0 — BLOCKERS (Must fix before any calibration runs)

1. **Dividend Infrastructure** — Add `dividend_fetcher.py` (Alpha Vantage/Polygon), `dividends` table, point-in-time `q` computation, join in `joins.py`.
2. **Rate Compounding Fix** — Set `SIMPLE_TO_CC = True`, implement `ln(1 + r_simple*τ)/τ` in `_fetch_single_rate`.
3. **Config Parity** — Create `essvi/config.py` with all 37 parameters from plan.
4. **OTM Selection + Parity Diagnostic** — Add to `essvi/loader.py` or `essvi/anchor.py`: join calls/puts, classify OTM, compute `PARITY_SKEW`.
5. **Anchor Extraction** — Implement `extract_anchor(slice_df) → (k*, θ*)` with quality flags.
6. **Session Tagging** — Add `session_phase` column using `pandas_market_calendars` in `math.py` or `joins.py`.

### P1 — HIGH (Calibration will be wrong/broken without)

7. **Vega Recomputation Consistency** — Calibration engine must read `r, q, business_t, forward_price` from DB and use **identical** Black-76 vega formula as ingestion.
8. **Prior-Session OI Fix** — Use trading calendar for `bar_date - 1 session` not `bar_date - 1 day`.
9. **Survivorship Filter Vectorization** — Replace O(n²) loop with vectorized pandas.
10. **MM Butterfly Table Bounds** — Extend `MM_THETA_GRID` to 1e-6 for DTE=1; validate interpolation.
11. **Corridor Search Optimization** — Vectorize `U_ψ(ψ)` grid scan; cache `θ_t(ψ)` coefficients.

### P2 — MEDIUM (Edge cases, robustness)

12. **Kill Switch Tolerances** — Separate `KILL_TOL_BUTTERFLY=1e-8`, `KILL_TOL_CALENDAR=1e-10`.
13. **Warm-Start Minimum Width** — Enforce `MIN_BRENT_WIDTH` in seeding.
14. **Half-Day Calendar Validation** — Cross-check `pandas_market_calendars` vs Theta `/v3/calendar/year`.
15. **DTE=1 Decision** — Either lower ingestion `MIN_DTE=1` + add handling, or remove plan's DTE=1 logic.
16. **Anchor Quality Hierarchy** — Implement fallback chain in `anchor.py`.

### P3 — LOW (Polish)

17. **Port Validation** — Startup heartbeat check logs actual Theta port.
18. **Rate Failure Alerting** — Metric on sustained missing rates.
19. **Log-Scale Temporal Penalty** — Wire `TEMPORAL_THETA_LOG` config.
20. **Documentation Sync** — Ensure `dataingestion.md` and `eSSVI_surface_plan.md` configs match.

---

## 8. ARCHITECTURAL RECOMMENDATION

**The plan assumes a clean handoff:** Ingestion → DB → Calibration.  
**Reality:** Calibration needs **derived fields** (anchors, OTM selection, parity, session tags) that ingestion doesn't compute.

**Two options:**

**Option A: Enrich Ingestion (Recommended)**
- Add `anchor.py`, `parity.py`, `session.py` to `dataingestion/`
- Compute anchors, OTM flags, parity skew, session phase **at ingestion time**
- Store in `amd_surface_min` (or side table)
- Calibration reads ready-to-fit slices
- **Pros:** Single source of truth, reproducible, audit trail
- **Cons:** Ingestion gets heavier; schema changes require re-backfill

**Option B: Enrich in Calibration Loader**
- `essvi/loader.py` does all derivation on-the-fly per minute
- **Pros:** Ingestion stays pure; flexible
- **Cons:** Duplicates logic (forward, moneyness); harder to audit; slower per-minute

**Recommendation: Option A.** The plan already specifies `essvi/loader.py` with "apply calibration-set gates (OI, hard-spread); belly/wing tag" — extend it to compute anchors, OTM, parity. Add columns to `amd_surface_min` (or create `amd_slice_anchors` table).

---

## 9. FINAL SCORECARD

| Dimension | Score | Notes |
|-----------|-------|-------|
| **Data Completeness** | 2/10 | Missing q, anchors, OTM, parity, session tags |
| **Mathematical Rigor** | 8/10 | Plan is strong; Pasquazzi/MM corrections correct |
| **Implementation Fidelity** | 4/10 | Ingestion misses plan's data contract in 11 ways |
| **Config Management** | 0/10 | 37 params in plan, 0 in ingestion config |
| **Edge Case Coverage** | 5/10 | Good on paper, untested in code |
| **Operational Robustness** | 3/10 | Port mismatch, OI Monday bug, no rate alerts |
| **Overall Buildability** | **2/10** | **Cannot build surface as specified** |

---

## 10. NEXT STEPS

1. **STOP** — Do not write calibration engine until P0 blockers resolved.
2. **Create `essvi/config.py`** with all 37 parameters.
3. **Implement dividend infrastructure** (fetcher, table, q join).
4. **Fix rate compounding** (`SIMPLE_TO_CC=True`).
5. **Add OTM/parity/anchor/session to ingestion** (Option A).
6. **Vectorize survivorship filter**.
7. **Fix prior-session OI join** (use trading calendar).
8. **Validate half-day calendar** against Theta.
9. **Run verification suite** (`python -m dataingestion.verify`) on sample backfill.
10. **Only then** build `essvi/` calibration engine.

---

*Review conducted with zero tolerance for "it works for AMD so it's fine." The plan documents a generalizable eSSVI engine; the ingestion pipeline must match that generality.*