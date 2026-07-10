# EO304: Duplicate compute_business_T in math.py Fix

## Persona

You are a **code archaeologist** who knows that duplicate function definitions are a maintenance nightmare. Python imports the last definition, making the first one dead code that confuses readers and wastes space.

## Core Objective

**Delete the first (non-cached) `compute_business_T` definition in `math.py` and all its helper functions, keeping only the cached version.**

## Current State

`math.py` has TWO `compute_business_T` functions:
1. **Lines 54-141**: Original version without `schedule_cache` parameter
2. **Lines 311-390**: Cached version with `schedule_cache` parameter (added in EH202)

Both are imported via `from dataingestion.math import compute_business_T` — Python uses the **second** one.

## Required Fix

### 1. Delete lines 54-141 (first definition) including helpers:
- `_session_minutes` (lines 17-24)
- `_get_tz` (lines 26-30) 
- `_to_eastern` (lines 32-40)
- First `compute_business_T` (lines 54-141)

### 2. Keep lines 311-390 (cached version) as THE definition

### 3. Update imports if needed (should be fine since second is kept)

## Invariants

- ✅ Only ONE `compute_business_T` exists in `math.py`
- ✅ It accepts `schedule_cache` parameter
- ✅ All existing tests pass (they use the cached version)
- ✅ No behavior change — the cached version IS the one being used

## Success Criteria

### Functional
1. `grep -n "def compute_business_T" dataingestion/math.py` returns exactly 1 match
2. All 17 math tests pass
2. All 46 orchestrator tests pass

### Testing
```bash
python -m pytest dataingestion/test_math.py -v
python -m pytest dataingestion/test_orchestrator.py -v
```

## Verification Agent

```bash
# Verify single definition
grep -n "def compute_business_T" dataingestion/math.py
# Should return exactly 1 line number
```