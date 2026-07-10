"""Spatial (term-structure) and temporal regularization for eSSVI calibration."""

from __future__ import annotations

import numpy as np

from essvi.config import (
    LAMBDA_PSI,
    LAMBDA_RHO,
    LAMBDA_TEMPORAL,
    SHORT_MATURITY_RHO_PRIOR,
    TEMPORAL_PSI_SCALE,
    TEMPORAL_RHO_SCALE,
    TEMPORAL_THETA_LOG,
    TEMPORAL_THETA_SCALE,
    THETA_PROJECTION_EPS,
)


def spatial_reg_penalty(
    rho_array: np.ndarray,
    psi_array: np.ndarray,
    lambda_rho: float = LAMBDA_RHO,
    lambda_psi: float = LAMBDA_PSI,
    scale_rho: float = TEMPORAL_RHO_SCALE,
    scale_psi: float = TEMPORAL_PSI_SCALE,
) -> float:
    """
    Term-structure velocity penalty across adjacent maturity slices.

    Σ_i (ρ_i − ρ_{i−1})² / sρ² · λρ  +  Σ_i (ψ_i − ψ_{i−1})² / sψ² · λψ
    """
    rho = np.asarray(rho_array, dtype=float)
    psi = np.asarray(psi_array, dtype=float)

    if rho.size < 2:
        return 0.0

    rho_diff = np.diff(rho)
    psi_diff = np.diff(psi)

    rho_term = lambda_rho * np.sum(rho_diff**2) / (scale_rho**2)
    psi_term = lambda_psi * np.sum(psi_diff**2) / (scale_psi**2)
    return float(rho_term + psi_term)


def temporal_reg_penalty(
    theta_current: np.ndarray,
    rho_current: np.ndarray,
    psi_current: np.ndarray,
    theta_prior: np.ndarray | None,
    rho_prior: np.ndarray | None,
    psi_prior: np.ndarray | None,
    lambda_temp: float = LAMBDA_TEMPORAL,
    scale_theta: float = TEMPORAL_THETA_SCALE,
    scale_rho: float = TEMPORAL_RHO_SCALE,
    scale_psi: float = TEMPORAL_PSI_SCALE,
    use_log_theta: bool = TEMPORAL_THETA_LOG,
) -> float:
    """
    Normalized Tikhonov penalty between successive minute snapshots.

    If any prior is None → return 0.0.
    Otherwise, L² norm of difference with log(θ) when use_log_theta is True.
    """
    if theta_prior is None or rho_prior is None or psi_prior is None:
        return 0.0

    theta_c = np.asarray(theta_current, dtype=float)
    rho_c = np.asarray(rho_current, dtype=float)
    psi_c = np.asarray(psi_current, dtype=float)
    theta_p = np.asarray(theta_prior, dtype=float)
    rho_p = np.asarray(rho_prior, dtype=float)
    psi_p = np.asarray(psi_prior, dtype=float)

    if use_log_theta:
        theta_c = np.log(np.clip(theta_c, THETA_PROJECTION_EPS, None))
        theta_p = np.log(np.clip(theta_p, THETA_PROJECTION_EPS, None))

    theta_term = lambda_temp * np.sum(((theta_c - theta_p) / scale_theta) ** 2)
    rho_term = lambda_temp * np.sum(((rho_c - rho_p) / scale_rho) ** 2)
    psi_term = lambda_temp * np.sum(((psi_c - psi_p) / scale_psi) ** 2)
    return float(theta_term + rho_term + psi_term)


def warmstart_params(
    prior_params: dict | None,
    n_slices: int,
    rho_fallback: float = SHORT_MATURITY_RHO_PRIOR,
    psi_fallback: float = TEMPORAL_PSI_SCALE,
) -> dict:
    """
    Build warm-start arrays for the solver.

    If prior_params exists → return dict with theta_0, rho_0, psi_0 arrays.
    If None → return flat fallback init from config.
    """
    if prior_params is not None:
        theta = prior_params.get(
            "theta_0", prior_params.get("theta_grid", prior_params.get("theta"))
        )
        rho = prior_params.get(
            "rho_0", prior_params.get("rho_grid", prior_params.get("rho"))
        )
        psi = prior_params.get(
            "psi_0", prior_params.get("psi_grid", prior_params.get("psi"))
        )
        return {
            "theta_0": np.asarray(theta, dtype=float),
            "rho_0": np.asarray(rho, dtype=float),
            "psi_0": np.asarray(psi, dtype=float),
        }

    return {
        "theta_0": np.full(n_slices, TEMPORAL_THETA_SCALE, dtype=float),
        "rho_0": np.full(n_slices, rho_fallback, dtype=float),
        "psi_0": np.full(n_slices, psi_fallback, dtype=float),
    }
