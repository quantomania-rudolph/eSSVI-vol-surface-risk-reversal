# EO316: Duplicate get_pool Import Removal

## Persona

You are a **clean code maintainer** who knows that importing the same function from two different paths creates confusion and potential version skew.

## Core Objective

**Remove the duplicate `get_pool` import from `orchestrator.py` since it's already imported from `db_writer` where it's defined.**

## Current State

```python
# orchestrator.py line 129
from dataingestion.db_writer import (
    ...
    get_pool,
)
```

This is the ONLY import of `get_pool` - which is correct! The issue is that `db_writer.py` has TWO `get_pool` definitions. The import is fine; the fix is in EO310.

**Wait** - re-reading the audit: "Two get_pool factories with different configs (orchestrator.py + db_writer.py)". The orchestrator doesn't define get_pool, it imports from db_writer. The issue is db_writer has two definitions.

So this prompt is actually covered by EO310. Let me reframe:

## Actual Fix Needed

**Ensure `orchestrator.py` only imports `get_pool` once from `db_writer`, and `db_writer` only has ONE `get_pool` definition.**

This is already the case for the import. The real work is in EO310 (consolidate db_writer's two get_pool definitions).

## Verification

```bash
# Verify single import
grep -n "get_pool" dataingestion/orchestrator.py
# Should show exactly 1 import line

# Verify single definition
grep -n "def get_pool" dataingestion/db_writer.py
# Should show exactly 1 definition
```