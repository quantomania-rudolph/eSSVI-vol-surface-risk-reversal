"""Verification script for dataingestion/db_writer.py (Agent A4).

Requires a running PostgreSQL with TimescaleDB accessible at the
environment variables DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD.

If no DB is available, tests are skipped with a clear message.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Check if PostgreSQL is configured
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "data_foundation")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

HAS_DB = bool(DB_PASSWORD)

pytestmark = pytest.mark.skipif(
    not HAS_DB,
    reason="PostgreSQL not configured. Set DB_PASSWORD env var to run DB tests.",
)


def _import_module():
    from dataingestion import db_writer as dmod

    return dmod


# -----------------------------------------------------------------------
# Synthetic math DataFrame (COLUMNS.md Section III)
# -----------------------------------------------------------------------

def _math_df(n: int = 10) -> pd.DataFrame:
    """Synthetic DataFrame matching math output (ready for DB)."""
    base_ts = pd.Timestamp("2026-06-15 10:30:00", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": pd.DatetimeIndex(
                [base_ts + dt.timedelta(minutes=i) for i in range(n)]
            ),
            "underlying": ["AMD"] * n,
            "expiration": pd.Timestamp("2026-07-21"),
            "strike": [150.0 + i * 2 for i in range(n)],
            "option_type": ["C" if i % 2 == 0 else "P" for i in range(n)],
            "spot_close": [158.0] * n,
            "forward_price": [158.2] * n,
            "implied_vol": [0.25] * n,
            "mid_price": [1.1] * n,
            "spread": [0.2] * n,
            "vega": [0.15] * n,
            "bid": [1.0] * n,
            "ask": [1.2] * n,
            "delta": [0.5] * n,
            "r": [0.045] * n,
            "q": [0.0] * n,
            "business_t": [0.1] * n,
            "dte_calendar": [36] * n,
            "log_moneyness": [np.log(k / 158.2) for k in range(150, 150 + n * 2, 2)],
            "open_interest": [500] * n,
            "quality_flags": [0] * n,
            "_phase": ["math"] * n,
        }
    )


def _quarantine_df(n: int = 3) -> pd.DataFrame:
    df = _math_df(n)
    df["reject_code"] = ["NO_QUOTE", "CROSSED", "ZERO_IV"][:n]
    df["reject_detail"] = ["Bid is zero", "Ask < Bid", "IV is 0.001"][:n]
    return df


# -----------------------------------------------------------------------
# Database connection fixture
# -----------------------------------------------------------------------

@pytest.fixture
async def pool():
    import asyncpg

    p = await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        min_size=1,
        max_size=2,
    )
    yield p
    await p.close()


@pytest.fixture
async def conn(pool):
    async with pool.acquire() as c:
        # Clean up from previous test runs
        for tbl in [
            "amd_surface_min",
            "amd_surface_min_staging",
            "amd_surface_quarantine",
            "ingest_progress",
        ]:
            try:
                await c.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
            except Exception:
                pass
        try:
            await c.execute("DROP SEQUENCE IF EXISTS ingest_run_id_seq")
        except Exception:
            pass
        yield c


# -----------------------------------------------------------------------
# Tests: init_schema
# -----------------------------------------------------------------------

class TestInitSchema:
    async def test_creates_all_tables(self, pool, conn):
        dmod = _import_module()
        await dmod.init_schema(pool)

        tables = await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                'amd_surface_min', 'amd_surface_min_staging',
                'amd_surface_quarantine', 'ingest_progress'
              )
        """
        )
        table_names = {r["table_name"] for r in tables}
        expected = {
            "amd_surface_min",
            "amd_surface_min_staging",
            "amd_surface_quarantine",
            "ingest_progress",
        }
        assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"

    async def test_is_idempotent(self, pool, conn):
        """Calling init_schema twice should not fail."""
        dmod = _import_module()
        await dmod.init_schema(pool)
        await dmod.init_schema(pool)  # No exception = pass

    async def test_hypertable_exists(self, pool, conn):
        dmod = _import_module()
        await dmod.init_schema(pool)

        ht = await conn.fetchval(
            "SELECT COUNT(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'amd_surface_min'"
        )
        assert ht > 0


# -----------------------------------------------------------------------
# Tests: write_staging_batch + load_from_staging
# -----------------------------------------------------------------------

