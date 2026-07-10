# A5 — Orchestrator

**Role:** Senior async Python engineer specializing in data pipeline orchestration and concurrency.

## Your Mission

Build `dataingestion/orchestrator.py` — the **run-all** entry point that ties together fetchers, cleaning, math, and DB writer into a single async pipeline for the AMD eSSVI backfill (2018-01-01 → present).

## What You Build

One file: `dataingestion/orchestrator.py`

One public entry point:

```python
async def run_backfill(
    start_date: dt.date = dt.date(2018, 1, 1),
    end_date: dt.date | None = None,
) -> dict:
    """Run the full AMD eSSVI backfill pipeline.
    
    Returns:
        dict with stats: total_clean_rows, total_quarantined, errors, duration_seconds
    """
```

### Orchestration Flow (Section 3 + 14)

```
1. heartbeat() — verify terminal is running
2. init_schema() — create TimescaleDB tables
3. Fetch rates globally (cache for entire backfill)
4. Fetch AMD expirations from list/expirations
5. Load watermark to find completed chunks (resume support)
6. For each expiration E in expirations:
   a. Build all ≤1-month date chunks in window [E-90cd, E-7cd]
   b. For each chunk not in watermark:
      - Acquire semaphore
      - Fetch greeks + OHLC + OI in parallel
      - Clean (filter + quality)
      - Compute T, forward, vega
      - Two-phase load into DB
      - Advance watermark
```

### Two Semaphores (CRITICAL)

```python
OPT_SEM = asyncio.Semaphore(4)   # Standard tier: greeks, OI, contracts
STK_SEM = asyncio.Semaphore(2)   # Value tier: stock OHLC, rates, calendar
```

