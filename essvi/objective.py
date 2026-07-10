"""eSSVI slice objective: variance-space weighted least squares with belly boost."""

from __future__ import annotations

from typing import Optional

import numpy as np

from essvi import config as cfg
from essvi.constraints import theta_from_psi


def w_slice(
    k: np.ndarray | float,
    theta: float,
    phi: float,
    rho: float,
) -> np.ndarray:
    """eSSVI implied total variance at log-moneyness k."""
    k_arr = np.asarray(k, dtype=float)
    u = phi * k_arr + rho
    sqrt_d = np.sqrt(u**2 + (1.0 - rho**2))
    return (theta / 2.0) * (1.0 + rho * phi * k_arr + sqrt_d)


def w_slice_derivatives(
    k: np.ndarray | float,
    theta: float,
    phi: float,
    rho: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (w, w', w'') — all closed-form."""
    k_arr = np.asarray(k, dtype=float)
    u = phi * k_arr + rho
    d = u**2 + (1.0 - rho**2)
    sqrt_d = np.sqrt(d)

    w = (theta / 2.0) * (1.0 + rho * phi * k_arr + sqrt_d)
    w_prime = (theta * phi / 2.0) * (rho + u / sqrt_d)
    w_double_prime = (theta * phi**2 * (1.0 - rho**2)) / (2.0 * d**1.5)

    return w, w_prime, w_double_prime


def belly_boost(k: np.ndarray | float) -> np.ndarray:
    """Return BELLY_BOOST for |k| <= BELLY_K_ABS, else 1.0."""
    k_arr = np.asarray(k, dtype=float)
    boost = np.where(np.abs(k_arr) <= cfg.BELLY_K_ABS, cfg.BELLY_BOOST, 1.0)
    return boost


def _compute_weights(
    w_arr: np.ndarray,
    vega_arr: np.ndarray,
    T: float,
    weight_mode: str,
) -> np.ndarray:
    """Compute weights for objective function using variance-space vega."""
    if weight_mode == "var_vega2":
        # Variance-space vega: ν_var = ν_vol / (2 * σ * sqrt(T))
        # σ = sqrt(w / T) since w = σ²T
        sigma_mkt = np.sqrt(w_arr / T)              # σ = √(w/T)
        nu_var = vega_arr / (2.0 * sigma_mkt * np.sqrt(T))  # ν_var = ν_vol / (2σ√T)
        weights = nu_var ** 2
        
        # Sanity: weights should be positive, higher at ATM
        # ATM has highest σ → highest ν_var
        assert np.all(weights > 0), f"Non-positive weights: {weights}"
        return weights
    
    elif weight_mode == "vol_vega1":
        # Vol-space vega (absolute)
        return np.abs(vega_arr)
    
    elif weight_mode == "vol_vega2":
        # Vol-space vega squared (inverse)
        return 1.0 / (vega_arr**2 * w_arr)
    
    elif weight_mode == "uniform":
        return np.ones_like(w_arr)
    
    else:
        raise ValueError(f"Unknown weight_mode: {weight_mode}")


def objective_slice(
    params: tuple[float, float, float],
    k_obs: np.ndarray,
    w_obs: np.ndarray,
    vega_obs: np.ndarray,
    T: float,
    theta_star: float,
    k_star: float,
    weight_mode: str = "var_vega2",
    lambda_spatial: float = 0.0,
    lambda_temporal: float = 0.0,
    prev_psi: Optional[float] = None,
    prev_theta: Optional[float] = None,
) -> float:
    """Weighted sum of squared variance-space errors with regularization.
    
    Args:
        params: (psi, rho, phi) - note: psi = theta * phi, theta is derived from anchor
        k_obs: Observed log-moneyness values
        w_obs: Observed total variance (σ²T)
        vega_obs: Observed vega (vol-space)
        T: Time to maturity (years) - REQUIRED for variance-space conversion
        theta_star: Anchor total variance at k_star
        k_star: Anchor log-moneyness
        weight_mode: Weighting scheme ("var_vega2", "vol_vega1", "vol_vega2", "uniform")
        lambda_spatial: Spatial regularization weight (log θ difference)
        lambda_temporal: Temporal regularization weight (ψ difference)
        prev_psi: Previous slice's ψ for temporal regularization
        prev_theta: Previous slice's θ for spatial regularization
        
    Returns:
        Weighted sum of squared errors + regularization penalties
    """
    psi, rho, phi = params

    k_arr = np.asarray(k_obs, dtype=float)
    w_arr = np.asarray(w_obs, dtype=float)
    vega_arr = np.asarray(vega_obs, dtype=float)

    # 1. Compute θ from ψ, ρ, anchor (exact closed form)
    theta = theta_from_psi(psi, rho, k_star, theta_star)
    
    if theta <= 0.0 or not np.isfinite(theta):
        return float("inf")

    # 2. Model total variance
    w_model = w_slice(k_arr, theta, phi, rho)
    errors = w_model - w_arr

    # 3. Weights (NOW CORRECT - variance-space vega²)
    weights = _compute_weights(w_arr, vega_arr, T, weight_mode)

    # 4. Weighted SSE with belly boost
    belly_w = belly_boost(k_arr)
    obj = float(np.sum(belly_w * weights * errors**2))
    
    # 5. Spatial regularization (log θ difference)
    if lambda_spatial > 0 and prev_theta is not None and prev_theta > 0:
        obj += lambda_spatial * (np.log(theta) - np.log(prev_theta))**2
    
    # 6. Temporal regularization (ψ difference)
    if lambda_temporal > 0 and prev_psi is not None:
        obj += lambda_temporal * (psi - prev_psi)**2
    
    return obj