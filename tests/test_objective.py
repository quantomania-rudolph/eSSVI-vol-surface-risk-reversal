"""Tests for essvi.objective — eSSVI slice formula and weighted objective."""

from __future__ import annotations

import numpy as np
import pytest

from essvi import config as cfg
from essvi.constraints import theta_from_psi
from essvi.objective import belly_boost, objective_slice, w_slice, w_slice_derivatives, _compute_weights


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
    # params = (psi, rho, phi)
    # psi = theta * phi = 0.10 * 1.0 = 0.10
    params = (0.10, -0.3, 1.0)
    k = np.array([-0.4, -0.1, 0.0, 0.2, 0.6])
    # w_slice expects (theta, phi, rho)
    w_obs = w_slice(k, 0.10, 1.0, -0.3)
    vega = np.array([0.2, 0.35, 0.5, 0.3, 0.15])
    T = 0.1
    theta_star = 0.10
    k_star = 0.0
    loss = objective_slice(params, k, w_obs, vega, T, theta_star, k_star)
    assert loss == pytest.approx(0.0, abs=1e-12)


def test_objective_positive_when_misfit():
    params = (0.10, -0.3, 1.0)  # psi=0.10, rho=-0.3, phi=1.0
    bad_params = (0.11, -0.25, 1.05)  # psi=0.11, rho=-0.25, phi=1.05
    k = np.array([-0.3, 0.0, 0.4])
    w_obs = w_slice(k, 0.10, 1.0, -0.3)
    vega = np.array([0.25, 0.45, 0.20])
    T = 0.1
    theta_star = 0.10
    k_star = 0.0
    loss = objective_slice(bad_params, k, w_obs, vega, T, theta_star, k_star)
    assert loss > 0


def test_var_vega2_weights_atm_heavy():
    """Test that var_vega2 weights ATM strikes HIGHER than wings."""
    # Create slice with ATM at k=0, wings at k=±0.5
    k_arr = np.array([-0.5, -0.1, 0.0, 0.1, 0.5])
    T = 0.1
    
    # w = σ²T, ATM has higher σ (smile shape)
    sigma = np.array([0.4, 0.35, 0.3, 0.35, 0.4])  # smile
    w_arr = sigma**2 * T
    
    # vega ≈ spot * sqrt(T) * φ(d1) — higher at ATM
    vega_arr = np.array([0.05, 0.08, 0.10, 0.08, 0.05])
    
    weights = _compute_weights(w_arr, vega_arr, T, "var_vega2")
    
    # ATM (index 2) should have highest weight
    assert weights[2] == max(weights), f"ATM weight {weights[2]} not max: {weights}"
    # Wings should have lower weight
    assert weights[0] < weights[2] and weights[4] < weights[2]
    
    # Weights should be positive
    assert np.all(weights > 0)


def test_var_vega2_weight_direction():
    """Verify weight direction: ATM gets HIGH weight, wings get LOW weight."""
    k_arr = np.array([-1.0, -0.5, 0.0, 0.5, 1.0])
    T = 0.25
    
    # ATM has highest variance
    sigma = np.array([0.5, 0.4, 0.3, 0.4, 0.5])
    w_arr = sigma**2 * T
    
    # Vega proportional to sqrt(T) * phi(d1) - highest at ATM
    vega_arr = np.array([0.1, 0.15, 0.2, 0.15, 0.1])
    
    weights = _compute_weights(w_arr, vega_arr, T, "var_vega2")
    
    # ATM (index 2) should have highest weight
    atm_weight = weights[2]
    wing_weight_avg = (weights[0] + weights[4]) / 2
    
    assert atm_weight > wing_weight_avg, f"ATM weight {atm_weight} <= wing avg {wing_weight_avg}"
    
    # Old bug: weights = 1/vega² would give ATM LOWEST weight
    # New correct: weights = ν_var² gives ATM HIGHEST weight


