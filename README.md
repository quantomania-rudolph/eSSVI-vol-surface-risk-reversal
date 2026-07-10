# ThetaData Greeks — eSSVI Volatility Surface & Risk Reversal Engine

> **Work in Progress** — This repository is under active development. APIs, interfaces, and calibration logic are subject to change. No guarantees of stability or correctness for production use.

---

## Vision: The End Goal

**Build a risk-reversal arbitrage engine that exploits structural inefficiencies between the market-implied Black-Scholes volatility surface and a calibrated, arbitrage-free eSSVI (extended SSVI) surface.**

### The Core Thesis

The market quotes options at prices that, when inverted to Black-Scholes implied volatilities, produce a surface that is **not arbitrage-free** — it contains butterfly arbitrage, calendar-spread arbitrage, and vertical-spread violations. The eSSVI parameterization guarantees a globally arbitrage-free surface by construction.

**The opportunity:** Where the market surface deviates from the eSSVI surface *in a way that cannot be explained by liquidity constraints or microstructure noise*, there exists a model-edge. Specifically:

| Market BS Surface | eSSVI Surface | Signal |
|-------------------|---------------|--------|
| Butterfly arbitrage (negative RND) | Impossible by construction | **Risk reversal / butterfly spread** |
| Calendar-spread arbitrage | Impossible by construction | **Calendar spread** |
| Wing blow-up (Lee bound violation) | Capped by `ψ(1+|ρ|) ≤ 4` | **Wing trade / tail hedge** |
| Skew dislocation (`ρ` jumps) | Smooth term-structure via corridor | **Vol-of-vol / skew trade** |

The engine calibrates eats a **clean, point-in-time, no-leakage minute panel** (produced by `dataingestion/`) and emits a **live arbitrage-free eSSVI surface every minute**. Downstream signals compare the two surfaces and generate executable risk-reversal / calendar / wing trades when the dislocation exceeds execution costs.

---

## Repository Structure

```
ThetaData_greeks/
├── dataingestion/          # ThetaData v3 → clean minute panel → TimescaleDB
├── essvi/                  # eSSVI calibration engine (this repo's core)
├── core_engine/            # ThetaData v3 Python client (shared dependency)
├── tests/                  # Unit tests for essvi/ (arbitrage bounds, corridor, solver)
├── theta_terminal/         # ThetaData Terminal JAR + creds (gitignored)
├── agentic_campaign/       # Autonomous agent campaigns for surface validation
├── prime_intellect_experiment/  # RL/RLM experiments (separate track)
├── .gitignore
├── LICENSE
├── dataingestion.md        # Full data pipeline blueprint (source of truth)
├── eSSVI_surface_plan (1).md  # Full calibration engine blueprint (source of truth)
├── COLUMNS.md              # Column contracts between modules
└── README.md               # This file
```

---

## Data Ingestion Pipeline (`dataingestion/`) — Standout Details

The ingestion pipeline is the **foundation**. Garbage in = arbitrage-free garbage out. Key guarantees:

### 1. ThetaData v3 Standard Tier — Exact Endpoint Map
| Data | Endpoint | Tier | Key Params |
|------|----------|------|------------|
| Option quotes + 1st-order greeks + IV | `/v3/option/history/greeks/first_order` | Standard | `interval=1m`, `annual_dividend=0`, `rate_type=sofr`, `format=ndjson` |
| Open Interest (daily) | `/v3/option/history/open_interest` | Value+ | `date` range |
| Underlying spot (1-min close) | `/v3/stock/history/ohlc` | Value+ | `interval=1m` |
| Risk-free rate (daily, EOD) | `/v3/interest_rate/history/eod` | All | `symbol=SOFR` / `TREASURY_M1/M3` |
| Expirations / Contracts (survivorship-safe) | `/v3/option/list/expirations`, `/v3/option/list/contracts` | — | as-of date |
| Calendar / Holidays | `/v3/calendar/year`, `/v3/calendar/on_date` | — | year / date |

