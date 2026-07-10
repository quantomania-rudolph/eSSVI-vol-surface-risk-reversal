# EH201: Async Fetcher Integration

## Persona

You are a **senior async Python engineer** specializing in high-throughput financial data pipelines. You understand that `asyncio.run()` inside an async function is a cardinal sin — it creates a new event loop, bypasses all concurrency control, and destroys semaphore semantics.

## Mission

**Refactor `dataingestion/orchestrator.py` to use the async fetcher variants from EH-01 (`async_fetch_*` functions) everywhere, eliminating ALL `asyncio.run()` calls from the async pipeline.**

## Current State (BROKEN)

```python
# ORCHESTRATOR (lines 237-253)
async def _fetch_opt():
    async with OPT_SEM:
        return fetch_option_greeks_first_order(...)  # SYNC WRAPPER!

async def _fetch_oi():
    async with OPT_SEM:
        return fetch_option_open_interest(...)  # SYNC WRAPPER!

# FETCHERS.PY (sync wrapper pattern)
def fetch_option_greeks_first_order(...):
    async def _run(): ...
    return asyncio.run(_run())  # CREATES NEW EVENT LOOP!
```

**Result:** Semaphore acquired → released immediately → real HTTP runs in isolated loop → **zero concurrency control**.

## Required Changes

### 1. Import Async Variants (Require EH-01 Complete)

```python
from dataingestion.fetchers import (
    async_fetch_option_greeks_first_order,
    async_fetch_stock_ohlc,
    async_fetch_option_open_interest,
    async_fetch_interest_rate_eod,
    async_fetch_option_list_expirations,
    # Keep sync for backward compat if needed
)
```

### 2. Refactor `_process_chunk` Fetch Logic

```python
# BEFORE (broken)
async def _fetch_opt():
    async with OPT_SEM:
        return fetch_option_greeks_first_order(client, "AMD", exp, chunk_start, chunk_end)

# AFTER (correct)
async def _fetch_opt():
    async with OPT_SEM:
        return await async_fetch_option_greeks_first_order(
            client, "AMD", exp, chunk_start, chunk_end
        )

async def _fetch_oi():
    async with OPT_SEM:
        return await async_fetch_option_open_interest(
            client, "AMD", exp, chunk_start, chunk_end
        )

async def _fetch_stk():
    async with STK_SEM:
        return await async_fetch_stock_ohlc(client, "AMD", chunk_start, chunk_end)
```

### 3. Refactor `_get_rates` (Line 94-95)

```python
# BEFORE
async with STK_SEM:
    _RATES_DF = fetch_interest_rate_eod(client, "SOFR", start_date, end_date)

# AFTER
async with STK_SEM:
    _RATES_DF = await async_fetch_interest_rate_eod(client, "SOFR", start_date, end_date)
```

### 4. Refactor `_get_stock_ohlc_cached` (Line 117-118)

```python
# BEFORE
async with STK_SEM:
    df = fetch_stock_ohlc(client, symbol, chunk_start, chunk_end)

# AFTER
async with STK_SEM:
    df = await async_fetch_stock_ohlc(client, symbol, chunk_start, chunk_end)
```

### 5. Refactor Expirations Fetch (Line 327)

```python
# BEFORE
expirations = fetch_option_list_expirations(client, "AMD")

# AFTER
expirations = await async_fetch_option_list_expirations(client, "AMD")
```

### 6. Verify NO `asyncio.run()` Remains in Orchestrator

Search entire file — there must be **zero** occurrences.

## Invariants (Must Preserve)

- ✅ Semaphores actually limit concurrent Theta requests
- ✅ All fetches run in the SAME event loop
- ✅ `asyncio.gather()` truly runs fetches in parallel
- ✅ OPT_SEM=4 limits option endpoints (greeks, OI, expirations, contracts)
- ✅ STK_SEM=2 limits stock endpoints (OHLC, rates, calendar)
- ✅ All existing pipeline logic unchanged (joins, clean, math, load)
- ✅ All existing tests pass

## Acceptance Criteria

### Functional
1. Zero `asyncio.run()` calls in `orchestrator.py`
2. All fetcher calls use `await async_fetch_*`
3. Semaphores correctly limit concurrency (verified by timing test)
4. All existing orchestrator tests pass

### Testing
```bash
# Run existing tests (should still pass)
python -m pytest dataingestion/test_orchestrator.py -v

# New test: verify async behavior
python -m pytest dataingestion/test_orchestrator.py::TestConcurrency -v
```

### New Test to Add in `test_orchestrator.py`

```python
class TestConcurrency:
    def test_semaphores_limit_concurrent_requests(self, patched_orchestrator):
        """Verify OPT_SEM and STK_SEM actually limit concurrent fetches."""
        # Track concurrent calls to async_fetch_* functions
        # Assert max concurrent ≤ semaphore limit
        
    def test_no_asyncio_run_in_pipeline(self):
        """Static analysis: no asyncio.run() in orchestrator.py source."""
        source = Path("dataingestion/orchestrator.py").read_text()
        assert "asyncio.run" not in source
```

## Dependencies

- **EH-01 MUST BE COMPLETE FIRST** — async fetcher variants must exist in `fetchers.py`
- If EH-01 not done, this agent BLOCKS — do not proceed

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` — all fetcher calls use async variants
2. **Verification** all orchestrator tests pass + new concurrency tests pass