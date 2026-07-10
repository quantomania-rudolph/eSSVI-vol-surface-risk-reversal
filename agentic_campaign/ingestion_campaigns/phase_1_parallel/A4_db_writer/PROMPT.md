# A4 — Database Writer

**Role:** PostgreSQL/TimescaleDB engineer specializing in high-throughput time-series ingestion.

## Your Mission

Build `dataingestion/db_writer.py` — the **only** module that writes to the database. It handles schema creation, hypertable setup, compression policies, watermark tracking, and the two-phase load pattern (staging → hypertable) for idempotent ingestion.

**No Theta. No math. No cleaning. No HTTP.** Only `asyncpg` and SQL.

## What You Build

One file: `dataingestion/db_writer.py`

### Functions

```python
async def init_schema(pool: asyncpg.Pool) -> None:
    """Idempotent schema creation. Creates tables, hypertable, indexes, compression.
    Safe to call multiple times — uses IF NOT EXISTS / OR REPLACE.
    """

async def write_staging_batch(
    conn: asyncpg.Connection,
    df: pd.DataFrame,
    table_name: str = "amd_surface_min_staging",
) -> int:
    """COPY a DataFrame into a staging table. Returns row count written."""

async def load_from_staging(
    conn: asyncpg.Connection,
    run_id: int,
) -> int:
    """Atomic INSERT ... SELECT from staging into hypertable with ON CONFLICT DO NOTHING.
    Returns row count loaded.
    """

async def write_quarantine_batch(
    conn: asyncpg.Connection,
    df: pd.DataFrame,
    run_id: int,
) -> int:
    """Write quarantine rows to amd_surface_quarantine. Returns row count."""

async def advance_watermark(
    conn: asyncpg.Connection,
    underlying: str,
    expiration: date,
    chunk_end_date: date,
    status: str,
    rows: int,
    run_id: int,
) -> None:
    """Record a completed chunk in ingest_progress."""

async def get_completed_chunks(
    conn: asyncpg.Connection,
    underlying: str,
) -> set[tuple[str, date]]:
    """Query ingest_progress and return set of (expiration_iso, chunk_end_date) tuples
    that are already completed. Used by orchestrator to skip finished work."""

async def next_run_id(conn: asyncpg.Connection) -> int:
    """Get the next ingest_run_id from a sequence."""
```

### Schema (TimescaleDB Hypertable)

Create two tables + one watermark table:

```sql
-- 1. Main hypertable
CREATE TABLE IF NOT EXISTS amd_surface_min (
    ts              timestamptz     NOT NULL,
    underlying      text            NOT NULL,
    expiration      date            NOT NULL,
    strike          numeric(12,4)   NOT NULL,
    option_type     char(1)         NOT NULL,
    spot_price      double precision,
    forward_price   double precision,
    implied_vol     double precision,
    option_mid      double precision,
    spread          double precision,
    vega            double precision,
    bid             double precision,
    ask             double precision,
    delta           double precision,
    r               double precision,
    q               double precision,
    business_t      double precision,
    dte_calendar    int,
    log_moneyness   double precision,
    open_interest   int,
    quality_flags   int,
    ingest_run_id   bigint,
    _phase          text,
    UNIQUE (underlying, expiration, strike, option_type, ts)
);

SELECT create_hypertable('amd_surface_min', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE);

-- 2. Staging table (mirror of main, no hypertable)
CREATE TABLE IF NOT EXISTS amd_surface_min_staging (
    LIKE amd_surface_min INCLUDING ALL
);
-- No UNIQUE constraint on staging (data loaded, then validated, then moved)

-- 3. Quarantine table
CREATE TABLE IF NOT EXISTS amd_surface_quarantine (
    LIKE amd_surface_min,
    reject_code     text,
    reject_detail   text,
    ingested_at     timestamptz DEFAULT NOW()
);

-- 4. Watermark / progress tracker
CREATE TABLE IF NOT EXISTS ingest_progress (
    underlying      text        NOT NULL,
    expiration      date        NOT NULL,
    chunk_end_date  date        NOT NULL,
    status          text        NOT NULL,  -- 'completed', 'failed'
    rows_loaded     int         DEFAULT 0,
    rows_quarantined int        DEFAULT 0,
    run_id          bigint,
    started_at      timestamptz DEFAULT NOW(),
    completed_at    timestamptz,
    PRIMARY KEY (underlying, expiration, chunk_end_date)
);

-- 5. Run ID sequence
CREATE SEQUENCE IF NOT EXISTS ingest_run_id_seq;
```

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_amd_surface_contract
    ON amd_surface_min (underlying, expiration, ts, strike);

CREATE INDEX IF NOT EXISTS idx_amd_surface_surface_fit
    ON amd_surface_min (underlying, expiration, ts, strike);

CREATE INDEX IF NOT EXISTS idx_amd_quarantine_code
    ON amd_surface_quarantine (reject_code);
```

### Compression Policy

```sql
ALTER TABLE amd_surface_min SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'underlying, expiration, strike, option_type',
    timescaledb.compress_orderby = 'ts DESC'
);

