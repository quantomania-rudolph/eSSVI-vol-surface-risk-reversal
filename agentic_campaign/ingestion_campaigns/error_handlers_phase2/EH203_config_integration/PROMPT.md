# EH203: Config Integration

## Persona

You are a **configuration architect** who believes hardcoded magic numbers in pipeline code are technical debt bombs. Every threshold — semaphore limits, DTE bands, chunk sizes — must be centralized, typed, and overridable via environment.

## Mission

**Eliminate ALL hardcoded thresholds from `orchestrator.py` by importing from `dataingestion.config` (EH-06).**

## Current State (HARDCODED)

```python
# ORCHESTRATOR - Hardcoded throughout

# Line 65-66: Semaphores
OPT_SEM = asyncio.Semaphore(4)
STK_SEM = asyncio.Semaphore(2)

# Line 191: Month chunking (MAX_CHUNK_DAYS = 31 implicit)
def _month_chunks(start, end):
    # Uses cur.month + 1 logic, effectively ≤31 days

# Line 207: DTE window
def _dte_window(exp, dte_min=7, dte_max=90):

# Line 317: Calendar (no config)
async def _get_calendar():
```

## Required Changes

### 1. Import All Config (Require EH-06 Complete)

```python
from dataingestion.config import (
    OPT_SEM_LIMIT,
    STK_SEM_LIMIT,
    MAX_CHUNK_DAYS,
    DTE_WINDOW_MIN,
    DTE_WINDOW_MAX,
    # For future use
    THETA_INTERVAL,
    THETA_FORMAT,
    THETA_ANNUAL_DIVIDEND,
    THETA_RATE_TYPE,
    THETA_VERSION,
)
```

### 2. Replace Semaphore Definitions

```python
# BEFORE
OPT_SEM = asyncio.Semaphore(4)
STK_SEM = asyncio.Semaphore(2)

# AFTER
OPT_SEM = asyncio.Semaphore(OPT_SEM_LIMIT)
STK_SEM = asyncio.Semaphore(STK_SEM_LIMIT)
```

### 3. Replace `_month_chunks` Logic

```python
# BEFORE
def _month_chunks(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    chunks = []
    cur = start
    while cur <= end:
        if cur.month == 12:
            next_month = dt.date(cur.year + 1, 1, 1)
        else:
            next_month = dt.date(cur.year, cur.month + 1, 1)
        chunk_end = min(next_month - dt.timedelta(days=1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + dt.timedelta(days=1)
    return chunks

# AFTER - Configurable max chunk size
def _month_chunks(start: dt.date, end: dt.date, max_days: int = MAX_CHUNK_DAYS) -> list[tuple[dt.date, dt.date]]:
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=max_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + dt.timedelta(days=1)
    return chunks
```

### 4. Replace `_dte_window` Defaults

```python
# BEFORE
def _dte_window(exp: dt.date, dte_min: int = 7, dte_max: int = 90) -> tuple[dt.date, dt.date]:

# AFTER
def _dte_window(exp: dt.date, dte_min: int = DTE_WINDOW_MIN, dte_max: int = DTE_WINDOW_MAX) -> tuple[dt.date, dt.date]:
```

### 5. Update All Call Sites

```python
# Line 323: _dte_window(exp) - uses defaults (now from config)
# Line 358: chunks = _month_chunks(exp_start, exp_end) - uses default max_days
# Line 364: chunks = _month_chunks(exp_start, exp_end) - uses default max_days
```

### 6. Verify ZERO Hardcoded Numeric Thresholds Remain

Search for: `4`, `2`, `7`, `90`, `31`, `30` used as limits (not dates/indices).

## Invariants (Must Preserve)

- ✅ Default behavior identical (OPT_SEM=4, STK_SEM=2, DTE=[7,90], chunks≤31 days)
- ✅ All values overridable via `dataingestion.config` → environment
- ✅ Type hints on all config imports
- ✅ All existing tests pass

## Acceptance Criteria

### Functional
1. Zero magic numbers in `orchestrator.py` for thresholds
2. All limits sourced from `dataingestion.config`
3. Environment variable overrides work (test by setting `OPT_SEM_LIMIT=2`)
4. All orchestrator tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
python -m pytest dataingestion/test_config.py -v
```

### New Test in `test_config.py`
```python
def test_orchestrator_uses_config():
    """Verify orchestrator imports and uses config constants."""
    from dataingestion import orchestrator
    import inspect
    source = inspect.getsource(orchestrator)
    
    # Check config imports present
    assert "from dataingestion.config import" in source
    assert "OPT_SEM_LIMIT" in source
    assert "STK_SEM_LIMIT" in source
    assert "MAX_CHUNK_DAYS" in source
    assert "DTE_WINDOW_MIN" in source
    assert "DTE_WINDOW_MAX" in source
    
    # Check no hardcoded semaphore values
    assert "Semaphore(4)" not in source
    assert "Semaphore(2)" not in source
```

## Dependencies

- **EH-06 MUST BE COMPLETE** — `dataingestion.config` must exist with all constants

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` — all thresholds from config
2. **Verification** all tests pass