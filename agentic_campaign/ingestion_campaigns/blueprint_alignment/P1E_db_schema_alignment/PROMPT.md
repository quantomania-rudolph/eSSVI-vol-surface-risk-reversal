# W1E — DB Schema Alignment & Ingestion Control

## Persona
You are a **TimescaleDB Schema Architect** who designs hypertables for high-frequency options data. You know that every redundant column slows inserts, every missing column creates downstream workarounds, and every implicit constraint is a future bug. You enforce schema alignment between the blueprint and the implementation.

## Blueprint Vision
Read `dataingestion.md` Sections 10, 11, 13 completely:
- **Section 10:** Schema: natural key `(ts, underlying, expiration, strike, option_type)`. Recommended columns include `underlying_timestamp`, `quality_flags`, `ingest_run_id`. Types: `timestamptz`, `date`, `numeric(12,4)`, `char(1)`, `double precision`.
- **Section 11:** TimescaleDB layout: hypertable on `ts`, `chunk_interval = 7 days`. Unique index includes `ts`. Secondary index for surface fit.
- **Section 13:** Ingestion control: explicit `ON CONFLICT (underlying,expiration,strike,option_type,ts) DO NOTHING`. Watermark PK includes `run_id`. Two-phase load: staging table WITHOUT unique constraints. `ChunkResult` with clear skip semantics.

## Core Objective
Align the DB schema and ingestion control with the blueprint:

1. **Remove `_phase` from final hypertable** — it's internal pipeline tracking, not data consumers need
2. **Add `underlying_timestamp` column** — for spot-alignment auditability
3. **Explicit ON CONFLICT** — specify constraint columns explicitly (not implicit)
4. **Watermark PK includes `run_id`** — so multiple runs don't overwrite each other's status
5. **Staging table WITHOUT unique constraints** — `LIKE ... INCLUDING DEFAULTS` (not `INCLUDING ALL`)
6. **Enhance `ChunkResult` skip semantics** — differentiate "empty data" from "already completed"

## Errors to Fix

### High #12: `_phase` Tracking Incomplete — Column in DB Schema but Not Recommended
**File:** `db_writer.py:58, 130`  
**Expected:** `_phase` is internal; not in the final hypertable.  
**Actual:** `_phase` column in `COLUMN_MAP` and DDL. This leaks internal pipeline detail.  
**Fix:** Remove `_phase` from `COLUMN_MAP` and the `CREATE TABLE` DDL. It can remain on DataFrames in-memory for debugging.

### High #24: Missing `underlying_timestamp` Column (Cross-Check)
**File:** `db_writer.py:107-131`  
**Expected:** `underlying_timestamp timestamptz` column for spot-alignment audit.  
**Actual:** Column not in schema.  
**Fix:** Add `underlying_timestamp timestamptz` to table DDL and `COLUMN_MAP`. Ensure orchestrator populates it (from `greeks/first_order.underlying_timestamp`).

### High #25: `ON CONFLICT DO NOTHING` Without Specifying Constraint
**File:** `db_writer.py:243`  
**Expected:** `ON CONFLICT (underlying, expiration, strike, option_type, ts) DO NOTHING`.  
**Actual:** `ON CONFLICT DO NOTHING` (implicit, uses whatever unique index exists). Works but fragile — if index order changes, behavior changes.  
**Fix:** Explicitly list constraint columns.

### High #26: Watermark PK Missing `run_id` — Can't Track Multiple Runs
**File:** `db_writer.py:157-168`  
**Expected:** PK = `(underlying, expiration, chunk_end_date, run_id)`.  
**Actual:** PK = `(underlying, expiration, chunk_end_date)`. Multiple runs' statuses overwrite each other.  
**Fix:** Add `run_id` to watermark PK. Update the `ON CONFLICT` clause in watermark upsert.

### High #27: Staging Table `LIKE INCLUDING ALL` Copies Unique Constraint
**File:** `db_writer.py:142-145`  
**Expected:** Staging table should not have unique constraints (to allow COPY to work without conflict on duplicate data within batch).  
**Actual:** `LIKE amd_surface_min INCLUDING ALL` copies the unique index → COPY may fail on intra-batch duplicates.  
**Fix:** Change to `LIKE amd_surface_min INCLUDING DEFAULTS` (no indexes, no constraints).

### High #32: `ChunkResult` Missing `skipped` Semantics for Empty Data vs Already Done
**File:** `orchestrator.py:75-81`  
**Expected:** Different skip reasons for "no data" vs "already completed".  
**Actual:** `skipped=True` used for both.  
**Fix:** Add `skip_reason: str = ""` field to `ChunkResult` dataclass. Use `skip_reason = "no_data"` for empty fetch, `skip_reason = "already_completed"` for watermark check.

## Invariants (MUST HOLD)
1. **Migration scripts** — schema changes must include `ALTER TABLE` or be safe for existing tables (use `IF NOT EXISTS`)
2. **No data loss** — removing `_phase` column: no existing queries depend on it (it's debugging data)
3. **Backward compatible** — all tests pass after changes
4. **Staging table change** — `LIKE INCLUDING DEFAULTS` only; ensure no regression on staging table functionality

## Success Criteria
- `_phase` not in `COLUMN_MAP` and not in `CREATE TABLE` DDL
- `underlying_timestamp` in `COLUMN_MAP` and DDL
- `ON CONFLICT (underlying, expiration, strike, option_type, ts) DO NOTHING` in the INSERT
- Watermark table PK includes `run_id`
- Staging table uses `LIKE ... INCLUDING DEFAULTS`
- `ChunkResult` has `skip_reason: str = ""` with descriptive values
- All tests pass

## Short Specialized Verification
```python
# 1) _phase not in COLUMN_MAP
from dataingestion.db_writer import COLUMN_MAP
assert "_phase" not in COLUMN_MAP, "_phase should not be in COLUMN_MAP"

# 2) underlying_timestamp in COLUMN_MAP
assert "underlying_timestamp" in COLUMN_MAP or "underlying_timestamp" in open("dataingestion/db_writer.py").read()

# 3) Explicit ON CONFLICT
src = open("dataingestion/db_writer.py").read()
assert "ON CONFLICT (underlying, expiration, strike, option_type, ts) DO NOTHING" in src

# 4) Staging table no constraints
assert "INCLUDING DEFAULTS" in src

# 5) ChunkResult has skip_reason
src_orch = open("dataingestion/orchestrator.py").read()
assert "skip_reason" in src_orch

# 6) Tests
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/db_writer.py` (COLUMN_MAP, DDL, staging table, ON CONFLICT, watermark PK)
- `dataingestion/orchestrator.py` (ChunkResult dataclass, populate skip_reason)
- `dataingestion/cleaning.py` (if _phase removal affects clean_option_chain output)