def test_objective_convex_in_psi():
    """For fixed ρ, objective should be convex in ψ."""
    anchor_theta_star = 0.10
    anchor_k_star = 0.0
    rho = -0.3
    k_arr = np.array([-0.4, -0.1, 0.0, 0.2, 0.6])
    sigma = np.array([0.4, 0.35, 0.3, 0.35, 0.4])
    T = 0.1
    w_arr = sigma**2 * T
    vega_arr = np.array([0.2, 0.35, 0.5, 0.3, 0.15])
    
    psi_vals = np.linspace(0.05, 0.3, 20)
    objs = []
    for psi in psi_vals:
        # phi = psi / theta, but we need theta first
        theta = anchor_theta_star - rho * psi * anchor_k_star - psi**2 * anchor_k_star**2 * (1 - rho**2) / (4 * anchor_theta_star)
        if theta <= 0:
            objs.append(float('inf'))
            continue
        phi = psi / theta
        params = (psi, rho, phi)
        obj = objective_slice(params, k_arr, w_arr, vega_arr, T, anchor_theta_star, anchor_k_star)
        objs.append(obj)
    
    # Check convex: second differences should be positive
    objs = np.array(objs)
    finite_objs = objs[np.isfinite(objs)]
    if len(finite_objs) >= 3:
        second_diff = np.diff(finite_objs, 2)
        # Allow small numerical tolerance
        assert np.all(second_diff > -1e-10), f"Objective not convex in ψ: {second_diff}"


def test_objective_spatial_regularization():
    """Test spatial regularization uses log(θ) difference."""
    # params = (psi, rho, phi)
    # psi = 0.10, rho = -0.2, phi = 1.0
    params = (0.10, -0.2, 1.0)
    k = np.array([-0.4, -0.1, 0.0, 0.2, 0.6])
    w_obs = w_slice(k, 0.10, 1.0, -0.2)
    vega = np.array([0.2, 0.35, 0.5, 0.3, 0.15])
    T = 0.1
    theta_star = 0.10
    k_star = 0.0
    
    # No regularization
    obj_no_reg = objective_slice(params, k, w_obs, vega, T, theta_star, k_star, 
                                  lambda_spatial=0.0)
    
    # With spatial regularization
    prev_theta = 0.09
    lambda_s = 10.0
    theta = theta_from_psi(params[0], params[1], k_star, theta_star)
    expected_penalty = lambda_s * (np.log(theta) - np.log(prev_theta))**2
    
    obj_with_reg = objective_slice(params, k, w_obs, vega, T, theta_star, k_star,
                                   lambda_spatial=lambda_s, prev_theta=prev_theta)
    
    assert abs(obj_with_reg - obj_no_reg - expected_penalty) < 1e-10


def test_objective_temporal_regularization():
    """Test temporal regularization uses ψ difference."""
    params = (0.10, 1.0, -0.2)
    k = np.array([-0.4, -0.1, 0.0, 0.2, 0.6])
    w_obs = w_slice(k, *params)
    vega = np.array([0.2, 0.35, 0.5, 0.3, 0.15])
    T = 0.1
    theta_star = 0.10
    k_star = 0.0
    
    # No regularization
    obj_no_reg = objective_slice(params, k, w_obs, vega, T, theta_star, k_star,
                                  lambda_temporal=0.0)
    
    # With temporal regularization
    prev_psi = 0.08
    lambda_t = 5.0
    expected_penalty = lambda_t * (params[0] - prev_psi)**2
    
    obj_with_reg = objective_slice(params, k, w_obs, vega, T, theta_star, k_star,
                                   lambda_temporal=lambda_t, prev_psi=prev_psi)
    
    assert abs(obj_with_reg - obj_no_reg - expected_penalty) < 1e-10


def test_objective_belly_boost_effect():
    """Test that belly boost applies within belly region but not in wings."""
    params = (0.10, -0.2, 1.0)  # psi=0.10, rho=-0.2, phi=1.0
    vega = np.array([0.3])
    T = 0.1
    theta_star = 0.10
    k_star = 0.0

    # k=0.05 is within belly region (|k| <= BELLY_K_ABS = 0.15)
    k_belly = np.array([0.05])
    w_belly = w_slice(k_belly, 0.10, 1.0, -0.2) + np.array([0.01])

    # k=0.50 is in wing region (|k| > BELLY_K_ABS)
    k_wing = np.array([0.50])
    w_wing = w_slice(k_wing, 0.10, 1.0, -0.2) + np.array([0.01])

    loss_belly = objective_slice(params, k_belly, w_belly, vega, T, theta_star, k_star)
    loss_wing = objective_slice(params, k_wing, w_wing, vega, T, theta_star, k_star)
    
    # Belly loss should be higher due to belly boost
    assert loss_belly > loss_wing
    
    # Ratio should be roughly BELLY_BOOST times weight ratio
    # Since same vega, weight ratio depends on sigma (via nu_var)
    # But at least we verify belly boost is applied
    assert loss_belly > 0 and loss_wing > 0


