"""Verification script for dataingestion/cleaning.py (Agent A2).

Runs offline with synthetic DataFrames.  Tests every check, pre-filter,
column contract, invariants, and row accounting.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# -----------------------------------------------------------------------
# Synthetic input DataFrame (COLUMNS.md Section I columns)
# -----------------------------------------------------------------------

def _base_df(n: int = 10) -> pd.DataFrame:
    base_ts = pd.Timestamp("2026-06-15 10:30:00", tz="UTC")
    ts = [base_ts + dt.timedelta(minutes=i) for i in range(n)] * 2
    ts = ts[:n]

    df = pd.DataFrame(
        {
            "timestamp": ts,
            "underlying": "AMD",
            "expiration": pd.Timestamp("2026-07-21"),
            "strike": [150.0 + i * 2 for i in range(n)],
            "option_type": ["C" if i % 2 == 0 else "P" for i in range(n)],
            "bid": [1.0] * n,
            "ask": [1.2] * n,
            "delta": [0.5] * n,
            "theta": [-0.02 - i * 0.005 for i in range(n)],
            "vega_api": [0.15 + i * 0.01 for i in range(n)],
            "rho": [0.03 + i * 0.002 for i in range(n)],
            "implied_vol": [0.25 + i * 0.02 for i in range(n)],
            "iv_error": [0.001] * n,
            "underlying_price": [158.0] * n,
            "underlying_timestamp": [base_ts] * n,
            "spot_close": [158.0] * n,
            "open_interest": [500] * n,
            "_phase": ["raw"] * n,
        }
    )
    # Ensure dte_calendar is within 7-90 band
    df["expiration"] = pd.Timestamp("2026-07-21")
    return df


def _import_module():
    from dataingestion import cleaning as cmod

    return cmod


# -----------------------------------------------------------------------
# Pre-filter tests (Section 4)
# -----------------------------------------------------------------------

class TestPreFilter:
    def test_delta_not_filtered_in_cleaning(self):
        """Delta band runs post-join; cleaning keeps out-of-band delta rows."""
        df = _base_df()
        df.loc[0, "delta"] = 0.05
        cmod = _import_module()
        clean, quar = cmod.clean_option_chain(df)
        assert len(clean) == len(df)
        assert len(quar) == 0

    def test_dte_band_rejects_zero_dte(self):
        df = _base_df()
        df["expiration"] = pd.Timestamp("2026-06-15")  # 0 day DTE
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert len(quar) == len(df)
        assert (quar["reject_code"] == "DTE_BAND").all()

    def test_dte_band_allows_dte_one(self):
        df = _base_df()
        df["expiration"] = pd.Timestamp("2026-06-16")  # 1 day DTE
        cmod = _import_module()
        clean, quar = cmod.clean_option_chain(df)
        assert len(clean) == len(df)
        assert len(quar) == 0
        assert (clean["quality_flags"] & 8).any()  # EXPIRY_IMMINENT bit

    def test_dte_band_filters_long_dte(self):
        df = _base_df()
        df["expiration"] = pd.Timestamp("2027-01-01")  # ~200 days
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert len(quar) == len(df)
        assert (quar["reject_code"] == "DTE_BAND").all()


# -----------------------------------------------------------------------
# Quality check tests (Section 5)
# -----------------------------------------------------------------------

class TestNoQuote:
    def test_zero_bid_rejected(self):
        df = _base_df()
        df.loc[0, "bid"] = 0.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "NO_QUOTE"

    def test_zero_ask_rejected(self):
        df = _base_df()
        df.loc[0, "ask"] = 0.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "NO_QUOTE"

    def test_negative_bid_rejected(self):
        df = _base_df()
        df.loc[0, "bid"] = -0.5
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "NO_QUOTE"


class TestCrossed:
    def test_bid_equals_ask_rejected(self):
        df = _base_df()
        df.loc[0, "bid"] = 1.0
        df.loc[0, "ask"] = 1.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "CROSSED"

    def test_bid_greater_than_ask_rejected(self):
        df = _base_df()
        df.loc[0, "bid"] = 2.0
        df.loc[0, "ask"] = 1.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "CROSSED"


class TestSubpenny:
    def test_bid_not_on_grid_rejected(self):
        df = _base_df()
        df.loc[0, "bid"] = 1.005
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "SUBPENNY"

    def test_ask_not_on_grid_rejected(self):
        df = _base_df()
        df.loc[0, "ask"] = 1.205
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "SUBPENNY"


class TestSpread:
    def test_hard_spread_rejected(self):
        df = _base_df()
        df.loc[0, "bid"] = 0.5
        df.loc[0, "ask"] = 2.0  # rel_spread = 1.5/1.25 = 1.2 > 0.25
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "SPREAD_HARD"

    def test_belly_spread_flagged_not_rejected(self):
        df = _base_df()
        df.loc[0, "bid"] = 1.0
        df.loc[0, "ask"] = 1.2  # mid=1.1, rel_spread=0.1818 > 0.10
        cmod = _import_module()
        clean, _ = cmod.clean_option_chain(df)
        # Row should be in clean (not quarantined)
        assert 0 in clean.index
        # But quality_flags bit 0 (1) should be set
        assert clean.loc[0, "quality_flags"] & 1 == 1


class TestZeroIV:
    def test_zero_iv_rejected(self):
        df = _base_df()
        df.loc[0, "implied_vol"] = 0.001  # below 0.005
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "ZERO_IV"

    def test_nan_iv_rejected(self):
        df = _base_df()
        df.loc[0, "implied_vol"] = np.nan
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "ZERO_IV"


class TestIntrinsic:
    def test_call_below_intrinsic_rejected(self):
        df = _base_df()
        df.loc[0, "option_type"] = "C"
        df.loc[0, "strike"] = 100.0
        df.loc[0, "bid"] = 57.0
        df.loc[0, "ask"] = 57.5  # mid = 57.25, spot=158, intrinsic = 58.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "INTRINSIC"

    def test_put_below_intrinsic_rejected(self):
        df = _base_df()
        df.loc[0, "option_type"] = "P"
        df.loc[0, "strike"] = 200.0
        df.loc[0, "bid"] = 41.0
        df.loc[0, "ask"] = 42.0  # mid = 41.5, spot=158, intrinsic = 42.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert quar.loc[0, "reject_code"] == "INTRINSIC"


class TestMonotonicity:
    def test_non_monotonic_call_not_filtered_in_cleaning(self):
        df = _base_df(n=4)
        df["timestamp"] = pd.Timestamp("2026-06-15 10:30:00", tz="UTC")
        df["option_type"] = "C"
        df["strike"] = [170.0, 172.0, 174.0, 176.0]
        df["bid"] = [2.0, 4.0, 6.0, 3.0]
        df["ask"] = [2.2, 4.2, 6.2, 3.2]
        cmod = _import_module()
        clean, quar = cmod.clean_option_chain(df)
        assert len(clean) == len(df)
        assert len(quar) == 0

    def test_monotonic_kept(self):
        df = _base_df(n=4)
        df["timestamp"] = pd.Timestamp("2026-06-15 10:30:00", tz="UTC")
        df["option_type"] = "C"
        df["strike"] = [150.0, 152.0, 154.0, 156.0]
        df["bid"] = [10.0, 8.0, 6.0, 4.0]
        df["ask"] = [10.5, 8.5, 6.5, 4.5]
        cmod = _import_module()
        clean, _ = cmod.clean_option_chain(df)
        assert len(clean) > 0


class TestLowOI:
    def test_low_oi_not_filtered_in_cleaning(self):
        df = _base_df()
        df.loc[0, "open_interest"] = 50
        cmod = _import_module()
        clean, quar = cmod.clean_option_chain(df)
        assert len(clean) == len(df)
        assert len(quar) == 0


# -----------------------------------------------------------------------
# Row accounting
# -----------------------------------------------------------------------

class TestRowAccounting:
    def test_all_rows_accounted_for(self):
        df = _base_df(20)
        # Inject multiple violations
        df.loc[0, "bid"] = 0.0  # NO_QUOTE
        df.loc[1, "ask"] = 0.5
        df.loc[1, "bid"] = 2.0  # CROSSED
        df.loc[2, "implied_vol"] = 0.0  # ZERO_IV
        df.loc[3, "bid"] = 1.0
        df.loc[3, "ask"] = 5.0  # SPREAD_HARD
        cmod = _import_module()
        clean, quar = cmod.clean_option_chain(df)
        assert len(clean) + len(quar) == len(df), (
            f"clean={len(clean)}, quar={len(quar)}, total={len(df)}"
        )

    def test_no_row_in_both(self):
        df = _base_df(20)
        df.loc[0, "bid"] = 0.0  # NO_QUOTE
        cmod = _import_module()
        clean, quar = cmod.clean_option_chain(df)
        clean_idx = set(clean.index)
        quar_idx = set(quar.index)
        assert len(clean_idx & quar_idx) == 0, (
            "Rows found in both clean and quarantine!"
        )


# -----------------------------------------------------------------------
# Output columns
# -----------------------------------------------------------------------

class TestOutputColumns:
    def test_clean_has_required_columns(self):
        df = _base_df(10)
        cmod = _import_module()
        clean, _ = cmod.clean_option_chain(df)
        required = {"mid_price", "spread", "rel_spread", "quality_flags", "dte_calendar"}
        missing = required - set(clean.columns)
        assert not missing, f"clean_df missing columns: {missing}"

    def test_quarantine_has_reject_columns(self):
        df = _base_df(10)
        df.loc[0, "bid"] = 0.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert "reject_code" in quar.columns
        assert "reject_detail" in quar.columns

    def test_clean_phase_is_clean(self):
        df = _base_df(10)
        cmod = _import_module()
        clean, _ = cmod.clean_option_chain(df)
        if not clean.empty:
            assert (clean["_phase"] == "clean").all()

    def test_quarantine_phase_is_quarantine(self):
        df = _base_df(10)
        df.loc[0, "bid"] = 0.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert (quar["_phase"] == "quarantine").all()


# -----------------------------------------------------------------------
# Quarantine detail values and ingest_run_id traceability
# -----------------------------------------------------------------------

class TestQuarantineDetailValues:
    def test_dte_band_detail_contains_values(self):
        """DTE_BAND reject_detail should include actual DTE, min, and max."""
        df = _base_df()
        df["expiration"] = pd.Timestamp("2026-06-15")  # 0 day DTE
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df, run_id=42)
        detail = quar["reject_detail"].iloc[0]
        assert "DTE=" in detail
        assert "min=1" in detail
        assert "max=90" in detail

    def test_no_quote_detail_contains_bid_ask(self):
        """NO_QUOTE reject_detail should include actual bid and ask."""
        df = _base_df()
        df.loc[0, "bid"] = 0.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df, run_id=42)
        detail = quar.loc[0, "reject_detail"]
        assert "bid=" in detail
        assert "ask=" in detail

    def test_crossed_detail_contains_bid_ask(self):
        """CROSSED reject_detail should include actual bid and ask."""
        df = _base_df()
        df.loc[0, "bid"] = 2.0
        df.loc[0, "ask"] = 1.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df, run_id=42)
        detail = quar.loc[0, "reject_detail"]
        assert "bid=" in detail
        assert "ask=" in detail

    def test_spread_hard_detail_contains_values(self):
        """SPREAD_HARD reject_detail should include actual rel_spread and limit."""
        df = _base_df()
        df.loc[0, "bid"] = 0.5
        df.loc[0, "ask"] = 2.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df, run_id=42)
        detail = quar.loc[0, "reject_detail"]
        assert "rel_spread=" in detail
        assert "limit=0.25" in detail

    def test_zero_iv_detail_contains_values(self):
        """ZERO_IV reject_detail should include actual iv and min."""
        df = _base_df()
        df.loc[0, "implied_vol"] = 0.001
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df, run_id=42)
        detail = quar.loc[0, "reject_detail"]
        assert "iv=" in detail
        assert "min=0.005" in detail

    def test_intrinsic_detail_contains_values(self):
        """INTRINSIC reject_detail should include actual mid and intrinsic."""
        df = _base_df()
        df.loc[0, "option_type"] = "C"
        df.loc[0, "strike"] = 100.0
        df.loc[0, "bid"] = 57.0
        df.loc[0, "ask"] = 57.5
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df, run_id=42)
        detail = quar.loc[0, "reject_detail"]
        assert "mid=" in detail
        assert "intrinsic=" in detail

    def test_ingest_run_id_in_quarantine(self):
        """Quarantine df should contain ingest_run_id when run_id is provided."""
        df = _base_df()
        df.loc[0, "bid"] = 0.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df, run_id=42)
        assert "ingest_run_id" in quar.columns
        assert (quar["ingest_run_id"] == 42).all()

    def test_ingest_run_id_absent_when_no_run_id(self):
        """Quarantine df should NOT have ingest_run_id when run_id is None."""
        df = _base_df()
        df.loc[0, "bid"] = 0.0
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df)
        assert "ingest_run_id" not in quar.columns

    def test_empty_quarantine_ingest_run_id(self):
        """Empty quarantine df should allow ingest_run_id column."""
        df = _base_df()
        cmod = _import_module()
        _, quar = cmod.clean_option_chain(df, run_id=42)
        assert "ingest_run_id" in quar.columns


# -----------------------------------------------------------------------
# Invariants: no HTTP, no DB, no file I/O
# -----------------------------------------------------------------------

class TestInvariants:
    def test_no_theta_imports(self):
        cmod = _import_module()
        source = Path(cmod.__file__).read_text()
        assert "theta_client" not in source
        assert "aiohttp" not in source
        assert "urllib" not in source

    def test_no_db_imports(self):
        cmod = _import_module()
        source = Path(cmod.__file__).read_text()
        assert "asyncpg" not in source
        assert "psycopg" not in source

    def test_no_file_io(self):
        cmod = _import_module()
        source = Path(cmod.__file__).read_text()
        forbidden = [".to_csv(", ".to_parquet(", ".to_sql(", ".to_feather("]
        for f in forbidden:
            assert f not in source, f"cleaning.py must not do file/DB I/O: '{f}'"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])