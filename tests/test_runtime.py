"""Tests for essvi.runtime — minute-level calibration loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from essvi import config as cfg
from essvi.runtime import (
    RuntimeState,
    calibrate_batch,
    calibrate_minute,
    get_runtime_summary,
    get_session_phase,
    is_surface_stale,
    should_calibrate,
)

ET = "America/New_York"


def _ts(hour: int, minute: int, date: str = "2024-06-03") -> pd.Timestamp:
    return pd.Timestamp(f"{date} {hour:02d}:{minute:02d}", tz=ET)


def _mock_minute_result(ts: pd.Timestamp | None = None) -> dict:
    ts = ts or _ts(10, 30)
    slices = [
        {
            "dte": 7,
            "rho": -0.4,
            "theta": 0.06,
            "phi": 0.9,
            "psi": 0.054,
            "anchor_k_star": 0.0,
            "anchor_theta_star": 0.06,
            "objective_value": 0.0,
            "is_valid": True,
            "n_strikes": 10,
            "n_belly": 5,
            "quality_flag": "VALID",
            "violations": [],
        },
        {
            "dte": 30,
            "rho": -0.4,
            "theta": 0.08,
            "phi": 0.675,
            "psi": 0.054,
            "anchor_k_star": 0.0,
            "anchor_theta_star": 0.08,
            "objective_value": 0.0,
            "is_valid": True,
            "n_strikes": 10,
            "n_belly": 5,
            "quality_flag": "VALID",
            "violations": [],
        },
        {
            "dte": 60,
            "rho": -0.4,
            "theta": 0.10,
            "phi": 0.54,
            "psi": 0.054,
            "anchor_k_star": 0.0,
            "anchor_theta_star": 0.10,
            "objective_value": 0.0,
            "is_valid": True,
            "n_strikes": 10,
            "n_belly": 5,
            "quality_flag": "VALID",
            "violations": [],
        },
    ]
    rho_grid = np.array([s["rho"] for s in slices])
    theta_grid = np.array([s["theta"] for s in slices])
    psi_grid = np.array([s["psi"] for s in slices])
    return {
        "timestamp": ts,
        "slices": slices,
        "rho_grid": rho_grid,
        "theta_grid": theta_grid,
        "psi_grid": psi_grid,
        "n_slices": 3,
        "n_valid": 3,
        "any_invalid": False,
        "is_total_kill": False,
    }


def _mock_audit_pass() -> dict:
    return {
        "kill_switch": {
            "kill_triggered": False,
            "total_violations": 0,
            "worst_severity": 0.0,
            "surface_usable": True,
        }
    }


def _mock_audit_fail() -> dict:
    return {
        "kill_switch": {
            "kill_triggered": True,
            "total_violations": 1,
            "worst_severity": 0.01,
            "surface_usable": False,
        }
    }


def _mock_loader_df(n_slices: int = 3) -> pd.DataFrame:
    rows = []
    for dte in [7, 30, 60][:n_slices]:
        for _ in range(cfg.MIN_STRIKES_PER_SLICE):
            rows.append(
                {
                    "expiration": pd.Timestamp("2024-06-10") + pd.Timedelta(days=dte),
                    "slice_strike_count": cfg.MIN_STRIKES_PER_SLICE,
                }
            )
    return pd.DataFrame(rows)


def test_get_session_phase_rth():
    assert get_session_phase(_ts(10, 30)) == "rth"


def test_get_session_phase_pre_open():
    assert get_session_phase(_ts(9, 15)) == "pre_open"


def test_get_session_phase_no_trade():
    assert get_session_phase(_ts(15, 30)) == "no_trade_window"


def test_get_session_phase_post_close():
    assert get_session_phase(_ts(16, 30)) == "post_close"


def test_should_calibrate_rth_only():
    for phase in ("pre_open", "no_trade_window", "post_close"):
        proceed, reason = should_calibrate(_ts(10, 0), phase)
        assert proceed is False
        assert reason == phase
    proceed, reason = should_calibrate(_ts(10, 0), "rth")
    assert proceed is True
    assert reason == "rth"


def test_calibrate_minute_pre_open_skips():
    state = RuntimeState()
    result = calibrate_minute(_ts(9, 15), conn=MagicMock(), state=state)
    assert result["calibrated"] is False
    assert result["reason"] == "pre_open"
    assert state.total_calibrations == 0


def test_calibrate_minute_no_trade_skips():
    state = RuntimeState()
    result = calibrate_minute(_ts(15, 30), conn=MagicMock(), state=state)
    assert result["calibrated"] is False
    assert result["reason"] == "no_trade_window"


@patch("essvi.runtime.audit.run_full_audit", return_value=_mock_audit_pass())
@patch("essvi.runtime.sequential.calibrate_one_minute")
@patch("essvi.runtime.loader.load_minute")
def test_calibrate_minute_success(mock_load, mock_calibrate, _mock_audit):
    ts = _ts(10, 30)
    minute = _mock_minute_result(ts)
    mock_load.return_value = _mock_loader_df()
    mock_calibrate.return_value = minute

    state = RuntimeState()
    result = calibrate_minute(ts, conn=MagicMock(), state=state)

    assert result["calibrated"] is True
    assert result["audit_passed"] is True
    assert result["n_slices"] == 3
    assert state.total_calibrations == 1
    assert state.last_minute_params is minute
    assert state.stale_surface is False


@patch("essvi.runtime.audit.run_full_audit", return_value=_mock_audit_fail())
@patch("essvi.runtime.sequential.calibrate_one_minute")
@patch("essvi.runtime.loader.load_minute")
def test_calibrate_minute_kill_switch_reuses_prior(mock_load, mock_calibrate, _mock_audit):
    ts = _ts(10, 31)
    prior_ts = _ts(10, 30)
    prior = _mock_minute_result(prior_ts)
    mock_load.return_value = _mock_loader_df()
    mock_calibrate.return_value = _mock_minute_result(ts)

    state = RuntimeState(
        last_minute_params=prior,
        last_calibration_time=prior_ts,
        last_surface_id="essvi-prior",
        cold_start=False,
    )
    result = calibrate_minute(ts, conn=MagicMock(), state=state)

    assert result["calibrated"] is True
    assert result["reused_prior"] is True
    assert result["audit_passed"] is False
    assert state.stale_surface is True
    assert state.last_minute_params is prior
    assert state.last_calibration_time == ts


@patch("essvi.runtime.audit.run_full_audit", return_value=_mock_audit_fail())
@patch("essvi.runtime.sequential.calibrate_one_minute")
@patch("essvi.runtime.loader.load_minute")
def test_calibrate_minute_kill_switch_no_prior(mock_load, mock_calibrate, _mock_audit):
    ts = _ts(10, 30)
    mock_load.return_value = _mock_loader_df()
    mock_calibrate.return_value = _mock_minute_result(ts)

    state = RuntimeState()
    result = calibrate_minute(ts, conn=MagicMock(), state=state)

    assert result["calibrated"] is False
    assert result["reason"] == "kill_switch"
    assert state.total_failures == 1
    assert state.last_minute_params is None


@patch("essvi.runtime.audit.run_full_audit", return_value=_mock_audit_pass())
@patch("essvi.runtime.sequential.calibrate_one_minute")
@patch("essvi.runtime.loader.load_minute")
def test_cold_start_resets_prior(mock_load, mock_calibrate, _mock_audit):
    ts = _ts(10, 30)
    mock_load.return_value = _mock_loader_df()
    mock_calibrate.return_value = _mock_minute_result(ts)

    state = RuntimeState(cold_start=True)
    calibrate_minute(ts, conn=MagicMock(), state=state)

    mock_calibrate.assert_called_once()
    args, kwargs = mock_calibrate.call_args
    assert args[1] is None
    assert state.cold_start is False


@patch("essvi.runtime.audit.run_full_audit", return_value=_mock_audit_pass())
@patch("essvi.runtime.sequential.calibrate_one_minute")
@patch("essvi.runtime.loader.load_minute")
def test_cold_start_consumed(mock_load, mock_calibrate, _mock_audit):
    ts1 = _ts(10, 30)
    ts2 = _ts(10, 31)
    minute1 = _mock_minute_result(ts1)
    mock_load.return_value = _mock_loader_df()
    mock_calibrate.side_effect = [minute1, _mock_minute_result(ts2)]

    state = RuntimeState(cold_start=True)
    calibrate_minute(ts1, conn=MagicMock(), state=state)
    calibrate_minute(ts2, conn=MagicMock(), state=state)

    second_call_prior = mock_calibrate.call_args_list[1][0][1]
    assert second_call_prior is minute1
    assert state.cold_start is False


def test_stale_surface_detection():
    state = RuntimeState(
        last_calibration_time=_ts(10, 0),
        stale_surface=False,
    )
    assert is_surface_stale(state, _ts(10, cfg.STALE_SLICE_MAX_MINUTES)) is False
    assert is_surface_stale(state, _ts(10, cfg.STALE_SLICE_MAX_MINUTES + 1)) is True
    assert is_surface_stale(RuntimeState(), _ts(10, 0)) is True


@patch("essvi.runtime.audit.run_full_audit", return_value=_mock_audit_pass())
@patch("essvi.runtime.sequential.calibrate_one_minute")
@patch("essvi.runtime.loader.load_minute")
def test_state_transitions(mock_load, mock_calibrate, _mock_audit):
    ts = _ts(11, 0)
    minute = _mock_minute_result(ts)
    mock_load.return_value = _mock_loader_df()
    mock_calibrate.return_value = minute

    state = RuntimeState()
    assert state.minute_count == 0
    result = calibrate_minute(ts, conn=MagicMock(), state=state)

    assert state.minute_count == 1
    assert state.total_calibrations == 1
    assert state.total_failures == 0
    assert state.last_audit_passed is True
    assert result["surface_id"] == state.last_surface_id


@patch("essvi.runtime.audit.run_full_audit", return_value=_mock_audit_pass())
@patch("essvi.runtime.sequential.calibrate_one_minute")
@patch("essvi.runtime.loader.load_minute")
def test_batch_calibration_iterates_all_minutes(mock_load, mock_calibrate, _mock_audit):
    start = _ts(10, 30)
    end = _ts(10, 32)
    mock_load.return_value = _mock_loader_df()
    mock_calibrate.side_effect = [
        _mock_minute_result(start),
        _mock_minute_result(_ts(10, 31)),
        _mock_minute_result(end),
    ]

    state = RuntimeState()
    results = calibrate_batch(start, end, freq="1min", conn=MagicMock(), state=state)

    assert len(results) == 3
    assert all(r["calibrated"] for r in results)
    assert state.minute_count == 3
    assert state.total_calibrations == 3


def test_runtime_summary_format():
    state = RuntimeState(
        minute_count=5,
        total_calibrations=3,
        total_failures=1,
        last_calibration_time=_ts(10, 30),
        last_surface_id="essvi-test",
        last_audit_passed=True,
        stale_surface=False,
        cold_start=False,
    )
    summary = get_runtime_summary(state)
    assert "eSSVI Runtime Summary" in summary
    assert "minute_count: 5" in summary
    assert "total_calibrations: 3" in summary
    assert "total_failures: 1" in summary
    assert "essvi-test" in summary
    assert "stale_surface: False" in summary
