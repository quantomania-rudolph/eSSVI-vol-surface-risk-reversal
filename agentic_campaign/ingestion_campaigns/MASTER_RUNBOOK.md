# Agentic Campaign — Master Runbook

## Project: AMD eSSVI Data Ingestion Pipeline

**Goal:** Backfill 1-minute option chain + Greeks data for AMD (2018-01-01 → present)
into a TimescaleDB hypertable, cleaned and verified, as input for an eSSVI volatility
surface fitter.

**Core Engine:** Stripped-down HTTP client in `core_engine/` — provides
`AsyncThetaClient.get()`, heartbeat, config, and parsing. No business logic.

**Data Ingestion:** Custom modules in `dataingestion/` — fetchers, cleaning,
math, DB writer, orchestrator, verification. Each built by a dedicated agent.

---

## Campaign Phases

### Phase 0 ✓ COMPLETED — Engine Strip + Column Contract

- [x] Stripped `core_engine/shared/config.py` to HTTP-only fields
- [x] Removed semaphore from `AsyncThetaClient` (caller-controlled)
- [x] Removed `fetchers.py` from core engine
- [x] Created `dataingestion/COLUMNS.md` — the shared column contract
- [x] Updated `core_engine/__init__.py` and `core_engine/shared/__init__.py`

### Phase 1 ✓ READY — Four Parallel Agents

**Status:** Prompts and verification scripts written. Ready to launch.

**Campaign Run:** `agentic_campaign/phase_1_parallel/CAMPAIGN_RUN.md`

| Agent | Builds | Test |
|-------|--------|------|
| A1 | `dataingestion/fetchers.py` | `test_fetchers.py` |
| A2 | `dataingestion/cleaning.py` | `test_cleaning.py` |
| A3 | `dataingestion/math.py` | `test_math.py` |
| A4 | `dataingestion/db_writer.py` | `test_db_writer.py` |

**Launch:** All four simultaneously. No code dependencies between them.

### Phase 2 ✓ READY — Orchestrator (Depends on Phase 1)

**Campaign Run:** `agentic_campaign/phase_2_orchestrator/CAMPAIGN_RUN.md`

| Agent | Builds | Test |
|-------|--------|------|
| A5 | `dataingestion/orchestrator.py` | `test_orchestrator.py` |

**Prerequisite:** All Phase 1 modules exist and pass their tests.

### Phase 3 ✓ READY — Verification (Depends on Phase 2)

**Campaign Run:** `agentic_campaign/phase_3_verify/CAMPAIGN_RUN.md`

| Agent | Builds | Test |
|-------|--------|------|
| A6 | `dataingestion/verify.py` | `test_verify.py` |

**Prerequisite:** Real data in TimescaleDB from Phase 2.

---

## File Inventory

```
ThetaData_greeks/
├── core_engine/                    # HTTP client library (Phase 0 done)
│   ├── shared/
│   │   ├── config.py               # THETA_HOST, THETA_PORT, timeout, rate limiting
│   │   ├── theta_client.py         # AsyncThetaClient.get(), heartbeat()
│   │   ├── parse.py                # parse_response_body(), to_dataframe()
│   │   ├── constants.py            # ET, UTC, parse_expiration(), normalize_right()
│   │   ├── db.py                   # IngestionLogger (optional)
│   │   └── __init__.py
│   ├── __init__.py
│   ├── pyproject.toml
│   └── requirements.txt
│
├── dataingestion/                  # Pipeline modules (Phase 1-3)
│   ├── COLUMNS.md                  # ** COLUMN CONTRACT — all agents reference this **
│   ├── fetchers.py                 # A1 builds this
│   ├── cleaning.py                 # A2 builds this
│   ├── math.py                     # A3 builds this
│   ├── db_writer.py                # A4 builds this
│   ├── orchestrator.py             # A5 builds this
│   ├── verify.py                   # A6 builds this
│   │
│   ├── test_fetchers.py            # A1 verification script
│   ├── test_cleaning.py            # A2 verification script
│   ├── test_math.py                # A3 verification script
│   ├── test_db_writer.py           # A4 verification script
│   ├── test_orchestrator.py        # A5 verification script
│   └── test_verify.py              # A6 verification script
│
├── agentic_campaign/               # Agent prompts and runbooks
│   ├── phase_1_parallel/
│   │   ├── CAMPAIGN_RUN.md
│   │   ├── A1_fetchers/PROMPT.md
│   │   ├── A2_cleaning/PROMPT.md
│   │   ├── A3_math/PROMPT.md
│   │   └── A4_db_writer/PROMPT.md
│   ├── phase_2_orchestrator/
│   │   ├── CAMPAIGN_RUN.md
│   │   └── A5_orchestrator/PROMPT.md
│   ├── phase_3_verify/
│   │   ├── CAMPAIGN_RUN.md
│   │   └── A6_verification/PROMPT.md
│   └── MASTER_RUNBOOK.md           # This file
│
├── theta_terminal/                 # Theta Terminal JAR + creds
├── dataingestion.md                # Master plan/spec
└── core_engine/THETA_API.md        # Engine architecture doc
```

