# THERMO-NUCLEAR REVIEW #2: eSSVI Surface Plan vs. Data Ingestion Reality

**Date:** 2026-07-09  
**Scope:** `@eSSVI_surface_plan (1).md` vs `@dataingestion.md` + `@dataingestion/` implementation  
**Verdict:** **NOT READY FOR PRODUCTION** -- Critical mismatches between what the eSSVI engine expects and what the data pipeline actually delivers. Multiple blockers and high-severity gaps identified.

---

## EXECUTIVE SUMMARY

| Severity | Count | Status |
|----------|-------|--------|
| **P0 -- BLOCKER** (engine cannot run) | 5 | Unfixed |
| **P1 -- HIGH** (wrong surface / silent bugs) | 8 | Unfixed |
| **P2 -- MEDIUM** (degraded quality / edge cases) | 6 | Partial |
| **P3 -- LOW** (config drift / docs) | 4 | Partial |

**Bottom line:** The eSSVI surface plan assumes a **perfect, complete data contract** that the dataingestion pipeline **does not yet fulfill**. The two documents were written in isolation and diverge in material ways. Before building the calibration engine, these mismatches must be resolved.

---

## P0 -- BLOCKERS (Engine Cannot Run)

### P0-1: Dividend Yield `q` -- Plan Requires It, Pipeline Produces `0.0` Hardcoded
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 0, 3, 30** | "`q` is NOT zero for single-stock equity options... Using `r=0, q=0` introduces systematic forward bias -- shifted `k` -- wrong skew." Forward: `F = S * e^{(r-q)T}` |
| **dataingestion.md 0** | "AMD has paid **no dividend**... **q = 0** for the whole window. Forward collapses to `F = S * e^{rT}`. Dividend handler is a guardrail, not a live input." |
| **dataingestion/math.py** | `compute_forward_with_dividends()` accepts `dividends_map` but **orchestrator.py passes `None`** -- `q=0` always |

**Impact:** If AMD ever pays a dividend, the surface will be silently wrong. More importantly, the **plan's architecture mandates point-in-time dividend yield** -- the pipeline has the infrastructure (`dividends.py`, `dividends_map` plumbing) but **never wires it up**. The orchestrator must fetch dividends and pass the map to `attach_rates_and_math()`.

**Fix required:** 
1. In `orchestrator._process_chunk()`: fetch dividend events for the chunk's date range, build `dividends_map`, pass to `attach_rates_and_math(opt_df, ..., dividends_map=dividends_map)`.
2. In `joins.attach_rates_and_math()`: use `dividends_map` to compute `q` per symbol per date (point-in-time by `announced_date`).
3. Add config `DIVIDEND_LOOKBACK_DAYS = 365`, `DIVIDEND_PROVIDER = "alphavantage"|"polygon"|"none"`, API keys to `config.py`.

---

### P0-2: Risk-Free Rate Compounding -- Pipeline Uses Simple %, Engine Expects Continuous
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 0, 3, 30** | Forward: `F = S * e^{(r-q)T}` where `r` is **continuously compounded**. Vega: `e^{-rT}` discount. |
| **dataingestion.md 7** | Theta returns `rate` as **percent** (e.g., `4.50` = 4.5%). **Simple money-market rate**. "BS expects continuous compounding... `r_cc = ln(1 + r_simple * tau)/tau`... make it a config switch (`SIMPLE_TO_CC`)." |
| **dataingestion/fetchers.py** | `async_fetch_interest_rate_eod` returns `rate = rate / 100.0` (simple decimal). **No conversion to continuous**. |
| **dataingestion/math.py** | `compute_forward()` uses `r` directly in `F = S * exp(r * T)` -- **treats simple rate as continuous**. |

**Impact:** For 90 DTE at 5% simple rate: `r_simple=0.05`, `r_cc=ln(1+0.05*0.2466)/0.2466 = 0.0494`. Forward error ~12 bps -- `k` shift -- systematic skew bias. Vega discount `e^{-rT}` error compounds.