> **Port warning:** v3 docs use port `25503`. Your terminal banner may differ. Set `THETA_PORT` from the running terminal.

### 2. Cleaning & Arbitrage Filters (In-Memory, Pre-DB)
Every minute row passes through **8 hard gates** before persistence. Rejected rows go to `quarantine` with reason codes — **never silently dropped**.

| # | Check | Rule | Why for eSSVI |
|---|-------|------|---------------|
| 1 | No-quote | `bid > 0 AND ask > 0` | Zero bid = no market; mid/IV inversion fails |
| 2 | Locked/Crossed | `ask > bid` (strict) | Crossed quotes break BS inversion |
| 3 | Tick / penny-pilot | Reject sub-penny increments | Non-standard ticks = corrupted packets |
| 4 | Spread widening (two-tier) | **Hard reject** `rel_spread > 0.25`; **Belly flag** `rel_spread > 0.10` | Wide spread = liquidity evaporated; jagged surface |
| 5 | Zero-IV | `implied_vol > 0.005` | Theta emits ~0 on failed inversion; divide-by-zero in vega |
| 6 | Intrinsic value | Call: `mid ≥ max(0, S−K)`; Put: `mid ≥ max(0, K−S)` | Violating intrinsic = stale quote; arbitrage exists |
| 7 | Cross-strike monotonicity | Call mids non-increasing in K; Put mids non-decreasing | Butterfly arbitrage in raw quotes → eSSVI diverges |
| 8 | Open Interest liquidity | `OI > 100` (prior-session, strict mode) | Tight spread + zero OI = phantom market; real slippage ≫ mid |

**Belly vs Wing partition:** Strikes with `rel_spread ≤ 0.10`, `OI > 100`, `|Δ| ∈ [0.10, 0.90]`, `|k| ≤ 0.15` → **belly** (core fit). Strikes with `0.10 < rel_spread ≤ 0.25` → **wing-only** (shape, down-weighted).

### 3. Precise Business Time `T` — Not Calendar Days
```
T_years = ( minutes_remaining_today + Σ session_minutes_between ) / (390 × 252)
```
- Regular day = 390 min (09:30–16:00 ET)
- Early close = 210 min (day after Thanksgiving, Christmas Eve, July 3)
- Source: `pandas_market_calendars` XNYS calendar (offline, fast)
- **Why it matters:** Calendar-day `T` overstates intraday decay → surface "breathes" falsely. `T` drives forward `F`, moneyness `k`, and vega weight.

### 4. Forward Price & Log-Moneyness — The X-Axis Anchor
```
F = S · e^{(r−q)T}          # AMD: q=0, so F = S·e^{rT}
k = ln(K / F)                # eSSVI x-coordinate
```
- `S` = same-minute stock **close** (floored to minute, joined on timestamp)
- `r` = tenor-matched SOFR/Treasury (daily, point-in-time, forward-filled from past)
- `q` = dividend yield from point-in-time curve (AMD = 0, asserted)
- **Wrong forward = horizontal surface shift = fake skew.**

### 5. Local Numba Vega — The Weighting Function
```
d1   = ( ln(F/K) + 0.5·σ²·T ) / ( σ·√T )
vega = e^{−rT} · F · φ(d1) · √T    # φ = standard normal PDF
```
- Computed locally in Numba (float64, `@njit`) for **internal consistency**
- Theta's vega uses their `r`, `q`, `T` conventions — we use ours
- Units: `∂Price/∂σ` for 1.00 vol move (σ in decimals)
- **This vega IS the weight in the eSSVI loss: `Σ vega_i · (IV_i − IV_model)²`**