class TestTwoPhaseLoad:
    async def test_write_and_load(self, pool, conn):
        dmod = _import_module()
        await dmod.init_schema(pool)

        df = _math_df(10)
        run_id = 1

        written = await dmod.write_staging_batch(conn, df)
        assert written == 10

        loaded = await dmod.load_from_staging(conn, run_id)
        assert loaded == 10

        row_count = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_min")
        assert row_count == 10

    async def test_column_mapping(self, pool, conn):
        """Verify column names are mapped correctly (dframe → DB)."""
        dmod = _import_module()
        await dmod.init_schema(pool)

        df = _math_df(1)
        run_id = 1
        await dmod.write_staging_batch(conn, df)
        await dmod.load_from_staging(conn, run_id)

        row = await conn.fetchrow("SELECT * FROM amd_surface_min LIMIT 1")
        actual_cols = set(dict(row).keys())
        expected_cols = {
            "ts", "underlying", "expiration", "strike", "option_type",
            "spot_price", "forward_price", "implied_vol", "option_mid",
            "spread", "vega", "bid", "ask", "delta", "r", "q",
            "business_t", "dte_calendar", "log_moneyness", "open_interest",
            "quality_flags", "ingest_run_id", "_phase",
        }
        assert expected_cols.issubset(actual_cols), (
            f"Missing DB columns: {expected_cols - actual_cols}"
        )

    async def test_on_conflict_do_nothing(self, pool, conn):
        """Writing the same data twice should not duplicate rows."""
        dmod = _import_module()
        await dmod.init_schema(pool)

        df = _math_df(3)
        run_id = 1
        await dmod.write_staging_batch(conn, df)
        await dmod.load_from_staging(conn, run_id)

        # Second write with same data
        await dmod.write_staging_batch(conn, df)

        # Clear staging (load_from_staging should handle this)
        # Actually the staging table needs to be truncated between loads
        # Just load again with ON CONFLICT DO NOTHING
        loaded2 = await dmod.load_from_staging(conn, run_id)
        # Should load 0 because they conflict (or 3 if staging was preserved)
        row_count = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_min")
        assert row_count == 3, f"Expected 3 rows, got {row_count} — ON CONFLICT DO NOTHING failed"


# -----------------------------------------------------------------------
# Tests: quarantine
# -----------------------------------------------------------------------

class TestQuarantine:
    async def test_write_quarantine(self, pool, conn):
        dmod = _import_module()
        await dmod.init_schema(pool)

        df = _quarantine_df(3)
        run_id = 1
        count = await dmod.write_quarantine_batch(conn, df, run_id)
        assert count == 3

        row_count = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_quarantine")
        assert row_count == 3

    async def test_quarantine_has_reject_codes(self, pool, conn):
        dmod = _import_module()
        await dmod.init_schema(pool)

        df = _quarantine_df(2)
        await dmod.write_quarantine_batch(conn, df, 1)

        codes = await conn.fetch("SELECT reject_code FROM amd_surface_quarantine")
        code_list = [r["reject_code"] for r in codes]
        assert "NO_QUOTE" in code_list
        assert "CROSSED" in code_list


# -----------------------------------------------------------------------
# Tests: watermark
# -----------------------------------------------------------------------

class TestWatermark:
    async def test_advance_and_query(self, pool, conn):
        dmod = _import_module()
        await dmod.init_schema(pool)

        await dmod.advance_watermark(
            conn, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 28), "completed", 100, 1,
        )

        chunks = await dmod.get_completed_chunks(conn, "AMD")
        assert len(chunks) > 0

    async def test_can_skip_completed(self, pool, conn):
        """Verify orchestrator can skip already-completed chunks."""
        dmod = _import_module()
        await dmod.init_schema(pool)

        exp = dt.date(2026, 7, 21)
        chunk_end = dt.date(2026, 6, 28)
        await dmod.advance_watermark(conn, "AMD", exp, chunk_end, "completed", 100, 1)

        completed = await dmod.get_completed_chunks(conn, "AMD")
        assert (exp.isoformat(), chunk_end) in completed


# -----------------------------------------------------------------------
# Tests: run_id
# -----------------------------------------------------------------------

class TestRunID:
    async def test_next_run_id(self, pool, conn):
        dmod = _import_module()
        await dmod.init_schema(pool)

        id1 = await dmod.next_run_id(conn)
        id2 = await dmod.next_run_id(conn)
        assert id2 > id1


# -----------------------------------------------------------------------
# Tests: invariants
# -----------------------------------------------------------------------

class TestInvariants:
    def test_no_theta_imports(self):
        dmod = _import_module()
        source = Path(dmod.__file__).read_text()
        assert "theta_client" not in source
        assert "aiohttp" not in source

    def test_no_math_imports(self):
        dmod = _import_module()
        source = Path(dmod.__file__).read_text()
        assert "from .math" not in source
        assert "from dataingestion.math" not in source

    def test_no_cleaning_imports(self):
        dmod = _import_module()
        source = Path(dmod.__file__).read_text()
        assert "from .cleaning" not in source
        assert "from dataingestion.cleaning" not in source


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])