"""Tests for essvi.regularize."""

from __future__ import annotations

import numpy as np
import pytest

from essvi import config as cfg
from essvi.regularize import (
    spatial_reg_penalty,
    temporal_reg_penalty,
    warmstart_params,
)


def test_spatial_reg_zero_when_constant():
    rho = np.array([-0.3, -0.3, -0.3])
    psi = np.array([0.4, 0.4, 0.4])
    assert spatial_reg_penalty(rho, psi) == 0.0


def test_spatial_reg_positive_when_jumps():
    rho = np.array([-0.3, 0.2])
    psi = np.array([0.4, 0.4])
    penalty = spatial_reg_penalty(rho, psi)
    assert penalty > 0.0


def test_spatial_reg_symmetric():
    rho_a = np.array([-0.3, 0.2])
    rho_b = np.array([0.2, -0.3])
    psi = np.array([0.4, 0.4])
    assert spatial_reg_penalty(rho_a, psi) == pytest.approx(
        spatial_reg_penalty(rho_b, psi)
    )


def test_spatial_reg_scales_with_lambda():
    rho = np.array([-0.3, 0.2])
    psi = np.array([0.4, 0.6])
    base = spatial_reg_penalty(rho, psi)
    doubled = spatial_reg_penalty(
        rho,
        psi,
        lambda_rho=cfg.LAMBDA_RHO * 2,
        lambda_psi=cfg.LAMBDA_PSI * 2,
    )
    assert doubled == pytest.approx(2.0 * base)


def test_temporal_reg_zero_when_no_prior():
    theta = np.array([0.1, 0.2])
    rho = np.array([-0.3, -0.2])
    psi = np.array([0.4, 0.5])
    assert (
        temporal_reg_penalty(theta, rho, psi, None, rho, psi) == 0.0
    )
    assert (
        temporal_reg_penalty(theta, rho, psi, theta, None, psi) == 0.0
    )
    assert (
        temporal_reg_penalty(theta, rho, psi, theta, rho, None) == 0.0
    )


def test_temporal_reg_zero_when_identical():
    theta = np.array([0.1, 0.2])
    rho = np.array([-0.3, -0.2])
    psi = np.array([0.4, 0.5])
    assert temporal_reg_penalty(theta, rho, psi, theta, rho, psi) == 0.0


def test_temporal_reg_positive_when_different():
    theta = np.array([0.1, 0.2])
    rho = np.array([-0.3, -0.2])
    psi = np.array([0.4, 0.5])
    theta_prior = np.array([0.15, 0.25])
    rho_prior = np.array([-0.25, -0.15])
    psi_prior = np.array([0.45, 0.55])
    penalty = temporal_reg_penalty(
        theta, rho, psi, theta_prior, rho_prior, psi_prior
    )
    assert penalty > 0.0


def test_temporal_reg_log_theta():
    theta = np.array([0.1, 0.2])
    theta_prior = np.array([0.2, 0.4])
    rho = np.array([-0.3, -0.2])
    psi = np.array([0.4, 0.5])

    log_penalty = temporal_reg_penalty(
        theta, rho, psi, theta_prior, rho, psi, use_log_theta=True
    )
    linear_penalty = temporal_reg_penalty(
        theta, rho, psi, theta_prior, rho, psi, use_log_theta=False
    )
    assert log_penalty != linear_penalty

    expected_log = cfg.LAMBDA_TEMPORAL * np.sum(
        (
            (np.log(theta) - np.log(theta_prior)) / cfg.TEMPORAL_THETA_SCALE
        )
        ** 2
    )
    assert log_penalty == pytest.approx(expected_log)


def test_temporal_reg_linear_theta():
    theta = np.array([0.1, 0.2])
    theta_prior = np.array([0.15, 0.25])
    rho = np.array([-0.3, -0.2])
    psi = np.array([0.4, 0.5])

    penalty = temporal_reg_penalty(
        theta, rho, psi, theta_prior, rho, psi, use_log_theta=False
    )
    expected = cfg.LAMBDA_TEMPORAL * np.sum(
        ((theta - theta_prior) / cfg.TEMPORAL_THETA_SCALE) ** 2
    )
    assert penalty == pytest.approx(expected)


def test_warmstart_returns_prior_when_available():
    prior = {
        "theta_grid": np.array([0.1, 0.2, 0.3]),
        "rho_grid": np.array([-0.4, -0.3, -0.2]),
        "psi_grid": np.array([0.5, 0.6, 0.7]),
    }
    result = warmstart_params(prior, n_slices=3)
    np.testing.assert_array_equal(result["theta_0"], prior["theta_grid"])
    np.testing.assert_array_equal(result["rho_0"], prior["rho_grid"])
    np.testing.assert_array_equal(result["psi_0"], prior["psi_grid"])


def test_warmstart_returns_fallback_when_none():
    n = 4
    result = warmstart_params(None, n_slices=n)
    assert result["theta_0"].shape == (n,)
    assert result["rho_0"].shape == (n,)
    assert result["psi_0"].shape == (n,)
    np.testing.assert_array_equal(
        result["rho_0"], np.full(n, cfg.SHORT_MATURITY_RHO_PRIOR)
    )
    np.testing.assert_array_equal(
        result["psi_0"], np.full(n, cfg.TEMPORAL_PSI_SCALE)
    )
    np.testing.assert_array_equal(
        result["theta_0"], np.full(n, cfg.TEMPORAL_THETA_SCALE)
    )


def test_scale_normalization():
    rho = np.array([-0.3, 0.2])
    psi = np.array([0.4, 0.6])
    penalty_default = spatial_reg_penalty(rho, psi)
    penalty_wide = spatial_reg_penalty(
        rho, psi, scale_rho=1.0, scale_psi=1.0
    )
    assert penalty_wide < penalty_default

    theta = np.array([0.1, 0.2])
    theta_prior = np.array([0.15, 0.25])
    rho_arr = np.array([-0.3, -0.2])
    psi_arr = np.array([0.4, 0.5])
    temp_default = temporal_reg_penalty(
        theta, rho_arr, psi_arr, theta_prior, rho_arr, psi_arr
    )
    temp_wide = temporal_reg_penalty(
        theta,
        rho_arr,
        psi_arr,
        theta_prior,
        rho_arr,
        psi_arr,
        scale_theta=1.0,
        scale_rho=1.0,
        scale_psi=1.0,
    )
    assert temp_wide < temp_default


def test_theta_log_prevents_negative():
    theta = np.array([0.0, 0.1])
    theta_prior = np.array([0.1, 0.2])
    rho = np.array([-0.3, -0.2])
    psi = np.array([0.4, 0.5])

    penalty = temporal_reg_penalty(
        theta, rho, psi, theta_prior, rho, psi, use_log_theta=True
    )
    assert np.isfinite(penalty)
    assert penalty >= 0.0

    clipped_theta = np.clip(theta, cfg.THETA_PROJECTION_EPS, None)
    expected = cfg.LAMBDA_TEMPORAL * np.sum(
        (
            (np.log(clipped_theta) - np.log(theta_prior))
            / cfg.TEMPORAL_THETA_SCALE
        )
        ** 2
    )
    assert penalty == pytest.approx(expected)
