# Phase 2 — CAMPAIGN RUN (Sequential — depends on Phase 1 completion)

**This phase runs AFTER all four Phase 1 agents (A1-A4) have delivered**
and their verification scripts pass.

## Dependency: Phase 1 Must Be Complete

Before launching A5, verify:

```bash
# All Phase 1 tests must pass
python -m pytest dataingestion/test_fetchers.py -v
python -m pytest dataingestion/test_cleaning.py -v
python -m pytest dataingestion/test_math.py -v
python -m pytest dataingestion/test_db_writer.py -v
```

All four files must exist:
- `dataingestion/fetchers.py`
- `dataingestion/cleaning.py`
- `dataingestion/math.py`
- `dataingestion/db_writer.py`

## Agent

| Agent | Prompt file | Builds | Verification |
|-------|-------------|--------|--------------|
| **A5** | `A5_orchestrator/PROMPT.md` | `dataingestion/orchestrator.py` | `dataingestion/test_orchestrator.py` |

## Execution Order

```
┌───────────────────────────────────────────────────┐
│  A5 (orchestrator)                                 │
│       │                                             │
│       ├── imports: fetchers.py (A1)                 │
│       ├── imports: cleaning.py (A2)                 │
│       ├── imports: math.py (A3)                     │
│       ├── imports: db_writer.py (A4)                │
│       └── imports: core_engine.shared.theta_client  │
│       │                                             │
│       ▼                                             │
│  orchestrator.py — the run-all backfill engine      │
└───────────────────────────────────────────────────┘
```

## What A5 Builds

`dataingestion/orchestrator.py` — the single entry point:

```python
async def run_backfill(
    start_date: dt.date = dt.date(2018, 1, 1),
    end_date: dt.date | None = None,
) -> dict:
```

This function:
1. Heartbeats the terminal
2. Inits schema
3. Fetches rates once, caches globally
4. Lists all AMD expirations
5. Loads watermark to resume from last completed chunk
6. Loops over `(expiration, date_chunk)` pairs, for each:
   - Acquires the correct semaphore (OPT_SEM=4 or STK_SEM=2)
   - Fetches greeks + OHLC + OI in parallel
   - Joins spot and OI onto the options DataFrame
   - Runs cleaning → math pipeline
   - Two-phase loads into DB
   - Advances watermark

### Critical: Two Semaphores

```python
OPT_SEM = asyncio.Semaphore(4)  # Standard tier: option endpoints
STK_SEM = asyncio.Semaphore(2)  # Value tier: stock OHLC, rates
```

## Verification

```bash
python -m pytest dataingestion/test_orchestrator.py -v
```

Tests verify:
- Heartbeat called first
- Chunks ≤ 30 days
- Watermark checked before each chunk
- Pipeline order (fetch → join → clean → math → load → watermark)
- Empty DataFrames skip cleanly
- DB errors don't crash
- Dual semaphore pattern present
- No raw HTTP in orchestrator

## Estimated Effort

| Difficulty | ~Lines | Why |
|-----------|--------|-----|
| High | ~350 | Integration point — must wire together A1-A4 correctly, handle caching, semaphore acquisition, resume logic, error boundaries, chunking. Most complex module by far. |