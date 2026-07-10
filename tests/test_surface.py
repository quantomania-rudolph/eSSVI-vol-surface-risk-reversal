"""Tests for essvi.surface — continuous eSSVI interpolation and extrapolation."""

from __future__ import annotations

import numpy as np
import pytest

from essvi import config as cfg
from essvi.objective import w_slice
from essvi.surface import (
    extrapolate_long_theta,
    extrapolate_short_theta,
    get_params_at_T,
    interpolate_psi,
    interpolate_rho,
    interpolate_theta,
    sigma_surface,
    surface_grid,
    tail_slope_check,
    w_surface,
)

# Calendar-compatible chain (same rho, monotone theta/psi).
SLICE_PARAMS = [
    {"T": 7 / 252.0, "theta": 0.06, "phi": 0.9, "psi": 0.06 * 0.9, "rho": -0.40},
    {"T": 30 / 252.0, "theta": 0.08, "phi": 0.675, "psi": 0.08 * 0.675, "rho": -0.40},
    {"T": 60 / 252.0, "theta": 0.10, "phi": 0.54, "psi": 0.10 * 0.54, "rho": -0.40},
]

TS = np.array([s["T"] for s in SLICE_PARAMS])
THETAS = np.array([s["theta"] for s in SLICE_PARAMS])
PSIS = np.array([s["psi"] for s in SLICE_PARAMS])
RHOS = np.array([s["rho"] for s in SLICE_PARAMS])


def test_theta_linear_interpolation():
    t_mid = (TS[0] + TS[1]) / 2.0
    theta_mid = interpolate_theta(t_mid, TS, THETAS)
    assert theta_mid == pytest.approx((THETAS[0] + THETAS[1]) / 2.0, rel=0, abs=1e-12)


def test_psi_flat_interpolation():
    t_mid = (TS[0] + TS[1]) / 2.0
    psi_mid = interpolate_psi(t_mid, TS, PSIS)
    assert psi_mid == pytest.approx(PSIS[0], rel=0, abs=1e-12)


def test_rho_flat_interpolation():
    t_mid = (TS[0] + TS[1]) / 2.0
    rho_mid = interpolate_rho(t_mid, TS, RHOS)
    assert rho_mid == pytest.approx(RHOS[0], rel=0, abs=1e-12)


def test_short_extrapolation_corbetta():
    theta_small = extrapolate_short_theta(TS[0] / 10.0, TS[0], THETAS[0], mode="corbetta")
    assert theta_small < THETAS[0]
    theta_zero = extrapolate_short_theta(0.0, TS[0], THETAS[0], mode="corbetta")
    assert theta_zero == pytest.approx(cfg.THETA_PROJECTION_EPS, rel=0, abs=1e-9)


def test_short_extrapolation_flat():
    theta_flat = extrapolate_short_theta(TS[0] / 10.0, TS[0], THETAS[0], mode="flat")
    assert theta_flat == pytest.approx(THETAS[0], rel=0, abs=1e-12)


def test_long_extrapolation_slope():
    t_long = TS[-1] + 0.05
    expected_slope = PSIS[-1] / (1.0 + abs(RHOS[-1]))
    theta_long = extrapolate_long_theta(t_long, TS[-1], THETAS[-1], PSIS[-1], RHOS[-1])
    assert theta_long == pytest.approx(THETAS[-1] + expected_slope * 0.05, rel=0, abs=1e-12)


def test_w_surface_at_calibrated_expiries():
    k = np.linspace(-0.5, 0.5, 11)
    for entry in SLICE_PARAMS:
        t_val = entry["T"]
        expected = w_slice(k, entry["theta"], entry["phi"], entry["rho"])
        actual = w_surface(k, t_val, SLICE_PARAMS)
        np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-10)


def test_w_surface_between_expiries():
    k = np.array([0.0, -0.2, 0.15])
    t_vals = np.linspace(TS[0], TS[-1], 25)
    w_path = np.array([w_surface(k, float(t), SLICE_PARAMS) for t in t_vals])
    diffs = np.diff(w_path, axis=0)
    assert np.all(np.isfinite(w_path))
    assert np.all(diffs[:, 0] >= -1e-8)


def test_sigma_surface_positive():
    k = np.linspace(-1.0, 1.0, 21)
    t_vals = np.linspace(TS[0] / 2.0, TS[-1] * 1.2, 15)
    for t_val in t_vals:
        sigma = sigma_surface(k, float(t_val), SLICE_PARAMS)
        assert np.all(np.asarray(sigma) > 0.0)


def test_tail_slope_within_cap():
    k = np.array([-5.0, -3.5, 3.5, 5.0])
    w = w_surface(k, TS[1], SLICE_PARAMS)
    assert tail_slope_check(k, w)


def test_tail_slope_violation_detected():
    k = np.array([-4.0, 4.0])
    w_bad = np.array([20.0, 20.0])
    assert not tail_slope_check(k, w_bad)


def test_surface_grid_shape():
    k_range = np.linspace(-0.5, 0.5, 9)
    t_range = np.linspace(TS[0], TS[-1], 5)
    grid = surface_grid(k_range, t_range, SLICE_PARAMS)
    assert grid.shape == (len(k_range), len(t_range))


def test_surface_monotonic_in_T():
    k = np.array([0.0, -0.15, 0.2])
    t_vals = np.linspace(TS[0], TS[-1] * 1.1, 30)
    for ki in range(k.size):
        w_vals = [float(w_surface(k[ki], float(t), SLICE_PARAMS)) for t in t_vals]
        assert all(w_vals[i + 1] >= w_vals[i] - 1e-9 for i in range(len(w_vals) - 1))


def test_surface_smooth_across_knots():
    k = 0.0
    eps = 1e-6
    for t_knot in TS[1:-1]:
        w_left = float(w_surface(k, float(t_knot - eps), SLICE_PARAMS))
        w_right = float(w_surface(k, float(t_knot + eps), SLICE_PARAMS))
        w_knot = float(w_surface(k, float(t_knot), SLICE_PARAMS))
        assert abs(w_left - w_knot) < 1e-4
        assert abs(w_right - w_knot) < 1e-4


def test_get_params_at_T_beyond_range():
    theta_short, phi_short, rho_short = get_params_at_T(TS[0] / 5.0, SLICE_PARAMS)
    theta_long, phi_long, rho_long = get_params_at_T(TS[-1] + 0.1, SLICE_PARAMS)

    assert theta_short > cfg.THETA_PROJECTION_EPS
    assert phi_short > cfg.THETA_PROJECTION_EPS
    assert rho_short == pytest.approx(RHOS[0], rel=0, abs=1e-12)

    assert theta_long > THETAS[-1]
    assert phi_long > cfg.THETA_PROJECTION_EPS
    assert rho_long == pytest.approx(RHOS[-1], rel=0, abs=1e-12)
