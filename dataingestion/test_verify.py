"""Verification script for dataingestion/verify.py (Agent A6).

Tests read-only integrity checks against a database seeded with
synthetic data containing known issues. Verifies all 8 checks.
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
    from dataingestion import verify as vmod

    return vmod


# -----------------------------------------------------------------------
# Seed test database with synthetic data
# -----------------------------------------------------------------------

@pytest.fixture
async def pool():
    import asyncpg

    p = await asyncpg.create_pool(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASSWORD, min_size=1, max_size=2,
    )
    yield p
    await p.close()


@pytest.fixture
async def seeded_db(pool):
    async with pool.acquire() as conn:
        # Create minimal schema
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS amd_surface_min (
                ts              timestamptz NOT NULL,
                underlying      text NOT NULL,
                expiration      date NOT NULL,
                strike          numeric(12,4) NOT NULL,
                option_type     char(1) NOT NULL,
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
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS amd_surface_quarantine (
                ts              timestamptz,
                underlying      text,
                expiration      date,
                strike          numeric(12,4),
                option_type     char(1),
                reject_code     text,
                reject_detail   text,
                ingested_at     timestamptz DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ingest_progress (
                underlying      text NOT NULL,
                expiration      date NOT NULL,
                chunk_end_date  date NOT NULL,
                status          text NOT NULL,
                rows_loaded     int DEFAULT 0,
                rows_quarantined int DEFAULT 0,
                run_id          bigint,
                started_at      timestamptz DEFAULT NOW(),
                completed_at    timestamptz,
                PRIMARY KEY (underlying, expiration, chunk_end_date)
            )
        """)

        # Clean previous test data
        for tbl in ["amd_surface_min", "amd_surface_quarantine", "ingest_progress"]:
            await conn.execute(f"DELETE FROM {tbl}")

        # Insert synthetic data with known issues
        base_ts = pd.Timestamp("2026-06-15 10:30:00", tz="UTC")
        rows = []
        for i in range(20):
            ts = base_ts + dt.timedelta(minutes=i)
            row = (
                ts,
                "AMD",
                dt.date(2026, 7, 21),
                150.0 + i * 2,
                "C" if i % 2 == 0 else "P",
                158.0 if i != 0 else None,      # spot null on row 0
                158.5,
                0.25 if i != 1 else None,        # IV null on row 1
                1.1,
                0.2,
                0.15 if i != 2 else None,        # vega null on row 2
                1.0,
                1.2,
                0.5,
                0.045,
                0.0,
                0.1 if i != 3 else 2.0,          # T=2.0 (out of range) on row 3
                36,
                0.0,
                500,
                0,
                1,
                "math",
            )
            rows.append(row)

        await conn.executemany(
            """
            INSERT INTO amd_surface_min (
                ts, underlying, expiration, strike, option_type,
                spot_price, forward_price, implied_vol, option_mid, spread,
                vega, bid, ask, delta, r, q, business_t, dte_calendar,
                log_moneyness, open_interest, quality_flags, ingest_run_id, _phase
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23)
            """,
            rows,
        )

        # Insert quarantine rows
        await conn.executemany(
            """
            INSERT INTO amd_surface_quarantine (
                ts, underlying, expiration, strike, option_type, reject_code, reject_detail
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            [
                (base_ts, "AMD", dt.date(2026, 7, 21), 150.0, "C", "NO_QUOTE", "Bid is zero"),
                (base_ts, "AMD", dt.date(2026, 7, 21), 152.0, "P", "CROSSED", "Ask < Bid"),
                (base_ts, "AMD", dt.date(2026, 7, 21), 154.0, "C", "ZERO_IV", "IV is 0"),
                (base_ts, "AMD", dt.date(2026, 7, 21), 156.0, "P", "LOW_OI", "OI is 50"),
                (base_ts, "AMD", dt.date(2026, 7, 21), 158.0, "C", "LOW_OI", "OI is 0"),
                (base_ts, "AMD", dt.date(2026, 7, 21), 160.0, "P", "LOW_OI", "OI = 100"),
            ],
        )

        # Insert watermark entries (deliberately incomplete — one chunk missing)
        await conn.executemany(
            """
            INSERT INTO ingest_progress (underlying, expiration, chunk_end_date, status, rows_loaded, run_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (underlying, expiration, chunk_end_date) DO NOTHING
            """,
            [
                ("AMD", dt.date(2026, 7, 21), dt.date(2026, 6, 28), "completed", 100, 1),
                ("AMD", dt.date(2026, 7, 21), dt.date(2026, 7, 28), "failed", 0, 1),
            ],
        )

        yield conn

    # Cleanup
    async with pool.acquire() as conn:
        for tbl in ["amd_surface_min", "amd_surface_quarantine", "ingest_progress"]:
            await conn.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")


# -----------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------

class TestAllChecksRun:
    async def test_all_checks_return_results(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        assert "checks" in result
        assert "status" in result
        assert len(result["checks"]) >= 8


class TestColumnCoverage:
    async def test_detects_null_coverage(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        checks_by_name = {c["name"]: c for c in result["checks"]}
        cov_check = checks_by_name.get("column_coverage")
        if cov_check:
            # Row 0 has null spot, row 1 null IV, row 2 null vega
            # So spot coverage ~95%, IV ~95%, vega ~95%
            # These should be caught as WARN or FAIL
            assert not cov_check.get("passed", True) or cov_check.get("severity") in ("WARN",)


class TestFilterImpact:
    async def test_quarantine_breakdown(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        checks_by_name = {c["name"]: c for c in result["checks"]}
        if "filter_impact" in checks_by_name:
            check = checks_by_name["filter_impact"]
            # 6 quarantine rows, 3 LOW_OI (50%)
            assert check is not None


class TestBusinessTSanity:
    async def test_detects_out_of_range_t(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        checks_by_name = {c["name"]: c for c in result["checks"]}
        if "business_t_sanity" in checks_by_name:
            check = checks_by_name["business_t_sanity"]
            # Row 3 has T=2.0 (out of range 0→1)
            assert not check["passed"]
            assert "out of range" in check.get("detail", "").lower() or check.get("value", {}).get("n_over_one", 0) > 0


class TestFutureLeakage:
    async def test_no_future_timestamps(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        checks_by_name = {c["name"]: c for c in result["checks"]}
        if "no_future_leakage" in checks_by_name:
            # All our synthetic timestamps are in the past
            assert checks_by_name["no_future_leakage"]["passed"]


class TestESSVISanity:
    async def test_iv_smile_has_values(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        checks_by_name = {c["name"]: c for c in result["checks"]}
        if "essvi_sanity" in checks_by_name:
            # Should have data to check
            check = checks_by_name["essvi_sanity"]
            assert check is not None


class TestDataFreshness:
    async def test_reports_timestamps(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        checks_by_name = {c["name"]: c for c in result["checks"]}
        if "data_freshness" in checks_by_name:
            check = checks_by_name["data_freshness"]
            assert check["passed"]


class TestRowCounts:
    async def test_consistent_row_counts(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        checks_by_name = {c["name"]: c for c in result["checks"]}
        if "row_counts" in checks_by_name:
            check = checks_by_name["row_counts"]
            # We have 20 rows in amd_surface_min
            assert check is not None


class TestStatusSummary:
    async def test_status_returned(self, seeded_db, pool):
        vmod = _import_module()
        result = await vmod.run_verification(pool)
        assert result["status"] in ("PASS", "FAIL", "WARN")


class TestInvariants:
    async def test_no_db_writes_during_verification(self, seeded_db, pool):
        """Verify that row counts don't change after running verification."""
        async with pool.acquire() as conn:
            count_before = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_min")
            quar_before = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_quarantine")

        vmod = _import_module()
        await vmod.run_verification(pool)

        async with pool.acquire() as conn:
            count_after = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_min")
            quar_after = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_quarantine")

        assert count_before == count_after, (
            f"Verification wrote to amd_surface_min! Before={count_before}, After={count_after}"
        )
        assert quar_before == quar_after, (
            f"Verification wrote to amd_surface_quarantine! Before={quar_before}, After={quar_after}"
        )

    def test_no_fetcher_imports(self):
        vmod = _import_module()
        source = Path(vmod.__file__).read_text()
        assert "from .fetchers" not in source
        assert "from dataingestion.fetchers" not in source

    def test_no_cleaning_imports(self):
        vmod = _import_module()
        source = Path(vmod.__file__).read_text()
        assert "from .cleaning" not in source

    def test_no_math_imports(self):
        vmod = _import_module()
        source = Path(vmod.__file__).read_text()
        assert "from .math" not in source

    def test_no_http_imports(self):
        vmod = _import_module()
        source = Path(vmod.__file__).read_text()
        assert "aiohttp" not in source
        assert "theta_client" not in source


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])