"""Tests for dataingestion/joins.py post-join filters and rate interpolation."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _math_ready_df(n: int = 4) -> pd.DataFrame:
    base_ts = pd.Timestamp("2026-06-15 10:30:00", tz="UTC")
    return pd.DataFrame({
        "timestamp": [base_ts] * n,
        "underlying": ["AMD"] * n,
        "expiration": pd.Timestamp("2026-07-21"),
        "strike": [150.0, 152.0, 154.0, 156.0],
        "option_type": ["C", "C", "C", "C"],
        "bid": [5.0, 4.0, 3.0, 2.0],
        "ask": [5.2, 4.2, 3.2, 2.2],
        "mid_price": [5.1, 4.1, 3.1, 2.1],
        "spread": [0.2, 0.2, 0.2, 0.2],
        "rel_spread": [0.04, 0.05, 0.06, 0.07],
        "implied_vol": [0.25, 0.24, 0.23, 0.22],
        "delta": [0.5, 0.45, 0.4, 0.35],
        "open_interest": [500, 500, 500, 500],
        "spot_close": [158.0] * n,
        "business_t": [0.1] * n,
        "r": [0.045] * n,
        "forward_price": [159.0] * n,
        "log_moneyness": np.log(np.array([150.0, 152.0, 154.0, 156.0]) / 159.0),
        "quality_flags": [0] * n,
        "dte_calendar": [36] * n,
        "_phase": ["clean"] * n,
    })


class TestPostJoinFilters:
    def test_delta_band_quarantines_out_of_band(self):
        from dataingestion.joins import apply_post_join_filters

        df = _math_ready_df()
        df.loc[0, "strike"] = 220.0
        df.loc[0, "option_type"] = "C"
        clean, quar = apply_post_join_filters(df)
        assert len(clean) < len(df)
        assert quar.iloc[0]["reject_code"] == "DELTA_BAND"

    def test_low_oi_quarantines(self):
        from dataingestion.joins import apply_post_join_filters

        df = _math_ready_df()
        df.loc[0, "open_interest"] = 50
        clean, quar = apply_post_join_filters(df)
        assert quar.iloc[0]["reject_code"] == "LOW_OI"

    def test_monotonicity_quarantines_violation(self):
        from dataingestion.joins import apply_post_join_filters

        df = _math_ready_df()
        df["mid_price"] = [5.1, 4.1, 6.1, 2.1]  # call mids increase at strike 154
        _, quar = apply_post_join_filters(df)
        assert "MONOTONICITY" in quar["reject_code"].values


class TestRateInterpolation:
    def test_linear_interp_mid_bucket(self):
        from dataingestion.joins import _linear_interp_rate

        rates = {"SOFR": 0.04, "TREASURY_M1": 0.05, "TREASURY_M3": 0.06}
        r = _linear_interp_rate(45.0, rates)
        assert 0.05 < r < 0.06
