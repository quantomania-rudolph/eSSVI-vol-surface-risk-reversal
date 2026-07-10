"""Database writer for the AMD eSSVI data ingestion pipeline.

Handles schema creation, hypertable setup, compression policies,
watermark tracking, and the two-phase load pattern (staging → hypertable)
for idempotent ingestion. Only module that writes to the database.
No Theta, no math, no cleaning — only asyncpg and SQL.
"""

from __future__ import annotations

import asyncio
import io
import os
from datetime import date

import asyncpg
import pandas as pd

# Pool factory - callers should use this to get a pool
async def get_pool() -> asyncpg.Pool:
    """Get a connection pool to TimescaleDB.
    
    Uses environment variables for connection params.
    """
    return await asyncpg.create_pool(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5432")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
        database=os.getenv("PGDATABASE", "postgres"),
        min_size=1,
        max_size=10,
    )

COLUMN_MAP = {
    "timestamp": "ts",
    "underlying": "underlying",
    "expiration": "expiration",
    "strike": "strike",
    "option_type": "option_type",
    "spot_close": "spot_price",
    "forward_price": "forward_price",
    "implied_vol": "implied_vol",
    "mid_price": "option_mid",
    "spread": "spread",
    "vega": "vega",
    "bid": "bid",
    "ask": "ask",
    "delta": "delta",
    "r": "r",
    "q": "q",
    "business_t": "business_t",
    "dte_calendar": "dte_calendar",
    "log_moneyness": "log_moneyness",
    "open_interest": "open_interest",
    "quality_flags": "quality_flags",
    "ingest_run_id": "ingest_run_id",
    "underlying_timestamp": "underlying_timestamp",
    "session_phase": "session_phase",
    "parity_skew": "parity_skew",
    "anchor_k_star": "anchor_k_star",
    "anchor_theta_star": "anchor_theta_star",
    "anchor_quality": "anchor_quality",
    "slice_strike_count": "slice_strike_count",
}


async def _get_table_columns(
    conn: asyncpg.Connection, table_name: str
) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name "
        "FROM information_schema.columns "
        "WHERE table_name = $1 AND table_schema = 'public' "
        "ORDER BY ordinal_position",
        table_name,
    )
    return [r["column_name"] for r in rows]


def _prepare_copy(
    df: pd.DataFrame, db_columns: list[str]
) -> tuple[io.StringIO, list[str]]:
    """Map df columns → DB names, keep only columns present in db_columns.

    Returns (buffer positioned at start, ordered list of DB column names).
    """
    mapped: dict[str, str] = {}
    for df_col in df.columns:
        db_col = COLUMN_MAP.get(df_col, df_col)
        if db_col in db_columns:
            mapped[df_col] = db_col

    ordered_db = [c for c in db_columns if c in mapped.values()]
    reverse = {v: k for k, v in mapped.items()}
    ordered_df_cols = [reverse[c] for c in ordered_db]

    sub = df[ordered_df_cols].copy()
    sub.columns = ordered_db

    buf = io.StringIO()
    sub.to_csv(buf, sep="\t", header=False, index=False, na_rep="\\N")
    buf.seek(0)
    return buf, ordered_db