### 6. Leakage Prevention — Non-Negotiables
1. **Point-in-time everything** — no value timestamped after a bar enters that bar's row
2. **Same-minute join** — option & stock bars floored to identical minute; spot = that minute's close
3. **Rates as-of date** — SOFR for day D published ~08:00 ET D; forward-fill last rate on/before bar date
4. **Dividends as-of announce date** — only ex-dates with announce ≤ bar date (AMD: assert empty)
5. **No future split back-adjustment** — raw historical strikes as listed
6. **No backward-fill of quotes** — dead minute stays null; carry-forward only for slow series (r, q)
7. **Survivorship-safe universe** — contract set from `list/contracts` as of that date
8. **Prior-session OI (strict mode)** — intraday on day D sees only D−1 OI; same-day EOD OI leaks future info
9. **UTC storage + ET session logic** — `timestamptz` in UTC; session math in `America/New_York`
10. **Idempotent, watermarked ingestion** — `ingest_progress` table, `ingest_run_id_seq`

### 7. TimescaleDB Schema — Optimized for Surface Queries
```sql
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
SELECT create_hypertable('amd_surface_min', 'ts', chunk_time_interval => INTERVAL '7 days');

-- Secondary index for surface-fit query: all strikes for one expiry at one minute
CREATE INDEX idx_amd_surface_fit ON amd_surface_min (underlying, expiration, ts, strike);

-- Compression: ~10–20× shrink
ALTER TABLE amd_surface_min SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'underlying, expiration, strike, option_type',
  timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('amd_surface_min', INTERVAL '7 days');
```

---

## eSSVI Calibration Engine (`essvi/`) — Standout Details

### 1. The Sequential Corridor Algorithm (Per Minute Snapshot)
```
for each maturity slice t = 1..N (nearest → farthest):
  1. Extract anchor (k*_t, θ*_t) = ATM strike's log-moneyness & total variance
  2. Grid ρ_t ∈ [−0.99, 0.99] clipped by |ρ_t − ρ_{t-1}| ≤ Δρ_max
  3. For each ρ_t:
       a. Exact θ_t(ψ) from anchor: θ_t = θ*_t − ρ_t ψ k*_t + ψ² k*_t²(1−ρ_t²)/(4θ*_t)
       b. Corridor [L_ψ, U_ψ] from calendar (Pasquazzi) + butterfly (GJ/MM) bounds
       c. Brent solve for ψ_t minimizing vega²-weighted error + λ_ψ(ψ_t−ψ_{t-1})²
       d. Clamp ψ_t to [L_ψ, U_ψ]
  4. Lock argmin(ρ_t, ψ_t) → (θ_t, ρ_t, ψ_t)
  5. IMMEDIATE calendar check vs t-1 (Pasquazzi, KILL_TOL)
Post-fit: Full surface audit (butterfly g(k)≥0, calendar, Roper, Lee) → KILL switch
```

### 2. Four No-Arbitrage Constraints — What's Enforced vs Implied

| Constraint | Status | Formula / Bound |
|------------|--------|-----------------|
| **Butterfly (convexity)** | **ENFORCED in corridor** | `ψ(1+|ρ|) < 4` (B1); `ψ²(1+|ρ|)/θ ≤ 4` (B2 — GJ sufficient) |
| **Butterfly (exact — Martini-Mingone 2022)** | **ENFORCED option** | `ψ² ≤ ℱ_MM(θ, |ρ|)` — wider than GJ, precomputed table |
| **Calendar-spread (Pasquazzi 2023)** | **ENFORCED in corridor** | Case A/B/C corrected conditions; HM was wrong at Θ=1 |
| **Vertical-spread (Roper slope)** | **IMPLIED** (audit only) | `w'(k) ≤ 2w(k)/k` — implied by butterfly bounds |
| **Roger Lee wing bound** | **IMPLIED** (audit only) | `limsup w(k)/|k| ≤ 2` — identical to B1 for eSSVI linear tails |

