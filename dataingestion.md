# AMD eSSVI Surface — Data Ingestion Blueprint (Updated to Implementation Reality)

**Scope:** 1-minute option + underlying data for **AMD**, **2018-01-01 → present**, filtered and cleaned at ingestion, written to a TimescaleDB hypertable as the input panel for an extended-SSVI (eSSVI) volatility surface fitter.

**Verified against the live Theta Data v3 docs (`docs.thetadata.us`), not the v2-style paths in the source images.** v2 endpoints return `410 GONE`.

---

## 0. Ticker-specific facts that simplify the build

| Fact | Consequence |
|------|-------------|
| AMD has paid **no dividend** in the 2018→now window (last/only payment $0.005 in 1995) | **q = 0** for the whole window. Forward collapses to `F = S·e^{rT}`. Dividend handler is a guardrail, not a live input. |
| AMD's **last split was 2000-08-22** (2:1) | **No split adjustment** needed inside the window. Historical strikes from Theta are already the correct raw strikes. |
| AMD trades in the **Penny Interval Program** | Quotes live on a `$0.01` grid → sub-penny detection is the tick check. |
| AMD is on the **UTP / Nasdaq tape** | Full history depth; not limited like CTA-only names (SPY). |

Build the dividend + split infrastructure anyway (Section 7) so the pipeline generalizes and catches a future AMD dividend, but for AMD today both are effectively no-ops.

---

## 0b. QUICK REFERENCE: What each data category is FOR (for new readers)

> **The eSSVI surface fitter minimizes:** `Σ vega_i · (IV_i - IV_model(k_i, T_i))^2`
>
> Every data element below either defines the **target** `IV_i`, the **coordinates** `(k_i, T_i)`, or the **weight** `vega_i`.

