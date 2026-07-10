# EO307: Context Variable Cleanup on Exception Fix

## Persona

You are an **observability engineer** who knows that `contextvars.ContextVar` values leak across async tasks if not properly cleaned up. If `_process_chunk` throws an exception, `exp_var` and `chunk_var` remain set, polluting all subsequent log lines with stale context.

## Core Objective

**Wrap all context variable sets in `try/finally` blocks to guarantee cleanup on both success and exception paths.**

## Current Buggy Code (Lines 683-684, 716-718)

```python
# In run_backfill, for each expiration:
exp_var.set(exp.isoformat())
for chunk_start, chunk_end in chunks:
    chunk_key = f"{chunk_start}_to_{chunk_end}"
    chunk_var.set(chunk_key)
    # ... process ...
    chunk_var.set(None)  # Only runs on success!
exp_var.set(None)  # Only runs on success!

# In _process_chunk:
chunk_var.set(chunk_key)
# ... process ...
chunk_var.set(None)  # Only runs on success!
```

**Problem**: Any exception in the processing loop leaves context vars polluted.

## Required Fix

```python
# In run_backfill:
for exp, exp_start, exp_end in valid_expirations:
    exp_var.set(exp.isoformat())
    try:
        chunks = _month_chunks(exp_start, exp_end)
        for chunk_start, chunk_end in chunks:
            chunk_key = f"{chunk_start}_to_{chunk_end}"
            chunk_var.set(chunk_key)
            try:
                conn = await _acquire_conn(pool)
                clean_rows, quar_rows, errors = await _process_chunk(...)
                await _release_conn(pool, conn)
                # ... logging ...
            finally:
                chunk_var.set(None)
    finally:
        exp_var.set(None)

# In _process_chunk:
chunk_var.set(chunk_key)
try:
    # ... all processing ...
finally:
    chunk_var.set(None)
```

## Invariants

- ✅ Context vars ALWAYS cleared, even on exception
- ✅ No log pollution across chunks/expirations
- ✅ `run_id_var` also cleaned in `run_backfill` finally block

## Success Criteria

### Functional
1. All `set()` calls have matching `finally: set(None)` 
2. Exception in `_process_chunk` doesn't leak context
3. All tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v -k "context" 
# Add test that raises exception and verifies context cleared
```

## Verification Agent

Add test:
```python
def test_context_vars_cleared_on_exception(self, patched_orchestrator):
    """exp_var and chunk_var cleared even when _process_chunk raises."""
    # Mock _process_chunk to raise, verify context vars are None after
```