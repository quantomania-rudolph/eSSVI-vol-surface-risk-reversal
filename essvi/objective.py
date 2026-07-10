"""eSSVI slice objective: variance-space weighted least squares with belly boost."""

from __future__ import annotations

import numpy as np

from essvi import config as cfg


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


def objective_slice(
    params: tuple[float, float, float],
    k_obs: np.ndarray,
    w_obs: np.ndarray,
    vega_obs: np.ndarray,
    mode: str | None = None,
) -> float:
    """Weighted sum of squared variance-space errors."""
    weight_mode = mode or cfg.VEGA_WEIGHT_MODE
    theta, phi, rho = params

    k_arr = np.asarray(k_obs, dtype=float)
    w_arr = np.asarray(w_obs, dtype=float)
    vega_arr = np.asarray(vega_obs, dtype=float)

    w_model = w_slice(k_arr, theta, phi, rho)
    errors = w_model - w_arr

    if weight_mode == "var_vega2":
        weights = 1.0 / vega_arr**2
    elif weight_mode == "vol_vega1":
        weights = 1.0 / np.sqrt(vega_arr**2 * w_arr)
    elif weight_mode == "vol_vega2":
        weights = 1.0 / vega_arr
    else:
        raise ValueError(f"Unknown VEGA_WEIGHT_MODE: {weight_mode}")

    belly_w = belly_boost(k_arr)
    return float(np.sum(belly_w * weights**2 * errors**2))