**Fix required:** 
1. In `fetchers.py`: add `simple_to_cc_rate(rate_simple, tenor_years)` using `math.log1p(rate_simple * tenor) / tenor`.
2. In `config.py`: `SIMPLE_TO_CC = True` (default on, exact math).
3. In `fetchers.async_fetch_interest_rate_eod`: determine tenor from rate symbol (`SOFR`=overnight, `TREASURY_M1`=1/12, `TREASURY_M3`=3/12) and convert if `SIMPLE_TO_CC`.

---

### P0-3: Session Tagging Missing -- Engine Cannot Tag No-Trade Windows
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 14** | "First hour 09:30-10:30 and last hour 15:00-16:00... engine **still calibrates** but tags rows `no_trade=True`... Half-days: last-hour boundary auto-shifts to 12:00." Config: `NO_TRADE_OPEN_MIN=60`, `NO_TRADE_CLOSE_MIN=60`. |
| **dataingestion.md** | No mention of session-phase tagging. |
| **dataingestion/math.py / joins.py / orchestrator.py** | **No `session_phase` column produced.** The pipeline produces `business_t` but no RTH/pre-open/post-close/no-trade flags. |

**Impact:** The engine has no way to know which minutes are in no-trade windows. It will calibrate on auction-imbalance quotes and the surface will have artifacts at open/close. The kill switch may trigger on noise.

**Fix required:**
1. In `math.py`: add `tag_session_phase(ts: pd.Series) -> pd.Series` returning enum: `pre_open`, `rth`, `no_trade_open`, `no_trade_close`, `post_close`, `half_day_no_trade`.
2. Use `pandas_market_calendars` schedule + `NO_TRADE_OPEN_MIN`/`NO_TRADE_CLOSE_MIN` config.
3. In `joins.attach_rates_and_math()`: call `tag_session_phase()` and add `session_phase` column.
4. In `config.py`: add session config constants (already in essvi/config.py -- **sync them**).

---

### P0-4: Prior-Session OI Join -- Plan Requires It, Pipeline Uses Same-Day (Leakage)
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 3.1, 12** | "OI prints EOD. In `OI_MODE='strict'` the engine sees only **prior session (D-1)** OI during day-D minutes. Treat OI as a *slow, past-only* liquidity mask." |
| **dataingestion.md 12.8** | "Prior-session OI (strict mode)... join *D-1* OI to *D*'s minutes for strict no-leakage; same-day EOD OI is the looser research default -- **pick one and document it**." |
| **dataingestion/joins.py _join_oi()** | Joins OI on `date` column directly: `oi_df['date'] = pd.to_datetime(oi_df['date']).dt.date` then merges on `bar_date == oi_date`. **This is same-day OI -- LEAKAGE.** |

**Impact:** Backtest sees OI at 10:00 AM that wasn't published until 4:00 PM. Strategies using OI filters will have **forward-looking bias**. The plan explicitly forbids this.

**Fix required:** In `joins._join_oi()`: if `config.OI_MODE == "strict"`, merge on `bar_date == oi_date + 1 day` (or prior trading day via calendar). Add `schedule_cache` parameter to resolve prior trading day correctly (skip weekends/holidays).

---

### P0-5: Survivorship Filter -- Plan Requires It, Pipeline Has O(n^2) Python Loop
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 3.4, 12.7** | "Build each date's contract set from `list/contracts`/`list/expirations` **as of that date**, not today's chain." |
| **dataingestion/orchestrator.py _process_chunk()** | Has survivorship filter but **implemented as Python loop over `contracts_by_date`** -- O(n^2), will not scale to 7-year backfill. Also uses `contracts_df` from `async_fetch_option_list_contracts` which is **per expiration**, not per date. |