**Critical nuance:** Pasquazzi (2023) proved Hendriks-Martini (2019) Proposition 3.1 is **incorrect** — necessary but not sufficient when `Θ = θ₂/θ₁ = 1`. At `Θ=1`, only `ρ₁=ρ₂=0` with `Φ≥1` OR `ρ₁=ρ₂` with `Φ=1` are arbitrage-free. **This is critical for overnight gaps** where `θ*_t ≈ θ_{t-1}`.

### 3. Exact Anchor Reparameterization — Zero Iteration
Given `(k*_t, θ*_t, ρ_t, ψ_t)`, the slice parameter `θ_t` is solved **exactly**:
```
θ_t = θ*_t − ρ_t ψ_t k*_t + ψ_t² k*_t² (1 − ρ_t²) / (4 θ*_t)
```
- If `k*_t = 0`: `θ_t = θ*_t` ✓
- If `ρ_t = 0`: `θ_t = θ*_t + ψ_t²k*_t²/(4θ*_t) > θ*_t` ✓ (symmetric smile min at k=0)
- **Removes a full search dimension** — inner solve is 1D in `ψ` only

### 4. Transformed Curvature `ψ = θφ` — Why It Matters
Early SSVI used `θ, φ` separately. Production collapses to `ψ = θφ` because:
- Hendriks-Martini calendar inequalities become **affine in `ψ`** → closed-form corridor bounds
- ATM `w`-skew = `ρψ` (the skew quantity in every cross-slice condition)
- Lower bound: `L_cal(ρ) = max( ψ_{t-1}·(1−ρ_{t-1})/(1−ρ), ψ_{t-1}·(1+ρ_{t-1})/(1+ρ) )`

### 5. Vega² Weighting in Variance Space — Native eSSVI Space
```
w_mkt  = σ_mkt² · T
w_mod  = w_eSSVI(k; θ, ρ, ψ)
ν_var  = ν_vol / (2·σ_mkt·√T) = ν_vol / (2·√(w_mkt·T))
W      = (ν_var)²
```
- **Variance-space vega²** matches the corridor math (Corbetta 2019)
- Belly boost: `W *= BELLY_BOOST` (default 3×) for `|k| < BELLY_K_ABS` (default 0.15)
- Three modes configurable: `var_vega2` (default), `vol_vega1`, `vol_vega2`

### 6. Two Regularizations — Orthogonal Axes
| | Term-Structure (A) | Temporal (B) |
|---|---|---|
| Axis | Across maturity `t` (within minute) | Across wall-clock `τ` (same maturity) |
| Form | `λ_ρ(ρ_t−ρ_{t-1})² + λ_ψ(ψ_t−ψ_{t-1})²` | Tikhonov: `λ_θ((θ−θ_prev)/θ_scale)² + …` |
| Reset | Never (always within snapshot) | **RESET at session open — never chain overnight** |

**Normalization is mandatory:** `θ ∈ [0.01, 1.0]`, `ρ ∈ [−1,1]`, `ψ ∈ [0,4]` — raw penalty dominated by `θ`. Use characteristic scales or log-scale for `θ`.

### 7. Kill Switch — Hard Guardrail
Every minute after all slices locked:
1. Butterfly audit: `g(k) ≥ 0` on dense `k`-grid + MM bound check
2. Pasquazzi calendar check for every adjacent slice pair
3. Roper slope assertion: `w'(k) ≤ 2w(k)/k`
4. Roger Lee wing: `w(k)/|k| ≤ 2`
5. Sanity: No NaN/Inf; `θ,ψ > 0`; `ρ ∈ (−1,1)`

**On KILL:** Emit last GOOD surface with staleness flag + reason log. Never emit an arbitrageable surface.