async def init_schema(pool: asyncpg.Pool) -> None:
    """Idempotent schema creation. Creates tables, hypertable, indexes, compression.
    Safe to call multiple times — uses IF NOT EXISTS / OR REPLACE.
    """
    async with pool.acquire() as conn:
        await conn.execute("""
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
                underlying_timestamp timestamptz,
                parity_skew     double precision,       -- put-call IV parity diagnostic
                anchor_k_star   double precision,       -- anchor log-moneyness
                anchor_theta_star double precision,     -- anchor total variance
                anchor_quality  text,                   -- EXACT_ATM|NEAREST_BELLY|WIDENED_GATES|NEAREST_ANY|DROP_SLICE
                session_phase   text,                   -- pre_open|rth|no_trade_open|...
                slice_strike_count int,                 -- belly-qualifying strikes per slice
                UNIQUE (underlying, expiration, strike, option_type, ts)
            )
        """)

        await conn.execute("""
            SELECT create_hypertable('amd_surface_min', 'ts',
                chunk_time_interval => INTERVAL '7 days',
                if_not_exists => TRUE)
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS amd_surface_min_staging (
                LIKE amd_surface_min INCLUDING DEFAULTS
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS amd_surface_quarantine (
                LIKE amd_surface_min,
                reject_code     text,
                reject_detail   text,
                ingested_at     timestamptz DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS dividends (
                symbol          text        NOT NULL,
                ex_date         date        NOT NULL,
                cash_amount     double precision NOT NULL,
                announced_date  date        NOT NULL,
                created_at      timestamptz DEFAULT NOW(),
                PRIMARY KEY (symbol, ex_date)
            )
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dividends_symbol_announced
                ON dividends (symbol, announced_date)
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ingest_progress (
                underlying      text        NOT NULL,
                expiration      date        NOT NULL,
                chunk_end_date  date        NOT NULL,
                status          text        NOT NULL,
                rows_loaded     int         DEFAULT 0,
                rows_quarantined int        DEFAULT 0,
                run_id          bigint,
                started_at      timestamptz DEFAULT NOW(),
                completed_at    timestamptz,
                PRIMARY KEY (underlying, expiration, chunk_end_date, run_id)
            )
        """)

        await conn.execute("CREATE SEQUENCE IF NOT EXISTS ingest_run_id_seq")

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_amd_surface_fit
                ON amd_surface_min (underlying, expiration, ts, strike)
        """)

        # Idempotent column adds for existing deployments
        for ddl in (
            "ALTER TABLE amd_surface_min ADD COLUMN IF NOT EXISTS parity_skew double precision",
            "ALTER TABLE amd_surface_min ADD COLUMN IF NOT EXISTS anchor_k_star double precision",
            "ALTER TABLE amd_surface_min ADD COLUMN IF NOT EXISTS anchor_theta_star double precision",
            "ALTER TABLE amd_surface_min ADD COLUMN IF NOT EXISTS anchor_quality text",
            "ALTER TABLE amd_surface_min ADD COLUMN IF NOT EXISTS session_phase text",
            "ALTER TABLE amd_surface_min ADD COLUMN IF NOT EXISTS slice_strike_count int",
            "ALTER TABLE amd_surface_min_staging ADD COLUMN IF NOT EXISTS parity_skew double precision",
            "ALTER TABLE amd_surface_min_staging ADD COLUMN IF NOT EXISTS anchor_k_star double precision",
            "ALTER TABLE amd_surface_min_staging ADD COLUMN IF NOT EXISTS anchor_theta_star double precision",
            "ALTER TABLE amd_surface_min_staging ADD COLUMN IF NOT EXISTS anchor_quality text",
            "ALTER TABLE amd_surface_min_staging ADD COLUMN IF NOT EXISTS session_phase text",
            "ALTER TABLE amd_surface_min_staging ADD COLUMN IF NOT EXISTS slice_strike_count int",
        ):
            await conn.execute(ddl)

        # TimescaleDB compression
        await conn.execute("""
            ALTER TABLE amd_surface_min SET (
                timescaledb.compress,
                timescaledb.compress_segmentby =
                    'underlying, expiration, strike, option_type',
                timescaledb.compress_orderby = 'ts DESC'
            )
        """)

        await conn.execute("""
            SELECT add_compression_policy('amd_surface_min', INTERVAL '7 days',
                if_not_exists => TRUE)
        """)



async def write_staging_batch(
    conn: asyncpg.Connection,
    df: pd.DataFrame,
    table_name: str = "amd_surface_min_staging",
) -> int:
    """COPY a DataFrame into a staging table. Returns row count written."""
    if df.empty:
        return 0

    db_columns = await _get_table_columns(conn, table_name)
    buf, cols = _prepare_copy(df, db_columns)

    if not cols:
        return 0

    col_list = ", ".join(f'"{c}"' for c in cols)
    copy_sql = (
        f"COPY {table_name} ({col_list}) "
        f"FROM STDIN WITH (FORMAT TEXT, DELIMITER E'\\t', NULL '\\N')"
    )

    data_bytes = buf.getvalue().encode("utf-8")
    await conn.copy_from(io.BytesIO(data_bytes), query=copy_sql)
    return len(df)


async def load_from_staging(
    conn: asyncpg.Connection,
    run_id: int,
) -> int:
    """Atomic INSERT ... SELECT from staging into hypertable with ON CONFLICT DO NOTHING.
    Returns row count loaded. Truncates staging after successful load.
    """
    columns = await _get_table_columns(conn, "amd_surface_min")

    select_parts: list[str] = []
    for col in columns:
        if col == "ingest_run_id":
            select_parts.append("$1::bigint")
        else:
            select_parts.append(f'"{col}"')

    col_identifiers = ", ".join(f'"{c}"' for c in columns)
    sql = (
        f"INSERT INTO amd_surface_min ({col_identifiers}) "
        f"SELECT {', '.join(select_parts)} "
        f"FROM amd_surface_min_staging "
        f"ON CONFLICT (underlying, expiration, strike, option_type, ts) DO NOTHING"
    )

    status = await conn.execute(sql, run_id)
    count = int(status.split()[-1])

    await conn.execute("TRUNCATE TABLE amd_surface_min_staging")
    return count


async def write_quarantine_batch(
    conn: asyncpg.Connection,
    df: pd.DataFrame,
    run_id: int,
) -> int:
    """Write quarantine rows to amd_surface_quarantine. Returns row count."""
    if df.empty:
        return 0

    df = df.copy()

    db_columns = await _get_table_columns(conn, "amd_surface_quarantine")
    buf, cols = _prepare_copy(df, db_columns)

    if not cols:
        return 0

    col_list = ", ".join(f'"{c}"' for c in cols)
    copy_sql = (
        f"COPY amd_surface_quarantine ({col_list}) "
        f"FROM STDIN WITH (FORMAT TEXT, DELIMITER E'\\t', NULL '\\N')"
    )

    data_bytes = buf.getvalue().encode("utf-8")
    await conn.copy_from(io.BytesIO(data_bytes), query=copy_sql)
    return len(df)


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
    await conn.execute(
        """
        INSERT INTO ingest_progress
            (underlying, expiration, chunk_end_date, status, rows_loaded, run_id, completed_at)
        VALUES ($1, $2, $3, $4, $5, $6, NOW())
        ON CONFLICT (underlying, expiration, chunk_end_date, run_id)
        DO UPDATE SET status        = EXCLUDED.status,
                      rows_loaded   = EXCLUDED.rows_loaded,
                      completed_at  = EXCLUDED.completed_at
        """,
        underlying,
        expiration,
        chunk_end_date,
        status,
        rows,
        run_id,
    )


async def get_completed_chunks(
    conn: asyncpg.Connection,
    underlying: str,
) -> set[tuple[str, date]]:
    """Query ingest_progress and return set of (expiration_iso, chunk_end_date) tuples
    that are already completed.
    """
    rows = await conn.fetch(
        "SELECT expiration, chunk_end_date "
        "FROM ingest_progress "
        "WHERE underlying = $1 AND status = 'completed'",
        underlying,
    )
    return {
        (r["expiration"].isoformat(), r["chunk_end_date"])
        for r in rows
    }


async def next_run_id(conn: asyncpg.Connection) -> int:
    """Get the next ingest_run_id from a sequence."""
    return await conn.fetchval("SELECT nextval('ingest_run_id_seq')")


# ============================================================================
# COMPRESSION CONTROL (for backfill optimization)
# ============================================================================

async def disable_compression(pool: asyncpg.Pool) -> None:
    """Disable TimescaleDB compression on the main hypertable for faster bulk loading."""
    async with pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE amd_surface_min SET (timescaledb.compress = FALSE)
        """)
        # Also remove the compression policy temporarily
        await conn.execute("""
            SELECT remove_compression_policy('amd_surface_min', if_exists => TRUE)
        """)


async def enable_compression(pool: asyncpg.Pool) -> None:
    """Re-enable TimescaleDB compression and add compression policy after backfill."""
    async with pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE amd_surface_min SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'underlying, expiration, strike, option_type',
                timescaledb.compress_orderby = 'ts DESC'
            )
        """)
        await conn.execute("""
            SELECT add_compression_policy('amd_surface_min', INTERVAL '7 days',
                if_not_exists => TRUE)
        """)