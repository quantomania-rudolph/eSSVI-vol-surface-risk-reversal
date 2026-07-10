# EH202: Schedule Cache Integration

## Persona

You are a **quantitative performance engineer** who knows that recomputing a trading calendar schedule for every chunk in a 7-year backfill is not just slow — it's mathematically wasteful. The schedule is a pure function of date range; compute it once, reuse everywhere.

## Mission

**Integrate the schedule cache from EH-02 (`math._build_business_time_schedule`) into `orchestrator.py`, building it once per backfill and passing it to every `compute_business_T` call.**

## Current State (INEFFICIENT)

```python
# ORCHESTRATOR (line 317)
cal = await _get_calendar()

# ... later in _process_chunk (line 269)
clean_df = compute_business_T(clean_df, cal)  # Rebuilds schedule EVERY CHUNK!
```

```python
# MATH.PY (compute_business_T, lines 54-141)
def compute_business_T(df, cal):
    schedule = cal.schedule(start_date=..., end_date=...)  # REBUILT EVERY CALL!
    # ... O(n × d) loop ...
```

## Required Changes

### 1. Import Schedule Builder (Require EH-02 Complete)

```python
from dataingestion.math import (
    compute_business_T,
    compute_forward,
    compute_vega,
    _build_business_time_schedule,  # NEW from EH-02
)
```

### 2. Build Schedule Cache Once (After Calendar, Before Chunk Loop)

```python
# In run_backfill(), after line 317 (cal = await _get_calendar())
# Build once for the FULL backfill range
schedule_cache = _build_business_time_schedule(cal, start_date, end_date)
```

### 3. Pass Cache to `_process_chunk`

```python
# Update _process_chunk signature
async def _process_chunk(
    client, exp, chunk_start, chunk_end,
    conn, run_id, cal, rates_df, completed_chunks,
    schedule_cache,  # NEW PARAMETER
) -> tuple[int, int, int]:
```

### 4. Pass Cache to `compute_business_T`

```python
# In _process_chunk, line 269
clean_df = compute_business_T(clean_df, cal, schedule_cache=schedule_cache)
```

### 5. Update `_get_calendar` to Return Calendar (Already Does)

```python
# Already correct - returns mcal calendar object
```

## Invariants (Must Preserve)

- ✅ `business_t` values **identical** to before (within float64 precision)
- ✅ Schedule covers full range: `[start_date - 5d, max_expiration + 5d]`
- ✅ Half-days and holidays handled correctly via `pandas_market_calendars`
- ✅ Timezone conversion to US/Eastern preserved
- ✅ Formula unchanged: `(minutes_remaining_today + between_minutes) / (390 * 252)`
- ✅ All existing math tests pass

## Acceptance Criteria

### Functional
1. `_build_business_time_schedule` called **once** per `run_backfill()`
2. `schedule_cache` passed through to every `compute_business_T` call
3. Zero schedule rebuilds inside chunk loop
4. All math tests pass (identical outputs)

### Performance
```bash
# Benchmark: 100K rows should be < 500ms (was ~5-10s)
python -m pytest dataingestion/test_math_perf.py -v
```

### Testing
```bash
python -m pytest dataingestion/test_math.py -v           # All 17 pass
python -m pytest dataingestion/test_orchestrator.py -v  # All pass
```

### New Test in `test_orchestrator.py`

```python
class TestScheduleCache:
    def test_schedule_built_once_per_backfill(self, patched_orchestrator):
        """Verify _build_business_time_schedule called exactly once."""
        call_count = 0
        
        def _count_build(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_build(*args, **kwargs)
        
        with patch("dataingestion.orchestrator._build_business_time_schedule",
                   side_effect=_count_build):
            await run_backfill(...)
            assert call_count == 1, "Schedule should be built once"
    
    def test_schedule_cache_passed_to_compute_business_T(self, patched_orchestrator):
        """Verify schedule_cache parameter passed correctly."""
        # Mock compute_business_T and verify schedule_cache kwarg
```

## Dependencies

- **EH-02 MUST BE COMPLETE** — `_build_business_time_schedule` must exist in `math.py`
- If not done, this agent BLOCKS

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` — schedule cache built once, passed throughout
2. **Verification** all math + orchestrator tests pass, performance benchmark met