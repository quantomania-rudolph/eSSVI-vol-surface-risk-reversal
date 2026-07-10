"""Tests for essvi.objective — eSSVI slice formula and weighted objective."""

from __future__ import annotations

import numpy as np
import pytest

from essvi import config as cfg
from essvi.objective import belly_boost, objective_slice, w_slice, w_slice_derivatives


def test_w_slice_atm():
  theta, phi, rho = 0.08, 1.2, -0.4
  w_atm = w_slice(0.0, theta, phi, rho)
  assert w_atm == pytest.approx(theta, rel=0, abs=1e-12)


def test_w_slice_symmetry():
  k = np.linspace(-1.5, 1.5, 17)
  theta, phi, rho = 0.10, 0.8, -0.35
  w_pos = w_slice(k, theta, phi, rho)
  w_neg = w_slice(-k, theta, phi, -rho)
  np.testing.assert_allclose(w_pos, w_neg, rtol=1e-12, atol=1e-12)


def test_w_slice_derivative_closed_form():
  k = np.array([-0.8, -0.2, 0.0, 0.3, 1.1])
  theta, phi, rho = 0.12, 1.0, -0.25
  _, w_prime, w_double_prime = w_slice_derivatives(k, theta, phi, rho)

  eps = 1e-5
  w_plus = w_slice(k + eps, theta, phi, rho)
  w_minus = w_slice(k - eps, theta, phi, rho)
  w_num_prime = (w_plus - w_minus) / (2.0 * eps)

  w_plus2 = w_slice(k + eps, theta, phi, rho)
  w_center = w_slice(k, theta, phi, rho)
  w_minus2 = w_slice(k - eps, theta, phi, rho)
  w_num_double = (w_plus2 - 2.0 * w_center + w_minus2) / (eps**2)

  np.testing.assert_allclose(w_prime, w_num_prime, rtol=0, atol=1e-6)
  np.testing.assert_allclose(w_double_prime, w_num_double, rtol=0, atol=1e-6)


def test_w_slice_monotonicity():
  # Negative skew: w decreases with k over the belly fit band (w' turns positive
  # only deep in the far OTM call wing).
  k = np.linspace(-1.5, 0.75, 46)
  theta, phi, rho = 0.15, 1.1, -0.5
  assert rho < 0
  _, w_prime, _ = w_slice_derivatives(k, theta, phi, rho)
  assert np.all(w_prime <= 1e-10)


def test_w_slice_convexity():
  k = np.linspace(-3.0, 3.0, 121)
  theta, phi, rho = 0.20, 0.9, 0.3
  _, _, w_double_prime = w_slice_derivatives(k, theta, phi, rho)
  assert np.all(w_double_prime > 0)


def test_belly_boost_within_range():
  k = np.array([-cfg.BELLY_K_ABS, -0.05, 0.0, 0.10, cfg.BELLY_K_ABS])
  boost = belly_boost(k)
  np.testing.assert_allclose(boost, cfg.BELLY_BOOST)


def test_belly_boost_outside_range():
  k = np.array([-0.20, -0.16, 0.16, 0.50])
  boost = belly_boost(k)
  np.testing.assert_allclose(boost, 1.0)


def test_objective_zero_when_perfect_fit():
  params = (0.10, 1.0, -0.3)
  k = np.array([-0.4, -0.1, 0.0, 0.2, 0.6])
  w_obs = w_slice(k, *params)
  vega = np.array([0.2, 0.35, 0.5, 0.3, 0.15])
  loss = objective_slice(params, k, w_obs, vega)
  assert loss == pytest.approx(0.0, abs=1e-12)


def test_objective_positive_when_misfit():
  params = (0.10, 1.0, -0.3)
  bad_params = (0.11, 1.05, -0.25)
  k = np.array([-0.3, 0.0, 0.4])
  w_obs = w_slice(k, *params)
  vega = np.array([0.25, 0.45, 0.20])
  loss = objective_slice(bad_params, k, w_obs, vega)
  assert loss > 0


def test_objective_vega_weighting_var_vega2():
  params = (0.10, 1.0, -0.2)
  k = np.array([0.0, 0.3])
  w_obs = w_slice(k, *params) + np.array([0.01, -0.005])
  vega = np.array([0.4, 0.2])
  errors = w_slice(k, *params) - w_obs
  expected = float(np.sum(belly_boost(k) * (errors**2) / (vega**4)))
  actual = objective_slice(params, k, w_obs, vega, mode="var_vega2")
  assert actual == pytest.approx(expected, rel=1e-12)


def test_objective_vega_weighting_vol_vega1():
  params = (0.10, 1.0, -0.2)
  k = np.array([0.0, 0.3])
  w_obs = w_slice(k, *params) + np.array([0.01, -0.005])
  vega = np.array([0.4, 0.2])
  errors = w_slice(k, *params) - w_obs
  expected = float(np.sum(belly_boost(k) * (errors**2) / (vega**2 * w_obs)))
  actual = objective_slice(params, k, w_obs, vega, mode="vol_vega1")
  assert actual == pytest.approx(expected, rel=1e-12)


def test_objective_belly_boost_effect():
  params = (0.10, 1.0, -0.2)
  vega = np.array([0.3])

  k_belly = np.array([0.05])
  w_belly = w_slice(k_belly, *params) + np.array([0.01])

  k_wing = np.array([0.50])
  w_wing = w_slice(k_wing, *params) + np.array([0.01])

  loss_belly = objective_slice(params, k_belly, w_belly, vega)
  loss_wing = objective_slice(params, k_wing, w_wing, vega)
  assert loss_belly == pytest.approx(cfg.BELLY_BOOST * loss_wing, rel=1e-12)


def test_objective_scalar_return():
  params = (0.10, 1.0, -0.2)
  k = np.array([-0.2, 0.1, 0.4])
  w_obs = w_slice(k, *params)
  vega = np.array([0.3, 0.4, 0.25])
  loss = objective_slice(params, k, w_obs, vega)
  assert isinstance(loss, float)
  assert np.isfinite(loss)


def test_objective_finite_for_all_k():
  params = (0.15, 0.7, -0.6)
  k = np.array([-10.0, -3.0, 0.0, 3.0, 10.0])
  w_obs = w_slice(k, *params)
  vega = np.full_like(k, 0.2)
  loss = objective_slice(params, k, w_obs, vega)
  assert np.isfinite(loss)


def test_objective_independent_of_ordering():
  params = (0.12, 0.9, -0.15)
  k = np.array([-0.5, -0.1, 0.0, 0.2, 0.7])
  w_obs = w_slice(k, *params) + np.array([0.005, -0.003, 0.002, -0.004, 0.001])
  vega = np.array([0.2, 0.35, 0.5, 0.3, 0.18])

  loss_ordered = objective_slice(params, k, w_obs, vega)

  perm = np.array([3, 0, 4, 1, 2])
  loss_shuffled = objective_slice(
    params, k[perm], w_obs[perm], vega[perm],
  )
  assert loss_shuffled == pytest.approx(loss_ordered, rel=1e-12)
