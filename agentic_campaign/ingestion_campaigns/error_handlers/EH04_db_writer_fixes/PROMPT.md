# EH-04: DB Writer Fixes

## Persona

You are a **database engineer** specializing in TimescaleDB, asyncpg, and high-throughput time-series ingestion. You understand COPY protocol, hypertable partitioning, compression policies, and idempotent upsert patterns.

## Mission

**Fix the identified issues in `dataingestion/db_writer.py` — primarily the duplicate `get_pool` definition and column metadata caching.**

## Current State Analysis

**File:** `dataingestion/db_writer.py` (347 lines)

**Issues to Fix:**

### 1. Duplicate `get_pool` Definition (Lines 20-33 and 336-347)
```python
# Line 20-33: Uses os.getenv()
async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5432")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
        database=os.getenv("PGDATABASE", "postgres"),
        min_size=1,
        max_size=10,
    )

# Line 336-347: Uses core_engine.shared.config.CFG (different!)
async def get_pool() -> asyncpg.Pool:
    from core_engine.shared.config import CFG
    return await asyncpg.create_pool(
        host=CFG.THETA_HOST,  # WRONG - this is Theta host, not PG host!
        port=5432,
        user="postgres",
        password="postgres",
        database="thetadata",
        min_size=2,
        max_size=10,
    )
```
**Problem:** Second definition overwrites first; uses Theta host for Postgres; hardcoded credentials.

### 2. Column Metadata Queries Per Call (Lines 62-72)
```python
async def _get_table_columns(conn, table_name) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1...",
        table_name,
    )
```
Called on every `write_staging_batch`, `load_from_staging`, `write_quarantine_batch`.

### 3. `copy_from` BytesIO Encoding (Lines 215-216)
```python
data_bytes = buf.getvalue().encode("utf-8")
await conn.copy_from(io.BytesIO(data_bytes), query=copy_sql)
```
Works but `asyncpg.copy_from` accepts string iterables directly — can avoid encoding step.

### 4. Config Integration
Pool parameters should come from `dataingestion.config`.

## Required Changes

### 1. Single `get_pool` Using Config
```python
from dataingestion.config import PG_CONFIG

async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(**PG_CONFIG)
```

### 2. Column Metadata Caching
```python
_TABLE_COLUMNS_CACHE: dict[str, list[str]] = {}

async def _get_table_columns(conn: asyncpg.Connection, table_name: str) -> list[str]:
    if table_name in _TABLE_COLUMNS_CACHE:
        return _TABLE_COLUMNS_CACHE[table_name]
    
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1 AND table_schema = 'public' ORDER BY ordinal_position",
        table_name,
    )
    cols = [r["column_name"] for r in rows]
    _TABLE_COLUMNS_CACHE[table_name] = cols
    return cols
```

### 3. Optimize `copy_from` (Optional)
```python
# Instead of BytesIO encoding, use StringIO directly with copy_from
buf.seek(0)
await conn.copy_from(buf, query=copy_sql)  # asyncpg accepts text file objects
```

### 4. Add Config Constants
```python
# In dataingestion/config.py (EH-06)
PG_CONFIG = {
    "host": os.getenv("PGHOST", "127.0.0.1"),
    "port": int(os.getenv("PGPORT", "5432")),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "postgres"),
    "database": os.getenv("PGDATABASE", "postgres"),
    "min_size": 1,
    "max_size": 10,
}
```

## Invariants (Must Preserve)

- ✅ Idempotent schema creation (tables, hypertable, indexes, compression)
- ✅ Two-phase load: COPY → staging → INSERT SELECT ON CONFLICT DO NOTHING → truncate
- ✅ Column mapping per COLUMNS.md §IV (COLUMN_MAP)
- ✅ Watermark tracking with ON CONFLICT DO UPDATE
- ✅ Quarantine writes with reject_code, reject_detail
- ✅ No Theta imports, no math, no cleaning
- ✅ All existing tests pass

## Acceptance Criteria

### Functional
1. Single `get_pool` function using config
2. Column metadata cached per table name
3. Pool config from environment via config module
4. All existing tests pass

### Testing
```bash
python -m pytest dataingestion/test_db_writer.py -v    # all 11 tests pass (when DB available)
```

## Deliverables

1. **Modified** `dataingestion/db_writer.py` with fixes
2. **Verification** all tests pass (with test DB)