"""Tests for essvi.sequential — forward DTE-ascending coordinator."""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.usefixtures("essvi_fast_calibration")

from essvi import config as cfg
from essvi.constraints import check_calendar_pasquazzi
from essvi.objective import w_slice
from essvi.sequential import (
    build_correlation_grid,
    calibrate_one_minute,
    handle_degenerate_slice,
    should_cold_start,
    validate_minute_result,
)

# Calendar-compatible chain (same rho, psi monotone) for multi-slice tests.
CALIB_SPECS = [
    (7, 0.06, 0.9, -0.40, 7 / 252.0),
    (30, 0.08, 0.675, -0.40, 30 / 252.0),
    (60, 0.10, 0.54, -0.40, 60 / 252.0),
]


def _row(
    *,
    log_moneyness: float,
    implied_vol: float,
    business_t: float = 0.25,
    rel_spread: float = 0.05,
    oi: int = 200,
    delta_black76: float = 0.50,
    vega: float = 0.30,
    anchor_k_star: float = 0.0,
    anchor_theta_star: float = 0.08,
    dte: int = 30,
    strike: float = 100.0,
    timestamp: pd.Timestamp | None = None,
    session_phase: str = "rth",
    belly_flag: bool | None = None,
) -> dict:
    row = {
        "timestamp": timestamp or pd.Timestamp("2024-06-03 14:30:00", tz="UTC"),
        "expiration": pd.Timestamp("2024-07-05", tz="UTC"),
        "strike": strike,
        "log_moneyness": log_moneyness,
        "implied_vol": implied_vol,
        "business_t": business_t,
        "rel_spread": rel_spread,
        "oi": oi,
        "delta_black76": delta_black76,
        "vega": vega,
        "anchor_k_star": anchor_k_star,
        "anchor_theta_star": anchor_theta_star,
        "dte": dte,
        "session_phase": session_phase,
    }
    if belly_flag is not None:
        row["belly_flag"] = belly_flag
    return row


def _synthetic_slice(
    theta: float,
    phi: float,
    rho: float,
    *,
    dte: int = 30,
    business_t: float = 0.25,
    strikes: np.ndarray | None = None,
    timestamp: pd.Timestamp | None = None,
    session_phase: str = "rth",
) -> pd.DataFrame:
    if strikes is None:
        strikes = np.linspace(-0.4, 0.4, 17)

    w = w_slice(strikes, theta, phi, rho)
    w_atm = float(w_slice(0.0, theta, phi, rho))
    rows = []
    for i, (k, w_k) in enumerate(zip(strikes, w)):
        iv = math.sqrt(max(w_k, 1e-12) / business_t)
        delta = 0.5 - 0.4 * k
        rows.append(
            _row(
                log_moneyness=float(k),
                implied_vol=iv,
                business_t=business_t,
                delta_black76=float(np.clip(delta, 0.05, 0.95)),
                anchor_k_star=0.0,
                anchor_theta_star=w_atm,
                dte=dte,
                strike=100.0 + float(k) * 10.0,
                timestamp=timestamp,
                session_phase=session_phase,
                belly_flag=abs(k) <= cfg.BELLY_K_ABS,
            )
        )
    return pd.DataFrame(rows)


def _minute_from_slices(
    specs: list[tuple[int, float, float, float, float]],
    *,
    timestamp: pd.Timestamp | None = None,
    session_phase: str = "rth",
) -> pd.DataFrame:
    ts = timestamp or pd.Timestamp("2024-06-03 14:30:00", tz="UTC")
    frames = []
    for dte, theta, phi, rho, business_t in specs:
        frames.append(
            _synthetic_slice(
                theta,
                phi,
                rho,
                dte=dte,
                business_t=business_t,
                timestamp=ts,
                session_phase=session_phase,
            )
        )
    return pd.concat(frames, ignore_index=True)


def _prior_from_specs(
    specs: list[tuple[int, float, float, float]],
) -> dict:
    dte_grid = np.array([s[0] for s in specs], dtype=int)
    theta_grid = np.array([s[1] for s in specs], dtype=float)
    rho_grid = np.array([s[3] for s in specs], dtype=float)
    psi_grid = np.array([s[1] * s[2] for s in specs], dtype=float)
    return {
        "timestamp": pd.Timestamp("2024-06-03 14:29:00", tz="UTC"),
        "dte_grid": dte_grid,
        "theta_grid": theta_grid,
        "rho_grid": rho_grid,
        "psi_grid": psi_grid,
    }


