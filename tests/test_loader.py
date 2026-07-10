"""Tests for essvi.loader.load_minute_slice."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from essvi import config as cfg
from essvi.exceptions import DataNotFoundError, MissingColumnError
from essvi.loader import _REQUIRED_COLUMNS, load_minute_slice, _REQUIRED_DB_COLUMNS

_TS = pd.Timestamp("2024-01-15 15:30:00", tz="UTC")


def _base_row(**overrides) -> dict:
    """Single row with ONLY DB columns (23 cols from amd_surface_min)."""
    row = {
        "ts": _TS,
        "underlying": "AMD",
        "expiration": pd.Timestamp("2024-02-16").date(),
        "strike": 150.0,
        "option_type": "C",
        "spot_price": 148.0,
        "forward_price": 149.0,
        "implied_vol": 0.35,
        "option_mid": 2.5,
        "spread": 0.1,
        "vega": 0.15,
        "bid": 2.45,
        "ask": 2.55,
        "delta": 0.45,
        "r": 0.05,
        "q": 0.0,
        "business_t": 4/252,
        "dte_calendar": 30,
        "log_moneyness": np.log(150/149),
        "open_interest": 1000,
        "quality_flags": 0,
        "ingest_run_id": 12345,
        "underlying_timestamp": _TS,
    }
    row.update(overrides)
    return row


def _panel(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows) if rows else [])


def test_load_empty_timestamp_raises():
    empty = _panel()
    with pytest.raises(DataNotFoundError) as exc_info:
        load_minute_slice(_TS, conn=empty)
    # Error message should contain the timestamp
    assert str(_TS) in str(exc_info.value)


def test_load_missing_column_raises():
    row = _base_row()
    del row["option_mid"]  # This is a DB column that will be needed
    panel = _panel(row)
    with pytest.raises(MissingColumnError) as exc_info:
        load_minute_slice(_TS, conn=panel)
    # Should fail on missing computed column that depends on option_mid


def test_belly_flag_correct():
    """Test that belly_flag is computed correctly."""
    # Create rows with different strikes around forward
    belly = _base_row(strike=149.0, option_mid=5.0, spread=0.05, open_interest=200, delta=0.50, log_moneyness=0.0)
    wide_spread = _base_row(strike=151.0, option_mid=5.0, spread=0.6, log_moneyness=0.05)
    low_oi = _base_row(strike=152.0, open_interest=50, log_moneyness=0.05)
    low_delta = _base_row(strike=153.0, delta=0.05, log_moneyness=0.05)
    far_k = _base_row(strike=154.0, log_moneyness=0.20)

    result = load_minute_slice(_TS, conn=_panel(belly, wide_spread, low_oi, low_delta, far_k))
    by_strike = result.set_index("strike")

    assert bool(by_strike.loc[149.0, "belly_flag"]) is True
    assert bool(by_strike.loc[151.0, "belly_flag"]) is False
    assert bool(by_strike.loc[152.0, "belly_flag"]) is False
    assert bool(by_strike.loc[153.0, "belly_flag"]) is False
    assert bool(by_strike.loc[154.0, "belly_flag"]) is False


def test_otm_flag_correct():
    call_otm = _base_row(option_type="C", log_moneyness=0.05, delta=0.45)
    call_itm = _base_row(strike=145.0, option_type="C", log_moneyness=-0.03, delta=0.70)
    put_otm = _base_row(strike=145.0, option_type="P", log_moneyness=-0.03, delta=-0.30)
    put_itm = _base_row(option_type="P", log_moneyness=0.05, delta=-0.55)

    result = load_minute_slice(
        _TS,
        conn=_panel(call_otm, call_itm, put_otm, put_itm),
    )
    by_right_k = {(r.right, r.log_moneyness): r.OTM for r in result.itertuples()}

    assert by_right_k[("C", 0.05)] is True
    assert by_right_k[("C", -0.03)] is False
    assert by_right_k[("P", -0.03)] is True
    assert by_right_k[("P", 0.05)] is False


def test_dte_filter_applied():
    in_band = _base_row(dte_calendar=30)
    too_low = _base_row(strike=140.0, dte_calendar=0)
    too_high = _base_row(strike=160.0, dte_calendar=cfg.MAX_DTE + 1)

    result = load_minute_slice(_TS, conn=_panel(in_band, too_low, too_high))
    assert len(result) == 1
    assert result.iloc[0]["strike"] == 150.0


def test_rel_spread_computed():
    row = _base_row(bid=4.0, ask=6.0, option_mid=5.0, spread=2.0)
    result = load_minute_slice(_TS, conn=_panel(row))
    assert result.iloc[0]["mid_price"] == pytest.approx(5.0)
    assert result.iloc[0]["rel_spread"] == pytest.approx(0.4)


def test_all_required_columns_present():
    result = load_minute_slice(_TS, conn=_panel(_base_row()))
    assert set(result.columns) == set(_REQUIRED_COLUMNS)
    assert len(result.columns) == len(_REQUIRED_COLUMNS)


def test_db_columns_count():
    """Verify we expect exactly 23 DB columns."""
    assert len(_REQUIRED_DB_COLUMNS) == 23


def test_session_phase_computed():
    """Test session_phase is computed from timestamp."""
    row = _base_row()
    result = load_minute_slice(_TS, conn=_panel(row))
    assert "session_phase" in result.columns
    assert result.iloc[0]["session_phase"] in ["premarket", "regular", "postmarket"]


def test_slice_strike_count_computed():
    """Test slice_strike_count is computed per expiration."""
    row1 = _base_row(strike=140.0, expiration=pd.Timestamp("2024-02-16").date())
    row2 = _base_row(strike=150.0, expiration=pd.Timestamp("2024-02-16").date())
    row3 = _base_row(strike=160.0, expiration=pd.Timestamp("2024-02-23").date())
    row4 = _base_row(strike=170.0, expiration=pd.Timestamp("2024-02-23").date())
    row5 = _base_row(strike=180.0, expiration=pd.Timestamp("2024-02-23").date())

    result = load_minute_slice(_TS, conn=_panel(row1, row2, row3, row4, row5))
    
    # Check slice_strike_count per expiration
    exp_20240216 = pd.Timestamp("2024-02-16").date()
    exp_20240223 = pd.Timestamp("2024-02-23").date()
    
    slice_20240216 = result[result["expiration"] == exp_20240216]
    slice_20240223 = result[result["expiration"] == exp_20240223]
    
    assert slice_20240216.iloc[0]["slice_strike_count"] == 2
    assert slice_20240223.iloc[0]["slice_strike_count"] == 3


def test_anchor_k_star_computed():
    """Test anchor_k_star (belly strike) is computed per expiration."""
    row1 = _base_row(strike=140.0, log_moneyness=-0.05)
    row2 = _base_row(strike=149.0, log_moneyness=0.001)  # Near ATM
    row3 = _base_row(strike=160.0, log_moneyness=0.05)

    result = load_minute_slice(_TS, conn=_panel(row1, row2, row3))
    
    # anchor_k_star should be the log_moneyness closest to 0 (row2)
    assert result.iloc[0]["anchor_k_star"] == pytest.approx(0.001, abs=0.01)


def test_anchor_theta_star_computed():
    """Test anchor_theta_star (ATM total variance) is computed per expiration."""
    row = _base_row(implied_vol=0.35, business_t=4/252)
    result = load_minute_slice(_TS, conn=_panel(row))
    
    expected_theta = (0.35 ** 2) * (4/252)
    assert result.iloc[0]["anchor_theta_star"] == pytest.approx(expected_theta, rel=0.01)


def test_parity_skew_computed():
    """Test parity_skew is computed (placeholder implementation returns 0)."""
    row = _base_row()
    result = load_minute_slice(_TS, conn=_panel(row))
    
    assert "parity_skew" in result.columns
    # Placeholder implementation returns 0.0
    assert result.iloc[0]["parity_skew"] == 0.0


def test_anchor_quality_computed():
    """Test anchor_quality is computed per expiration."""
    row = _base_row()
    result = load_minute_slice(_TS, conn=_panel(row))
    
    assert "anchor_quality" in result.columns
    assert result.iloc[0]["anchor_quality"] > 0


def test_belly_flag_near_anchor():
    """Test belly_flag is True for strikes near anchor_k_star."""
    row_atm = _base_row(strike=149.0, log_moneyness=0.001)
    row_far = _base_row(strike=200.0, log_moneyness=0.3)

    result = load_minute_slice(_TS, conn=_panel(row_atm, row_far))
    
    # ATM row should have belly_flag = True
    atm_row = result[result["strike"] == 149.0]
    far_row = result[result["strike"] == 200.0]
    
    assert bool(atm_row.iloc[0]["belly_flag"]) is True
    assert bool(far_row.iloc[0]["belly_flag"]) is False