SELECT add_compression_policy('amd_surface_min', INTERVAL '7 days',
    if_not_exists => TRUE);
```

### Two-Phase Load Pattern

This is the critical pattern for idempotent ingestion (Section 13):

```python
async def load_chunk(pool, conn, df_clean, df_quarantine, run_id, underlying, expiration, chunk_end):
    # Phase 1: Load into staging
    await write_staging_batch(conn, df_clean)
    
    # Phase 2: Validate + move to hypertable
    staging_count = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_min_staging")
    if staging_count != len(df_clean):
        raise ValueError(f"Staging count mismatch: {staging_count} != {len(df_clean)}")
    
    loaded = await load_from_staging(conn, run_id)
    
    # Write quarantine
    quar_count = await write_quarantine_batch(conn, df_quarantine, run_id)
    
    # Advance watermark
    await advance_watermark(conn, "AMD", expiration, chunk_end, "completed",
                            loaded, run_id)
    
    return loaded, quar_count
```

### Column Mapping

From `dataingestion/COLUMNS.md` Section IV — exact mapping:

```python
COLUMN_MAP = {
    "timestamp":       "ts",
    "underlying":      "underlying",
    "expiration":      "expiration",
    "strike":          "strike",
    "option_type":     "option_type",
    "spot_close":      "spot_price",
    "forward_price":   "forward_price",
    "implied_vol":     "implied_vol",
    "mid_price":       "option_mid",
    "spread":          "spread",
    "vega":            "vega",
    "bid":             "bid",
    "ask":             "ask",
    "delta":           "delta",
    "r":               "r",
    "q":               "q",
    "business_t":      "business_t",
    "dte_calendar":    "dte_calendar",
    "log_moneyness":   "log_moneyness",
    "open_interest":   "open_interest",
    "quality_flags":   "quality_flags",
    "ingest_run_id":   "ingest_run_id",
    "_phase":          "_phase",
}
```

### COPY-Based Bulk Insert

Use `COPY` (binary via DataFrame) not row-by-row INSERT:

```python
async def write_staging_batch(conn, df, table_name="amd_surface_min_staging"):
    # Map columns from dframe names to db names
    db_df = df.rename(columns=COLUMN_MAP)
    # Keep only columns that exist in the staging table
    db_cols = [c for c in db_df.columns if c in COLUMN_MAP.values()]
    db_df = db_df[db_cols]
    
    # COPY via StringIO
    output = io.StringIO()
    db_df.to_csv(output, sep="\t", header=False, index=False, na_rep="\\N")
    output.seek(0)
    result = await conn.copy_to_table(table_name, source=output, columns=db_cols)
    return len(db_df)
```

### Invariants — NEVER Violate

1. **No Theta, no HTTP.** This is a database module only.
2. **No math, no cleaning.** Input DataFrames are already processed.
3. **No pandas in-memory grouping/transformation.** Only reshape for COPY.
4. **Use COPY, not INSERT.** COPY is order-of-magnitude faster.
5. **Two-phase load always.** Never INSERT directly into the hypertable.
6. **Watermark BEFORE committing.** The transaction commits when the connection context exits.
7. **ON CONFLICT DO NOTHING** on the hypertable INSERT for idempotency.
8. **Schema init must be idempotent** — safe to call at startup.
9. **Never truncate the staging table manually.** Let the two-phase load's transaction scope handle it (or explicitly TRUNCATE in the staging write function).
10. **Always pass `run_id` through** to maintain provenance.

### Key Reference Files

- `dataingestion.md` Sections 11, 12, 13 — **schema, indexes, compression, idempotency, watermark**
- `dataingestion/COLUMNS.md` Section IV — **exact column mapping and UNIQUE constraint**
- `core_engine/shared/config.py` — DB connection settings (may be taken from env)

### Verification Script

```bash
cd c:\Users\Rudol\Desktop\ThetaData_greeks
python -m pytest dataingestion/test_db_writer.py -v
```

The verification script will:
1. Start a local PostgreSQL instance (or use a test fixture).
2. Call `init_schema()` and verify all tables/indexes/compression exist.
3. Write synthetic data via `write_staging_batch` + `load_from_staging`.
4. Verify the hypertable has the written rows with correct column mapping.
5. Write quarantine data and verify reject codes.
6. Advance and query watermark, verify completed chunks are tracked.
7. Test idempotency: write same PK twice, verify no duplicates.
8. Verify `ON CONFLICT DO NOTHING` behavior.
9. Test `get_completed_chunks` for resume logic.

**Do not write the verification script.** It lives at `dataingestion/test_db_writer.py`.

### Common Mistakes to Avoid

- Using row-by-row INSERT instead of COPY.
- Forgetting to map DataFrame columns to DB column names.
- Not handling NaN → SQL NULL correctly (COPY needs `\\N`).
- Creating the hypertable before the table exists.
- Missing the staging table in schema init.
- Not including `ts` in the UNIQUE constraint (TimescaleDB requires it).
- Forgetting compression policy setup.
- Not making schema init idempotent.