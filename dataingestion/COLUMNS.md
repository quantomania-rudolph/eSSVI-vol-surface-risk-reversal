# Column Contract — AMD eSSVI Data Ingestion Pipeline

Every module in `dataingestion/` reads and produces DataFrames with these exact columns.
No module may add or rename columns without updating this document and coordinating
with all downstream consumers.

---

## Phase-Defining Convention

All DataFrames carry a `_phase` column (set by the producer) that tracks which
pipeline steps have been applied. This is purely informational for debugging.
Every producer sets it once in its output.

| Phase tag | Set by | Meaning |
|-----------|--------|---------|
| `raw` | `fetchers.py` | Fresh from Theta, no cleaning |
| `clean` | `cleaning.py` | All pre-filters and quality checks passed |
| `quarantine` | `cleaning.py` | Rejected by pre-filter or quality check |
| `math` | `math.py` | T, forward, vega computed |
| `loaded` | `db_writer.py` | Written to TimescaleDB |

---

## I. Raw Fetch Output Columns (fetchers.py → cleaning.py)

`fetchers.py` produces a **single** DataFrame per `(expiration, date_chunk)` from
`/v3/option/history/greeks/first_order` joined with spot and OI. The output columns
are a superset of the Theta response plus joined-in data.

| Column | dtype | Source | Nullable? | Notes |
|--------|-------|--------|-----------|-------|
| `timestamp` | `datetime64[ns, UTC]` | `greeks/first_order.timestamp` | **No** | Floored to the minute boundary. The primary time axis. |
| `underlying` | `str` | Hardcoded `"AMD"` | **No** | Ticker symbol (constant for this pipeline). |
| `expiration` | `datetime64[ns]` | Worker param | **No** | The expiration date for this option contract. |
| `strike` | `float64` | `greeks/first_order.strike` | **No** | Raw strike as returned by Theta. No split adjustment. |
| `option_type` | `str` (`"C"` or `"P"`) | `greeks/first_order.right` | **No** | Normalized to single character. |
| `bid` | `float64` | `greeks/first_order.bid` | Yes | |
| `ask` | `float64` | `greeks/first_order.ask` | Yes | |
| `delta` | `float64` | `greeks/first_order.delta` | Yes | Theta's computed delta. |
| `theta` | `float64` | `greeks/first_order.theta` | Yes | Theta's computed theta. |
| `vega_api` | `float64` | `greeks/first_order.vega` | Yes | Theta's computed vega (for cross-check only). |
| `rho` | `float64` | `greeks/first_order.rho` | Yes | Theta's computed rho. |
| `implied_vol` | `float64` | `greeks/first_order.implied_vol` | Yes | Raw IV from Theta. |
| `iv_error` | `float64` | `greeks/first_order.iv_error` | Yes | Theta's IV fit error (if emitted). |
| `underlying_price` | `float64` | `greeks/first_order.underlying_price` | Yes | Theta's underlying mid at option timestamp (cross-check only). |
| `underlying_timestamp` | `datetime64[ns, UTC]` | `greeks/first_order.underlying_timestamp` | Yes | |
| `spot_close` | `float64` | Joined `stock/history/ohlc.close` | **No** | Minute-close of AMD. Joined on floored timestamp. |
| `open_interest` | `int64` | Joined `option/history/open_interest` | Yes | Daily OI, prior-session in strict mode. |
| `_phase` | `str` | Set to `"raw"` | **No** | |

**Invariants for fetchers.py:**
- Every row MUST have `timestamp` and `spot_close` non-null.
- `expiration` MUST be the same value for all rows in this DataFrame (it's the worker's expiration).
- `underlying` MUST be `"AMD"` for all rows.
- The DataFrame is chunked by `(expiration, date_chunk)` — one call per chunk.
- Returns an empty DataFrame if Theta returns no data for that chunk.

---

## II. Clean Output Columns (cleaning.py → math.py)

`cleaning.py` inputs the raw DataFrame and outputs **two** DataFrames:

### II.A. Clean DataFrame (`cleaning.py` output, `math.py` input)

Identical columns to the raw input, with these changes:

| Column | Change |
|--------|--------|
| `mid_price` | **Added** — `(bid + ask) / 2`, float64, non-null |
| `spread` | **Added** — `ask - bid`, float64, non-null |
| `rel_spread` | **Added** — `(ask - bid) / mid_price`, float64 |
| `quality_flags` | **Added** — int32 bitmask (see below) |
| `dte_calendar` | **Added** — int, `(expiration - bar_date).days` |
| `_phase` | Set to `"clean"` |

### II.B. Quarantine DataFrame

| Column | dtype | Notes |
|--------|-------|-------|
| All columns from raw input | same as raw | The rejected row as-is |
| `reject_code` | `str` | One of the codes below |
| `reject_detail` | `str` | Human-readable reason |
| `_phase` | `str` | Set to `"quarantine"` |

**Reject codes** (Section 4-5 of dataingestion.md):

