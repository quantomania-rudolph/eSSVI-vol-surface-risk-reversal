"""Continuous eSSVI surface: linear θ interpolation, flat ψ/ρ, tail cap (plan §15)."""

from __future__ import annotations

import numpy as np

from essvi import config as cfg
from essvi.objective import w_slice


def _slice_T(entry: dict) -> float:
    if "T" in entry:
        return float(entry["T"])
    if "business_t" in entry:
        return float(entry["business_t"])
    raise KeyError("slice entry requires 'T' or 'business_t'")


def _slice_psi(entry: dict) -> float:
    if "psi" in entry:
        return float(entry["psi"])
    return float(entry["theta"]) * float(entry["phi"])


def _extract_knots(slice_params: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    ts = np.array([_slice_T(s) for s in slice_params], dtype=float)
    thetas = np.array([float(s["theta"]) for s in slice_params], dtype=float)
    psis = np.array([_slice_psi(s) for s in slice_params], dtype=float)
    rhos = np.array([float(s["rho"]) for s in slice_params], dtype=float)
    order = np.argsort(ts)
    return ts[order], thetas[order], psis[order], rhos[order]


def _clamp_theta(theta: float) -> float:
    return max(float(theta), cfg.THETA_PROJECTION_EPS)


def _clamp_phi(phi: float) -> float:
    return max(float(phi), cfg.THETA_PROJECTION_EPS)


def interpolate_theta(T: float, ts: np.ndarray | list[float], thetas: np.ndarray | list[float]) -> float:
    """Linear interpolation of θ at T from (ts, thetas) knots."""
    t_arr = np.asarray(ts, dtype=float)
    theta_arr = np.asarray(thetas, dtype=float)
    t_val = float(T)

    if t_val <= t_arr[0]:
        return _clamp_theta(theta_arr[0])
    if t_val >= t_arr[-1]:
        return _clamp_theta(theta_arr[-1])

    idx = int(np.searchsorted(t_arr, t_val, side="right") - 1)
    t1, t2 = t_arr[idx], t_arr[idx + 1]
    th1, th2 = theta_arr[idx], theta_arr[idx + 1]
    weight = (t_val - t1) / (t2 - t1)
    return _clamp_theta(th1 + (th2 - th1) * weight)


def interpolate_psi(T: float, ts: np.ndarray | list[float], psis: np.ndarray | list[float]) -> float:
    """Flat / left-piecewise-constant ψ at T."""
    t_arr = np.asarray(ts, dtype=float)
    psi_arr = np.asarray(psis, dtype=float)
    t_val = float(T)

    if t_val <= t_arr[0]:
        return float(psi_arr[0])
    if t_val >= t_arr[-1]:
        return float(psi_arr[-1])

    idx = int(np.searchsorted(t_arr, t_val, side="right") - 1)
    return float(psi_arr[idx])


def interpolate_rho(T: float, ts: np.ndarray | list[float], rhos: np.ndarray | list[float]) -> float:
    """Flat / left-piecewise-constant ρ at T."""
    t_arr = np.asarray(ts, dtype=float)
    rho_arr = np.asarray(rhos, dtype=float)
    t_val = float(T)

    if t_val <= t_arr[0]:
        return float(rho_arr[0])
    if t_val >= t_arr[-1]:
        return float(rho_arr[-1])

    idx = int(np.searchsorted(t_arr, t_val, side="right") - 1)
    return float(rho_arr[idx])


def extrapolate_short_theta(
    T: float,
    T1: float,
    theta1: float,
    mode: str | None = None,
) -> float:
    """Short extrapolation: Corbetta → linear to 0; flat → clamp."""
    extrap_mode = mode or cfg.SHORT_EXTRAP_MODE
    t_val = float(T)
    t1 = float(T1)
    th1 = float(theta1)

    if extrap_mode == "corbetta":
        if t1 <= 0.0:
            return cfg.THETA_PROJECTION_EPS
        return _clamp_theta(th1 * (t_val / t1))
    if extrap_mode == "flat":
        return _clamp_theta(th1)
    raise ValueError(f"Unknown short extrapolation mode: {extrap_mode}")


def extrapolate_long_theta(
    T: float,
    TN: float,
    thetaN: float,
    psiN: float,
    rhoN: float,
) -> float:
    """Long extrapolation: linear with slope ψ_N/(1+|ρ_N|)."""
    t_val = float(T)
    t_n = float(TN)
    slope = float(psiN) / (1.0 + abs(float(rhoN)))
    return _clamp_theta(float(thetaN) + slope * (t_val - t_n))


def get_params_at_T(T: float, slice_params: list[dict]) -> tuple[float, float, float]:
    """Return (θ, φ, ρ) for any T (interpolated or extrapolated)."""
    ts, thetas, psis, rhos = _extract_knots(slice_params)
    t_val = float(T)

    if t_val < ts[0]:
        theta = extrapolate_short_theta(t_val, ts[0], thetas[0])
        psi = float(psis[0])
        rho = float(rhos[0])
    elif t_val > ts[-1]:
        theta = extrapolate_long_theta(t_val, ts[-1], thetas[-1], psis[-1], rhos[-1])
        psi = float(psis[-1])
        rho = float(rhos[-1])
    else:
        theta = interpolate_theta(t_val, ts, thetas)
        psi = interpolate_psi(t_val, ts, psis)
        rho = interpolate_rho(t_val, ts, rhos)

    phi = _clamp_phi(psi / theta)
    return theta, phi, rho


def _tail_slopes(psi: float, rho: float) -> tuple[float, float]:
    c_plus = (psi / 2.0) * (1.0 + rho)
    c_minus = (psi / 2.0) * (1.0 - rho)
    cap = cfg.TAIL_SLOPE_CAP
    return min(c_plus, cap), min(c_minus, cap)


def _apply_tail_cap(
    k: np.ndarray,
    w: np.ndarray,
    theta: float,
    phi: float,
    rho: float,
    psi: float,
) -> np.ndarray:
    k_arr = np.asarray(k, dtype=float)
    w_arr = np.asarray(w, dtype=float).copy()
    k_max = cfg.K_AUDIT
    c_plus, c_minus = _tail_slopes(psi, rho)

    w_at_kmax = float(w_slice(k_max, theta, phi, rho))
    w_at_neg_kmax = float(w_slice(-k_max, theta, phi, rho))

    right_mask = k_arr > k_max
    left_mask = k_arr < -k_max
    w_arr[right_mask] = w_at_kmax + c_plus * (k_arr[right_mask] - k_max)
    w_arr[left_mask] = w_at_neg_kmax + c_minus * (-k_max - k_arr[left_mask])
    return w_arr


def w_surface(
    k: float | np.ndarray,
    T: float,
    slice_params: list[dict],
) -> float | np.ndarray:
    """Full continuous eSSVI total variance at (k, T)."""
    theta, phi, rho = get_params_at_T(T, slice_params)
    ts, _, psis, _ = _extract_knots(slice_params)
    psi = interpolate_psi(T, ts, psis) if ts[0] <= T <= ts[-1] else (
        float(psis[0]) if T < ts[0] else float(psis[-1])
    )

    k_arr = np.asarray(k, dtype=float)
    scalar_input = k_arr.ndim == 0
    if scalar_input:
        k_arr = k_arr.reshape(1)

    w = w_slice(k_arr, theta, phi, rho)
    w = _apply_tail_cap(k_arr, w, theta, phi, rho, psi)

    if scalar_input:
        return float(w[0])
    return w


def sigma_surface(
    k: float | np.ndarray,
    T: float,
    slice_params: list[dict],
) -> float | np.ndarray:
    """Implied volatility: σ_imp(k, T) = sqrt(w_surface(k, T) / T)."""
    t_val = max(float(T), cfg.THETA_PROJECTION_EPS)
    w = w_surface(k, T, slice_params)
    sigma = np.sqrt(np.maximum(np.asarray(w, dtype=float), 0.0) / t_val)
    if np.ndim(k) == 0:
        return float(sigma)
    return sigma


def tail_slope_check(
    k: np.ndarray | list[float],
    w_k: np.ndarray | list[float],
    tol: float | None = None,
) -> bool:
    """Verify |w(k)/|k|| ≤ TAIL_SLOPE_CAP for large |k|."""
    tol_val = cfg.TAIL_SLOPE_CAP_EPS if tol is None else float(tol)
    k_arr = np.asarray(k, dtype=float)
    w_arr = np.asarray(w_k, dtype=float)
    cap = cfg.TAIL_SLOPE_CAP + tol_val

    large = np.abs(k_arr) > 1e-8
    if not np.any(large):
        return True

    slopes = np.abs(w_arr[large] / k_arr[large])
    return bool(np.all(slopes <= cap))


def surface_grid(
    k_range: np.ndarray | list[float],
    T_range: np.ndarray | list[float],
    slice_params: list[dict],
) -> np.ndarray:
    """2D evaluation: w[k_idx, t_idx] for a meshgrid of (k, T)."""
    k_arr = np.asarray(k_range, dtype=float)
    t_arr = np.asarray(T_range, dtype=float)
    grid = np.empty((k_arr.size, t_arr.size), dtype=float)
    for j, t_val in enumerate(t_arr):
        grid[:, j] = w_surface(k_arr, float(t_val), slice_params)
    return grid
