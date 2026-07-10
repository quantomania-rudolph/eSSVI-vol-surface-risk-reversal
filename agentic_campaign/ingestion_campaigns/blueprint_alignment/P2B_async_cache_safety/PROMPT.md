# W2B — Async Cache Safety & Deprecation Fix

## Persona
You are a **Concurrency and Thread-Safety Architect** who knows that shared mutable state in async code is a silent data corruption time bomb. You ensure that every data structure accessed by multiple async tasks has proper synchronization.

## Blueprint Vision
Read `dataingestion.md` Section 3 and 15. The blueprint's concurrency model uses `asyncio.Semaphore` to control access to the ThetaData API, but the `BoundedCache` itself must also be safe for concurrent `get()`/`set()` calls from multiple async tasks fetching different expirations.

## Core Objective
Make `BoundedCache` safe for concurrent async access and fix the deprecated `datetime.utcnow()`:

1. **Add `asyncio.Lock`** to `BoundedCache` for all mutations (`_cache`, `_access_order`)
2. **Replace `datetime.utcnow()`** with `datetime.now(timezone.utc)` for Python 3.12+ compatibility

## Errors to Fix

### Medium #37: `BoundedCache` Not Thread-Safe (Asyncio Tasks May Race)
**File:** `cache.py:21-89`  
**Expected:** All cache mutations (get, set, clear, _update_access) protected by `asyncio.Lock`.  
**Actual:** `_access_order` list manipulated without locks. Multiple async tasks calling `get()`/`set()` concurrently can corrupt the LRU ordering.  
**Fix:** Add `self._lock = asyncio.Lock()` in `__init__`. Use `async with self._lock` around all mutation operations. Note: `get()` needs to be async now (was sync before).

### Medium #38: `datetime.utcnow()` Deprecated in Python 3.12+
**File:** `cache.py:17, 39, 68`  
**Expected:** `datetime.now(timezone.utc)`.  
**Actual:** `datetime.utcnow()` (deprecated).  
**Fix:** Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)`.

## Invariants (MUST HOLD)
1. **Backward compatibility** — `BoundedCache.get()` was sync; making it async requires updating all call sites. Use `asyncio_run` wrapper or make it `async def` and update callers.
2. **No performance regression** — the lock should be a fast-path uncontested lock (most cache hits are single-task)
3. **UTCNOW fix only** — no other datetime changes

## Success Criteria
- `BoundedCache` has `self._lock = asyncio.Lock()`
- All mutation methods acquire the lock: `get()`, `set()`, `clear()`, `_update_access()`
- No `datetime.utcnow()` calls anywhere in `cache.py`
- All callers of `BoundedCache.get()` updated for async if needed
- All existing tests pass (cache tests in `test_cache.py` updated for async if needed)

## Short Specialized Verification
```python
# 1) Check no utcnow
src = open("dataingestion/cache.py").read()
assert "utcnow" not in src, "utcnow still present"
assert "datetime.now" in src, "should use now()"

# 2) Check asyncio.Lock
assert "asyncio.Lock" in src
assert "async with self._lock" in src

# 3) Check cache get is async
src = open("dataingestion/orchestrator.py").read()
# Look for await on get calls
assert "await" in src  # check orchestrator uses async cache get

# 4) Full test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/test_cache.py", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/cache.py` (add asyncio.Lock, fix utcnow → now(timezone.utc))
- `dataingestion/orchestrator.py` (update cache get/set calls for async if needed)
- `dataingestion/test_cache.py` (update for async get)