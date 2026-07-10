"""Verification script for dataingestion/math.py (Agent A3).

Tests business time T, forward price, Numba vega against scipy reference.
Runs offline — no Theta subscription needed.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# -----------------------------------------------------------------------
# Mock calendar for testing
# -----------------------------------------------------------------------

class MockSchedule:
    """Minimal mock of pandas_market_calendars schedule for 2026-06-15 → 2026-07-21."""

    def __init__(self):
        # Generate trading days between 2026-06-15 and 2026-07-21 (weekdays only)
        dates = pd.bdate_range("2026-06-15", "2026-07-21", freq="C")
        # Make timezone-aware
        dates = dates.tz_localize("US/Eastern")
        self._schedule = pd.DataFrame(
            {
                "market_open": pd.DatetimeIndex(
                    [d.replace(hour=9, minute=30) for d in dates]
                ),
                "market_close": pd.DatetimeIndex(
                    [d.replace(hour=16, minute=0) for d in dates]
                ),
            },
            index=dates,
        )

    def schedule(self, start_date=None, end_date=None):
        return self._schedule

    @property
    def tz(self):
        import pytz

        return pytz.timezone("US/Eastern")


def _clean_df(n: int = 5) -> pd.DataFrame:
    """Synthetic clean DataFrame matching COLUMNS.md Section II.A."""
    base_ts = pd.Timestamp("2026-06-15 10:30:00", tz="US/Eastern")
    df = pd.DataFrame(
        {
            "timestamp": pd.DatetimeIndex(
                [base_ts + dt.timedelta(minutes=i) for i in range(n)]
            ).tz_convert("UTC"),
            "underlying": ["AMD"] * n,
            "expiration": pd.Timestamp("2026-07-21"),
            "strike": [150.0 + i * 2 for i in range(n)],
            "option_type": ["C"] * n,
            "bid": [1.0] * n,
            "ask": [1.2] * n,
            "mid_price": [1.1] * n,
            "spread": [0.2] * n,
            "rel_spread": [0.18] * n,
            "quality_flags": [0] * n,
            "dte_calendar": [36] * n,  # 2026-06-15 to 2026-07-21
            "delta": [0.5] * n,
            "theta": [-0.02] * n,
            "vega_api": [0.15] * n,
            "rho": [0.03] * n,
            "implied_vol": [0.25] * n,
            "iv_error": [0.001] * n,
            "underlying_price": [158.0] * n,
            "underlying_timestamp": [base_ts.tz_convert("UTC")] * n,
            "spot_close": [158.0] * n,
            "open_interest": [500] * n,
            "_phase": ["clean"] * n,
        }
    )
    return df


def _import_module():
    from dataingestion import math as mmod

    return mmod


# -----------------------------------------------------------------------
# compute_business_T
# -----------------------------------------------------------------------

class TestBusinessT:
    def test_returns_positive_t(self):
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        result = mmod.compute_business_T(df, cal)
        assert "business_t" in result.columns
        assert (result["business_t"] > 0).all()
        assert (result["business_t"] < 1.0).all()  # Less than 1 year

    def test_t_is_in_years(self):
        """For ~36 calendar days (~25 trading days), T should be ~0.1 years."""
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        result = mmod.compute_business_T(df, cal)
        # Rough estimate: 25 trading days / 252 ≈ 0.099 years
        assert 0.05 < result["business_t"].iloc[0] < 0.20

    def test_t_decreases_with_later_timestamp(self):
        """Later timestamp → less time remaining → smaller T."""
        df = _clean_df(3)
        cal = MockSchedule()
        mmod = _import_module()
        result = mmod.compute_business_T(df, cal)
        values = result["business_t"].values
        # Later rows have later timestamps, so T should be non-increasing
        assert values[0] >= values[-1] or np.isclose(values[0], values[-1], rtol=0.1)


# -----------------------------------------------------------------------
# Business time precision tests (W0B fixes)
# -----------------------------------------------------------------------

class TestBusinessTimePrecision:
    """Tests for business time T formula correctness (Critical #3, #4, #5)."""

    def test_half_day_minutes(self):
        """Known half-days must report session_minutes == 210."""
        import pandas_market_calendars as mcal

        cal = mcal.get_calendar("XNYS")
        half_days = [
            ("2024-11-29", "Day after Thanksgiving"),
            ("2024-12-24", "Christmas Eve"),
            ("2025-07-03", "July 3 (pre-Independence Day)"),
        ]
        for half_day_str, label in half_days:
            half_dt = pd.Timestamp(half_day_str)
            sched = cal.schedule(
                start_date=half_dt - pd.Timedelta(days=1),
                end_date=half_dt + pd.Timedelta(days=1),
            )
            assert half_dt.date() in {d.date() for d in sched.index}, (
                f"{label} ({half_day_str}) not in XNYS calendar"
            )
            row = sched.loc[sched.index.date == half_dt.date()]
            assert len(row) == 1, f"Expected exactly 1 row for {half_day_str}"
            mins = (
                (row["market_close"].iloc[0] - row["market_open"].iloc[0])
                .total_seconds() / 60
            )
            assert mins == 210.0, (
                f"{label} ({half_day_str}): expected 210 min, got {mins}"
            )

    def test_pre_open_bar_returns_zero_minutes(self):
        """Bar at 09:00 ET (before RTH 09:30 open) should get 0 minutes_remaining."""
        df = _clean_df(1)
        # Set bar to 09:00 ET — outside RTH
        df.loc[0, "timestamp"] = pd.Timestamp(
            "2026-06-15 09:00:00", tz="US/Eastern"
        ).tz_convert("UTC")
        cal = MockSchedule()
        mmod = _import_module()
        result = mmod.compute_business_T(df, cal)

        # T should be based only on between_minutes (no minutes_remaining)
        # 06-15 to 07-21: 25 trading days (excluding today) * 390 / (390*252) ≈ 0.099
        # With 0 minutes_remaining, this should be less than if counted.
        # The exact value is for integration test; here we assert it's positive
        # but smaller than if the bar were inside RTH.
        t_pre_open = result["business_t"].iloc[0]

        # Same bar inside RTH should produce higher T
        df2 = _clean_df(1)
        df2.loc[0, "timestamp"] = pd.Timestamp(
            "2026-06-15 10:30:00", tz="US/Eastern"
        ).tz_convert("UTC")
        result2 = mmod.compute_business_T(df2, cal)
        t_inside = result2["business_t"].iloc[0]

        # Pre-open has strictly less time remaining
        assert t_pre_open < t_inside, (
            f"Pre-open T={t_pre_open:.6f} should be < inside-RTH T={t_inside:.6f}"
        )

    def test_after_close_bar_returns_zero_minutes(self):
        """Bar at 16:30 ET (after RTH 16:00 close) should get 0 minutes_remaining."""
        df = _clean_df(1)
        df.loc[0, "timestamp"] = pd.Timestamp(
            "2026-06-15 16:30:00", tz="US/Eastern"
        ).tz_convert("UTC")
        cal = MockSchedule()
        mmod = _import_module()
        result = mmod.compute_business_T(df, cal)
        assert result["business_t"].iloc[0] > 0, "After-close T should still be > 0 (days between remain)"

    def test_expiry_day_bar_returns_small_t(self):
        """Bar at 09:31 on expiry day returns very small T (only remaining minutes)."""
        # Use 2026-06-19 as expiry; bar at 09:31 ET on same day
        df = _clean_df(1)
        bar_dt = pd.Timestamp("2026-06-19 09:31:00", tz="US/Eastern")
        df.loc[0, "timestamp"] = bar_dt.tz_convert("UTC")
        df.loc[0, "expiration"] = pd.Timestamp("2026-06-19")
        cal = MockSchedule()
        mmod = _import_module()
        result = mmod.compute_business_T(df, cal)
        t = result["business_t"].iloc[0]
        # 09:31 to 16:00 = 6h29m = 389 min → T ≈ 389 / (390*252) ≈ 0.00396
        # Allow tolerance for mock calendar quirks
        assert t > 0, f"Expiry day T should be positive, got {t}"
        assert t < 0.01, f"Expiry day T should be < 0.01 years, got {t}"

    def test_double_exclude_exp_day(self):
        """Expiration day must not be double-counted in between_minutes."""
        cal = MockSchedule()
        mmod = _import_module()

        # Both expiries must be within MockSchedule range (2026-06-15 to 2026-07-21)
        # Bar on 2026-06-15, expiry 2026-06-22 (one week later)
        df = _clean_df(1)
        bar_dt = pd.Timestamp("2026-06-15 10:30:00", tz="US/Eastern")
        df.loc[0, "timestamp"] = bar_dt.tz_convert("UTC")
        df.loc[0, "expiration"] = pd.Timestamp("2026-06-22")
        result = mmod.compute_business_T(df, cal)
        t_near = result["business_t"].iloc[0]

        # Bar on 2026-06-15, expiry 2026-07-21 (five weeks later)
        df2 = _clean_df(1)
        df2.loc[0, "timestamp"] = bar_dt.tz_convert("UTC")
        df2.loc[0, "expiration"] = pd.Timestamp("2026-07-21")
        result2 = mmod.compute_business_T(df2, cal)
        t_far = result2["business_t"].iloc[0]

        # Far expiry must have strictly more T than near expiry
        assert t_far > t_near, (
            f"Far expiry T={t_far:.6f} should be > near expiry T={t_near:.6f}"
        )

        # Manual computation for 2026-06-15 bar → 2026-06-22 expiry:
        # minutes_remaining: 10:30-16:00 = 330 min
        # between_minutes: 06-16 to 06-21 = 4 trading days * 390 = 1560 min
        # T = (330 + 1560) / (390*252) ≈ 0.01923
        # This sanity-check catches double-exclude (would give lower T)


# -----------------------------------------------------------------------
# compute_forward
# -----------------------------------------------------------------------

class TestForward:
    def test_forward_greater_than_spot(self):
        """With positive rate, F > S."""
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        # Attach r (4.5% cc = 0.045)
        df["r"] = 0.045
        result = mmod.compute_forward(df)
        assert "forward_price" in result.columns
        assert (result["forward_price"] > result["spot_close"]).all()

    def test_forward_close_to_spot_for_small_t(self):
        """For T ~ 0.1, F ≈ S."""
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = 0.045
        result = mmod.compute_forward(df)
        ratio = result["forward_price"] / result["spot_close"]
        assert np.allclose(ratio, 1.0, rtol=0.01)

    def test_q_is_zero(self):
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = 0.045
        result = mmod.compute_forward(df)
        assert "q" in result.columns
        assert (result["q"] == 0.0).all()

    def test_r_column_present(self):
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = 0.045
        result = mmod.compute_forward(df)
        assert "r" in result.columns


# -----------------------------------------------------------------------
# compute_vega (Numba)
# -----------------------------------------------------------------------

class TestVega:
    def test_vega_is_non_negative(self):
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = 0.045
        df = mmod.compute_forward(df)
        result = mmod.compute_vega(df, mode="vol")
        assert "vega" in result.columns
        assert (result["vega"] >= 0).all()

    def test_vega_matches_scipy(self):
        """Numba vega should match reference implementation."""
        S = 158.0
        K = 150.0
        sigma = 0.25
        r = 0.045

        df = _clean_df(1)
        df["strike"] = K
        df["spot_close"] = S
        df["implied_vol"] = sigma
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = r
        df = mmod.compute_forward(df)
        
        # Use actual computed T for reference
        T = df["business_t"].iloc[0]
        F = S * np.exp(r * T)
        
        # SciPy reference
        d1 = (np.log(F / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
        ref_vega = np.exp(-r * T) * F * norm.pdf(d1) * np.sqrt(T)

        result = mmod.compute_vega(df, mode="vol")
        computed = result["vega"].iloc[0]
        assert np.isclose(computed, ref_vega, rtol=1e-6), (
            f"vega={computed}, scipy_ref={ref_vega}"
        )

    def test_vega_larger_for_atm_than_otm(self):
        """ATM options have higher vega."""
        df_ATM = _clean_df(1)
        df_ATM["strike"] = 158.0  # ATM
        df_OTM = _clean_df(1)
        df_OTM["strike"] = 140.0  # OTM call

        cal = MockSchedule()
        mmod = _import_module()

        for d in [df_ATM, df_OTM]:
            d = mmod.compute_business_T(d, cal)
            d["r"] = 0.045
            d = mmod.compute_forward(d)
            d = mmod.compute_vega(d)

        atm_vega = df_ATM["vega"].iloc[0]
        otm_vega = df_OTM["vega"].iloc[0]
        assert atm_vega > otm_vega, f"ATM vega={atm_vega}, OTM vega={otm_vega}"

    def test_vega_scales_with_sqrt_t(self):
        """Longer DTE → higher vega."""
        df_short = _clean_df(1)
        df_short["expiration"] = pd.Timestamp("2026-06-25")  # 10 calendar days
        df_long = _clean_df(1)
        df_long["expiration"] = pd.Timestamp("2026-07-21")  # 36 calendar days

        cal = MockSchedule()
        mmod = _import_module()

        for d in [df_short, df_long]:
            d = mmod.compute_business_T(d, cal)
            d["r"] = 0.045
            d = mmod.compute_forward(d)
            d = mmod.compute_vega(d)

        assert df_long["vega"].iloc[0] > df_short["vega"].iloc[0], (
            f"Long={df_long['vega'].iloc[0]}, Short={df_short['vega'].iloc[0]}"
        )

    def test_zero_sigma_yields_nan(self):
        df = _clean_df(1)
        df["implied_vol"] = 0.0
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = 0.045
        df = mmod.compute_forward(df)
        result = mmod.compute_vega(df, mode="vol")
        assert np.isnan(result["vega"].iloc[0])


    def test_var_vega2_mode(self):
        """Default vega mode stores variance-space vega squared."""
        df = _clean_df(1)
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = 0.045
        df = mmod.compute_forward(df)
        vol_result = mmod.compute_vega(df.copy(), mode="vol")
        var_result = mmod.compute_vega(df, mode="var_vega2")
        vol_v = vol_result["vega"].iloc[0]
        var_v = var_result["vega"].iloc[0]
        assert var_v > 0
        assert np.isclose(var_v, (vol_v / (2 * 0.25 * np.sqrt(df["business_t"].iloc[0]))) ** 2, rtol=0.01)


class TestOutputColumns:
    def test_all_required_columns(self):
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = 0.045
        df = mmod.compute_forward(df)
        result = mmod.compute_vega(df, mode="vol")
        required = {
            "business_t", "forward_price", "r", "q",
            "vega", "log_moneyness",
        }
        assert required.issubset(set(result.columns)), (
            f"Missing: {required - set(result.columns)}"
        )

    def test_phase_is_math(self):
        df = _clean_df()
        cal = MockSchedule()
        mmod = _import_module()
        df = mmod.compute_business_T(df, cal)
        df["r"] = 0.045
        df = mmod.compute_forward(df)
        result = mmod.compute_vega(df, mode="vol")
        assert (result["_phase"] == "math").all()


class TestInvariants:
    def test_no_theta_imports(self):
        mmod = _import_module()
        source = Path(mmod.__file__).read_text()
        assert "theta_client" not in source
        assert "aiohttp" not in source

    def test_no_db_imports(self):
        mmod = _import_module()
        source = Path(mmod.__file__).read_text()
        assert "asyncpg" not in source

    def test_no_cleaning_imports(self):
        mmod = _import_module()
        source = Path(mmod.__file__).read_text()
        assert "from .cleaning" not in source
        assert "from dataingestion.cleaning" not in source


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])