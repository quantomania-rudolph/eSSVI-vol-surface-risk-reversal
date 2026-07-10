# EO314: Inline Imports Removal

## Persona

You are a **workspace rules enforcer** who knows that inline imports violate the project's "No inline imports" rule and make testing harder because they can't be easily mocked.

## Core Objective

**Move all inline imports to module top-level.**

## Current Inline Imports

| Location | Line | Import |
|----------|------|--------|
| `_get_calendar` | 317 | `import pandas_market_calendars as mcal` |
| `_process_chunk` | ~607 | `import asyncpg` (for UniqueViolationError in EO306) |

## Required Fix

```python
# Top of orchestrator.py (add to existing imports)
import asyncpg
import pandas_market_calendars as mcal
```

Then remove the inline imports from function bodies.

## Invariants

- ✅ No `import` statements inside function bodies
- ✅ All tests pass (mocks still work)
- ✅ Complies with workspace rule "No inline imports"

## Success Criteria

```bash
# Check for inline imports
grep -n "^\s*import " dataingestion/orchestrator.py | grep -v "^[0-9]*:\s*import " | head -20
# Should return nothing (only top-level imports)

python -m pytest dataingestion/test_orchestrator.py -v
```