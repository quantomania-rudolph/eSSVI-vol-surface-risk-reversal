# EO309: Separation of Concerns - Business Logic Out of Orchestrator

## Persona

You are a **clean architecture advocate** who knows that `_process_chunk` mixes fetching (I/O), transformation, cleaning (business rules), math (computation), and persistence (DB) — making it impossible to unit test any step in isolation.

## Core Objective

**Extract pure business logic functions from `_process_chunk` so each step can be tested independently without mocking I/O.**

## Current State (Lines 453-573)

```python
async def _process_chunk(...):
    # 1. FETCH (I/O)
    opt_df, oi_df, stk_df = await asyncio.gather(_fetch_opt(), _fetch_oi(), _fetch_stk())
    
    # 2. TRANSFORM (joins)
    opt_df = _join_spot(opt_df, stk_df)
    opt_df = _join_oi(opt_df, oi_df)
    
    # 3. CLEAN (business rules)
    clean_df, quar_df = clean_option_chain(opt_df)
    
    # 4. MATH (computation)
    clean_df = compute_business_T(clean_df, cal, schedule_cache=schedule_cache)
    clean_df = _attach_rates(clean_df, rates_df)
    clean_df = compute_forward(clean_df)
    clean_df = compute_vega(clean_df)
    
    # 5. PERSIST (DB)
    await write_staging_batch(...)
    await load_from_staging(...)
    await write_quarantine_batch(...)
    await advance_watermark(...)
```

## Required Refactoring

Extract pure functions (no I/O, no DB, no async):

```python
# In joins.py (already EO308)
def join_spot_and_oi(opt_df: pd.DataFrame, stk_df: pd.DataFrame, oi_df: pd.DataFrame) -> pd.DataFrame:
    """Pure: joins spot and OI, returns transformed DataFrame."""

def attach_rates_and_math(clean_df: pd.DataFrame, rates_df: pd.DataFrame, 
                          cal, schedule_cache: dict) -> pd.DataFrame:
    """Pure: attaches rates, computes T, forward, vega."""

# In orchestrator.py
async def _process_chunk(...):
    # Fetch (I/O)
    opt_df, oi_df, stk_df = await asyncio.gather(...)
    
    # Transform (pure)
    opt_df = join_spot_and_oi(opt_df, stk_df, oi_df)
    
    # Clean (pure, existing)
    clean_df, quar_df = clean_option_chain(opt_df)
    
    # Math (pure)
    clean_df = attach_rates_and_math(clean_df, rates_df, cal, schedule_cache)
    
    # Persist (I/O)
    await write_staging_batch(...)
    ...
```

## Invariants

- ✅ Pure functions have no side effects, no I/O, no async
- ✅ Each pure function testable with simple DataFrame inputs
- ✅ `_process_chunk` only handles I/O orchestration
- ✅ All 77 tests pass

## Success Criteria

### Functional
1. `_process_chunk` reduced to ~40 lines (I/O orchestration only)
2. New pure functions in `joins.py` or `pipeline.py`
3. All tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
# Add unit tests for pure functions in test_joins.py or test_pipeline.py
```