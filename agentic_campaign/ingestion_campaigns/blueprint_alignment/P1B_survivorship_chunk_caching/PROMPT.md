# W1B — Survivorship-Safe Universe & Chunk Caching

## Persona
You are a **Survivorship Bias Prevention Engineer** who knows that look-ahead bias is the #1 silent killer of backtest validity. You ensure every contract in the dataset actually existed and was traded on that date — no "future contracts" leaking into historical data. You also optimize data fetching to minimize API calls.

## Blueprint Vision
Read `dataingestion.md` Sections 3, 8, and 12 completely. The blueprint defines:
- **Acquisition order:** per-expiration, chunked ≤1 month; survivorship-safe via `list/contracts` per date
- **Caching:** stock OHLC and rates cached per chunk (not across chunks/expirations)
- **Leakage rule 7:** "Build each date's contract set from `list/contracts`/`list/expirations` **as of that date**, not today's chain."
- **Schedule buffer:** the business time schedule cache needs enough coverage for DTE window + buffer

## Core Objective
Implement survivorship-safe contract filtering and correct caching:

1. **Survivorship-safe universe** — for each chunk, fetch `list/contracts` for that date range and filter greeks to only those `(strike, right)` pairs that existed
2. **Rates cached per chunk** — not across the entire backfill range
3. **Schedule cache buffer** — increase from 5 to 14 days for holiday safety
4. **Expiration date filtering** — pass date range to `list/expirations` API if supported

## Errors to Fix

### High #13: No Survivorship-Safe `list/contracts` Per Date
**File:** `orchestrator.py:412-413`, `fetchers.py:229-249`  
**Blueprint:** Section 3, Leakage Rule 7  
**Expected:** For each chunk date, call `list/contracts` to get the as-of contract universe. Filter greeks to only those contracts.  
**Actual:** Fetches greeks with `strike="*"` — returns contracts that didn't exist on that date (look-ahead bias).  
**Fix:** Add contract set filtering:
  - For each chunk, call `async_fetch_option_list_contracts(symbol, date)` once per date
  - Build a `{(strike, right)}` set per date
  - After fetching greeks, filter to only rows in that date's contract set

### High #14: Rates Cached Globally (Full Backfill Range), Not Per Chunk
**File:** `orchestrator.py:192-231, 463-464`  
**Blueprint:** Section 3  
**Expected:** Rates cache key includes chunk boundaries.  
**Actual:** Rates cached with key `(rate_symbol, start_date, end_date)` where dates = full backfill range. Fetched once for entire run.  
**Fix:** Include chunk_start/chunk_end in rates cache key, or fetch rates per chunk.

### High #46: Schedule Cache +5 Day Buffer Insufficient
**File:** `orchestrator.py:416-417`  
**Expected:** Buffer ≥ 14 calendar days for long holiday periods (Christmas+New Year).  
**Actual:** `earliest_needed = start_date - (DTE_MAX + 5)` — 5 days buffer.  
**Fix:** Change to `DTE_MAX + 14`.

### High #55: `list/expirations` Fetches ALL Expirations Ever, No Date Filter
**File:** `orchestrator.py:413`  
**Expected:** If Theta supports filtering, pass `start_date`/`end_date` to reduce data transferred.  
**Actual:** Fetches ALL expirations (thousands), then filters locally.  
**Fix:** Check if `async_fetch_option_list_expirations` accepts date params. If so, pass the backfill date range.

## Invariants (MUST HOLD)
1. **Performance** — fetching `list/contracts` per date must not dominate runtime (contracts endpoint is fast)
2. **No data loss** — filtering must only remove contracts that DID NOT exist; never remove valid ones
3. **Cache key uniqueness** — no cache key collisions between different chunks
4. **Backward compatible** — all tests pass

## Success Criteria
- For each chunk, a set of `(strike, right)` pairs is built from `list/contracts`
- Greeks are filtered to only contracts in that date's universe
- Rates cache key includes chunk boundaries
- Schedule buffer = `DTE_MAX + 14`
- `list/expirations` call passes date range if API supports it
- All existing tests pass

## Short Specialized Verification
```python
# 1) Check contract filtering
# Verify orchestrator calls async_fetch_option_list_contracts and filters

# 2) Rates cache key includes chunk
src = open("dataingestion/orchestrator.py").read()
assert "chunk" in open("dataingestion/orchestrator.py").read()  # weak check, intent check manually

# 3) Schedule buffer
assert cfg.DTE_MAX + 14 == cfg.DTE_MAX + 14  # internal consistency

# 4) Full test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/orchestrator.py` (contract filtering logic, cache keys, schedule buffer)
- `dataingestion/fetchers.py` (contract fetch + filtering helper)
- `dataingestion/config.py` (schedule buffer constant)
- `dataingestion/cache.py` (line-of-code changes only)