---

## Data Flow

```
Theta Terminal v3 (Java, port 25510)
    │
    ▼ HTTP (aiohttp)
┌──────────────────────┐
│  core_engine/         │
│  AsyncThetaClient     │  ← retries, rate limiting, parsing
│  heartbeat()          │
└──────┬───────────────┘
       │ status, payload
       ▼
┌──────────────────────┐
│  A1: fetchers.py      │  ← 6 async functions, one per endpoint
│  fetch_greeks()       │     standard tier, 1m interval, ndjson
│  fetch_ohlc()         │
│  fetch_oi()  ...      │
└──────┬───────────────┘
       │ pd.DataFrame (COLUMNS.md §I)
       ▼
┌──────────────────────┐
│  A2: cleaning.py      │  ← pre-filter + 8 checks in order
│  clean_option_chain() │     returns (clean_df, quarantine_df)
└──────┬───────────────┘
       │ pd.DataFrame (COLUMNS.md §II)
       ▼
┌──────────────────────┐
│  A3: math.py          │  ← business time T, forward, Numba vega
│  compute_business_T() │     pandas_market_calendars for half-days
│  compute_forward()    │
│  compute_vega()       │
└──────┬───────────────┘
       │ pd.DataFrame (COLUMNS.md §III)
       ▼
┌──────────────────────┐
│  A4: db_writer.py     │  ← schema + COPY + two-phase load
│  init_schema()        │     hypertable, compression, watermark
│  write_staging_batch()│
│  load_from_staging()  │
│  advance_watermark()  │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  TimescaleDB          │
│  amd_surface_min      │  (hypertable, 7-day chunks)
│  amd_surface_quarant. │
│  ingest_progress      │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  A6: verify.py        │  ← 8 read-only integrity checks
│  run_verification()   │     chunk completeness, null coverage,
│                        │     T sanity, no leakage, IV smile
└──────────────────────┘

┌──────────────────────┐
│  A5: orchestrator.py  │  ← wires A1→A2→A3→A4 together
│  run_backfill()       │     dual semaphores (OPT=4, STK=2)
│                        │     caching, resume, error boundaries
└──────────────────────┘
```

---

## Verification Strategy

### Offline Tests (no subscription needed)

| Module | Test file | Strategy |
|--------|-----------|----------|
| fetchers.py | `test_fetchers.py` | Mock AsyncThetaClient, inject synthetic NDJSON |
| cleaning.py | `test_cleaning.py` | Synthetic DataFrames with known violations |
| math.py | `test_math.py` | Synthetic clean DataFrames, scipy as reference |
| db_writer.py | `test_db_writer.py` | Real PostgreSQL (if available), otherwise skip |
| orchestrator.py | `test_orchestrator.py` | Mock all downstream modules |
| verify.py | `test_verify.py` | Real PostgreSQL seeded with synthetic data |

### Live Test (requires subscription + terminal)

After all modules pass offline tests:
1. Start Theta Terminal with a Standard-tier subscription
2. Run orchestrator with a 3-day window: `run_backfill(start=date(2026,6,1), end=date(2026,6,3))`
3. Run verification: `run_verification(pool)`
4. Verify all checks pass on real data

---

## Quick Reference: Launching Agents

Each agent prompt is self-contained. To launch an agent:

1. Open the agent's `PROMPT.md` file
2. Give it to the agent (paste into Cursor chat)
3. The agent reads `dataingestion.md` and `dataingestion/COLUMNS.md` for reference
4. The agent builds the specified file
5. Run the corresponding `test_*.py` to verify
6. If tests fail, retry with specific error details

**Phase 0 is already complete.** Start with Phase 1.