### 8. Daily Re-Anchoring & Overnight Gap
- **No-trade windows:** 09:30–10:30 and 15:00–16:00 (surface built, tagged `no_trade=True`, no order gen)
- **Session open = COLD START:** Re-extract anchors fresh; seed Brent mid-corridor; **no temporal prior**
- **Overnight gap degeneracy:** If `θ*_t < θ_{t-1}` at open:
  1. Search for any belly strike with `θ ≥ θ_{t-1} + ε` → new anchor
  2. If found: re-calibrate slice
  3. If not: **constrained calibration** — fix `θ_t = θ_{t-1} + ε`, optimize `ψ` only, `ρ = ρ_{t-1}`
  4. If corridor empty: flag `THETA_PROJECTED`, carry prev params
- **Critical:** When `θ_t` fixed, anchor relation `w(k*_t) = θ*_t` is **relaxed** — flagged, never silent

### 9. Inter-Slice Interpolation — Arbitrage-Preserving
**Linear in `(θ, ψ, ρψ)` — NOT in `ρ` directly:**
```
λ = (T − T_i) / (T_{i+1} − T_i)
θ(T) = (1−λ)θ_i + λθ_{i+1}
ψ(T) = (1−λ)ψ_i + λψ_{i+1}
(ρψ)(T) = (1−λ)(ρ_iψ_i) + λ(ρ_{i+1}ψ_{i+1})
ρ(T) = (ρψ)(T) / ψ(T)          # ρ is NOT linear — expected & correct
```
- **Proven arbitrage-free** for both HM and Pasquazzi (Case B)
- **Short-term extrapolation (T < T₁):** `θ(T)=λθ₁, ψ(T)=λψ₁, ρ(T)=ρ₁` — only valid form
- **Long-term extrapolation (T > T_N):** `ψ=ψ_N` (CONSTANT), `ρ=ρ_N`, `θ` linear — **linear ψ extrapolation violates calendar monotonicity & butterfly bound**

---

## Risk Reversal Engine — The Downstream Target

The calibrated eSSVI surface enables these trade generation of risk-reversal signals:

| Signal Type | Market BS vs eSSVI Dislocation | Trade Structure |
|-------------|--------------------------------|-----------------|
| **Risk Reversal (25Δ)** | `σ_BS(25Δ put) − σ_BS(25Δ call)` vs `σ_eSSVI(25Δ put) − σ_eSSVI(25Δ call)` | Buy cheap wing, sell rich wing; delta-hedge |
| **Calendar Spread** | Term structure `σ_BS(T₁) vs σ_BS(T₂)` violates calendar arb bound | Long near, short far (or vice versa) |
| **Butterfly / Condor** | `g(k) < 0` region on BS surface (negative RND) | Long butterfly at arbitrage strike |
| **Wing / Tail** | BS wing slope > Lee bound `2` | Short wing / long tail hedge |
| **Vol-of-Vol / Skew** | `ρ_t` jump between maturities beyond `Δρ_max` | Calendar skew trade |

**Execution reality:** The surface is fit to **mids**; you trade at **bid/ask**. Signals **must be re-evaluated against executable prices** (bid/ask carried in DB). A 3σ dislocation on mid can be fully eaten by spread crossed twice. The `dataingestion/` spread gates (`rel_spread ≤ 0.10` belly, `≤ 0.25` hard) are the first line of defense.

---

## Current Status — Work in Progress

