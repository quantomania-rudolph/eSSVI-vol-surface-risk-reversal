# Data Ingestion Pipeline — Run Order & Architecture Guide

## Overview

The AMD eSSVI backfill pipeline is a modular, async-first data ingestion system that processes option chain data from ThetaData API, applies cleaning/math transformations, and writes to TimescaleDB with exactly-once semantics.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        run_backfill() ENTRY POINT                             │
│  (dataingestion/orchestrator.py)                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  PHASE 0: INITIALIZATION                                                    │
│  ────────────────────────────                                                │
│  • Heartbeat check (Theta terminal connectivity)                           │
│  • DB schema init (hypertable, staging, quarantine, watermarks)            │
│  • Get NYSE calendar (pandas_market_calendars)                             │
│  • Build business-time schedule cache for full DTE range                   │
│  • Acquire run_id from sequence                                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  PHASE 1: EXPIRATION DISCOVERY & FILTERING                                  │
│  ─────────────────────────────────────────────                              │
│  • Fetch all expirations for underlying                                    │
│  • Filter to DTE windows overlapping [start_date, end_date]                │
│  • Split each expiration into month-bounded chunks                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  PHASE 2: PRE-FETCH SHARED DATA                                             │
│  ─────────────────────────────────────                                      │
│  • Create fresh OHLC & rates caches                                        │
│  • Pre-fetch rates (SOFR) for entire [start_date, end_date] range          │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  PHASE 3: PROCESS EACH CHUNK (PARALLEL WITHIN CHUNK)                       │
│  ───────────────────────────────────────────────────────────────            │
│  For each (expiration, chunk_start, chunk_end):                            │
│                                                                             │
│  ┌─ _process_chunk() ─────────────────────────────────────────────────┐    │
│  │ 1. PARALLEL FETCH (3 concurrent, semaphore-controlled)             │    │
│  │    ├─ Greeks: async_fetch_option_greeks_first_order (OPT_SEM=4)    │    │
│  │    ├─ OI:     async_fetch_option_open_interest (OPT_SEM=4)        │    │
│  │    └─ Stock:  _get_stock_ohlc_cached (STK_SEM=10, cached)         │    │
│  │                                                                      │    │
│  │ 2. TRANSFORM (pure functions in joins.py)                          │    │
│  │    opt_df = join_spot_and_oi(opt_df, stk_df, oi_df)               │    │
│  │                                                                      │    │
│  │ 3. CLEAN (dataingestion/cleaning.py)                               │    │
│  │    clean_df, quar_df = clean_option_chain(opt_df)                 │    │
│  │                                                                      │    │
│  │ 4. MATH (pure functions in joins.py + math.py)                    │    │
│  │    clean_df = attach_rates_and_math(clean_df, rates_df,           │    │
│  │                                      cal, schedule_cache)          │    │
│  │    ├─ compute_business_T(cal, schedule_cache)                     │    │
│  │    ├─ _attach_rates(rates_df) → NaN on missing                    │    │
│  │    ├─ compute_forward()                                           │    │
│  │    └─ compute_vega()                                              │    │
│  │                                                                      │    │
│  │ 5. PERSIST — single DB transaction (exactly-once)                 │    │
│  │    ├─ Re-check watermark inside transaction                       │    │
│  │    ├─ COPY clean_df → staging table                               │    │
│  │    ├─ INSERT SELECT staging → hypertable (ON CONFLICT DO NOTHING) │    │
│  │    ├─ COPY quar_df → quarantine table                             │    │
│  │    └─ UPSERT watermark (ingest_progress)                          │    │
│  │                                                                      │    │
│  │ 6. RETURN ChunkResult (unambiguous status)                        │    │
│  └────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  PHASE 4: AGGREGATION & RETURN                                              │
│  ──────────────────────────────────                                          │
│  • Sum clean_rows, quar_rows, errors (fetch_error ∨ db_error)              │
│  • Log completion metrics                                                   │
│  • Return {total_clean_rows, total_quarantined, errors, duration_seconds}  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Module Dependency Graph

