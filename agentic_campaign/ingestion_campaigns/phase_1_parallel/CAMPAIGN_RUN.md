# Phase 1 — CAMPAIGN RUN ✓✓✓ PARALLEL ✓✓✓

**All four agents in this phase can and SHOULD be run simultaneously.**
They have zero inter-dependencies at the code level. The column contract
(`dataingestion/COLUMNS.md`) is the shared interface.

## Execution Order

```
┌─────────────────────────────────────────────────────────┐
│  Launch all four simultaneously:                         │
│                                                          │
│  A1 (fetchers)    A2 (cleaning)    A3 (math)   A4 (DB)  │
│       │                 │               │         │      │
│       ▼                 ▼               ▼         ▼      │
│  fetchers.py       cleaning.py      math.py   db_writer.py│
│       │                 │               │         │      │
│       └────────┬────────┴───────┬───────┘         │      │
│                │                │                  │      │
│          All four are independent. They share only │      │
│          the column contract in COLUMNS.md.        │      │
└─────────────────────────────────────────────────────────┘
```

## Agent Prompts and Verification

| Agent | Prompt file | Builds | Verification script |
|-------|-------------|--------|---------------------|
| **A1** | `A1_fetchers/PROMPT.md` | `dataingestion/fetchers.py` | `dataingestion/test_fetchers.py` |
| **A2** | `A2_cleaning/PROMPT.md` | `dataingestion/cleaning.py` | `dataingestion/test_cleaning.py` |
| **A3** | `A3_math/PROMPT.md` | `dataingestion/math.py` | `dataingestion/test_math.py` |
| **A4** | `A4_db_writer/PROMPT.md` | `dataingestion/db_writer.py` | `dataingestion/test_db_writer.py` |

## How to Launch

### Option A: Four separate Cursor sessions (recommended)

Open four Cursor windows and paste each agent's PROMPT.md. Each agent works independently.

### Option B: Sequential in one session

Feed each PROMPT.md in order. The order doesn't matter since they're independent,
but A1 → A2 → A3 → A4 matches the data flow for readability.

## Column Contract (shared interface)

**Every agent MUST read and adhere to `dataingestion/COLUMNS.md`.**

Key interfaces:

| Module | Input columns | Output columns |
|--------|---------------|----------------|
| fetchers.py | N/A (raw Theta response) | `COLUMNS.md` §I |
| cleaning.py | `COLUMNS.md` §I | `COLUMNS.md` §II |
| math.py | `COLUMNS.md` §II | `COLUMNS.md` §III |
| db_writer.py | `COLUMNS.md` §III | N/A (writes to DB per §IV) |

## Dependency Rules (enforced by verification tests)

| Module | May import from | May NOT import from |
|--------|----------------|---------------------|
| fetchers.py | `core_engine.shared.*`, `pandas` | `dataingestion.*`, `asyncpg` |
| cleaning.py | `pandas`, `numpy` | `dataingestion.*`, `core_engine.*`, `asyncpg` |
| math.py | `pandas`, `numpy`, `numba`, `pandas_market_calendars` | `dataingestion.*`, `core_engine.*`, `asyncpg` |
| db_writer.py | `asyncpg`, `pandas` | `dataingestion.fetchers`, `dataingestion.cleaning`, `dataingestion.math`, `core_engine.shared.theta_client` |

## Verification Process

After each agent completes their file, run their test suite:

```bash
# From project root (ThetaData_greeks)
cd c:\Users\Rudol\Desktop\ThetaData_greeks

# A1 - Fetchers (no DB needed, fully mocked)
python -m pytest dataingestion/test_fetchers.py -v

# A2 - Cleaning (no DB needed, synthetic data)
python -m pytest dataingestion/test_cleaning.py -v

# A3 - Math (no DB needed, synthetic data + scipy reference)
python -m pytest dataingestion/test_math.py -v

# A4 - DB Writer (requires PostgreSQL + TimescaleDB)
python -m pytest dataingestion/test_db_writer.py -v
```

### What "Passing" Means for Each

| Agent | Passing criteria |
|-------|-----------------|
| A1 | All 6 functions return correct columns. No semaphore/DB/HTTP imports. Empty on error. Correct params sent to client. |
| A2 | All 8 checks catch violations with correct codes. Row accounting (clean + quar = input). Output columns added. Belly spread flagged not quarantined. |
| A3 | business_t in years, positive, monotonic. forward > S for r > 0. vega matches scipy to 1e-6. vega non-negative, ATM > OTM. Guards work. |
| A4 | Tables/indexes/hypertable/compression created idempotently. Two-phase load writes correct rows. Column mapping correct. Watermark tracks progress. ON CONFLICT DO NOTHING works. |

## Estimated Effort

| Agent | Difficulty | ~Lines | Why |
|-------|-----------|--------|-----|
| A1 | Medium | ~200 | 6 async functions, all similar shape, heavy on parameter correctness |
| A2 | High | ~250 | 8 checks + pre-filter in exact order, row accounting, cross-section monotonicity is the hardest |
| A3 | Medium | ~150 | Business time is the trickiest part; Numba vega is straightforward |
| A4 | Medium | ~250 | Schema SQL + COPY logic + two-phase load + watermark; lots of boilerplate but clear patterns |