# EO310: Pool Consolidation - Single Source of Truth

## Persona

You are a **database engineer** who knows that having two `get_pool` functions with different connection parameters is a recipe for connection leaks, credential drift, and debugging nightmares.

## Core Objective

**Consolidate to a single `get_pool` in `db_writer.py` and remove the duplicate from `orchestrator.py` imports.**

## Current State

```python
# orchestrator.py line 129
from dataingestion.db_writer import (
    ...
    get_pool,  # Imported from db_writer
)

# db_writer.py lines 20-33 AND 336-347 — TWO definitions!
# First (lines 20-33): PGConfig class with env var defaults
# Second (lines 336-347): get_pool() creating pool with hardcoded config
```

## Required Fix

1. **Keep ONLY the `get_pool` in `db_writer.py`** (the more complete one with `PGConfig`)
2. **Remove any duplicate `get_pool` definition** in `db_writer.py`
3. **Ensure `orchestrator.py` imports from `db_writer`** (already does)
4. **Verify both use same connection parameters**

## Invariants

- ✅ Single `get_pool` function in entire codebase
- ✅ Single `PGConfig` class
- ✅ Connection parameters from environment variables only
- ✅ All tests pass

## Success Criteria

### Functional
1. `grep -r "def get_pool" dataingestion/` returns exactly ONE definition
2. All tests pass

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py dataingestion/test_db_writer.py -v
```