def test_calibrate_one_minute_basic():
    df = _minute_from_slices(CALIB_SPECS)
    result = calibrate_one_minute(df, prior_minute_params=None)
    assert result["n_slices"] == 3
    assert result["n_valid"] == 3
    assert not result["any_invalid"]
    assert not result["is_total_kill"]
    assert validate_minute_result(result)


def test_calibrate_one_minute_slice_order():
    df = _minute_from_slices(list(reversed(CALIB_SPECS)))
    result = calibrate_one_minute(df, prior_minute_params=None)
    dtes = [sl["dte"] for sl in result["slices"]]
    assert dtes == sorted(dtes)
    assert dtes == [7, 30, 60]


def test_calendar_holds_between_slices():
    df = _minute_from_slices(CALIB_SPECS)
    result = calibrate_one_minute(df, prior_minute_params=None)
    slices = result["slices"]
    for i in range(len(slices) - 1):
        psi_i = slices[i]["psi"]
        psi_j = slices[i + 1]["psi"]
        assert psi_i <= psi_j + cfg.THETA_MONOTONICITY_EPS
        prev = {
            "theta": slices[i]["theta"],
            "phi": slices[i]["phi"],
            "rho": slices[i]["rho"],
        }
        curr = {
            "theta": slices[i + 1]["theta"],
            "phi": slices[i + 1]["phi"],
            "rho": slices[i + 1]["rho"],
        }
        ok, msg = check_calendar_pasquazzi(prev, curr)
        assert ok, msg


def test_calibrate_no_prior():
    df = _minute_from_slices([(30, 0.08, 1.0, -0.35, 30 / 252.0)])
    result = calibrate_one_minute(df, prior_minute_params=None)
    assert result["cold_start"]
    assert not result["temporal_active"]
    assert result["temporal_penalty"] == 0.0


def test_calibrate_with_prior():
    prior_specs = [(dte, theta, phi, rho) for dte, theta, phi, rho, _ in CALIB_SPECS]
    df = _minute_from_slices(CALIB_SPECS)
    prior = _prior_from_specs(prior_specs)
    cold = calibrate_one_minute(df, prior_minute_params=prior, warmstart=False)
    warm = calibrate_one_minute(df, prior_minute_params=prior, warmstart=True)

    assert not warm["cold_start"]
    assert warm["temporal_active"]
    assert warm["temporal_penalty"] >= 0.0

    for sl in warm["slices"]:
        prior_rho = float(prior["rho_grid"][list(prior["dte_grid"]).index(sl["dte"])])
        cold_sl = next(s for s in cold["slices"] if s["dte"] == sl["dte"])
        warm_dist = abs(sl["rho"] - prior_rho)
        cold_dist = abs(cold_sl["rho"] - prior_rho)
        assert warm_dist <= cold_dist + 0.05


def test_short_maturity_degenerate(monkeypatch):
    monkeypatch.setattr(cfg, "SHORT_MATURITY_RHO_FALLBACK", "next_slice")
    df_short = _synthetic_slice(0.05, 1.0, -0.40, dte=1, business_t=1 / 252.0)
    df_short = df_short.iloc[:2].copy()
    df_short["belly_flag"] = False
    df_long = _synthetic_slice(0.08, 1.0, -0.35, dte=30, business_t=30 / 252.0)
    df = pd.concat([df_short, df_long], ignore_index=True)

    result = calibrate_one_minute(df, prior_minute_params=None)
    short_sl = next(sl for sl in result["slices"] if sl["dte"] == 1)
    long_sl = next(sl for sl in result["slices"] if sl["dte"] == 30)
    assert short_sl["quality_flag"] in {
        "EXPIRY_IMMINENT",
        "EXPIRY_IMMINENT_DEFERRED",
        "EXPIRY_IMMINENT_PSI_ONLY",
    }
    assert long_sl["is_valid"]
    assert result["n_slices"] == 2


def test_all_slices_invalid_total_kill():
    bad = _synthetic_slice(0.04, 1.0, -0.20, dte=7, business_t=7 / 252.0)
    bad = bad.iloc[:1].copy()
    bad["implied_vol"] = 0.001
    bad["belly_flag"] = False
    df = pd.concat(
        [
            bad.assign(dte=7),
            bad.assign(dte=30),
            bad.assign(dte=60),
        ],
        ignore_index=True,
    )
    result = calibrate_one_minute(df, prior_minute_params=None)
    assert result["is_total_kill"]
    assert result["n_valid"] == 0
    assert result["any_invalid"]


