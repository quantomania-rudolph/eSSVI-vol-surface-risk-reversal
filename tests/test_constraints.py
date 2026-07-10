"""Tests for essvi.constraints no-arbitrage checks and corridor builder."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from essvi import config as cfg
from essvi.constraints import (
    build_corridor,
    check_butterfly,
    check_butterfly_gj,
    check_butterfly_mm,
    check_calendar_pasquazzi,
    check_lee_bound,
    check_vertical_spread,
    compute_f_MM,
)


def _slice_df(
    *,
    k_star: float = 0.0,
    theta_star: float = 0.04,
    business_t: float = 0.05,
    strikes: tuple[float, ...] = (-0.5, 0.0, 0.5, 1.0),
) -> pd.DataFrame:
    rows = []
    for k in strikes:
        rows.append(
            {
                "anchor_k_star": k_star,
                "anchor_theta_star": theta_star,
                "business_t": business_t,
                "log_moneyness": k,
            }
        )
    return pd.DataFrame(rows)


def test_butterfly_b1_violated():
    ok, msg = check_butterfly_gj(theta=1.0, phi=5.0, rho=0.0)
    assert not ok
    assert "B1" in msg


def test_butterfly_b2_violated():
    ok, msg = check_butterfly_gj(theta=0.001, phi=100.0, rho=0.0)
    assert not ok
    assert "B2" in msg


def test_butterfly_both_pass():
    ok, msg = check_butterfly_gj(theta=0.04, phi=1.0, rho=-0.5)
    assert ok, msg
    ok_mm, msg_mm = check_butterfly_mm(theta=0.04, phi=1.0, rho=-0.5)
    assert ok_mm, msg_mm


def test_butterfly_mm_tighter_than_gj():
    """MM boundary is at least as permissive as GJ: F_MM >= 4*theta/(1+|rho|)."""
    thetas = np.logspace(-3, 0, 20)
    rhos = np.linspace(0.0, 0.95, 10)
    for theta in thetas:
        for abs_rho in rhos:
            f_mm = compute_f_MM(float(theta), float(abs_rho))
            gj = 4.0 * theta / (1.0 + abs_rho)
            assert f_mm + 1e-6 >= gj

    # Borderline: GJ B2 fails while MM-2 still passes when F_MM > psi^2.
    theta = 0.01
    rho = 0.0
    phi = 20.57609856878415
    gj_ok, _ = check_butterfly_gj(theta, phi, rho)
    mm_ok, _ = check_butterfly_mm(theta, phi, rho)
    assert not gj_ok
    assert mm_ok


def test_calendar_pasquazzi_case_a():
    params1 = {"theta": 0.04, "phi": 1.0, "rho": -0.4}
    params2 = {"theta": 0.05, "phi": 0.8, "rho": -0.4}
    ok, msg = check_calendar_pasquazzi(params1, params2)
    assert ok, msg


def test_calendar_pasquazzi_violated_theta_ratio():
    params1 = {"theta": 0.05, "phi": 1.0, "rho": -0.3}
    params2 = {"theta": 0.04, "phi": 1.0, "rho": -0.3}
    ok, msg = check_calendar_pasquazzi(params1, params2)
    assert not ok
    assert "Theta" in msg


def test_calendar_pasquazzi_violated_delta_rho():
    params1 = {"theta": 0.04, "phi": 1.0, "rho": -0.8}
    params2 = {"theta": 0.05, "phi": 1.0, "rho": 0.7}
    ok, msg = check_calendar_pasquazzi(params1, params2)
    assert not ok
    assert "calendar" in msg.lower()


def test_vertical_spread_violated():
    df = _slice_df(strikes=(-2.0, -1.0, 0.0, 1.0, 2.0))
    params = {"theta": 5.0, "phi": 20.0, "rho": 0.95}
    ok, msg = check_vertical_spread(params, df, tolerance=0.0)
    assert not ok
    assert "vertical spread" in msg.lower()


def test_lee_bound_pass():
    ok, msg = check_lee_bound(theta=0.04, phi=1.0, rho=-0.5)
    assert ok, msg


def test_lee_bound_violated():
    ok, msg = check_lee_bound(theta=1.0, phi=5.0, rho=0.0)
    assert not ok
    assert "Lee" in msg


def test_corridor_nonempty():
    df = _slice_df(theta_star=0.04, k_star=0.0)
    corridor = build_corridor(rho=-0.3, prev_slice_params=None, df_slice=df)
    assert corridor["valid"]
    assert corridor["phi_min"] > 0.0
    assert corridor["phi_max"] >= corridor["phi_min"]
    assert math.isfinite(corridor["phi_min"])
    assert math.isfinite(corridor["phi_max"])


def test_corridor_empty_when_rho_extreme():
    df = _slice_df(theta_star=0.04, k_star=0.0)
    prev = {"theta": 0.04, "phi": 50.0, "rho": -0.95}
    corridor = build_corridor(rho=0.89, prev_slice_params=prev, df_slice=df)
    assert not corridor["valid"]
    assert len(corridor["violations"]) > 0


def test_corridor_edges_butterfly():
    df = _slice_df(theta_star=0.04, k_star=0.0)
    corridor = build_corridor(rho=0.0, prev_slice_params=None, df_slice=df)
    assert corridor["valid"]

    psi_hi = corridor["psi_max"]
    assert psi_hi <= 4.0 / (1.0 + 0.0) + 1e-3


def test_corridor_edges_calendar():
    df = _slice_df(theta_star=0.05, k_star=0.0)
    prev = {"theta": 0.04, "phi": 1.0, "rho": -0.4}
    corridor = build_corridor(rho=-0.35, prev_slice_params=prev, df_slice=df)
    assert corridor["valid"]

    phi_mid = 0.5 * (corridor["phi_min"] + corridor["phi_max"])
    theta_min = corridor["theta_min_phi"](phi_mid)
    assert theta_min >= prev["theta"] - 1e-8


def test_f_MM_monotonic():
    abs_rho = 0.4
    thetas = np.linspace(0.01, 0.2, 15)
    f_vals = [compute_f_MM(float(t), abs_rho) for t in thetas]
    for left, right in zip(f_vals, f_vals[1:]):
        assert right >= left - 1e-6


def test_check_butterfly_dispatches_to_config(monkeypatch):
    monkeypatch.setattr(cfg, "BUTTERFLY_BOUND_MODE", "gj_conservative")
    ok, _ = check_butterfly(theta=0.04, phi=1.0, rho=-0.5)
    assert ok
