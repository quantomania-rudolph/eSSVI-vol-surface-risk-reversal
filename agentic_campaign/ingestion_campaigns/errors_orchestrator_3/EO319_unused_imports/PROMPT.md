# EO319: Remove Unused Config Imports

## Persona

You are a **code hygiene enforcer** who knows that unused imports pollute namespace, increase load time, and confuse readers.

## Core Objective

**Remove 5 unused config imports from orchestrator.py.**

## Current State (Lines 95-100)

```python
from dataingestion.config import (
    ...
    THETA_INTERVAL,      # UNUSED
    THETA_FORMAT,        # UNUSED
    THETA_ANNUAL_DIVIDEND,  # UNUSED
    THETA_RATE_TYPE,     # UNUSED
    THETA_VERSION,       # UNUSED
)
```

These are imported but never referenced in orchestrator.py.

## Required Fix

Remove these 5 constants from the import statement.

## Invariants

- ✅ `flake8 dataingestion/orchestrator.py` reports no unused imports
- ✅ All tests pass

## Success Criteria

```bash
flake8 dataingestion/orchestrator.py
# No F401 errors for config imports
```