def test_reanchor_cold_start():
    df = _minute_from_slices(
        [(30, 0.08, 1.0, -0.35, 30 / 252.0)],
        session_phase="pre_open",
    )
    prior = _prior_from_specs([(30, 0.08, 1.0, -0.35)])
    assert should_cold_start(df, prior)
    result = calibrate_one_minute(df, prior_minute_params=prior, warmstart=True)
    assert result["cold_start"]
    assert not result["temporal_active"]
    assert result["temporal_penalty"] == 0.0


def test_build_correlation_grid_correct():
    slices = [
        {"dte": 30, "rho": -0.35},
        {"dte": 7, "rho": -0.40},
        {"dte": 60, "rho": -0.30},
    ]
    grid = build_correlation_grid(slices)
    assert grid.tolist() == pytest.approx([-0.40, -0.35, -0.30])


def test_validate_minute_result_ordering():
    df = _minute_from_slices(CALIB_SPECS[:2])
    result = calibrate_one_minute(df, prior_minute_params=None)
    assert validate_minute_result(result)

    bad = dict(result)
    bad["slices"] = list(reversed(result["slices"]))
    assert not validate_minute_result(bad)


def test_degeneracy_fallback_copies_prior():
    df = _synthetic_slice(0.08, 1.0, -0.35, dte=30, business_t=30 / 252.0)
    prev = {"theta": 0.04, "phi": 50.0, "rho": -0.95}
    prior = _prior_from_specs([(30, 0.08, 1.0, -0.35)])

    sl = handle_degenerate_slice(
        df,
        prev,
        "degeneracy_first",
        prior_minute_params=prior,
        dte=30,
    )
    assert not sl["is_valid"]
    assert sl["quality_flag"] == "DEGENERATE"
    assert sl["rho"] == pytest.approx(-0.35)
    assert sl["theta"] == pytest.approx(0.08)


def test_stale_slice_handling():
    ts = pd.Timestamp("2024-06-03 14:30:00", tz="UTC")
    stale_ts = ts - timedelta(minutes=cfg.STALE_SLICE_MAX_MINUTES + 1)
    df = _minute_from_slices([(30, 0.08, 0.675, -0.40, 30 / 252.0)])
    df.loc[:, "timestamp"] = stale_ts
    df["minute_timestamp"] = ts
    result = calibrate_one_minute(df, prior_minute_params=None)
    assert result["slices"][0]["quality_flag"] == "STALE_SLICE"


def test_n_strikes_per_slice_reported():
    df = _minute_from_slices(CALIB_SPECS[:2])
    result = calibrate_one_minute(df, prior_minute_params=None)
    for sl in result["slices"]:
        assert sl["n_strikes"] > 0
        assert sl["n_belly"] > 0
        assert isinstance(sl["n_strikes"], int)
        assert isinstance(sl["n_belly"], int)


def test_single_slice_minute():
    df = _minute_from_slices([(30, 0.08, 1.0, -0.35, 30 / 252.0)])
    result = calibrate_one_minute(df, prior_minute_params=None)
    assert result["n_slices"] == 1
    assert result["slices"][0]["is_valid"]
    assert validate_minute_result(result)


def test_minute_result_columns_match():
    df = _minute_from_slices(CALIB_SPECS[:2])
    result = calibrate_one_minute(df, prior_minute_params=None)
    required_top = {
        "timestamp",
        "slices",
        "rho_grid",
        "theta_grid",
        "psi_grid",
        "n_slices",
        "n_valid",
        "any_invalid",
        "is_total_kill",
    }
    assert required_top <= set(result.keys())
    required_slice = {
        "dte",
        "rho",
        "theta",
        "phi",
        "psi",
        "anchor_k_star",
        "anchor_theta_star",
        "objective_value",
        "is_valid",
        "n_strikes",
        "n_belly",
        "quality_flag",
        "violations",
    }
    for sl in result["slices"]:
        assert required_slice <= set(sl.keys())
        assert math.isfinite(sl["rho"])
        assert math.isfinite(sl["theta"])
        assert math.isfinite(sl["phi"])
        assert math.isfinite(sl["psi"])