**Rules:**
- Options endpoints (greeks, OI, contracts) → acquire `OPT_SEM` before calling `client.get()`
- Stock endpoints (OHLC) → acquire `STK_SEM` before calling `client.get()`
- Rates/calendar → acquire `STK_SEM` (they're non-options endpoints)
- Heartbeat → outside both semaphores, called once at start

### Caching Strategy

```python
# Global caches (per backfill run):
_OHLC_CACHE: dict[tuple[date, date], pd.DataFrame] = {}
_RATES_DF: pd.DataFrame | None = None

# Per-chunk parallelism within one expiration:
async def process_chunk(exp, chunk_start, chunk_end, client, conn, ...):
    # These three CAN run in parallel (different endpoints, using different semaphores):
    opt_task  = fetch_option_greeks_first_order(client, "AMD", exp, chunk_start, chunk_end)
    oi_task   = fetch_option_open_interest(client, "AMD", exp, chunk_start, chunk_end)
    stk_task  = fetch_stock_ohlc_cached(client, "AMD", chunk_start, chunk_end)
    
    opt_df, oi_df, stk_df = await asyncio.gather(opt_task, oi_task, stk_task)
```

But note: `asyncio.gather` with the semaphores means you need to acquire them properly. The pattern:

```python
async def process_chunk(exp, chunk_start, chunk_end, client, ...):
    async def _fetch_opt():
        async with OPT_SEM:
            return await fetch_option_greeks_first_order(client, "AMD", exp, chunk_start, chunk_end)
    
    async def _fetch_oi():
        async with OPT_SEM:
            return await fetch_option_open_interest(client, "AMD", exp, chunk_start, chunk_end)
    
    async def _fetch_stk():
        async with STK_SEM:
            return await fetch_stock_ohlc(client, "AMD", chunk_start, chunk_end)
    
    opt_df, oi_df, stk_df = await asyncio.gather(
        _fetch_opt(), _fetch_oi(), _fetch_stk()
    )
```

### Joining Data

After fetching:
1. Join `stk_df.close` onto `opt_df` by floored minute timestamp → becomes `spot_close`
2. Join `oi_df.open_interest` onto `opt_df` by date (daily join)
3. Forward-fill `spot_close` for any minute gaps (same-day only, `ffill` within day)

### Resume / Idempotency

Before processing each `(expiration, chunk_end)`:
- Call `get_completed_chunks(conn, "AMD")`
- If `(exp.isoformat(), chunk_end)` is in the completed set → **skip**

This means the backfill can be interrupted and restarted without re-fetching data.

### Pipeline Order Per Chunk

```python
# 1. Fetch
opt_df = await fetch_option_greeks_first_order(...)
stk_df = await fetch_stock_ohlc(...)
oi_df  = await fetch_option_open_interest(...)

# 2. Join
opt_df = _join_spot(opt_df, stk_df)
opt_df = _join_oi(opt_df, oi_df)

# 3. Clean
clean_df, quar_df = clean_option_chain(opt_df)

# 4. Math (skip if clean_df is empty)
if not clean_df.empty:
    clean_df = compute_business_T(clean_df, cal)
    clean_df = _attach_rates(clean_df, rates_df)
    clean_df = compute_forward(clean_df)
    clean_df = compute_vega(clean_df)

# 5. Load
await write_staging_batch(conn, clean_df)
await load_from_staging(conn, run_id)
if not quar_df.empty:
    await write_quarantine_batch(conn, quar_df, run_id)
await advance_watermark(...)
```

### Error Handling

- If any fetch fails (empty DataFrame) → log, skip chunk, do NOT abort the entire backfill
- If cleaning produces all quarantine → still advance watermark (no data to write, but chunk is done)
- If DB write fails → log error, DO NOT advance watermark (so it retries next run)
- If the terminal goes down → raise `ThetaTerminalDown`, let the outer runner decide

### Logging

Use Python's `logging` module with informative messages:

```python
logger.info("Exp %s chunk [%s, %s]: %s clean, %s quar", exp, start, end, n_clean, n_quar)
logger.info("Backfill %s%% complete: %s/%s chunks", pct, done, total)
logger.info("Backfill done: %s clean rows, %s quarantined, %s errors, %.1fs",
            total_clean, total_quar, errors, elapsed)
```

### Invariants — NEVER Violate

1. **Never fetch the same chunk twice** — check watermark first.
2. **Never exceed tier limits** — always acquire the correct semaphore.
3. **Never skip the heartbeat** — call it before any HTTP.
4. **Never write directly to the hypertable** — always go through two-phase load.
5. **Never drop errors silently** — log and track them.
6. **Never modify the DataFrame from fetchers before cleaning** — the pipeline order is fixed.
7. **Never hardcode dates** beyond the defaults — accept `start_date` and `end_date`.
8. **Never share `OPT_SEM` with stock endpoints** — two semaphores, two tiers.
9. **Always floor timestamps to the minute** before joining.
10. **Always advance watermark AFTER successful write, not before.**

### Key Reference Files

- `dataingestion.md` Sections 3 (acquisition order) and 14 (end-to-end pseudocode) — **the master blueprint**
- `dataingestion/COLUMNS.md` Sections V (concurrency) and VI (dependency graph) — **semaphore rules**
- `dataingestion/fetchers.py` — import all fetch functions
- `dataingestion/cleaning.py` — import `clean_option_chain`
- `dataingestion/math.py` — import `compute_business_T`, `compute_forward`, `compute_vega`
- `dataingestion/db_writer.py` — import all DB functions
- `core_engine/shared/theta_client.py` — import `AsyncThetaClient`, `heartbeat`

### Verification Script

```bash
cd c:\Users\Rudol\Desktop\ThetaData_greeks
python -m pytest dataingestion/test_orchestrator.py -v
```

The verification script (`dataingestion/test_orchestrator.py`) will:
1. Mock all downstream modules (fetchers, cleaning, math, db_writer).
2. Call `run_backfill()` with a tiny date range (1 day, 1 expiration).
3. Verify:
   - heartbeat is called first.
   - The correct number of chunks is created (≤1 month each).
   - Semaphores are used correctly (OPT_SEM=4, STK_SEM=2).
   - Watermark is checked before each chunk.
   - Pipeline order is correct (fetch → join → clean → math → load → watermark).
   - Empty DataFrames from fetchers skip the chunk cleanly.
   - DB errors don't crash the pipeline.
   - Completed chunks are skipped on resume.
4. Verify no direct HTTP calls (uses mock fetchers).
5. Verify `run_backfill()` returns a stats dict with correct keys.

**Do not write the verification script.** It lives at `dataingestion/test_orchestrator.py`.

### Common Mistakes to Avoid

- Using one semaphore for everything — options and stocks have different tier limits.
- Forgetting the `async with OPT_SEM:` pattern inside gather tasks.
- Not checking watermark before fetching (wastes subscription quota).
- Using `end_date = dt.date.today()` and having partial day issues.
- Not handling the case where `end_date` is None → use today.
- Letting a single fetch failure crash the entire backfill.
- Not passing `annual_dividend=0` and `rate_type="sofr"` to the greeks endpoint.
- Using `strike_range` instead of `strike="*"`.