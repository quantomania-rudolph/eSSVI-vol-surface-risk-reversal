# EO318: ContextVar Type Annotations

## Persona

You are a **type safety advocate** who knows that `ContextVar[int | None]` should be `ContextVar[Optional[int]]` for proper mypy support.

## Core Objective

**Fix ContextVar type annotations to use `Optional[]` from typing.**

## Current State (Lines 25-27)

```python
run_id_var: ContextVar[int | None] = ContextVar("run_id", default=None)
exp_var: ContextVar[str | None] = ContextVar("exp", default=None)
chunk_var: ContextVar[str | None] = ContextVar("chunk", default=None)
```

## Required Fix

```python
from typing import Optional

run_id_var: ContextVar[Optional[int]] = ContextVar("run_id", default=None)
exp_var: ContextVar[Optional[str]] = ContextVar("exp", default=None)
chunk_var: ContextVar[Optional[str]] = ContextVar("chunk", default=None)
```

Note: Python 3.10+ supports `int | None` but mypy prefers `Optional[int]`.

## Invariants

- ✅ `mypy dataingestion/orchestrator.py` passes
- ✅ Consistent with project type hint style

## Success Criteria

```bash
mypy dataingestion/orchestrator.py
# No ContextVar type errors
```