```
orchestrator.py (entry point)
├── config.py (cfg constants)
├── logging.py (ContextVars for structured logging)
├── cache.py (BoundedCache with TTL + LRU)
├── chunking.py (_month_chunks, _dte_window)
├── joins.py (pure DataFrame transforms)
│   ├── _join_spot, _join_oi, _attach_rates
│   ├── join_spot_and_oi, attach_rates_and_math
│   └── math.py (compute_business_T, compute_forward, compute_vega)
├── cleaning.py (clean_option_chain)
├── retry.py (fetch_with_retry, _is_retryable_error)
├── fetchers.py (async ThetaData API calls)
└── db_writer.py (all asyncpg SQL operations)
    ├── get_pool, init_schema
    ├── write_staging_batch, load_from_staging, write_quarantine_batch
    ├── advance_watermark, get_completed_chunks, next_run_id
```

---

## Concurrency Model

| Resource | Semaphore | Limit | Protects |
|----------|-----------|-------|----------|
| Greeks / OI / Contracts | `OPT_SEM` | 4 | Standard tier API rate limit |
| Stock OHLC / Rates / Calendar | `STK_SEM` | 10 | Value tier API rate limit |

- **Semaphores released during backoff** — `fetch_with_retry` releases `_sem` before `asyncio.sleep()`
- **Parallel fetch within chunk** — `asyncio.gather(_fetch_opt(), _fetch_oi(), _fetch_stk())`
- **Sequential chunk processing** — one chunk at a time per expiration (watermark serialization)

---

## Run Order (for an agent executing the pipeline)

### Prerequisites
```bash
# 1. Environment variables
export PGHOST=127.0.0.1
export PGPORT=5432
export PGUSER=postgres
export PGPASSWORD=postgres
export PGDATABASE=postgres

# 2. ThetaData terminal running and accessible
# 3. Python deps installed
pip install -r requirements.txt  # asyncpg, pandas, pandas-market-calendars, scipy, etc.
```

### Execute Backfill

```python
# Minimal invocation (uses defaults: AMD, SOFR, 2018-01-01 to today)
from dataingestion.orchestrator import run_backfill
import asyncio

result = asyncio.run(run_backfill())

# Custom parameters
result = asyncio.run(run_backfill(
    start_date=dt.date(2024, 1, 1),
    end_date=dt.date(2024, 12, 31),
    underlying="NVDA",
    rate_symbol="TREASURY_M1",
))
```

### Verify Before/After

```bash
# 1. Run verification suite (22 checks)
python verify_phase3.py

# 2. Full test suite
python -m pytest dataingestion/ -v --tb=short

# 3. Type check
mypy dataingestion/

# 4. Lint
flake8 dataingestion/
```

### Expected Output Structure

```python
{
    "total_clean_rows": 123456,      # Rows written to amd_surface_min
    "total_quarantined": 789,        # Rows written to amd_surface_quarantine
    "errors": 0,                     # Chunks with fetch_error or db_error
    "duration_seconds": 345.67       # Wall-clock time
}
```

---

## Key Invariants (must hold for correctness)

1. **Exactly-once writes** — Watermark checked & advanced in same DB transaction
2. **No silent rate corruption** — Missing rates = `NaN`, never `0.0`
3. **Context propagation** — `run_id`, `expiration`, `chunk` via `ContextVar` in all logs
4. **Semaphore hygiene** — Always released on retry backoff, exceptions, success
5. **Cache isolation** — Fresh `BoundedCache` per backfill run
6. **Default backward compat** — `underlying="AMD"`, `rate_symbol="SOFR"`

---

## Troubleshooting Checklist

| Symptom | Check |
|---------|-------|
| `test_orchestrator` failures | Run `verify_phase3.py` first |
| DB connection errors | Verify `PG*` env vars, TimescaleDB running |
| Empty results | Check ThetaData terminal, symbol validity |
| Rate NaN propagation | `_attach_rates` logs warnings — check structured logs |
| Chunk skipped unexpectedly | Watermark table `ingest_progress` — look for `status=completed` |
| Semaphore deadlock | Ensure `fetch_with_retry` releases sem before `sleep()` |