| Data Category | Endpoint(s) | What It Feeds | Why It Matters |
|---------------|-------------|---------------|----------------|
| **Option quotes + 1st-order greeks + IV** | `greeks/first_order` | `bid, ask` → mid/spread; `implied_vol` → **target IV_i**; `delta` → filter; `vega_api` → cross-check | **Core input.** Without clean quotes and IV, there is no surface to fit. |
| **Open Interest (daily)** | `option/history/open_interest` | Cleaning filter `OI > 100` (Section 5 #8); stored for audit | **Liquidity reality check.** Tight spread + zero OI = phantom market. Filters out contracts where execution slippage would destroy the mid-price assumption. |
| **Underlying spot (1-min close)** | `stock/history/ohlc` | `spot_price` → `forward_price` → `k = ln(K/F)` | **Moneyness anchor.** Wrong spot = horizontal shift of entire surface. |
| **Risk-free rate (daily, tenor-matched)** | `interest_rate/history/eod` | `r` → forward `F = S·e^{rT}`; vega discount `e^{-rT}` | **Forward & vega scaling.** Rate error = forward error (moneyness shift) + vega weight error. |
| **Dividend yield q** | External (Alpha Vantage/Polygon) | Forward `F = S·e^{(r-q)T}` | For AMD = 0. For other tickers, non-zero q shifts forward. |
| **Business time T (precise)** | Computed from `pandas_market_calendars` | `T` in forward, vega, moneyness | **Decay clock.** Calendar-day T overstates intraday decay → surface "breathes" falsely. |
| **Vega (computed locally, Numba)** | `math.py` → `_vega_kernel` | **Weight `vega_i` in eSSVI loss** | High-vega (ATM, long-dated) options dominate the fit. Zero/missing vega = unweighted fit = noise fitting. |
| **Expirations + Contracts lists** | `list/expirations`, `list/contracts` | Survivorship-safe universe per date | Prevents look-ahead bias: only trade contracts that actually existed on each date. |
| **Trading calendar / holidays** | `calendar/year`, `calendar/on_date` | Business time T calculation | Exact session minutes including half-days (Jul 3, day after Thanksgiving, Christmas Eve). |

---

## 1. Endpoint map (v3, Standard tier)

Base URL: `http://{THETA_HOST}:{THETA_PORT}/v3/...`. Terminal must be running.

> **PORT WARNING:** v3 docs use **`25503`**. Your `THETA_API.md` config uses `25510`. Read the running terminal's startup banner and set `THETA_PORT` accordingly before backfill. Do not assume.

| Data needed | Endpoint (v3) | Tier | Key params | Returns (relevant columns) |
|-------------|---------------|------|------------|----------------------------|
| **Option quote + 1st-order greeks + IV** (primary workhorse) | `/v3/option/history/greeks/first_order` | **STANDARD** | `symbol`, `expiration`, `strike=*`, `right=both`, `start_date`,`end_date` (or `date`), `interval=1m`, `annual_dividend=0`, `rate_type=sofr`, `version=latest`, `format=ndjson` | `timestamp, strike, right, bid, ask, delta, theta, vega, rho, implied_vol, iv_error, underlying_price, underlying_timestamp` |
| Option IV (fallback / cross-check) | `/v3/option/history/greeks/implied_volatility` | STANDARD | same shape | `implied_vol, bid, ask, underlying_price` |
| Option raw quote (fallback if greeks unavailable) | `/v3/option/history/quote` | VALUE+ | same shape | `bid, ask, bid_size, ask_size` |
| **Open interest** (liquidity filter) | `/v3/option/history/open_interest` | VALUE+ | `symbol`, `expiration`, `strike`, `date` range | `date, open_interest` (**daily**, not per-minute) | **Liquidity gatekeeper.** Filters out contracts with `OI ≤ 100` (Section 5 #8). A tight spread with zero OI = phantom liquidity; real slippage ≫ mid. Used as hard filter in cleaning and stored for audit. |
| Expirations as-of a date | `/v3/option/list/expirations` | — | `symbol` | sorted expiration dates |
| Contracts listed on a date | `/v3/option/list/contracts` | — | `symbol`, `date` | strikes/rights live that day (survivorship-safe universe) |
| **Underlying spot (minute close)** | `/v3/stock/history/ohlc` | VALUE+ | `symbol=AMD`, `start_date`,`end_date`, `interval=1m` | `timestamp, open, high, low, close, volume` → use **`close`** |
| **Risk-free rate** | `/v3/interest_rate/history/eod` | ALL | `symbol=SOFR` (or `TREASURY_M1/M3`), `start_date`,`end_date` | `created, rate` (**percent**, e.g. `4.50` = 4.5%) |
| Splits (guardrail) | `/v3/stock/history/split` *(listed under Standard in tier table; confirm exact path)* | STANDARD+ | `symbol` | split events |
| Dividends (q) | **NOT IN THETA** → Alpha Vantage / Polygon | — | — | cash dividend amounts + ex-dates → for AMD = empty |
| Trading calendar / holidays | `/v3/calendar/year`, `/v3/calendar/on_date` | — | `year` / `date` | holidays, early closes (cross-check for business time) |

### Corrections to `THETA_API.md` (your engine doc is partly v2/invalid)

| `THETA_API.md` claim | Reality (v3) |
|---------------------|--------------|
| `option_chain_greeks_history` → `/v3/option/history/greeks/all` | `greeks/all` is **PRO**. On Standard use `greeks/first_order`. |
| default interval "5-minute bars" | We need **`interval=1m`**. |
| `risk_free_rate_cc` → `/v3/stock/history/rate?tenor=90` | **Invalid path.** Use `/v3/interest_rate/history/eod?symbol=SOFR`. Rate is a **percent**, not a cc decimal — convert (Section 7). |
| `annual_dividend_amount` → `/v3/stock/history/dividend` | **No such endpoint.** Use external provider; for AMD q=0. |
| `spot_price` → `/v3/stock/snapshot/quote` | Snapshot is **real-time only**. For 2018→now backfill use `/v3/stock/history/ohlc`. |
| greeks multi-day requests | **Capped at 1 month per request** → chunk the backfill (Section 3). |

---

## 2. Field → source crosswalk + WHY each is needed

Everything the eSSVI surface fitter needs, where it comes from, and **exactly what breaks if it's missing or wrong**.

| Field | Source | Purpose / Downstream Use | Failure Mode if Missing/Wrong |
|-------|--------|--------------------------|-------------------------------|
| `bid`, `ask` | `greeks/first_order` | Raw market quotes → mid price, spread, liquidity assessment | No mid price → no option price → cannot invert IV, cannot weight by vega |
| `option_midprice` | computed: `(bid + ask) / 2` | **Primary option price** for IV inversion and eSSVI loss function | eSSVI fits to mid prices; wrong mid = biased surface |
| `spread` | computed: `ask − bid` | Liquidity proxy; drives cleaning filter (Section 5 #4) and vega-weighting | Wide spread = noisy mid; if unfiltered, surface gets spurious kinks |
| `implied_vol` (σ) | `greeks/first_order.implied_vol` | **Target variable** for eSSVI; also used in vega calc | Missing/wrong IV = no surface fit; zero IV → divide-by-zero in vega |
| `delta` (for filter) | `greeks/first_order.delta` | Pre-filter: keep `0.10 ≤ \|delta\| ≤ 0.90` (Section 4) | Deep ITM/OTM options have unreliable IV → pollutes surface wings |
| `spot_price` (S) | `stock/history/ohlc.close`, same minute | Forward price `F = S·e^{(r−q)T}`; log-moneyness `k = ln(K/F)` | Wrong spot → wrong forward → wrong moneyness → surface shifted horizontally |
| `forward_price` (F) | computed: `S·e^{(r−q)T}` (AMD: `S·e^{rT}`) | **eSSVI x-axis anchor**; all strikes mapped to `k = ln(K/F)` | Wrong forward = moneyness distortion = surface shape error |
| `r` | `interest_rate/history/eod`, tenor-matched, percent→decimal | Forward price `F`; vega discount factor `e^{-rT}` | Wrong rate → forward error (small for short DTE) + vega scaling error |
| `q` | external; AMD = 0 | Forward price `F = S·e^{(r−q)T}` | Non-zero q for AMD would shift forward incorrectly |
| `business_t` (T) | computed from exchange calendar (Section 6) | **Time to expiry in years** — used in forward, vega, moneyness | Calendar-day T overstates decay intraday → surface "breathes" falsely |
| `vega` | computed locally, Numba BS (Section 9) | **Weight** in eSSVI loss function: `min Σ vega_i · (IV_i - IV_model)^2` | Missing/zero vega = unweighted fit = surface fits noise in illiquid options |
| timestamp | `greeks/first_order.timestamp`, floored to minute | Primary time key; joins to spot, rates, OI; watermark partitioning | Misaligned timestamp = wrong spot/rate/OI = leakage or noise |

**Key insight for downstream consumers:** The eSSVI fitter minimizes `Σ vega_i · (IV_i - IV_model(k_i, T_i))^2`. Every column above either (a) defines the target `IV_i`, (b) defines the coordinates `(k_i, T_i)`, or (c) provides the weight `vega_i`. If any column is wrong, the surface fit is wrong.

---

## 3. Acquisition order (per-`(expiration)` worker)

The greeks endpoint is keyed by `(symbol, expiration)` and capped at 1 month/request, so the natural unit of work is **one expiration at a time**, chunked into ≤1-month date windows.

```
for each expiration E in list/expirations(AMD):                 # survivorship-safe
    # relevant life of E under DTE∈[7,90]:  window = [E-90cd, E-7cd]
    for each ≤1-month date-chunk C in window:
        opt   = GET greeks/first_order(AMD, E, strike=*, right=both,
                                       interval=1m, date-range=C,
                                       annual_dividend=0, rate_type=sofr)
        stock = GET stock/history/ohlc(AMD, interval=1m, date-range=C)   # cache per chunk, reuse across E
        oi    = GET option/history/open_interest(AMD, E, date-range=C)   # daily
        rates = GET interest_rate/history/eod(SOFR or TREASURY_M*, C)    # daily, cache globally
        --> assemble + clean in memory (Sections 4-9)
        --> two-phase load into TimescaleDB (Section 11/13)
```

**Concurrency:** respect the Standard cap of **4 concurrent requests**. Use an `asyncio.Semaphore(4)` (leave headroom for the heartbeat). Cache `stock/history/ohlc` and `interest_rate` per date-chunk so they're fetched once and joined to every expiration.

---

## 4. Pre-filter (applied at selection / immediately on pull, before cleaning)

Shrinks the panel before any heavy work.

1. **Delta band:** keep `0.10 ≤ |delta| ≤ 0.90` (calls `delta∈[0.10,0.90]`, puts `delta∈[−0.90,−0.10]`). Uses Theta's `delta`. Drops deep ITM/OTM.
2. **Days-to-maturity band:** keep `7 ≤ DTE ≤ 90` where `DTE = calendar_days(expiration − bar_date)`. (Calendar days select *which contracts*; precise **business** time `T` is computed later for the math.)

Selecting expirations by DTE up front means each expiration only contributes data over its ~83-day eligible life.

---

## 5. In-memory quality + arbitrage cleaning (consolidated)

Run **all** of these in-memory before any DB write. Order matters: cheap structural rejects first, cross-strike checks last. Rejected rows are not dropped silently — they go to a `quarantine` table with a reason code (Section 13).

| # | Check | Rule | Why it matters for eSSVI | Failure if skipped |
|---|-------|------|---------------------------|-------------------|
| 1 | **No-quote** | `bid > 0 AND ask > 0` | Zero bid = no real market; can't form a meaningful mid price | Mid = 0 or NaN → IV inversion fails or produces garbage |
| 2 | **Locked/Crossed** | reject if `bid ≥ ask` (require `ask > bid`) | Crossed/locked quotes are stale or corrupted; break BS inversion | IV from crossed quotes is meaningless; surface gets outliers |
| 3 | **Tick / penny-pilot** | reject sub-penny: `round(bid*100)≠bid*100` or `round(ask*100)≠ask*100`; optional `$0.05` grid for premium ≥ `$3.00` | Non-standard increments = corrupted/bad-aggregation packet | Sub-penny quotes are artifacts; including them adds noise to mid/IV |
| 4 | **Spread-widening (two-tier)** | `rel_spread = (ask−bid)/mid`. **Hard reject** `rel_spread > 0.25`. **Belly flag** `rel_spread > 0.10` → exclude from core fit region, keep for wings | Wide spread = liquidity evaporated (halt/macro event) → jagged surface. Text said 25%; Image 2 said 10% "for the belly." Both encoded. | Unfiltered wide spreads = noisy mids → surface kinks where liquidity dried up |
| 5 | **Zero-IV** | `implied_vol > 0.005` | Theta emits ~0 IV on failed/illiquid inversion → divide-by-zero in Numba vega | Zero IV crashes vega kernel; near-zero IV = unreliable inversion |
| 6 | **Intrinsic value** | calls: `mid ≥ max(0, S − K)`; puts: `mid ≥ max(0, K − S)` | Violating intrinsic = stale/crossed quote; arbitrage exists | Surface would fit arbitrageable prices → nonsense implied vols |
| 7 | **Monotonicity across strikes** | per `(expiration, timestamp, right)`, sort by K: call mids **non-increasing** in K; put mids **non-decreasing** in K | A kink = broken quote → eSSVI fails to converge or fabricates fake risk-reversal | Non-monotonic mids = butterfly arbitrage → eSSVI optimization diverges |
| 8 | **Open-interest liquidity** | keep `open_interest > 100` (per contract, per day) | Tight spread with zero structural depth = dangerous; real slippage ≫ mid | Phantom liquidity: you think you can trade at mid, but size isn't there |

**OI note:** OI is **daily** (EOD). For strict no-leakage use **prior session's** OI joined to the day's minutes (`OI_MODE="strict"`, Section 12). Research mode uses same-day EOD OI (leaks forward info).

After cleaning, every surviving minute row carries a clean `(bid, ask, mid, spread, implied_vol, delta)` plus a clean spot from Section 8.

---

## 6. Precise intraday business time `T` (in depth)

**Purpose:** `T` is the **time-to-expiry in years** used in three critical places:
1. Forward price: `F = S·e^{rT}` → defines moneyness `k = ln(K/F)`
2. Vega: `vega = e^{-rT} · F · φ(d1) · √T` → weights the eSSVI loss function
3. d1 in BS formula: `d1 = (ln(F/K) + 0.5σ²T) / (σ√T)`

**Why calendar days are wrong:** Treating `T` as integer days injects a large decay error between 10:00 and 15:30 of the same day. Options barely decay overnight/weekends/holidays because no information flows. Track **business minutes**, not calendar time.

### Formula (Image 2, made calendar-accurate)

```
T_years = ( minutes_remaining_today  +  Σ_d session_minutes(d) ) / (390 × 252)
```
- `minutes_remaining_today = max(0, session_close_today − bar_timestamp)` in minutes, only counted if today is a trading session and the bar is within it; `0` outside RTH.
- `Σ_d` runs over **trading days strictly between** the snapshot date and the expiration date (exclude today and expiration day), each weighted by **its own** session length:
  - regular day = **390** min (09:30–16:00 ET),
  - **early-close half-day = 210** min (09:30–13:00 ET, e.g. day after Thanksgiving, Christmas Eve, July 3).
- Denominator `390 × 252` = canonical "business minutes per year" (institutional convention). `T` is in years.

### Why the refinements matter
The flat-390 version in the image silently over-counts half-days and any day the exchange was closed. Use a real exchange calendar:

- **Primary:** `pandas_market_calendars` with the `XNYS`/Nasdaq calendar → gives exact session opens/closes including half-days and ad-hoc closures, offline and fast.
- **Cross-check:** Theta `/v3/calendar/year` holidays.

### Point-in-time / leakage rule for `T`
`T` depends only on the bar timestamp and the (known, fixed) expiration date — no future information. Store `T` (float64), and also store `dte_calendar` (int) for auditing. AMD options are PM-settled (expire at the 16:00 close); the "exclude expiration day" convention is the simplification used here — optionally add expiration-day session minutes up to 16:00 if you want the last-day decay.

### Pseudocode
```python
def business_T(bar_ts, expiry_date, cal):           # cal = mcal schedule
    sched = cal[(cal.index.date > bar_ts.date()) &
                (cal.index.date < expiry_date)]      # strictly between
    full_minutes = ((sched.market_close - sched.market_open)
                    .dt.total_seconds() / 60).sum()  # per-day real minutes
    today = cal.loc[cal.index.date == bar_ts.date()]
    if len(today):
        close = today.iloc[0].market_close
        mins_today = max(0, (close - bar_ts).total_seconds() / 60)
    else:
        mins_today = 0.0
    return (mins_today + full_minutes) / (390 * 252)
```

---

## 7. Structural variables `r` and `q`

### Risk-free `r`

**Purpose:** Used in two places — forward price `F = S·e^{(r−q)T}` and vega discount factor `e^{-rT}`. Even small rate errors compound in the forward (moneyness shift) and vega weight.

- Endpoint: `/v3/interest_rate/history/eod?symbol={SOFR|TREASURY_M1|TREASURY_M3|...}`.
- Returned `rate` is a **percent** → `r_simple = rate / 100`.
- **Tenor-match the option's DTE** (Image 1: "SOFR or 1-month T-bill matching your expiration bucket"). For DTE∈[7,90]: use `TREASURY_M1` for short, `TREASURY_M3` for the upper end, or linearly interpolate across `SOFR / M1 / M3` by DTE.
- **Compounding:** SOFR/T-bills are simple money-market rates; BS expects continuous compounding. For these short tenors the gap is tiny, but to be exact: `r_cc = ln(1 + r_simple·τ)/τ`, τ = tenor in years. A flat `r = rate/100` treated as cc is the acceptable simplification; make it a config switch (`SIMPLE_TO_CC`).
- Rates are **daily** → fetch once, cache, and join by date with point-in-time discipline (Section 12).

### Dividend yield `q`

**Purpose:** Forward price `F = S·e^{(r−q)T}`. For non-zero q, the forward drops relative to spot, shifting moneyness.

- **AMD = 0.0** across the window. Hard-code `q=0` for AMD and assert no ex-dates land in `[bar_date, expiration]`.
- General handler (Image 2): nightly worker hits **Alpha Vantage / Polygon** for cash dividend amounts + ex-dates → store in a `dividends(symbol, ex_date, cash_amount, announced_date)` table. Continuous yield `q ≈ trailing_12m_cash / S`, applied **point-in-time** (only ex-dates whose **announce date ≤ bar date**).

### Splits (guardrail)

- AMD: none since 2000 → no action in-window.
- General: store split events; **never back-adjust historical strikes with future splits** (that is look-ahead). Theta already returns the raw historical strikes that traded.

---

## 8. Spot, forward, and log-moneyness

- **Spot `S`:** the **`close`** of the AMD 1-minute bar, joined to the option bar on the floored-to-minute timestamp. Using the close (and pushing everything to the close) is the leakage-control choice you specified.
  - Theta's `first_order.underlying_price` is the underlying **mid at the option timestamp**; keep it as a sanity cross-check but treat the stock-OHLC `close` as canonical `S`.
  - *Known minor inconsistency:* Theta's `implied_vol`/`delta` were computed off the underlying mid, not your close. Acceptable for a vega-weighting input. **Optional rigor upgrade:** re-invert IV locally from the option mid against your own `F` for full internal consistency.
  - **Why it matters for eSSVI:** Spot is the anchor for forward price. A 1% spot error = 1% forward error = horizontal shift of the entire volatility smile.

- **Forward:** `F = S·e^{(r−q)T}` → for AMD `F = S·e^{rT}`.
  - **Why it matters for eSSVI:** Forward converts strikes to log-moneyness `k = ln(K/F)`. The eSSVI surface is parameterized in `(k, T)` space. Wrong forward = wrong moneyness = surface fits the wrong coordinates.

- **Log-moneyness (eSSVI x-axis):** `k = ln(K / F)`. Store `forward_price`; `k` can be derived or materialized.
  - **Why it matters for eSSVI:** This is the **primary x-coordinate** of the surface. The eSSVI parameterization `θ(k) = ...` expects moneyness, not raw strikes. Using strikes directly would make the surface non-stationary across time/spot moves.

---

## 9. Black-Scholes vega (local, Numba)

**Purpose:** Vega = `∂Price/∂σ` is the **weighting function** for the eSSVI loss. The surface fit minimizes `Σ vega_i · (IV_i − IV_model(k_i, T_i))²`. High-vega options (ATM, longer-dated) get more weight; low-vega options (wings, near-expiry) get less. This is the theoretically correct weighting — it's the sensitivity of the option price to vol error.

Compute vega in the **forward (Black-76) convention** for consistency with `k = ln(K/F)` (numerically identical to the spot form when `F=S·e^{(r−q)T}`):

```
d1   = ( ln(F/K) + 0.5·σ²·T ) / ( σ·√T )
vega = e^{−rT} · F · φ(d1) · √T          # φ(x) = exp(−x²/2)/√(2π)
```

Equivalent spot form (use either): `vega = S·e^{−qT}·φ(d1_spot)·√T`.

**Numba guards** (the prefilters already enforce most):
- `σ > 0.005` (Section 5 #5) → no divide-by-zero.
- `T > 0` (DTE ≥ 7 guarantees this).
- `S, K, F > 0`.
- `@njit(fastmath=False)`, float64 throughout; vectorize over the minute panel.
- **Units:** σ in decimals (Theta IV is decimal, e.g. `0.42`). Output is `∂Price/∂σ` for a full `1.00` vol move; divide by 100 if you want per-vol-point. Document which you store (`VEGA_UNITS = "per_1.0_vol"` in config).

**Why local Numba instead of Theta's `vega` column?**
1. **Consistency:** Theta's vega uses their `r`, `q`, `T` conventions. We use our precise `business_t`, our tenor-matched `r`, our `q=0`. Recomputing guarantees internal consistency.
2. **Units control:** Theta's vega may be per-vol-point; we want per-1.00-vol-move.
3. **Auditability:** Our `vega` is reproducible from stored `F, K, σ, T, r`.

---

## 10. Per-minute schema (the "uniform set")

**Composite natural key (your stated index):** `(timestamp, underlying_ticker, expiration, strike, option_type)`.

**Stored (your list):** `spot_price, forward_price, implied_vol, option_midprice, spread, vega`.

**Recommended additional stored columns** (auditability + recomputation, no leakage cost):

| Column | Why store it? |
|--------|---------------|
| `bid`, `ask` | Reproduce mid/spread exactly; re-run filters offline with different thresholds; debug quote quality |
| `delta` | Reproduce the band filter (0.10–0.90); debug which strikes were kept/dropped |
| `r`, `q` | The exact rate/yield used in `F` and `vega` — reproduce forward & vega without re-joining rates |
| `business_t` | The precise `T` used — don't recompute downstream; calendar logic is complex and must match exactly |
| `dte_calendar` | Quick filtering/auditing by calendar DTE (simpler than business_t) |
| `open_interest` | Liquidity audit; verify `OI > 100` filter worked; analyze OI vs spread relationship |
| `underlying_timestamp` | Spot-alignment audit — verify option bar timestamp matches spot bar timestamp |
| `quality_flags` | Bitmask: belly-spread (bit 0), intrinsic-tol (bit 1), monotonicity-tol (bit 2) — post-hoc filter analysis |
| `ingest_run_id` | Provenance / idempotency — which backfill run produced this row |

**Types:** `timestamp timestamptz` (UTC), `expiration date`, `strike numeric(12,4)` (or integer cents), `option_type char(1)` (`C`/`P`), prices/greeks `double precision`.

**Downstream contract:** The eSSVI fitter reads `ts, expiration, strike, option_type, implied_vol, forward_price, business_t, vega`. Everything else is for audit/debug/reproducibility.

---

## 11. TimescaleDB layout for speed

```sql
CREATE TABLE amd_surface_min (
  ts            timestamptz   NOT NULL,
  underlying    text          NOT NULL,
  expiration    date          NOT NULL,
  strike        numeric(12,4) NOT NULL,
  option_type   char(1)       NOT NULL,
  spot_price    double precision,
  forward_price double precision,
  implied_vol   double precision,
  option_mid    double precision,
  spread        double precision,
  vega          double precision,
  bid double precision, ask double precision, delta double precision,
  r double precision, q double precision,
  business_t double precision, dte_calendar int, open_interest int,
  quality_flags int, ingest_run_id bigint
);

SELECT create_hypertable('amd_surface_min', 'ts',
                         chunk_time_interval => INTERVAL '7 days');
```

### Indexes & keys
- TimescaleDB requires any UNIQUE index to include the partition column (`ts`). Use the dedup key:
  `UNIQUE (underlying, expiration, strike, option_type, ts)` — also clusters a single contract's bars contiguously (fast per-contract time-series scans) and matches the compression `segment_by`.
- Secondary index for the **surface-fit query** ("all strikes for one expiry at one minute"):
  `(underlying, expiration, ts, strike)`.
- *Tradeoff vs your stated order:* leading with `ts` (your spec) is best for "whole-panel time-range" scans; leading with the contract keys is best for "one contract over time" and for compression locality. Keeping the contract-led unique key + the expiry/ts secondary index serves both. Choose the primary by your dominant read pattern.

### Compression (biggest win on millions–billions of rows)
```sql
ALTER TABLE amd_surface_min SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'underlying, expiration, strike, option_type',
  timescaledb.compress_orderby   = 'ts DESC'
);
SELECT add_compression_policy('amd_surface_min', INTERVAL '7 days');
```
Expect ~10–20× shrink and faster scans, since one contract's series compresses as a columnar run.

### Other speedups
- Bulk-load with **`COPY`** (binary/CSV), never row-by-row `INSERT`.
- **Continuous aggregates** if downstream wants coarser bars (5-min/EOD snapshots) materialized.
- Generated column for `k = ln(strike/forward_price)` if you query by moneyness band often.
- One ticker ⇒ no space-partitioning needed.

---

## 12. Data-leakage prevention (expanded)

The non-negotiables, beyond "use the close":

1. **Point-in-time everything.** No value timestamped *after* a bar may enter that bar's row.
2. **Same-minute join.** Floor option and stock bars to the identical minute boundary; the spot is that minute's `close`. No cross-minute borrowing.
3. **Rates as-of date.** SOFR for day *D* is published ~08:00 ET *D* (for prior activity). Forward-fill the **last rate published on/before** the bar date; never a rate printed later.
4. **Dividends as-of announce date.** Apply only ex-dates whose **announcement ≤ bar date**. (AMD: none — assert empty.)
5. **No future split back-adjustment.** Use raw historical strikes exactly as listed then.
6. **No backward-fill of quotes.** A dead minute stays null/dropped. Carry-forward is allowed *only* for slow exogenous series (`r`, `q`) and *only* from the past.
7. **Survivorship-safe universe.** Build each date's contract set from `list/contracts`/`list/expirations` **as of that date**, not today's chain.
8. **Prior-session OI (strict mode).** EOD OI prints after the close, so intraday on day *D* only day *D−1*'s OI is truly known. Join *D−1* OI to *D*'s minutes for strict no-leakage; same-day EOD OI is the looser research default — pick one and document it.
   - **Why:** If you use same-day EOD OI during intraday backtesting, you're leaking information that wasn't available until after the close. A strategy that "sees" high OI at 10 AM when it only printed at 4 PM is cheating.
9. **UTC storage + ET session logic.** Store `timestamptz` in UTC; do all session/business-time math in `America/New_York`; guard DST.
10. **Idempotent, watermarked ingestion** so reruns never duplicate or gap (Section 13).

---

## 13. Ingestion control (robustness)

- **Idempotency:** `INSERT ... ON CONFLICT (underlying,expiration,strike,option_type,ts) DO NOTHING` (or `DO UPDATE` keyed to a newer `ingest_run_id`).
- **Watermark table:** `ingest_progress(underlying, expiration, chunk_end_date, status, rows, run_id)` so restarts resume exactly where they stopped — no gaps, no overlaps.
- **Quarantine, don't drop:** failed rows → `amd_surface_quarantine(... , reject_code)` with codes from Section 5 (`NO_QUOTE, CROSSED, SUBPENNY, SPREAD_HARD, ZERO_IV, INTRINSIC, MONOTONICITY, LOW_OI`). Lets you prove the filters didn't eat good data and measure each filter's impact.
- **Two-phase load per `(expiration, chunk)`:** assemble + validate + compute in memory → `COPY` into a `staging` table → row-count/PK validation → atomic `INSERT … SELECT` into the hypertable → advance watermark. Prevents partial writes.
- **Retries:** honor the engine's existing backoff on transient HTTP (`429/5xx`). Non-retryable (`400/401/404`) → log + quarantine the chunk, continue.

---

## 14. End-to-end worker (pseudocode)

```python
sem = asyncio.Semaphore(4)                      # Standard tier cap
cal = mcal.get_calendar("XNYS")                 # business-time source
rates = load_rates_cached()                     # interest_rate/history/eod
for E in option_list_expirations("AMD"):
    for C in month_chunks(window=[E-90, E-7]):
        async with sem:
            opt   = greeks_first_order("AMD", E, strike="*", right="both",
                                       interval="1m", date_range=C,
                                       annual_dividend=0, rate_type="sofr")
            stock = ohlc_cached("AMD", C)        # minute close
            oi    = open_interest("AMD", E, C)   # daily
        df = (opt
              .pipe(prefilter_delta_dte)                 # §4
              .pipe(clean_quotes_and_arbitrage)          # §5 (1-7)
              .merge(stock_close, on="minute")           # §8 spot
              .pipe(join_oi_prior_session, oi)           # §5 #8 + §12.8
              .pipe(attach_rate_pit, rates)              # §7/§12.3
              .assign(q=0.0)                             # AMD
              .pipe(compute_business_T, cal)             # §6
              .pipe(compute_forward)                     # §8
              .pipe(numba_bs_vega))                      # §9
        two_phase_load(df, run_id)                       # §11/§13
        advance_watermark("AMD", E, C)
```

---

## 15. Open items to confirm before backfill

1. **Port:** `25503` (v3 docs) vs `25510` (your config) — read the terminal banner.
2. **Splits path:** tier table lists Splits at Standard, but it's absent from the REST sidebar — verify the exact endpoint (likely `/v3/stock/history/split`). Non-blocking for AMD.
3. **`first_order` exact column names:** confirmed to mirror `greeks/all` minus 2nd/3rd-order (it includes `bid, ask, delta, vega, implied_vol, underlying_price`). Sanity-check one live response (`format=ndjson`) before mass backfill.
4. **Rate choice:** SOFR vs tenor-matched treasury, and simple vs continuous compounding — set the config switch.
5. **Spread threshold:** confirm `0.25` hard / `0.10` belly (your text vs Image 2).
6. **OI mode:** prior-session (strict) vs same-day EOD (research) — document the choice.

---

## 16. Implementation Reality: `dataingestion/` module map

The pipeline is implemented as a modular, async-first Python package under `dataingestion/`. Each module has a single responsibility and is tested in isolation.

```
dataingestion/
├── __init__.py
├── config.py           # ALL constants, thresholds, DB config, env overrides
├── orchestrator.py     # run_backfill() entry point, phase orchestration
├── fetchers.py         # ONLY module speaking HTTP to Theta v3
├── cleaning.py         # Pure pandas/numpy quality & arbitrage checks
├── math.py             # Numba JIT Black-76 vega, business time T, forward
├── joins.py            # Pure DataFrame transforms: spot, OI, rates, math
├── chunking.py         # Date-range splitting, DTE window helpers
├── cache.py            # Bounded LRU+TTL cache for OHLC, rates, contracts
├── retry.py            # Exponential backoff + semaphore-aware retries
├── db_writer.py        # ONLY module writing to TimescaleDB (asyncpg)
├── logging.py          # Structured JSON logging with ContextVars
├── verify.py           # Post-ingestion read-only integrity checks
├── COLUMNS.md          # Column contract between every module
├── run_order.md        # Architecture guide & run instructions
└── tests/
    ├── test_cleaning.py
    ├── test_math.py
    ├── test_fetchers.py
    ├── test_orchestrator.py
    ├── test_db_writer.py
    ├── test_chunking.py
    ├── test_config.py
    └── test_verify.py
```

### Module responsibilities & invariants

| Module | Responsibility | MUST NOT do |
|--------|---------------|-------------|
| `fetchers.py` | Raw HTTP → DataFrame, column normalization, Theta v3 only | Filtering, cleaning, DB, math, semaphore creation |
| `cleaning.py` | All quality/arbitrage checks, quarantine split | HTTP, DB, file I/O, math |
| `math.py` | `business_t`, `forward_price`, Numba `vega` | HTTP, DB, cleaning logic |
| `joins.py` | Pure DataFrame composition: spot, OI, rates → math | HTTP, DB |
| `cache.py` | Bounded LRU+TTL DataFrame cache | HTTP, DB |
| `retry.py` | Retry policy, error classification, semaphore hygiene | Business logic |
| `db_writer.py` | Schema, COPY, watermark, quarantine, TimescaleDB | HTTP, math, cleaning |
| `orchestrator.py` | Wire everything, semaphores, caches, watermarks, run_id | Raw SQL, inline HTTP, inline cleaning |

---

## 17. Configuration (`config.py`) — single source of truth

All thresholds, constants, and tunable parameters live in `config.py`. **Never hardcode values in pipeline modules.**

### Theta API Parameters
```python
THETA_INTERVAL = "1m"
THETA_FORMAT = "ndjson"
THETA_ANNUAL_DIVIDEND = 0
THETA_RATE_TYPE = "sofr"
THETA_VERSION = "latest"
```

### Cleaning Thresholds (Sections 4-5)
```python
MIN_DTE = 7; MAX_DTE = 90
MIN_DELTA_ABS = 0.10; MAX_DELTA_ABS = 0.90
MAX_REL_SPREAD_HARD = 0.25; MAX_REL_SPREAD_BELLY = 0.10
MIN_IV = 0.005
MIN_OI = 100
SUBPENNY_EPS = 1e-8
QUALITY_BELLY_SPREAD = 1  # bit 0
```

### Business Time (Section 6)
```python
BUSINESS_MINUTES_PER_DAY = 390
TRADING_DAYS_PER_YEAR = 252
BUSINESS_MINUTES_PER_YEAR = 390 * 252
NUMBA_SIGMA_EPS = 1e-10
NUMBA_T_EPS = 1e-10
```

### Database (TimescaleDB)
```python
PG_CONFIG = PGConfig(host=os.getenv("PGHOST", "127.0.0.1"),
                     port=int(os.getenv("PGPORT", "5432")),
                     user=os.getenv("PGUSER", "postgres"),
                     password=os.getenv("PGPASSWORD", "postgres"),
                     database=os.getenv("PGDATABASE", "postgres"))
CHUNK_TIME_INTERVAL_DAYS = 7
COMPRESSION_INTERVAL_DAYS = 7
```

### Concurrency (Tier- `OPT_SEM_LIMIT = 4` (Standard tier: greeks, OI, contracts)
- `STK_SEM_LIMIT = 10` (Value tier: stock OHLC, rates, calendar)

### Orchestrator
```python
DTE_WINDOW_MIN = 7; DTE_WINDOW_MAX = 90
SCHEDULE_BUFFER_DAYS = 14  # holiday safety (Christmas+New Year)
MAX_CHUNK_DAYS = 31
MAX_TRADING_DAYS_PER_CHUNK = 21
```

### Cache (EH-06 / EH209)
```python
OHLC_CACHE_MAX_CHUNKS = 50
OHLC_CACHE_TTL_HOURS = 24
RATES_CACHE_TTL_HOURS = 6
```

### Fetch Retry (EH206)
```python
FETCH_MAX_RETRIES = 3
FETCH_BASE_DELAY = 1.0
FETCH_MAX_DELAY = 30.0
FETCH_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
FETCH_NON_RETRYABLE_STATUS = {400, 401, 403, 404}
```

### OI Mode (Section 12.8)
```python
OI_MODE = "strict"  # prior-session EOD OI (D-1) — no leakage
# "research" = same-day EOD OI (D) — may leak forward information
```

### Rate Configuration (Section 7)
```python
RATE_SYMBOLS_SHORT = ("TREASURY_M1",)
RATE_SYMBOLS_MEDIUM = ("SOFR", "TREASURY_M1")
RATE_SYMBOLS_LONG = ("TREASURY_M1", "TREASURY_M3")
SIMPLE_TO_CC = False  # True = convert simple→cc via ln(1+r*τ)/τ
DTE_BUCKET_SHORT_MAX = 30
DTE_BUCKET_MEDIUM_MAX = 60
DTE_BUCKET_LONG_MAX = 90
VEGA_UNITS = "per_1.0_vol"  # ∂Price/∂σ for 1.00 vol move (σ in decimals)
```

---

## 18. Concurrency model (two semaphores)

| Resource | Semaphore | Limit | Protects |
|----------|-----------|-------|----------|
| Greeks / OI / Contracts | `OPT_SEM` | 4 | Standard tier API rate limit |
| Stock OHLC / Rates / Calendar | `STK_SEM` | 10 | Value tier API rate limit |

**Critical invariants:**
- Semaphores released **during backoff** — `fetch_with_retry` releases `_sem` before `asyncio.sleep()`.
- Parallel fetch within chunk: `asyncio.gather(_fetch_opt(), _fetch_oi(), _fetch_stk())`
- Sequential chunk processing — one chunk at a time per expiration (watermark serialization).

---

## 19. Column contract (COLUMNS.md summary)

Every module reads and produces DataFrames with exact columns. See `COLUMNS.md` for full detail.

| Phase | Module | Input `_phase` | Output `_phase` | Key columns added |
|-------|--------|----------------|-----------------|-------------------|
| Raw | `fetchers.py` | — | `"raw"` | `vega_api`, `spot_close`, `open_interest`, `_phase` |
| Clean | `cleaning.py` | `"raw"` | `"clean"` / `"quarantine"` | `mid_price`, `spread`, `rel_spread`, `quality_flags`, `dte_calendar`, `reject_code`, `reject_detail` |
| Math | `math.py` + `joins.py` | `"clean"` | `"math"` | `business_t`, `r`, `q`, `forward_price`, `vega`, `log_moneyness` |
| DB | `db_writer.py` | `"math"` | `"loaded"` | column rename (e.g., `timestamp`→`ts`, `spot_close`→`spot_price`, `mid_price`→`option_mid`) |

---

## 20. PostgreSQL / TimescaleDB — full schema & indexes

### Tables created by `db_writer.init_schema(pool)`

```sql
-- Main hypertable
CREATE TABLE amd_surface_min (
  ts timestamptz NOT NULL,
  underlying text NOT NULL,
  expiration date NOT NULL,
  strike numeric(12,4) NOT NULL,
  option_type char(1) NOT NULL,
  spot_price double precision,
  forward_price double precision,
  implied_vol double precision,
  option_mid double precision,
  spread double precision,
  vega double precision,
  bid double precision, ask double precision, delta double precision,
  r double precision, q double precision,
  business_t double precision, dte_calendar int, log_moneyness double precision,
  open_interest int, quality_flags int, ingest_run_id bigint,
  underlying_timestamp timestamptz,
  UNIQUE (underlying, expiration, strike, option_type, ts)
);

SELECT create_hypertable('amd_surface_min', 'ts',
                         chunk_time_interval => INTERVAL '7 days');

-- Staging table (identical structure, no constraints)
CREATE TABLE amd_surface_min_staging (LIKE amd_surface_min INCLUDING DEFAULTS);

-- Quarantine table (adds reject metadata)
CREATE TABLE amd_surface_quarantine (
  LIKE amd_surface_min,
  reject_code text,
  reject_detail text,
  ingested_at timestamptz DEFAULT NOW()
);

-- Watermark / progress tracking
CREATE TABLE ingest_progress (
  underlying text NOT NULL,
  expiration date NOT NULL,
  chunk_end_date date NOT NULL,
  status text NOT NULL,
  rows_loaded int DEFAULT 0,
  rows_quarantined int DEFAULT 0,
  run_id bigint,
  started_at timestamptz DEFAULT NOW(),
  completed_at timestamptz,
  PRIMARY KEY (underlying, expiration, chunk_end_date, run_id)
);

CREATE SEQUENCE IF NOT EXISTS ingest_run_id_seq;
```

### Indexes

```sql
-- Secondary index for surface-fit query pattern: (expiry, ts, strike)
CREATE INDEX idx_amd_surface_fit
  ON amd_surface_min (underlying, expiration, ts, strike);
```

### Compression policy

```sql
ALTER TABLE amd_surface_min SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'underlying, expiration, strike, option_type',
  timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('amd_surface_min', INTERVAL '7 days',
                              if_not_exists => TRUE);
```

**Expected compression:** ~10–20× shrink; one contract's series compresses as a columnar run.

### Connection pool (shared)
```python
# config.py PGConfig + db_writer.get_pool()
asyncpg.Pool(min_size=1, max_size=10, host=PGHOST, port=PGPORT, ...)
```

---

## 21. Ingestion control & idempotency (implementation detail)

### Watermark table: `ingest_progress`
| Column | Purpose |
|--------|---------|
| `underlying`, `expiration`, `chunk_end_date`, `run_id` | Composite PK — uniquely identifies a chunk attempt |
| `status` | `"completed"` / `"failed"` / `"in_progress"` |
| `rows_loaded`, `rows_quarantined` | Counters for verification |
| `started_at`, `completed_at` | Timing audit |

### Two-phase load per chunk (inside single DB transaction)
1. **Re-check watermark** inside transaction (race-safe).
2. `COPY` clean DataFrame → `amd_surface_min_staging`
3. `INSERT ... SELECT` staging → `amd_surface_min` `ON CONFLICT DO NOTHING`
4. `COPY` quarantine DataFrame → `amd_surface_quarantine`
5. `UPSERT` `ingest_progress` with `status='completed'`
6. `TRUNCATE` staging

### Run ID
Sequence `ingest_run_id_seq` → `next_run_id()` at backfill start. Propagated via `ContextVar` to all log lines and quarantine rows.

---

## 22. Verification suite (`verify.py`)

Post-ingestion read-only checks (8 checks):

| Check | What it validates |
|-------|-------------------|
| `chunk_completeness` | No gaps in `ingest_progress` per expiration |
| `column_coverage` | Null % on critical columns (`spot_price`, `implied_vol`, `vega` ≥ 99%) |
| `filter_impact` | Quarantine breakdown by `reject_code`; warns if `LOW_OI` > 50% or `NO_QUOTE` > 80% |
| `business_t_sanity` | `business_t` ∈ (0, 1], no NULLs |
| `no_future_leakage` | No rows with `ts > NOW()` |
| `essvi_sanity` | IV smile smoothness on a sample (ts, expiry): no IV > 5.0, no adjacent strike jumps > 5 vol pts |
| `data_freshness` | Reports oldest/newest timestamps |
| `row_counts` | Hypertable row count ≈ sum of `rows_loaded` in `ingest_progress` (within 1%) |

Run: `python -m dataingestion.verify` (requires DB).

---

## 23. Test coverage (offline, no Theta/DB required)

| Test file | Target | Key invariants checked |
|-----------|--------|------------------------|
| `test_cleaning.py` | `cleaning.py` | Every reject code, row accounting, output columns, no HTTP/DB/file I/O |
| `test_math.py` | `math.py` | `business_t` vs SciPy, forward price, Numba vega vs SciPy, half-day minutes, pre-open/after-close, expiry-day T |
| `test_fetchers.py` | `fetchers.py` | Column contracts, normalization, error handling (empty on 5xx), no semaphores/DB/heartbeat |
| `test_orchestrator.py` | `orchestrator.py` | Full pipeline mock, semaphore limits, watermark atomicity, retry integration, client lifecycle |
| `test_db_writer.py` | `db_writer.py` | Schema, COPY, ON CONFLICT, quarantine, watermark (requires DB) |
| `test_chunking.py` | `chunking.py` | Month chunks cover range, no gaps, DTE window math |
| `test_verify.py` | `verify.py` | All 8 checks on seeded data with known issues |
| `test_config.py` | `config.py` | All constants present, env overrides, orchestrator uses config |

Run: `pytest dataingestion/ -v --tb=short`

---

## 24. Running the backfill

### Prerequisites
```bash
# 1. Environment variables
export PGHOST=127.0.0.1
export PGPORT=5432
export PGUSER=postgres
export PGPASSWORD=postgres
export PGDATABASE=postgres

# 2. ThetaData terminal running and accessible
# java -jar ThetaTerminalv3.jar   (port from banner → THETA_PORT)

# 3. Python deps
pip install -r requirements.txt  # asyncpg, pandas, pandas-market-calendars, scipy, numba, etc.
```

### Execute
```python
from dataingestion.orchestrator import run_backfill
import asyncio
import datetime as dt

# Minimal (defaults: AMD, SOFR, 2018-01-01 → today)
result = asyncio.run(run_backfill())

# Custom
result = asyncio.run(run_backfill(
    start_date=dt.date(2024, 1, 1),
    end_date=dt.date(2024, 12, 31),
    underlying="NVDA",
    rate_symbol="TREASURY_M1",
))
```

### Returns
```python
{
    "total_clean_rows": 123456,
    "total_quarantined": 789,
    "errors": 0,
    "duration_seconds": 345.67
}
```

### Verify after
```bash
python -m dataingestion.verify
pytest dataingestion/ -v --tb=short
mypy dataingestion/
flake8 dataingestion/
```

---

## 25. Troubleshooting checklist

| Symptom | Check |
|---------|-------|
| `test_orchestrator` failures | Run `verify_phase3.py` first |
| DB connection errors | Verify `PG*` env vars, TimescaleDB running |
| Empty results | Check ThetaData terminal, symbol validity |
| Rate NaN propagation | `_attach_rates` logs warnings — check structured logs |
| Chunk skipped unexpectedly | Watermark table `ingest_progress` — look for `status=completed` |
| Semaphore deadlock | Ensure `fetch_with_retry` releases sem before `sleep()` |
| Memory pressure on 7-year backfill | `OHLC_CACHE_MAX_CHUNKS`, `RATES_CACHE_TTL_HOURS` tuning |

---

**End of blueprint.** This document now reflects the complete implemented reality of the `dataingestion/` pipeline, including every module, every column contract, every safety measure, and the full TimescaleDB schema with indexes and compression.