**Impact:** Either survivorship is broken (contracts that didn't exist yet appear in historical data) OR the backfill will take weeks due to the O(n^2) loop.

**Fix required:** Vectorize the survivorship check. Pre-fetch `list/contracts` for **each date** in the chunk (not per expiration), build a `MultiIndex` of `(date, strike, right)` valid contracts, then `opt_df.merge(valid_contracts, on=[date, strike, right], how='inner')`.

---

## P1 -- HIGH SEVERITY (Wrong Surface / Silent Bugs)

### P1-1: Vega Weighting Mode Mismatch
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 10** | **Default: `var_vega2`** -- variance-space vega^2 (`nu_var = nu_vol / (2*sigma*sqrt(T))`, weight = `nu_var^2`). "Corbetta, variance-space, matches theory." |
| **dataingestion.md 9** | "Vega = `e^{-rT} * F * phi(d1) * sqrt(T)`... Output is `dPrice/dsigma` for a full `1.00` vol move... **Vol-space vega**." |
| **dataingestion/math.py** | `compute_vega()` returns Black-76 vega (`nu_vol`). **No variance-space conversion.** |
| **essvi/config.py** | `VEGA_WEIGHT_MODE = "var_vega2"` (default) |

**Impact:** If engine uses `var_vega2` but receives `nu_vol`, weights are wrong by factor `~1/(4*sigma^2*T)` -- long-dated ATM overweighted, wings underweighted. Surface shape distorted.

**Fix:** In `math.compute_vega()`: add `mode` parameter (`"vol"` | `"var_vega1"` | `"var_vega2"`). Default to `"var_vega2"` to match engine config. Return `nu_vol / (2 * sigma * sqrt(T))` squared for `var_vega2`.

---

### P1-2: Forward Price Uses Theta's `underlying_price`, Not Same-Minute Stock Close
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 0, 3** | "`S` = same-minute stock `close` (ingestion 8)... **Strict time-matching is a hard prerequisite.** Theta's `underlying_price` is the underlying **mid at the option timestamp**; keep it as a sanity cross-check but treat the stock-OHLC `close` as canonical `S`." |
| **dataingestion/joins.py attach_rates_and_math()** | Uses `stk_df` (stock OHLC close) joined on floored minute -- **CORRECT.** |
| **dataingestion/fetchers.py** | Option greeks fetch returns `underlying_price` (Theta's mid). **Not used for forward** -- good. |
| **BUT:** `cleaning.py` pre-filter uses `delta` from Theta, which was computed with Theta's `underlying_price` (mid), not our `close`. **Delta band filter is inconsistent with our forward.** |

**Impact:** A strike passes delta filter (|delta| in [0.1,0.9]) under Theta's mid-based forward, but under our close-based forward its true delta is outside the band -- we keep strikes we should drop, or drop strikes we should keep. The belly/wing partition is contaminated.

**Fix:** Re-compute delta locally in `cleaning.py` using our `forward_price` (after joins) **or** move delta filter to post-join (after `attach_rates_and_math()`). The latter is cleaner: clean only structural checks (bid>0, spread, IV>0) pre-join; do delta/OI/monotonicity post-join with correct forward.

---

### P1-3: Business Time `T` -- Half-Day Handling Diverges
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 6, 14** | `business_t` from `pandas_market_calendars` XNYS. "Half-days = 210 min (09:30-13:00 ET, e.g. day after Thanksgiving, Christmas Eve, July 3)." |
| **dataingestion.md 6** | Same: "early-close half-day = **210** min (09:30-13:00 ET, e.g. day after Thanksgiving, Christmas Eve, July 3)." |
| **dataingestion/math.py compute_business_T()** | Uses `pandas_market_calendars.get_calendar("XNYS")` -- **correctly gets half-days from calendar**. |
| **ESSVI config** | `HALF_DAY_SESSION_MINUTES = 210` (hardcoded fallback). |

**Gap:** The plan says half-days are **calendar-driven** (via `mcal`). The config has a hardcoded fallback. If the calendar is missing a half-day (e.g., ad-hoc closure), the fallback applies. Need to ensure the calendar is the **source of truth** and the constant is only a safety net.

**Fix:** In `math.compute_business_T()`: assert that `session_minutes` from calendar matches expected (390 or 210). Log warning if not. Remove hardcoded half-day list from config; derive from calendar.

---

### P1-4: Rate Tenor Matching -- Plan Requires It, Pipeline Uses Single Symbol
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 7** | "Tenor-match the option's DTE... use `TREASURY_M1` for short, `TREASURY_M3` for the upper end, or linearly interpolate across `SOFR / M1 / M3` by DTE." |
| **dataingestion.md 7** | "For DTE in [7,90]: use `TREASURY_M1` for short, `TREASURY_M3` for the upper end, or linearly interpolate across `SOFR / M1 / M3` by DTE." Config has `RATE_SYMBOLS_SHORT/MEDIUM/LONG` buckets. |
| **dataingestion/fetchers.py async_fetch_interest_rate_eod()** | Fetches **one symbol at a time**. Orchestrator calls it once per chunk with a single `rate_symbol` (from config `RATE_SYMBOLS_MEDIUM` default). **No DTE-based interpolation.** |

**Impact:** 7 DTE options get 1-month rate (too long tenor -- rate too high), 90 DTE get 1-month rate (too short tenor -- rate too low). Forward price error up to ~5-10 bps on wings.

**Fix:** In `orchestrator._get_rates()`: fetch all three symbols (`SOFR`, `TREASURY_M1`, `TREASURY_M3`) for the chunk date range. In `joins._attach_rates()`: for each row, interpolate `r` by DTE using the three tenors. Add `RATE_INTERPOLATION_METHOD = "linear"` config.

---

### P1-5: Anchor Strike Extraction -- Plan Has Exact Logic, Pipeline Has None
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 5** | Detailed anchor extraction: "Find the market quote whose strike is closest to the forward -- its log-moneyness `k*_t` and total implied variance `theta*_t`. Fallback if exact ATM strike fails: take nearest *belly-qualifying* strike... do NOT fabricate `k*=0`." |
| **dataingestion/** | **No anchor extraction logic anywhere.** The pipeline produces clean rows but does not identify `(k*_t, theta*_t)` per slice per minute. |

**Impact:** The calibration engine will have to re-implement anchor extraction from raw rows every minute. This duplicates logic and risks divergence. The anchor is the **single most important point** on the slice -- it should be computed once at ingestion and stored.

**Fix:** In `math.py` or new `anchors.py`: add `extract_anchor(slice_df: pd.DataFrame) -> (k_star, theta_star, anchor_quality_flag)`. Compute per `(ts, expiration)` group. Store `anchor_k_star`, `anchor_theta_star`, `anchor_quality` columns in `amd_surface_min` (add to schema). Quality flags: `EXACT_ATM`, `NEAREST_BELLY`, `WIDENED_GATES`, `RHO_FALLBACK`.

---

### P1-6: Parity Skew Diagnostic -- Plan Requires It, Pipeline Doesn't Compute
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 3.3** | "Put-call IV consistency check: under the correct forward, the put and call IV at the same strike must match; a systematic call-minus-put IV bias across strikes is a **forward/rate error signature** -- surface it as a `PARITY_SKEW` diagnostic rather than fitting through it." |
| **dataingestion/** | **No put-call parity check.** Both rights carried through but no diagnostic column. |

**Impact:** Forward/rate errors (which are systematic) will be absorbed into the eSSVI fit as fake skew, corrupting `rho` estimates. The diagnostic is the **early warning system** for Trap #1.

**Fix:** In `joins.attach_rates_and_math()`: after computing `forward_price`, group by `(ts, expiration, strike)`, compute `call_iv - put_iv` -- `parity_skew`. Store column. Flag if `|mean(parity_skew)| > PARITY_SKEW_TOL` (config, e.g., 0.005 vol points).

---

### P1-7: Column Name Drift -- Engine Expects Specific Names, Pipeline Produces Different Ones
| eSSVI Plan expects | dataingestion produces | dataingestion DB schema |
|---------------------|------------------------|------------------------|
| `implied_vol` | `implied_vol` | `implied_vol` |
| `forward_price` | `forward_price` | `forward_price` |
| `business_t` | `business_t` | `business_t` |
| `vega` (variance-space) | `vega` (vol-space) | `vega` |
| `log_moneyness` / `k` | **missing** | `log_moneyness` |
| `session_phase` | **missing** | **missing** |
| `parity_skew` | **missing** | **missing** |
| `anchor_k_star` | **missing** | **missing** |
| `anchor_theta_star` | **missing** | **missing** |
| `anchor_quality` | **missing** | **missing** |
| `quality_flags` | `quality_flags` | `quality_flags` |

**Fix:** Align pipeline output to engine contract. Add missing columns in `joins.attach_rates_and_math()` and `db_writer.COLUMN_MAP`.

---

### P1-8: Kill Switch Tolerances -- Plan Has Per-Audit Tolerances, Pipeline Has Single `KILL_TOL`
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 12** | "All checks use `KILL_TOL = 1e-10` numerical tolerance." Single tolerance. |
| **essvi/config.py** | **Four separate tolerances:** `KILL_TOL_BUTTERFLY=1e-8`, `KILL_TOL_CALENDAR=1e-10`, `KILL_TOL_ROPER=1e-10`, `KILL_TOL_LEE=1e-10`. Legacy `KILL_TOL=1e-10` deprecated. |

**Impact:** Engine will use per-audit tolerances; pipeline verification (`verify.py`) uses single tolerance. Mismatch means verification may pass slices the engine kills, or vice versa.

**Fix:** In `verify.py` and `config.py`: adopt the four-tolerance scheme. Deprecate single `KILL_TOL`.

---

## P2 -- MEDIUM SEVERITY (Degraded Quality / Edge Cases)

### P2-1: Belly/Wing Partition Thresholds Diverge
| Source | Belly spread max | Wing spread max | Belly |delta| range | OI min |
|--------|------------------|-----------------|--------|-------|
| **eSSVI Plan 3.2, 13** | 0.10 | 0.25 | [0.10, 0.90] | 100 |
| **dataingestion.md 5#4** | 0.10 | 0.25 | -- | -- |
| **dataingestion/config.py** | `MAX_REL_SPREAD_BELLY=0.10` | `MAX_REL_SPREAD_HARD=0.25` | `MIN_DELTA_ABS=0.10`, `MAX_DELTA_ABS=0.90` | `MIN_OI=100` |
| **essvi/config.py** | `BELLY_REL_SPREAD_MAX=0.10` | `WING_REL_SPREAD_MAX=0.25` | `BELLY_DELTA_LO=0.10`, `BELLY_DELTA_HI=0.90` | `BELLY_OI_MIN=100` |

**Status:** **Aligned** -- but duplicated in two configs. Single source of truth needed.

---

### P2-2: DTE Window -- Plan Says [7,90], Pipeline Config Says [7,90]
Aligned. But ingestion pre-filter uses **calendar DTE** while engine uses **business_t** for math. Document this clearly: "Calendar DTE selects contract membership; business_t drives all math."

---

### P2-3: Minimum Strikes Per Slice
| Source | Value |
|--------|-------|
| **eSSVI Plan 4.1** | `MIN_STRIKES_PER_SLICE = 3` (config) |
| **essvi/config.py** | `MIN_STRIKES_PER_SLICE = 3` |
| **dataingestion/** | No enforcement -- slices with <3 strikes pass through to engine. |

**Fix:** In `cleaning.py` or `orchestrator`: after grouping by `(ts, expiration)`, flag slices with `< MIN_STRIKES_PER_SLICE` belly-qualifying strikes. Add `slice_strike_count` column. Engine can then apply fallback logic.

---

### P2-4: Expiration-Day (DTE=1) Handling
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 4.1, 12** | "DTE=1: Include but flag `EXPIRY_IMMINENT`: widen corridor `epsilon_psi` by 10x, increase temporal penalty `lambda_temp` by 10x. Numerical handling: use `long double` precision, scale vega weights, skip psi solve if `T < MIN_T_FOR_PSI_SOLVE`." |
| **dataingestion.md 4** | "DTE in [7,90]... DTE in [2,6] excluded by ingestion." **DTE=1 is EXCLUDED.** |
| **dataingestion/cleaning.py** | `MAX_DTE = 90`, `MIN_DTE = 7` -- DTE=1 filtered out. |

**Conflict:** Plan wants DTE=1 for surface continuity; pipeline drops it. If engine expects DTE=1 slices (for short extrapolation), they won't exist.

**Fix:** Either (a) change pipeline `MIN_DTE = 1` and add `EXPIRY_IMMINENT` flag in cleaning, or (b) change plan to start at DTE=7 and adjust short extrapolation (15.2) accordingly. **Recommend (a)** -- more data is better, engine handles the numerics.

---

### P2-5: Temporal Regularization Config Drift
| Source | Params |
|--------|--------|
| **eSSVI Plan 11** | `TEMPORAL_THETA_SCALE=0.1`, `TEMPORAL_RHO_SCALE=0.5`, `TEMPORAL_PSI_SCALE=0.5`, `LAMBDA_TEMPORAL_THETA/RHO/PSI`, `TEMPORAL_THETA_LOG=True` |
| **essvi/config.py** | All present |
| **dataingestion/config.py** | **Missing entirely** -- pipeline doesn't know about temporal regularization. |

**Fix:** Sync temporal regularization configs to `dataingestion/config.py` (engine reads from `essvi/config.py` but pipeline should know the scales for any temporal features it computes).

---

### P2-6: Warm-Start / Cold-Start -- Plan Has Detailed Logic, Pipeline Has None
| Source | What it says |
|--------|--------------|
| **eSSVI Plan 11, 14** | "Within a session, seed each minute's solver from prior locked params... At session open: **COLD START** -- re-extract anchor fresh, seed Brent mid-corridor, no temporal prior." |
| **dataingestion/** | **No session detection, no warm-start state persisted.** The pipeline is stateless per chunk. |

**Impact:** Engine must implement warm/cold start internally. Pipeline should at least tag `session_phase` (session_open, session_close, is_first_rth_bar_of_day) so engine knows when to cold-start.

---

## P3 -- LOW SEVERITY (Config Drift / Docs)

### P3-1: Config Duplication -- `dataingestion/config.py` vs `essvi/config.py`
Many constants duplicated: `MIN_DTE`, `MAX_DTE`, `MIN_DELTA_ABS`, `MAX_DELTA_ABS`, `MAX_REL_SPREAD_HARD`, `MAX_REL_SPREAD_BELLY`, `MIN_OI`, `BELLY_BOOST`, `BELLY_K_ABS`, session times, kill tolerances, etc.

**Fix:** Create `core_engine/shared/calibration_config.py` as single source of truth. Both packages import from it. Or use a shared `pyproject.toml` `[tool.calibration]` section + runtime loader.

---

### P3-2: `dataingestion.md` 15 Open Items -- Still Open
1. **Port:** 25503 vs 25510 -- unverified.
2. **Splits path:** endpoint unverified.
3. **`first_order` exact columns:** unverified against live response.
4. **Rate choice:** SOFR vs treasury, simple vs cc -- config switch exists (`SIMPLE_TO_CC`) but **not implemented in fetcher** (see P0-2).
5. **Spread threshold:** 0.25 hard / 0.10 belly -- aligned in code but doc says "confirm".
6. **OI mode:** prior-session vs same-day -- code uses same-day (bug, see P0-4).

---

### P3-3: `verify.py` Checks Don't Match Engine Audit
| Engine audit (12) | `verify.py` check |
|---------------------|-------------------|
| Butterfly `g(k) >= 0` on dense grid | `essvi_sanity`: "IV smile smoothness... no adjacent strike jumps > 5 vol pts" -- **not the same** |
| Calendar arbitrage (Pasquazzi) | Not checked |
| Roper slope | Not checked |
| Roger Lee wing | Not checked |
| Kill switch tolerances | Single `KILL_TOL` vs four per-audit |

**Fix:** Enhance `verify.py` to run the **exact same audit suite** as the engine (12). Use shared `audit.py` module.

---

### P3-4: Documentation Drift
- `eSSVI_surface_plan.md` references "Image 1, 2, 3, 4, 6" -- these are figures from the source PDFs. In the markdown they're just placeholders. Should either embed the figures or replace with descriptive text.
- `dataingestion.md` says "Updated to Implementation Reality" but several sections (dividend handler, rate compounding, session tagging) describe **planned** not **implemented** behavior.

---

## DATA AVAILABILITY MATRIX: Can We Build the Surface Perfectly?

| eSSVI Engine Requirement | Pipeline Provides? | Gap | Blocker? |
|--------------------------|-------------------|-----|----------|
| Clean option quotes (bid/ask/mid/IV) | Yes | -- | No |
| Precise `business_t` (years) | Yes | -- | No |
| Same-minute spot `close` -- forward `F` | Yes | -- | No |
| Tenor-matched, continuously-compounded `r` | No | Simple rate, no interpolation | **P0-2** |
| Point-in-time dividend yield `q` | No | Hardcoded 0, infra exists but unwired | **P0-1** |
| Numba vega (variance-space vega^2) | No | Vol-space vega only | **P1-1** |
| Prior-session OI (strict no-leakage) | No | Same-day OI join | **P0-4** |
| Survivorship-safe contract universe | No | O(n^2) loop, per-expiration not per-date | **P0-5** |
| Anchor `(k*, theta*)` per slice per minute | No | Not computed | **P1-5** |
| Put-call parity skew diagnostic | No | Not computed | **P1-6** |
| Session phase tags (RTH/no-trade) | No | Not computed | **P0-3** |
| Belly/wing partition flags | Yes (via quality_flags) | -- | No |
| DTE=1 slices | No | Filtered out at ingestion | **P2-4** |
| Kill-switch-compatible audit data | No | Verification uses different checks | **P1-8** |

**Score: 4/15 requirements fully met. 5 P0 blockers. NOT READY.**

---

## RECOMMENDED REMEDIATION ORDER

1. **Fix P0-2 (Rate Compounding)** -- 30 min. Add `simple_to_cc_rate()` in fetchers, enable `SIMPLE_TO_CC=True`, determine tenor per symbol.
2. **Fix P0-4 (Prior-Session OI)** -- 1 hr. Update `joins._join_oi()` to use calendar for prior trading day when `OI_MODE="strict"`.
3. **Fix P0-3 (Session Tagging)** -- 1 hr. Add `tag_session_phase()` in `math.py`, call from `joins.attach_rates_and_math()`.
4. **Fix P0-1 (Dividend Yield)** -- 2 hr. Wire `dividends.py` fetcher in orchestrator, pass `dividends_map` to joins, compute `q` point-in-time.
5. **Fix P0-5 (Survivorship)** -- 4 hr. Vectorize: fetch `list/contracts` per date, merge on `(date, strike, right)`.
6. **Fix P1-1 (Vega Weighting)** -- 30 min. Add `mode` param to `compute_vega()`, default `var_vega2`.
7. **Fix P1-2 (Delta Filter Consistency)** -- 1 hr. Move delta/OI/monotonicity filters to post-join (after forward computed).
8. **Fix P1-5 (Anchor Extraction)** -- 2 hr. Add `extract_anchor()` in `math.py`, store columns in DB.
9. **Fix P1-6 (Parity Skew)** -- 30 min. Add `parity_skew` column in `joins.attach_rates_and_math()`.
10. **Fix P1-3/4/7/8 (Config Sync, Half-Day, Rate Interpolation, Kill Tolerances)** -- 3 hr.
11. **Fix P2-4 (DTE=1)** -- 1 hr. Lower `MIN_DTE=1`, add `EXPIRY_IMMINENT` flag.
12. **Unify Configs (P3-1)** -- 2 hr. Shared config module.
13. **Align Verification (P3-3)** -- 2 hr. Shared `audit.py`.

**Total estimated effort: ~20 hours** before the calibration engine can be built on a solid foundation.

---

## CONCLUSION

The eSSVI surface plan is **mathematically rigorous and theoretically sound** (Martini-Mingone butterfly, Pasquazzi calendar, Corbetta interpolation, Mingone global parametrization upgrade path). The dataingestion pipeline is **architecturally clean** (modular, async, tested, idempotent, leakage-aware).

**But they don't connect.** The plan assumes a data contract that the pipeline doesn't fulfill. The pipeline produces data the plan doesn't consume (and misses data the plan requires).

**Do not build the calibration engine until the 5 P0 blockers are resolved.** The engine will either fail to run, produce silently wrong surfaces, or both.

**Next step:** Create a `SPEC_ALIGNMENT.md` document that defines the **exact column contract** between ingestion and calibration -- every column name, type, meaning, and who computes it. Both teams (ingestion + calibration) sign off. Then fix the pipeline to match. Then build the engine.

---

*Review conducted with zero tolerance for "it'll probably work." Every mismatch documented above has a concrete failure mode. Fix them.*