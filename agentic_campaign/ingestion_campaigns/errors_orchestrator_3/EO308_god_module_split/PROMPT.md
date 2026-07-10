# EO308: God Module Split

## Persona

You are a **software architect** who knows that a 741-line module with 7 responsibilities violates the Single Responsibility Principle and the 1k-line rule. The orchestrator mixes logging infrastructure, cache implementation, retry logic, data joins, chunking logic, and pipeline orchestration — making it impossible to test, maintain, or reuse.

## Core Objective

**Split `orchestrator.py` into 6 focused modules, each with a single responsibility.**

## Target Module Structure

```
dataingestion/
├── logging.py          # StructuredFormatter, setup_structured_logging, context vars
├── cache.py            # BoundedCache, CacheEntry
├── retry.py            # fetch_with_retry, _is_retryable_error
├── joins.py            # _join_spot, _join_oi, _attach_rates
├── chunking.py         # _month_chunks, _dte_window
└── orchestrator.py     # Pure orchestration: _process_chunk, run_backfill (~200 lines)
```

## Migration Plan

### 1. Create `dataingestion/logging.py`
Move lines 30-76:
- `run_id_var`, `exp_var`, `chunk_var` context vars
- `StructuredFormatter` class
- `setup_structured_logging()` function

### 2. Create `dataingestion/cache.py`
Move lines 139-215:
- `CacheEntry` dataclass
- `BoundedCache` class

### 3. Create `dataingestion/retry.py`
Move lines 239-307:
- `FETCH_MAX_RETRIES`, `FETCH_BASE_DELAY`, `FETCH_MAX_DELAY`, `FETCH_RETRYABLE_STATUS`, `FETCH_NON_RETRYABLE_STATUS` (import from config)
- `_is_retryable_error()`
- `fetch_with_retry()`

### 4. Create `dataingestion/joins.py`
Move lines 372-432:
- `_join_spot()`
- `_join_oi()` (with EO302 fix)
- `_attach_rates()`

### 5. Create `dataingestion/chunking.py`
Move lines 435-450:
- `_month_chunks()`
- `_dte_window()`

### 6. Update `dataingestion/orchestrator.py`
- Import from new modules
- Keep only: `_acquire_conn`, `_release_conn`, `_heartbeat_once`, `_get_calendar`, `_get_rates`, `_get_stock_ohlc_cached`, `_process_chunk`, `run_backfill`
- Should be ~200 lines

## Invariants

- ✅ All 77 tests pass after split
- ✅ No circular imports
- ✅ Each module < 300 lines
- ✅ Public API unchanged (imports still work via `from dataingestion.orchestrator import ...`)
- ✅ `orchestrator.py` only orchestrates — no business logic

## Success Criteria

### Functional
1. All 77 tests pass
2. `wc -l dataingestion/orchestrator.py` < 300
3. 5 new modules exist with clear responsibilities

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py dataingestion/test_math.py dataingestion/test_config.py -v
```

## Verification Agent

```bash
# Verify line counts
wc -l dataingestion/*.py
# orchestrator.py should be ~200 lines
# Each module should be < 300 lines
```