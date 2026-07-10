# Phase 3 — CAMPAIGN RUN (After Phase 2 has produced real data)

**This phase runs after the orchestrator (A5) has successfully ingested data**
into the TimescaleDB hypertable. Without real data in the DB, most checks
will return SKIP or FAIL with zero rows.

## Prerequisites

1. Phase 2 complete — `dataingestion/orchestrator.py` exists and passes tests
2. PostgreSQL + TimescaleDB running with `amd_surface_min` populated
3. At least one expiration's worth of data ingested (can be a test run)

## Agent

| Agent | Prompt file | Builds | Verification |
|-------|-------------|--------|--------------|
| **A6** | `A6_verification/PROMPT.md` | `dataingestion/verify.py` | `dataingestion/test_verify.py` |

## What A6 Builds

`dataingestion/verify.py` — a read-only integrity checker:

```python
async def run_verification(pool: asyncpg.Pool) -> dict:
```

Runs 8 checks (all SELECT queries, no writes):

| # | Check | What it validates |
|---|-------|-------------------|
| 1 | Chunk completeness | No gaps in `ingest_progress` vs actual data |
| 2 | Column coverage | spot_price, implied_vol, vega >= 99% non-null |
| 3 | Filter impact | Quarantine breakdown by reject_code |
| 4 | Business T sanity | T in [0, 1], monotonic |
| 5 | No future leakage | No future timestamps |
| 6 | eSSVI sanity | IV smile smooth, no jumps, positive |
| 7 | Data freshness | Oldest / newest timestamps |
| 8 | Row count consistency | Progress rows match DB rows |

Returns:
```python
{
    "status": "PASS" | "FAIL" | "WARN",
    "checks": [...],
    "summary": {...},
}
```

## Verification

```bash
# Requires DB_PASSWORD set and data in the database
python -m pytest dataingestion/test_verify.py -v
```

Tests verify:
- All 8 checks execute (even with empty tables)
- Known-injected issues are detected (null columns, out-of-range T, incomplete progress)
- No writes to DB during verification (row counts unchanged)
- Status correctly aggregates
- No imports from other dataingestion modules

## Post-Verification

After A6 passes, the pipeline is ready for the full backfill:

```bash
cd c:\Users\Rudol\Desktop\ThetaData_greeks
python -c "
import asyncio
from dataingestion.orchestrator import run_backfill
asyncio.run(run_backfill())
"
```

Then verify results:

```bash
python -c "
import asyncio
from dataingestion.verify import run_verification
from dataingestion.db_writer import get_pool
async def main():
    pool = await get_pool()
    result = await run_verification(pool)
    print(result['status'])
    for c in result['checks']:
        print(f\"  {c['name']}: {'PASS' if c['passed'] else 'FAIL'}\")
asyncio.run(main())
"
```