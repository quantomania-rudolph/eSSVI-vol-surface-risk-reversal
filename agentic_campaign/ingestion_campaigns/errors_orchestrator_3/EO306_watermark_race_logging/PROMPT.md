# EO306: Watermark Race Retry Logging Fix

## Persona

You are a **distributed systems engineer** who knows that silent retries are debugging nightmares. When the watermark unique constraint causes a transaction rollback (because a concurrent transaction already committed the same chunk), the retry should be explicitly logged with context, not just silently retried.

## Core Objective

**Add explicit logging when watermark unique constraint violation triggers a transaction rollback and retry.**

## Current Code (Lines 549-556)

```python
completed = await get_completed_chunks(conn, "AMD")
chunk_key = (exp.isoformat(), chunk_end)
if chunk_key in completed:
    log.info("Skipping completed chunk: exp=%s end=%s", exp, chunk_end)
    return 0, 0, 0
```

The re-check inside the transaction is correct for serializability, but if a concurrent transaction commits between the initial check (outside transaction) and this re-check, both will process the chunk. The second one hits the unique constraint on `advance_watermark` → transaction rolls back → chunk is retried on next backfill run. This is correct behavior but **not logged**.

## Required Fix

Add logging in the transaction error path. The `advance_watermark` call will raise a unique violation which rolls back the transaction. We should catch this specific error and log it.

```python
try:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # ... re-check watermark ...
            # ... process chunk ...
            await advance_watermark(conn, "AMD", exp, chunk_end, "completed", len(clean_df), run_id)
except asyncpg.UniqueViolationError:
    # Another process already completed this chunk
    log.warning(
        "Watermark race detected — chunk already completed by concurrent runner",
        extra={
            "expiration": exp.isoformat(),
            "chunk_end": chunk_end.isoformat(),
            "run_id": run_id,
        }
    )
    return 0, 0, 0  # Treat as already completed
```

Note: Need to import `asyncpg` for the exception type.

## Invariants

- ✅ No duplicate data (unique constraint enforces)
- ✅ Race condition logged with full context (exp, chunk, chunk, chunk, run_id)
- ✅ Chunk treated as completed (not errored) — correct for idempotent retry
- ✅ All existing tests pass

## Success Criteria

### Functional
1. `asyncpg.UniqueViolationError` caught and logged at WARNING level
2. Log includes `expiration`, `chunk_end`, `run_id`
3. Returns `(0, 0, 0)` to indicate chunk already done

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py::TestWatermarkAtomicity -v
```

## Verification Agent

Add test:
```python
def test_watermark_race_logged_and_handled(self, patched_orchestrator):
    """UniqueViolationError logged and chunk treated as completed."""
    # Mock advance_watermark to raise UniqueViolationError
    # Verify warning log emitted with correct extra fields
```