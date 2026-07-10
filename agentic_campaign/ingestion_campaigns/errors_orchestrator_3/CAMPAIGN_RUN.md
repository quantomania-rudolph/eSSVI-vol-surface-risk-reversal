# Campaign Run: errors_orchestrator_3

## Overview

This campaign addresses 22 issues identified in the thermo-nuclear audit of Phase 2's `orchestrator.py`. Issues are organized into **4 priority tiers** with **5 execution waves**. Each agent runs from its exact `PROMPT.md` file.

## Priority Tiers

| Tier | Count | Description |
|------|-------|-------------|
| **P0** | 7 | Critical bugs causing incorrect results |
| **P1** | 5 | Architecture violations (1k-line rule, SRP) |
| **P2** | 6 | Code quality (types, imports, magic numbers) |
| **P3** | 4 | Test gaps and verification |

---

## Wave 1: P0 Critical Bugs (PARALLEL)

**All 7 agents can run simultaneously — no dependencies between them.**

| Agent | Target | Dependency |
|-------|--------|------------|
| `EO301_cache_hit_bug` | `orchestrator.py` lines 152, 158 | None |
| `EO302_oi_join_nullifies` | `orchestrator.py` `_join_oi` | None |
| `EO303_schedule_cache_range` | `orchestrator.py` `_get_calendar` | None |
| `EO304_duplicate_business_T` | `math.py` | None |
| `EO305_rates_cache_key` | `orchestrator.py` `_get_rates` | None |
| `EO306_watermark_race_logging` | `orchestrator.py` `_process_chunk` | None |
| `EO307_context_var_cleanup` | `orchestrator.py` `_process_chunk` | None |

**Verification after Wave 1:**
```bash
python -m pytest dataingestion/test_orchestrator.py::TestIntegration -v
# All integration tests pass
```

---

## Wave 2: P1 Architecture Refactoring (SEQUENTIAL)

**Must run in order — each creates modules the next depends on.**

| Order | Agent | Creates | Depends On |
|-------|-------|---------|------------|
| 1 | `EO308_god_module_split` | `logging.py`, `cache.py`, `retry.py`, `joins.py`, `chunking.py` | Wave 1 complete |
| 2 | `EO309_separation_of_concerns` | Refactors `joins.py` with new pure functions | EO308 |
| 3 | `EO310_pool_consolidation` | Single `get_pool` in `db_writer.py` | EO308 |
| 4 | `EO311_parameterize_ticker` | Updates `run_backfill` signature | EO308 |
| 5 | `EO312_config_imports` | Changes imports to `import config as cfg` | EO308 |

**Verification after Wave 2:**
```bash
# Module structure
ls dataingestion/*.py
# logging.py cache.py retry.py joins.py chunking.py db_writer.py math.py config.py orchestrator.py

# Line count
wc -l dataingestion/orchestrator.py
# < 300 lines

# Single get_pool
grep -n "def get_pool" dataingestion/db_writer.py
# Exactly 1 match
```

---

## Wave 3: P2 Code Quality (PARALLEL within groups)

### Group 3a: Immediate (after Wave 2)

| Agent | Target | Dependency |
|-------|--------|------------|
| `EO313_type_hints` | All private functions | Wave 2 complete |
| `EO314_inline_imports` | Module top-level | Wave 2 complete |
| `EO315_magic_numbers` | `joins.py` `_attach_rates` | Wave 2 complete (uses `joins.py`) |

### Group 3b: After Group 3a

| Agent | Target | Dependency |
|-------|--------|------------|
| `EO316_duplicate_get_pool` | Verify import cleanup | EO310 |
| `EO317_docstrings` | All private functions | EO313 |
| `EO318_contextvar_types` | `orchestrator.py` ContextVar | EO313 |
| `EO319_unused_imports` | `orchestrator.py` config imports | EO312 |

