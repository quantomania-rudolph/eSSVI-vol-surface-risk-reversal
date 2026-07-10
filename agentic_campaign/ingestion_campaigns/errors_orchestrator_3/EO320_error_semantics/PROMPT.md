# EO320: Consistent Error Semantics in _process_chunk

## Persona

You are a **reliability engineer** who knows that inconsistent error counting makes monitoring and alerting impossible. "Fetch error", "DB error", and "empty fetch" must be distinguishable.

## Core Objective

**Standardize error return values in `_process_chunk` to have clear semantics.**

## Current Inconsistent Behavior

| Scenario | Return Value | Meaning |
|----------|--------------|---------|
| Empty greeks fetch | `(0, 0, 0)` | Silent skip — not an error |
| Fetch exception (retries exhausted) | `(0, 0, 1)` | Error counted |
| DB write exception | `(clean_len, quar_len, 1)` | Error counted, but partial success lost |
| Non-retryable fetch error | `(0, 0, 1)` | Error counted |

## Required Fix

Define clear error types and return a structured result:

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ChunkResult:
    clean_rows: int
    quar_rows: int
    fetch_error: bool = False
    db_error: bool = False
    skipped: bool = False

# In _process_chunk:
# Empty fetch → ChunkResult(0, 0, skipped=True)
# Fetch exception → ChunkResult(0, 0, fetch_error=True)
# DB exception → ChunkResult(clean, quar, db_error=True)
# Success → ChunkResult(clean, quar)
```

Then `run_backfill` aggregates:
```python
total_fetch_errors = sum(1 for r in results if r.fetch_error)
total_db_errors = sum(1 for r in results if r.db_error)
total_skipped = sum(1 for r in results if r.skipped)
```

## Invariants

- ✅ Every chunk result has unambiguous status
- ✅ `run_backfill` return dict includes `fetch_errors`, `db_errors`, `skipped_chunks`
- ✅ All tests pass (may need test updates)

## Success Criteria

```bash
python -m pytest dataingestion/test_orchestrator.py -v
# All tests pass with new semantics
```