def test_objective_scalar_return():
    params = (0.10, 1.0, -0.2)
    k = np.array([-0.2, 0.1, 0.4])
    w_obs = w_slice(k, *params)
    vega = np.array([0.3, 0.4, 0.25])
    T = 0.1
    theta_star = 0.10
    k_star = 0.0
    loss = objective_slice(params, k, w_obs, vega, T, theta_star, k_star)
    assert isinstance(loss, float)
    assert np.isfinite(loss)


def test_objective_finite_for_all_k():
    params = (0.15, 0.7, -0.6)
    k = np.array([-10.0, -3.0, 0.0, 3.0, 10.0])
    w_obs = w_slice(k, *params)
    vega = np.full_like(k, 0.2)
    T = 0.25
    theta_star = 0.15
    k_star = 0.0
    loss = objective_slice(params, k, w_obs, vega, T, theta_star, k_star)
    assert np.isfinite(loss)


def test_objective_independent_of_ordering():
    params = (0.12, 0.9, -0.15)
    k = np.array([-0.5, -0.1, 0.0, 0.2, 0.7])
    w_obs = w_slice(k, *params) + np.array([0.005, -0.003, 0.002, -0.004, 0.001])
    vega = np.array([0.2, 0.35, 0.5, 0.3, 0.18])
    T = 0.1
    theta_star = 0.12
    k_star = 0.0

    loss_ordered = objective_slice(params, k, w_obs, vega, T, theta_star, k_star)

    perm = np.array([3, 0, 4, 1, 2])
    loss_shuffled = objective_slice(
        params, k[perm], w_obs[perm], vega[perm], T, theta_star, k_star
    )
    assert loss_shuffled == pytest.approx(loss_ordered, rel=1e-12)


def test_compute_weights_vol_vega1():
    """Test vol_vega1 weight mode."""
    w_arr = np.array([0.01, 0.02, 0.03])
    vega_arr = np.array([0.2, 0.3, 0.25])
    T = 0.1
    weights = _compute_weights(w_arr, vega_arr, T, "vol_vega1")
    expected = np.abs(vega_arr)
    np.testing.assert_allclose(weights, expected)


def test_compute_weights_uniform():
    """Test uniform weight mode."""
    w_arr = np.array([0.01, 0.02, 0.03])
    vega_arr = np.array([0.2, 0.3, 0.25])
    T = 0.1
    weights = _compute_weights(w_arr, vega_arr, T, "uniform")
    np.testing.assert_allclose(weights, np.ones_like(w_arr))


def test_compute_weights_unknown_mode():
    """Test unknown weight mode raises error."""
    w_arr = np.array([0.01])
    vega_arr = np.array([0.2])
    T = 0.1
    with pytest.raises(ValueError, match="Unknown weight_mode"):
        _compute_weights(w_arr, vega_arr, T, "unknown_mode")


def test_var_vega2_requires_T():
    """Test that var_vega2 mode needs T for conversion."""
    w_arr = np.array([0.01, 0.02, 0.03])
    vega_arr = np.array([0.2, 0.3, 0.25])
    T = 0.1
    
    # This should work without error
    weights = _compute_weights(w_arr, vega_arr, T, "var_vega2")
    assert np.all(weights > 0)
    assert np.all(np.isfinite(weights))


def test_objective_gradient_smooth():
    """Test objective is smooth and finite across psi range."""
    anchor_theta_star = 0.10
    anchor_k_star = 0.0
    rho = -0.3
    k_arr = np.array([-0.4, -0.1, 0.0, 0.2, 0.6])
    sigma = np.array([0.4, 0.35, 0.3, 0.35, 0.4])
    T = 0.1
    w_arr = sigma**2 * T
    vega_arr = np.array([0.2, 0.35, 0.5, 0.3, 0.15])
    
    psi_vals = np.linspace(0.02, 0.2, 20)
    prev_obj = None
    
    for psi in psi_vals:
        theta = anchor_theta_star - rho * psi * anchor_k_star - psi**2 * anchor_k_star**2 * (1 - rho**2) / (4 * anchor_theta_star)
        if theta <= 0:
            continue
        phi = psi / theta
        params = (psi, rho, phi)
        obj = objective_slice(params, k_arr, w_arr, vega_arr, T, anchor_theta_star, anchor_k_star)
        assert np.isfinite(obj), f"Non-finite objective at psi={psi}"
        if prev_obj is not None:
            # Objective should vary smoothly (not jump wildly)
            assert abs(obj - prev_obj) < 1e3, f"Objective jumps at psi={psi}"
        prev_obj = obj