**Verification after Wave 3:**
```bash
mypy dataingestion/
# No errors

flake8 dataingestion/orchestrator.py
# No F401, no other errors

python -c "
import ast
with open('dataingestion/orchestrator.py') as f:
    tree = ast.parse(f.read())
missing = []
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('_'):
        if not ast.get_docstring(node):
            missing.append(f'{node.name}:{node.lineno}')
print('Missing docstrings:' if missing else 'All docstrings present')
for m in missing: print(f'  {m}')
"
```

---

## Wave 4: P3 Test & Semantics (PARALLEL)

| Agent | Target | Dependency |
|-------|--------|------------|
| `EO320_error_semantics` | `_process_chunk` return type | Wave 2 complete |
| `EO321_test_coverage` | New test files | Wave 2 complete |

**Verification after Wave 4:**
```bash
python -m pytest dataingestion/test_*.py -v
# All tests pass (77+)

# Verify ChunkResult usage
grep -n "ChunkResult" dataingestion/orchestrator.py
# Should appear in _process_chunk and run_backfill
```

---

## Wave 5: Final Verification (SEQUENTIAL)

| Order | Agent | Purpose |
|-------|-------|---------|
| 1 | `EO322_verification_suite` | Runs all 23 automated checks |
| 2 | — | Full test suite + manual spot-check |

**Final Verification Commands:**
```bash
# 1. Automated verification
python verify_phase3.py
# All 23 checks pass

# 2. Full test suite
python -m pytest dataingestion/ -v --tb=short
# All 77+ tests pass

# 3. Type check
mypy dataingestion/
# No errors

# 4. Lint
flake8 dataingestion/
# No errors

# 5. Spot-check key behaviors
python -c "
import asyncio
from dataingestion.orchestrator import run_backfill
# Quick smoke test with mocks
print('Import successful, modules load')
"
```

---

## Dependency Graph

```
                    ┌─ EO301
                    ├─ EO302
                    ├─ EO303
Wave 1 (P0) ───────┼─ EO304  ──┐
  (parallel)       ├─ EO305    │
                    ├─ EO306    ▼
                    └─ EO307  Wave 2 (P1)
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
               EO308        (creates 5      (waits)
                │          new modules)
                ▼
          ┌─────┴─────┐
          ▼           ▼
       EO309       EO310
          │           │
          ▼           ▼
       EO311       EO312
          │           │
          └─────┬─────┘
                ▼
         Wave 3 (P2)
    ┌────┬────┬────┬────┐
    ▼    ▼    ▼    ▼    ▼
  EO313 EO314 EO315 EO316 EO317 EO318 EO319
    │    │    │
    └────┴────┘
         │
         ▼
    Wave 4 (P3)
    ┌──────┬──────┐
    ▼      ▼
  EO320  EO321
    │      │
    └──────┘
         │
         ▼
    Wave 5
         ▼
      EO322
```

---

## Execution Instructions

### Launch Wave 1 (all 7 in parallel)
```bash
# Launch each agent with its exact PROMPT.md
# Example for one agent:
python -m cursor.agent --prompt @agentic_campaign/errors_orchestrator_3/EO301_cache_hit_bug/PROMPT.md
```

### Launch Wave 2 (sequential)
Wait for Wave 1 verification, then run EO308 → EO309 → EO310 → EO311 → EO312 in order.

### Launch Wave 3
Groups 3a and 3b can run internally in parallel.

### Launch Wave 4
Both agents in parallel after Wave 3 verification.

### Launch Wave 5
EO322 runs last, then manual verification.

---

## Rollback Plan

If any wave breaks existing tests:
1. Stop the wave
2. Revert the specific agent's changes
3. Debug and re-run that agent
4. Continue from that point

All changes are isolated per-agent — no cross-agent conflicts expected.

---

## Success Definition

**Campaign complete when:**
1. ✅ All 22 agents completed without test regressions
2. ✅ `verify_phase3.py` passes all 23 checks
3. ✅ Full test suite: 77+ tests pass
4. ✅ `mypy` and `flake8` clean
5. ✅ `orchestrator.py` < 300 lines
6. ✅ 6 new modules exist and are imported