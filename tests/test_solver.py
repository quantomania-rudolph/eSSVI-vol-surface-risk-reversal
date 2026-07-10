"""Tests for essvi.solver — per-slice rho grid search + Brent inner."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.usefixtures("essvi_fast_calibration")

from essvi import config as cfg
from essvi.constraints import (
    build_corridor,
    check_butterfly,
    check_calendar_pasquazzi,
)
from essvi.objective import w_slice
from essvi.solver import (
    build_rho_grid,
    clamp_params,
    kill_switch,
    refine_rho_grid,
    solve_single_slice,
)


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
) -> dict:
    return {
        "log_moneyness": log_moneyness,
        "implied_vol": implied_vol,
        "business_t": business_t,
        "rel_spread": rel_spread,
        "oi": oi,
        "delta_black76": delta_black76,
        "vega": vega,
        "anchor_k_star": anchor_k_star,
        "anchor_theta_star": anchor_theta_star,
    }


def _synthetic_slice(
    theta: float,
    phi: float,
    rho: float,
    *,
    business_t: float = 0.25,
    strikes: np.ndarray | None = None,
) -> pd.DataFrame:
    if strikes is None:
        strikes = np.linspace(-0.4, 0.4, 17)

    w = w_slice(strikes, theta, phi, rho)
    w_atm = float(w_slice(0.0, theta, phi, rho))
    rows = []
    for k, w_k in zip(strikes, w):
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
            )
        )
    return pd.DataFrame(rows)


def test_build_rho_grid_no_prev():
    grid = build_rho_grid(None)
    assert grid.size > 0
    assert grid[0] == pytest.approx(cfg.RHO_GRID_LO)
    assert grid[-1] <= cfg.RHO_GRID_HI + cfg.RHO_GRID_STEP * 0.5
    step = np.diff(grid)
    np.testing.assert_allclose(step, cfg.RHO_GRID_STEP, rtol=0, atol=1e-12)


def test_build_rho_grid_constrained():
    rho_prev = -0.30
    step = 0.01
    grid = build_rho_grid(rho_prev, step=step, max_step=cfg.RHO_MAX_STEP)
    assert grid.size > 0
    assert np.all(np.abs(grid - rho_prev) <= cfg.RHO_MAX_STEP + 1e-12)
    assert np.isclose(grid, -0.30).any()


def test_build_rho_grid_empty_when_no_rho_possible():
    grid = build_rho_grid(5.0, max_step=0.01)
    assert grid.size == 0


def test_refine_rho_grid_correct_size():
    center = -0.25
    refined = refine_rho_grid(center, cfg.RHO_GRID_STEP, cfg.RHO_GRID_REFINE_FACTOR)
    assert refined.size == cfg.RHO_GRID_REFINE_FACTOR
    assert refined[0] == pytest.approx(center - cfg.RHO_GRID_STEP / 2.0)
    assert refined[-1] == pytest.approx(center + cfg.RHO_GRID_STEP / 2.0)


def test_solve_single_slice_basic():
    theta_true, phi_true, rho_true = 0.08, 1.0, -0.35
    df = _synthetic_slice(theta_true, phi_true, rho_true)
    rho_grid = build_rho_grid(None, step=0.01)
    result = solve_single_slice(df, prev_slice_params=None, rho_grid=rho_grid)

    assert result["is_valid"]
    assert result["objective_value"] < 1e-6
    assert result["rho"] == pytest.approx(rho_true, abs=0.05)
    assert result["theta"] == pytest.approx(theta_true, rel=0.05)
    assert result["phi"] == pytest.approx(phi_true, rel=0.05)


def test_solve_single_slice_respects_calendar():
    prev = {"theta": 0.06, "phi": 0.9, "rho": -0.40}
    # Same rho as prev; phi scaled so Theta*Phi stays calendar-feasible (Case A).
    df = _synthetic_slice(0.08, 0.675, -0.40)
    rho_grid = build_rho_grid(-0.40, step=0.01)
    result = solve_single_slice(df, prev_slice_params=prev, rho_grid=rho_grid)
    assert result["is_valid"]

    current = {
        "theta": result["theta"],
        "phi": result["phi"],
        "rho": result["rho"],
    }
    ok, msg = check_calendar_pasquazzi(prev, current)
    assert ok, msg


def test_solve_single_slice_respects_butterfly():
    df = _synthetic_slice(0.08, 0.8, -0.30)
    result = solve_single_slice(df, prev_slice_params=None)
    ok, msg = check_butterfly(result["theta"], result["phi"], result["rho"])
    assert ok, msg


def test_solve_single_slice_handles_empty_corridor():
    prev = {"theta": 0.04, "phi": 50.0, "rho": -0.95}
    df = _synthetic_slice(0.04, 1.0, -0.20)
    result = solve_single_slice(df, prev_slice_params=prev)
    assert not result["is_valid"]
    assert len(result["violations"]) > 0


def test_solve_single_slice_belly_weighted():
    theta, phi, rho = 0.10, 1.0, -0.25
    strikes = np.linspace(-0.5, 0.5, 21)
    df_clean = _synthetic_slice(theta, phi, rho, strikes=strikes)

    w_belly = w_slice(0.0, theta, phi, rho) * 1.02
    w_wing = w_slice(0.45, theta, phi, rho) * 1.02
    df_pert = df_clean.copy()
    belly_idx = df_pert["log_moneyness"].abs().idxmin()
    wing_idx = (df_pert["log_moneyness"] - 0.45).abs().idxmin()
    df_pert.loc[belly_idx, "implied_vol"] = math.sqrt(w_belly / 0.25)
    df_pert.loc[wing_idx, "implied_vol"] = math.sqrt(w_wing / 0.25)

    result = solve_single_slice(df_pert, prev_slice_params=None)
    assert result["is_valid"]

    w_obs_belly = (df_pert.loc[belly_idx, "implied_vol"] ** 2) * 0.25
    w_obs_wing = (df_pert.loc[wing_idx, "implied_vol"] ** 2) * 0.25
    params = (result["theta"], result["phi"], result["rho"])
    err_belly = abs(w_slice(0.0, *params) - w_obs_belly)
    err_wing = abs(w_slice(0.45, *params) - w_obs_wing)
    assert err_belly < err_wing


def test_solve_single_slice_deterministic():
    df = _synthetic_slice(0.09, 0.9, -0.28)
    r1 = solve_single_slice(df, prev_slice_params=None)
    r2 = solve_single_slice(df, prev_slice_params=None)
    assert r1["rho"] == pytest.approx(r2["rho"])
    assert r1["theta"] == pytest.approx(r2["theta"])
    assert r1["phi"] == pytest.approx(r2["phi"])
    assert r1["objective_value"] == pytest.approx(r2["objective_value"])


def test_clamp_project_theta_upward():
    df = _synthetic_slice(0.08, 1.0, -0.30)
    corridor = build_corridor(-0.30, prev_slice_params=None, df_slice=df)
    assert corridor["valid"]
    phi = corridor["phi_min"]
    theta_low = corridor["theta_min_phi"](phi) - 0.01
    _, theta_out, phi_out = clamp_params(-0.30, theta_low, phi, corridor, None)
    assert theta_out >= corridor["theta_min_phi"](phi_out) - 1e-12
    assert phi_out == pytest.approx(phi)


def test_clamp_project_phi_outside():
    df = _synthetic_slice(0.08, 1.0, -0.30)
    corridor = build_corridor(-0.30, prev_slice_params=None, df_slice=df)
    rho, _, phi_out = clamp_params(
        -0.30,
        0.08,
        corridor["phi_max"] + 1.0,
        corridor,
        None,
    )
    assert phi_out == pytest.approx(corridor["phi_max"])
    assert cfg.RHO_GRID_LO <= rho <= cfg.RHO_GRID_HI


def test_kill_switch_all_pass():
    df = _synthetic_slice(0.08, 0.8, -0.30)
    params = {
        "theta": 0.08,
        "phi": 0.8,
        "rho": -0.30,
        "df_slice": df,
        "prev_slice_params": None,
    }
    ok, violations = kill_switch(params)
    assert ok
    assert violations == []


def test_kill_switch_butterfly_fail():
    params = {
        "theta": 1.0,
        "phi": 5.0,
        "rho": 0.0,
        "df_slice": None,
        "prev_slice_params": None,
    }
    ok, violations = kill_switch(params)
    assert not ok
    assert any(v[0] == "BUTTERFLY" for v in violations)


def test_kill_switch_tolerances_respected():
    ok_borderline, violations_borderline = kill_switch(
        {
            "theta": 0.04,
            "phi": 1.0,
            "rho": -0.5,
            "df_slice": None,
            "prev_slice_params": None,
        }
    )
    assert ok_borderline
    assert violations_borderline == []

    ok_fail, violations_fail = kill_switch(
        {
            "theta": 1.0,
            "phi": 5.0,
            "rho": 0.0,
            "df_slice": None,
            "prev_slice_params": None,
        }
    )
    assert not ok_fail
    assert len(violations_fail) > 0


def test_solver_output_contains_all_required_keys():
    df = _synthetic_slice(0.08, 1.0, -0.30)
    result = solve_single_slice(df, prev_slice_params=None)
    required = {
        "rho",
        "theta",
        "phi",
        "objective_value",
        "corridor",
        "is_valid",
        "violations",
        "n_iterations",
        "anchor_k_star",
        "anchor_theta_star",
    }
    assert required <= set(result.keys())
    assert result["n_iterations"] > 0
    assert isinstance(result["corridor"], dict)