| Code | Check |
|------|-------|
| `DELTA_BAND` | Pre-filter: `abs(delta)` outside [0.10, 0.90] |
| `DTE_BAND` | Pre-filter: `dte_calendar` outside [7, 90] |
| `NO_QUOTE` | `bid <= 0` or `ask <= 0` |
| `CROSSED` | `bid >= ask` |
| `SUBPENNY` | `bid` or `ask` not on penny grid |
| `SPREAD_HARD` | `rel_spread > 0.25` |
| `ZERO_IV` | `implied_vol <= 0.005` |
| `INTRINSIC` | Mid violates intrinsic value |
| `MONOTONICITY` | Strike monotonicity violated |
| `LOW_OI` | `open_interest <= 100` (or null OI) |

**Quality flag bitmask:**

| Bit | Flag | Meaning |
|-----|------|---------|
| 0 | `BELLY_SPREAD` | `rel_spread > 0.10` — exclude from core fit, keep for wings |

---

## III. Math Output Columns (math.py → db_writer.py)

| Column | dtype | Source | Notes |
|--------|-------|--------|-------|
| All columns from clean input | same as II.A | Forwarded | |
| `forward_price` | `float64` | Computed | `F = spot_close * exp(r * T)` |
| `r` | `float64` | Attached | The risk-free rate (decimal, cc) used for this row |
| `q` | `float64` | Attached | 0.0 for AMD |
| `business_t` | `float64` | Computed | Business time in years (Section 6) |
| `vega` | `float64` | Computed | Numba BS vega (Black-76 convention) |
| `log_moneyness` | `float64` | Computed | `ln(strike / forward_price)` |
| `_phase` | `str` | Set to `"math"` | |

---

## IV. DB Schema Columns (db_writer.py → TimescaleDB)

These are the columns that land in `amd_surface_min`:

| Column | TimescaleDB type |
|--------|-----------------|
| `ts` | `timestamptz NOT NULL` |
| `underlying` | `text NOT NULL` |
| `expiration` | `date NOT NULL` |
| `strike` | `numeric(12,4) NOT NULL` |
| `option_type` | `char(1) NOT NULL` |
| `spot_price` | `double precision` |
| `forward_price` | `double precision` |
| `implied_vol` | `double precision` |
| `option_mid` | `double precision` |
| `spread` | `double precision` |
| `vega` | `double precision` |
| `bid` | `double precision` |
| `ask` | `double precision` |
| `delta` | `double precision` |
| `r` | `double precision` |
| `q` | `double precision` |
| `business_t` | `double precision` |
| `dte_calendar` | `int` |
| `log_moneyness` | `double precision` |
| `open_interest` | `int` |
| `quality_flags` | `int` |
| `ingest_run_id` | `bigint` |
| `_phase` | `text` |

**Mapping from dframe column → db column:**

| dframe column | db column |
|---------------|-----------|
| `timestamp` | `ts` |
| `underlying` | `underlying` |
| `expiration` | `expiration` |
| `strike` | `strike` |
| `option_type` | `option_type` |
| `spot_close` | `spot_price` |
| `forward_price` | `forward_price` |
| `implied_vol` | `implied_vol` |
| `mid_price` | `option_mid` |
| `spread` | `spread` |
| `vega` | `vega` |
| `bid` | `bid` |
| `ask` | `ask` |
| `delta` | `delta` |
| `r` | `r` |
| `q` | `q` |
| `business_t` | `business_t` |
| `dte_calendar` | `dte_calendar` |
| `log_moneyness` | `log_moneyness` |
| `open_interest` | `open_interest` |
| `quality_flags` | `quality_flags` |
| `ingest_run_id` | `ingest_run_id` (supplied by orchestrator) |
| `_phase` | `_phase` |

**UNIQUE constraint:** `(underlying, expiration, strike, option_type, ts)`

---

## V. Concurrency Plan (Two Semaphores)

The orchestrator (`A5`) manages two separate semaphores, reflecting
the different Theta Data tier limits:

```python
# Standard tier: options data (greeks, OI, contracts)
OPT_SEM  = asyncio.Semaphore(4)

# Value tier: stock data (OHLC)
STK_SEM  = asyncio.Semaphore(2)
```

Every `client.get()` for an options endpoint acquires `OPT_SEM` first.
Every `client.get()` for a stock/rate/calendar endpoint acquires `STK_SEM` first.
The heartbeat is outside both.

**Parallelism within a single chunk:**
```python
async with asyncio.TaskGroup() as tg:
    async with OPT_SEM:
        opt_task = tg.create_task(fetch_greeks(exp, chunk))
        oi_task  = tg.create_task(fetch_oi(exp, chunk))
    async with STK_SEM:
        stk_task = tg.create_task(fetch_ohlc(chunk))
```

Rates are fetched once globally, not per-chunk.

---

## VI. Module Dependency Graph

```
fetchers.py  ──depends on──>  core_engine.shared.theta_client
                             core_engine.shared.parse
                             core_engine.shared.config

cleaning.py  ──depends on──>  pandas, numpy  (NO Theta, NO DB)

math.py      ──depends on──>  pandas, numpy, numba, pandas_market_calendars
                              (NO Theta, NO DB, NO cleaning)

db_writer.py ──depends on──>  asyncpg  (NO Theta, NO math, NO cleaning)

orchestrator.py ──depends on──>  fetchers, cleaning, math, db_writer
                                  core_engine.shared.theta_client
                                  core_engine.shared.config

verify.py    ──depends on──>  asyncpg, pandas  (reads DB, checks results)
```