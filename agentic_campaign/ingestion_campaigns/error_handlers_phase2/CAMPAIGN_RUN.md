# Phase 2 Error Handlers Campaign — Orchestrator Hardening

**All agents in this campaign target `dataingestion/orchestrator.py` and its integration with Phase 1 modules.**  
Agents can run in **parallel waves** (see Execution Order below).

## Execution Order (Waves)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  WAVE 1 (Parallel — Foundation Fixes)                                       │
│  ─────────────────────────────────────────────────────────────────────────  │
│  EH201  EH202  EH203  EH209                                                 │
│  Async   Schedule  Config  Global Cache                                     │
│  Fetchers Cache    Module  Management                                       │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  WAVE 2 (Parallel — Pipeline Correctness)                                   │
│  ─────────────────────────────────────────────────────────────────────────  │
│  EH204  EH205  EH206                                                       │
│  Client  Watermark  Fetch                                                  │
│  Lifecycle Race     Resilience                                             │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  WAVE 3 (Sequential — Observability & Verification)                         │
│  ─────────────────────────────────────────────────────────────────────────  │
│  EH207  EH208                                                                │
│  Logging  Async Test                                                         │
│  Setup    Verification                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Agent Registry

| Agent | Prompt File | Target | Wave | Depends On |
|-------|-------------|--------|------|------------|
| **EH201** | `EH201_async_fetcher_integration/PROMPT.md` | orchestrator.py + fetchers.py | 1 | EH-01 (async fetchers) |
| **EH202** | `EH202_schedule_cache_integration/PROMPT.md` | orchestrator.py + math.py | 1 | EH-02 (schedule cache) |
| **EH203** | `EH203_config_integration/PROMPT.md` | orchestrator.py + config.py | 1 | EH-06 (config module) |
| **EH209** | `EH209_global_cache_management/PROMPT.md` | orchestrator.py | 1 | — |
| **EH204** | `EH204_client_lifecycle/PROMPT.md` | orchestrator.py | 2 | EH201, EH203 |
| **EH205** | `EH205_watermark_race_fix/PROMPT.md` | orchestrator.py + db_writer.py | 2 | EH-04 |
| **EH206** | `EH206_fetch_resilience/PROMPT.md` | orchestrator.py + fetchers.py | 2 | EH201 |
| **EH207** | `EH207_logging_setup/PROMPT.md` | orchestrator.py | 3 | EH204 |
| **EH208** | `EH208_test_async_verification/PROMPT.md` | test_orchestrator.py | 3 | All above |

---

## Shared Interface (Column Contract)

**Every agent MUST adhere to `dataingestion/COLUMNS.md`** — the pipeline column contract is immutable.

| Module | Input | Output |
|--------|-------|--------|
| fetchers.py | Raw Theta | §I + `_phase="raw"` |
| cleaning.py | §I | §II.A (clean) + §II.B (quarantine) |
| math.py | §II.A | §III + `_phase="math"` |
| db_writer.py | §III | DB (§IV) |
| orchestrator.py | All above | Pipeline orchestration |

---

## Dependency Rules (Enforced by Tests)

| Module | May Import | May NOT Import |
|--------|------------|----------------|
| orchestrator.py | All `dataingestion.*`, `core_engine.shared.*`, `pandas_market_calendars`, `asyncio`, `pandas` | Direct `aiohttp`, raw SQL outside db_writer |

---

## Verification Process

```bash
# From project root (ThetaData_greeks)
cd c:\Users\Rudol\Desktop\ThetaData_greeks

# After each wave, run:
python -m pytest dataingestion/test_orchestrator.py -v

# After all waves:
python -m pytest dataingestion/test_orchestrator.py dataingestion/test_integration.py -v
```

---

## What "Passing" Means for Phase 2

| Criterion | Verification |
|-----------|--------------|
| **Async fetchers used** | No `asyncio.run()` inside async functions; semaphores actually limit concurrency |
| **Schedule cached** | `compute_business_T` called with `schedule_cache`; built once per backfill |
| **Config driven** | Zero hardcoded thresholds in orchestrator; all from `dataingestion.config` |
| **Client lifecycle** | Single `AsyncThetaClient` per backfill (or per expiration); proper `__aenter__`/`__aexit__` |
| **Watermark race-free** | `get_completed_chunks` + `advance_watermark` atomic per chunk; no double-skip |
| **Fetch resilience** | Transient errors retry with backoff; non-retryable quarantined; chunk marked failed not crashed |
| **Structured logging** | JSON logs with chunk/expiration/run_id; log levels configurable |
| **Async tests pass** | `test_orchestrator.py` uses async fetcher mocks; verifies semaphore behavior |
| **Integration test passes** | `test_integration.py` full pipeline with synthetic data |

---

## Grade Baseline (Pre-Fixes)

| Category | Grade | Key Issues |
|----------|-------|------------|
| **Async Correctness** | D | Sync wrappers block event loop; semaphores ineffective |
| **Performance** | C | Schedule rebuilt per chunk; no client reuse |
| **Config Hygiene** | F | All thresholds hardcoded |
| **Resource Management** | D | New client per chunk; caches unbounded |
| **Concurrency Safety** | D | Watermark check/advance not atomic |
| **Error Handling** | C | No retry/backoff; crashes on transient errors |
| **Observability** | D | Basic logging only |
| **Testability** | C | Tests mock sync functions, not async variants |

**Overall Phase 2 Baseline: D+** — Functional but not production-ready.