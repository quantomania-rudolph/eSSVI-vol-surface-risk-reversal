# EH205: Watermark Race Condition Fix

## Persona

You are a **distributed systems engineer** who knows that "check-then-act" race conditions in idempotency logic are silent data corruptors. The pattern `check_watermark() → process() → advance_watermark()` has a window where a crash after process but before advance causes re-processing, while a crash after advance but before commit causes skipping.

## Mission

**Make the watermark check-and-advance atomic per chunk in `orchestrator.py`, using database transactions or optimistic locking.**

## Current State (RACE CONDITION)

```python
# _process_chunk (lines 230-280)
chunk_key = (exp.isoformat(), chunk_end)
if chunk_key in completed_chunks:  # CHECK
    return 0, 0, 0

# ... process chunk ...

try:
    # ... write to DB ...
    await advance_watermark(conn, "AMD", exp, chunk_end, "completed", len(clean_df), run_id)  # ADVANCE
except Exception:
    errors = 1
    # Don't advance watermark on error
```

**Race Windows:**
1. **Crash after write, before advance** → chunk reprocessed next run (duplicate data risk if UNIQUE constraint fails)
2. **Crash after advance, before commit** → watermark says done but data not committed (gap)
3. **Concurrent runners** → both see chunk not completed, both process

## Required Changes

### Option A: Database Transaction (Recommended)

Wrap the entire chunk processing + watermark advance in a single DB transaction:

```python
async def _process_chunk(
    client, exp, chunk_start, chunk_end,
    pool, run_id, cal, rates_df, completed_chunks,
    schedule_cache
) -> tuple[int, int, int]:
    """Process chunk with atomic watermark using transaction."""
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Re-check inside transaction (fresh read)
            completed = await get_completed_chunks(conn, "AMD")
            chunk_key = (exp.isoformat(), chunk_end)
            if chunk_key in completed:
                return 0, 0, 0
            
            # ... fetch, join, clean, math ...
            
            # Two-phase load
            if not clean_df.empty:
                await write_staging_batch(conn, clean_df)
                await load_from_staging(conn, run_id)
            if not quar_df.empty:
                await write_quarantine_batch(conn, quar_df, run_id)
            
            # Advance watermark INSIDE transaction
            await advance_watermark(conn, "AMD", exp, chunk_end, "completed", len(clean_df), run_id)
            
            return len(clean_df), len(quar_df), 0
```

### Option B: Optimistic Locking (If Transaction Not Feasible)

Add a `version` column to `ingest_progress` and use `WHERE version = expected_version` in UPDATE.

### Required Changes to `db_writer.py` (Coordination with EH-04)

```python
# In db_writer.py - advance_watermark must support transaction
async def advance_watermark(
    conn: asyncpg.Connection,
    underlying: str,
    expiration: date,
    chunk_end_date: date,
    status: str,
    rows: int,
    run_id: int,
) -> None:
    # Use ON CONFLICT DO UPDATE - works inside transaction
    await conn.execute(...)
```

### Update `run_backfill` to Pass Pool (Not Connection)

```python
# In run_backfill:
pool = await get_pool()

# Pass pool to _process_chunk, not connection
await _process_chunk(
    client, exp, chunk_start, chunk_end,
    pool, run_id, cal, rates_df, completed_chunks,  # completed_chunks now stale but rechecked
    schedule_cache
)

# Remove per-chunk acquire/release
# Remove completed_chunks refresh per chunk (rechecked in transaction)
```

## Invariants (Must Preserve)

- ✅ **Exactly-once semantics**: Each chunk processed 0 or 1 times
- ✅ **No duplicates**: UNIQUE constraint + transaction prevent double-write
- ✅ **No gaps**: Watermark advanced iff data committed
- ✅ **Resume works**: Interrupted run resumes from last completed chunk
- ✅ **Concurrent runners safe**: Multiple processes can run safely
- ✅ **Error handling**: Failed chunks retry next run (watermark not advanced)
- ✅ All existing tests pass

## Acceptance Criteria

### Functional
1. Watermark check + advance atomic per chunk
2. No duplicate processing under concurrent runs
3. Crash after write but before advance → retry (not skip, not duplicate)
4. All orchestrator tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
python -m pytest dataingestion/test_db_writer.py -v
```

### New Test in `test_orchestrator.py`
```python
class TestWatermarkAtomicity:
    def test_watermark_check_and_advance_atomic(self, patched_orchestrator):
        """Verify watermark advance happens in same transaction as write."""
        # Mock pool.acquire to return connection with transaction tracking
        # Verify begin/commit called around process + advance
        
    def test_crash_before_advance_retries(self, patched_orchestrator):
        """Simulate crash after load_from_staging but before advance_watermark."""
        # Mock advance_watermark to raise after load succeeds
        # Verify chunk retried on next run
        
    def test_concurrent_runners_no_duplicate(self, patched_orchestrator):
        """Two parallel _process_chunk calls for same chunk → only one succeeds."""
        # Requires DB-level test with real pool
```

## Dependencies

- **EH-04 MUST BE COMPLETE** — `db_writer.advance_watermark` must work in transactions
- **EH201, EH202 SHOULD BE COMPLETE** — async fetchers + schedule cache

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` — atomic watermark via transaction
2. **Modified** `dataingestion/db_writer.py` (if needed) — transaction-safe advance
3. **Verification** all tests pass