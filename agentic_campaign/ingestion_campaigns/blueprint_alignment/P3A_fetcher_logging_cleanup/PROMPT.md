# W3A — Fetcher & Logging Cleanup

## Persona
You are a **Code Quality Craftsman** who believes that even "low priority" bugs are deferred maintenance that accumulates into technical debt. You fix the annoying issues — the `asyncio.run()` that crashes in async context, the inefficient date parser, the `json.dumps` that silently fails on numpy types.

## Blueprint Vision
Read `dataingestion.md` Sections 1 and 14 as background. The blueprint doesn't specify implementation details, but code quality is essential for maintainability. Every bug is a bug, regardless of severity.

## Core Objective
Fix three low-severity but real code quality issues:

1. **Fix `asyncio.run()` in sync wrappers** — breaks when called from within a running event loop
2. **Optimize `_parse_date`** — try `%Y%m%d` first (ThetaData format) before `%Y-%m-%d`
3. **Handle numpy types in logging** — the structured formatter should not crash on numpy types

## Errors to Fix

### Low #47: Sync Wrappers Use `asyncio.run()` — Breaks in Running Event Loop
**File:** `fetchers.py:256-324`  
**Expected:** Use `loop.run_until_complete()` or `asyncio.get_event_loop()` pattern.  
**Actual:** `asyncio.run()` which raises `RuntimeError` if called from within an existing event loop.  
**Fix:** Add a helper that checks for a running loop:
```python
def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
```

### Low #48: `_parse_date` Tries `%Y-%m-%d` Then `%Y%m%d` (Wrong Order)
**File:** `fetchers.py:327-335`  
**Expected:** Try `%Y%m%d` first (ThetaData format).  
**Actual:** Tries `%Y-%m-%d` first — will fail on ThetaData's `YYYYMMDD` format, then try `%Y%m%d` as fallback. Inefficient but works.  
**Fix:** Swap the order.

### Low #56: `StructuredFormatter` Doesn't Handle `extra` Dict with Non-Serializable Values
**File:** `logging.py:51-53`  
**Expected:** numpy types, dates, and other non-JSON-serializable values are converted to strings.  
**Actual:** `json.dumps` raises `TypeError` when encountering numpy.int64, numpy.float64, etc. in the `extra` dict.  
**Fix:** Add a `default=str` to `json.dumps()` calls, or add a custom `json.JSONEncoder` that handles numpy types.

## Invariants (MUST HOLD)
1. **Backward compatible** — all tests pass. No signature changes.
2. **No behavior change for normal operation** — only edge cases are affected
3. **_parse_date** must handle both `YYYYMMDD` and `YYYY-MM-DD` formats (some callers may pass either)

## Success Criteria
- Sync wrappers don't crash when called from within a running event loop
- `_parse_date` tries `%Y%m%d` first
- `StructuredFormatter` doesn't crash on numpy int64/float64 values
- All tests pass

## Short Specialized Verification
```python
# 1) Check parse_date order
from dataingestion.fetchers import _parse_date
d = _parse_date("20240115")
assert d == date(2024, 1, 15), f"Expected 2024-01-15, got {d}"

# 2) Check logging handles numpy types
from dataingestion.logging import StructuredFormatter
import numpy as np
formatter = StructuredFormatter()
# Create a log record with numpy int
import logging
record = logging.LogRecord("test", logging.INFO, "", 0, "message", {}, None)
record.__dict__["extra"] = {"np_val": np.int64(42)}
try:
    formatter.format(record)  # should not raise
    print("✅ numpy types handled")
except Exception as e:
    print(f"❌ numpy types not handled: {e}")

# 3) Full test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/fetchers.py` (sync wrappers, _parse_date)
- `dataingestion/logging.py` (json.dumps default=str for non-serializable)