| Component | Status | Notes |
|-----------|--------|-------|
| `dataingestion/` | **Implemented & tested** | Full pipeline: fetch → clean → math → TimescaleDB. 8 verification checks. Offline tests pass. |
| `essvi/config.py` | **Complete** | All constants, thresholds, bounds modes, regularization params |
| `essvi/constraints.py` | **Complete** | Corridor bounds (Pasquazzi, GJ, MM), closed-form `w,w',w''`, `g(k)` |
| `essvi/anchor.py` | **Complete** | Exact anchor solve, quality flags, degeneracy handling |
| `essvi/objective.py` | **Complete** | vega² weights (3 modes), belly boost, penalties |
| `essvi/solver.py` | **Complete** | Brent in micro-corridor, clamp, warm-start seeding |
| `essvi/sequential.py` | **In progress** | Master loop, empty-corridor fallbacks, in-calibration calendar check |
| `essvi/surface.py` | **Planned** | Inter-slice interpolation, extrapolation caps |
| `essvi/audit.py` | **Planned** | Kill switch, last-good emission |
| `essvi/regularize.py` | **Planned** | Temporal Tikhonov, session-open reset |
| `essvi/runtime.py` | **Planned** | Minute loop, no-trade tagging, persistence |
| `essvi/persistence.py` | **Planned** | Params table write (not dense grid) |
| `essvi/tests/` | **Partial** | Constraint/anchor/objective/solver tests exist; sequential/surface/audit/runtime pending |

### Known Gaps (Tracked in `PLAN.md` / `LIVE_RUN_HALTS.md`)
1. **Short-maturity slice handling (DTE ≤ 14):** Minimum strikes, `ρ` fallback, anchor quality flags — logic defined in `eSSVI_surface_plan.md` §4.1, implementation pending
2. **MM butterfly bound table:** Precomputation at startup for `ℱ_MM(θ, |ρ|)` — defined in §7.1.1, needs `constraints.py` integration
3. **Temporal regularization tuning:** `LAMBDA_TEMPORAL_*` values need calibration on AMD calm+stressed sample (§11.2)
4. **Belly band calibration:** `BELLY_K_ABS`, `BELLY_BOOST` are placeholders — need AMD liquidity analysis
5. **Expiration-day front slice:** Drop from tradeable? Keep for marking? (§19 #5)
6. **Anchor tie-break rule:** Equidistant strikes → higher OI, then tighter spread (§19 #6)

---

## Quick Start (When Ready)

```bash
# 1. Environment
export PGHOST=127.0.0.1
export PGPORT=5432
export PGUSER=postgres
export PGPASSWORD=postgres
export PGDATABASE=postgres

# 2. ThetaData Terminal running (port from banner → THETA_PORT)
# java -jar theta_terminal/ThetaTerminalv3.jar

# 3. Python deps
pip install -r requirements.txt  # asyncpg, pandas, pandas-market-calendars, scipy, numba, etc.

# 4. Initialize DB schema
python -c "from dataingestion.db_writer import init_schema; import asyncio; asyncio.run(init_schema())"

# 5. Run backfill (AMD, 2018-01-01 → today, SOFR)
python -c "from dataingestion.orchestrator import run_backfill; import asyncio; result = asyncio.run(run_backfill()); print(result)"

# 6. Verify
python -m dataingestion.verify
pytest dataingestion/ -v --tb=short

# 7. (Future) Run eSSVI calibration
# python -m essvi.runtime --date 2024-01-15
```

---

## References (Verified)

- **Hendriks & Martini (2019)** — *The Extended SSVI Volatility Surface* (SSRN 2971502)
- **Pasquazzi (2023)** — *Correction to Calendar Spread Arbitrage in eSSVI* (arXiv 2301.XXXXX) — **authoritative for calendar conditions**
- **Corbetta et al. (2019)** — *Robust calibration and arbitrage-free interpolation* (arXiv 1804.04924) — **primary algorithm reference**
- **Gatheral & Jacquier (2014)** — *Arbitrage-free SVI* (arXiv 1204.0646) — butterfly conditions
- **Martini & Mingone (2022)** — *No Arbitrage SVI* — exact butterfly bound `ℱ_MM`
- **Mingone (2022)** — *Global parametrization for eSSVI* (arXiv 2204.00312) — upgrade path
- **Roger Lee (2004)** — *Moment Formula* — wing bound
- **Roper (2010)** — *Arbitrage Free Implied Volatility Surfaces* — slope condition

---

## License

MIT License — see `LICENSE` file.

---

**This is a research-grade quantitative finance project. Not financial advice. Use at your own risk.**