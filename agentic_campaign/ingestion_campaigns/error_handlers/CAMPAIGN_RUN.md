# Error Handlers Campaign — Phase 1 Fixes

**All agents in this campaign are independent and can run in parallel.**  
They each fix a specific gap/error identified in the Phase 1 audit.

## Execution Order

```
┌────────────────────────────────────────────────────────────────────┐
│  Launch all simultaneously (no inter-dependencies):                │
│                                                                     │
│  EH-01  EH-02  EH-03  EH-04  EH-05  EH-06  EH-07  EH-08           │
│   │      │      │      │      │      │      │      │              │
│   ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼              │
│ fetcher math   cleaning db     orch   config  verify  integrate   │
│ async  opt    fixes   writer  fixes  module  tests   tests         │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

## Agent Registry

| Agent | Prompt File | Target Module(s) | Verification |
|-------|-------------|------------------|--------------|
| **EH-01** | `EH01_fetchers_async/PROMPT.md` | `dataingestion/fetchers.py` | `test_fetchers.py` + new async tests |
| **EH-02** | `EH02_math_business_time/PROMPT.md` | `dataingestion/math.py` | `test_math.py` + perf benchmarks |
| **EH-03** | `EH03_cleaning_fixes/PROMPT.md` | `dataingestion/cleaning.py` | `test_cleaning.py` |
| **EH-04** | `EH04_db_writer_fixes/PROMPT.md` | `dataingestion/db_writer.py` | `test_db_writer.py` |
| **EH-05** | `EH05_orchestrator_integration/PROMPT.md` | `dataingestion/orchestrator.py` | `test_orchestrator.py` |
| **EH-06** | `EH06_config_module/PROMPT.md` | `dataingestion/config.py` (new) | All tests use config |
| **EH-07** | `EH07_verification_hardening/PROMPT.md` | `dataingestion/test_verify.py` | `test_verify.py` |
| **EH-08** | `EH08_integration_tests/PROMPT.md` | `dataingestion/test_integration.py` (new) | Full pipeline test |

---

## Shared Interface (Column Contract)

**Every agent MUST read and adhere to `dataingestion/COLUMNS.md`.**  
Key invariants that must hold after fixes:

| Module | Input Columns | Output Columns |
|--------|---------------|----------------|
| fetchers.py | N/A (raw Theta) | `COLUMNS.md` §I (+ async variants) |
| cleaning.py | `COLUMNS.md` §I | `COLUMNS.md` §II |
| math.py | `COLUMNS.md` §II | `COLUMNS.md` §III |
| db_writer.py | `COLUMNS.md` §III | N/A (writes to DB per §IV) |
| orchestrator.py | All above | Pipeline orchestration |

---

## Dependency Rules (Enforced by Verification Tests)

| Module | May Import From | May NOT Import From |
|--------|-----------------|---------------------|
| fetchers.py | `core_engine.shared.*`, `pandas`, `asyncio` | `dataingestion.*`, `asyncpg`, `numba` |
| cleaning.py | `pandas`, `numpy`, `dataingestion.config` | `dataingestion.*`, `core_engine.*`, `asyncpg`, `numba` |
| math.py | `pandas`, `numpy`, `numba`, `pandas_market_calendars`, `dataingestion.config` | `dataingestion.*`, `core_engine.*`, `asyncpg` |
| db_writer.py | `asyncpg`, `pandas`, `dataingestion.config` | `dataingestion.fetchers`, `dataingestion.cleaning`, `dataingestion.math`, `core_engine.shared.theta_client` |
| orchestrator.py | All `dataingestion.*`, `core_engine.shared.*`, `pandas_market_calendars` | Direct `aiohttp`, raw SQL outside db_writer |

---

## Verification Process

After each agent completes, run their test suite:

```bash
# From project root (ThetaData_greeks)
cd c:\Users\Rudol\Desktop\ThetaData_greeks

# EH-01: Fetchers (async variants + existing tests)
python -m pytest dataingestion/test_fetchers.py -v
python -m pytest dataingestion/test_fetchers_async.py -v  # new

# EH-02: Math (business time optimization + existing tests)
python -m pytest dataingestion/test_math.py -v

# EH-03: Cleaning (fixes + existing tests)
python -m pytest dataingestion/test_cleaning.py -v

# EH-04: DB Writer (fixes + existing tests)
python -m pytest dataingestion/test_db_writer.py -v

# EH-05: Orchestrator (integration fixes + existing tests)
python -m pytest dataingestion/test_orchestrator.py -v

# EH-06: Config module (all modules import from it)
python -m pytest dataingestion/test_config.py -v  # new

# EH-07: Verification hardening
python -m pytest dataingestion/test_verify.py -v

# EH-08: Integration tests (end-to-end pipeline)
python -m pytest dataingestion/test_integration.py -v  # new
```

---

## What "Passing" Means for Each Agent

| Agent | Passing Criteria |
|-------|-----------------|
| **EH-01** | All 6 fetcher functions have async variants; existing sync wrappers preserved; no semaphore/DB/HTTP imports in module; correct params sent to client; empty on error |
| **EH-02** | `compute_business_T` uses prefix-sum schedule (O(1) per row); business_t in years, positive, monotonic; T decreases with later timestamp; schedule cached |
| **EH-03** | Subpenny uses epsilon comparison; all 8 checks catch violations with correct codes; row accounting holds; belly spread flagged not quarantined; thresholds from config |
| **EH-04** | Single `get_pool`; column caching; tables/indexes/hypertable/compression idempotent; two-phase load correct; watermark tracks progress; ON CONFLICT works |
| **EH-05** | Orchestrator joins OHLC→spot, OI, rates BEFORE cleaning; async fetcher variants used; semaphores correct; watermark resume works; heartbeat first; chunks ≤30 days |
| **EH-06** | Single `config.py` with all thresholds; all 4 modules import from it; no hardcoded thresholds remain; type-safe config class |
| **EH-07** | Verification tests detect: null coverage, filter impact, business T sanity, future leakage, ESSVI IV smile, data freshness, row counts, status summary; no DB writes during verify |
| **EH-08** | Full pipeline test: synthetic data → fetchers (mocked) → cleaning → math → db_writer (staging mock) → asserts clean+quarantine=input, columns match contract |

---

## Estimated Effort

| Agent | Difficulty | ~Lines | Why |
|-------|-----------|--------|-----|
| EH-01 | Medium | ~150 | Add async variants to 6 functions, preserve sync wrappers, test both |
| EH-02 | Medium | ~80 | Prefix-sum optimization in compute_business_T, schedule caching |
| EH-03 | Low | ~50 | Subpenny epsilon, config imports, threshold constants |
| EH-04 | Low | ~60 | Remove duplicate get_pool, cache columns, minor cleanup |
| EH-05 | Medium | ~100 | Ensure join order, use async fetchers, verify semaphore usage |
| EH-06 | Low | ~80 | New config.py with typed constants, update 4 modules |
| EH-07 | Medium | ~120 | Strengthen verify.py tests, add missing checks |
| EH-08 | High | ~200 | New integration test file, synthetic pipeline, mock orchestration |