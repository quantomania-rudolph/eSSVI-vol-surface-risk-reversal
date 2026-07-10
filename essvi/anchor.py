"""Anchor extraction (k*, theta*) for eSSVI calibration slices.

Recomputes anchors from slice market data; DB anchor columns are not ground truth.
Uses psi = theta * phi convention and exact closed-form theta* inversion.

Reference: Corbetta 2019 §3.2, Blueprint §5.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from essvi import config as cfg
from essvi.exceptions import AnchorError


@dataclass(frozen=True)
class AnchorParams:
    """Market anchor parameters for one expiry slice.

    These are INDEPENDENT of candidate (rho, psi) — computed ONCE per slice.
    The solver computes theta_t(rho, psi) using constraints.theta_from_psi.
    """

    k_star: float
    """Belly strike (log-moneyness at anchor)."""
    theta_star: float
    """Market ATM total variance theta*_t = sigma*^2 * T."""
    quality: str
    """Anchor quality: EXACT_ATM, NEAREST_BELLY, WIDENED_GATES, NEAREST_ANY."""
    n_belly: int
    """Number of strikes in belly region."""

    # No theta, phi, rho here — those are per-candidate (rho, psi)


def _delta_series(df: pd.DataFrame) -> pd.Series:
    if "delta_black76" in df.columns:
        return df["delta_black76"]
    if "delta" in df.columns:
        return df["delta"]
    raise KeyError("slice requires delta_black76 or delta")


def _oi_series(df: pd.DataFrame) -> pd.Series:
    if "oi" in df.columns:
        return df["oi"]
    if "open_interest" in df.columns:
        return df["open_interest"]
    raise KeyError("slice requires oi or open_interest")


def _belly_mask_with_params(
    df: pd.DataFrame,
    *,
    rel_spread_max: float,
    oi_min: int,
    delta_lo: float,
    delta_hi: float,
    k_abs: float,
) -> np.ndarray:
    if df.empty:
        return np.array([], dtype=bool)

    delta_ok = _delta_series(df).abs().between(delta_lo, delta_hi)
    spread_ok = df["rel_spread"] <= rel_spread_max
    oi_ok = _oi_series(df).notna() & (_oi_series(df) >= oi_min)
    k_ok = df["log_moneyness"].abs() <= k_abs
    return (delta_ok & spread_ok & oi_ok & k_ok).to_numpy(dtype=bool)


def belly_mask(df: pd.DataFrame) -> np.ndarray:
    """Vectorized boolean mask for belly-qualifying strikes."""
    return _belly_mask_with_params(
        df,
        rel_spread_max=cfg.BELLY_REL_SPREAD_MAX,
        oi_min=cfg.BELLY_OI_MIN,
        delta_lo=cfg.BELLY_DELTA_LO,
        delta_hi=cfg.BELLY_DELTA_HI,
        k_abs=cfg.BELLY_K_ABS,
    )


def relaxed_belly_mask(df: pd.DataFrame) -> np.ndarray:
    """Relaxed belly criteria for anchor fallback."""
    return _belly_mask_with_params(
        df,
        rel_spread_max=cfg.RELAXED_BELLY_REL_SPREAD_MAX,
        oi_min=cfg.RELAXED_BELLY_OI_MIN,
        delta_lo=cfg.RELAXED_BELLY_DELTA_LO,
        delta_hi=cfg.RELAXED_BELLY_DELTA_HI,
        k_abs=cfg.BELLY_K_ABS,
    )


def _valid_row_mask(df: pd.DataFrame) -> pd.Series:
    required = {"log_moneyness", "implied_vol", "business_t", "rel_spread"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"anchor slice missing columns: {sorted(missing)}")

    return (
        df["log_moneyness"].notna()
        & df["implied_vol"].notna()
        & df["business_t"].notna()
        & df["rel_spread"].notna()
        & (df["implied_vol"] > cfg.MIN_IV)
        & (df["business_t"] > 0)
    )


def _pick_nearest_k_row(df: pd.DataFrame, mask: np.ndarray) -> pd.Series | None:
    if df.empty or not mask.any():
        return None

    candidates = df.loc[mask].copy()
    candidates["_abs_k"] = candidates["log_moneyness"].abs()
    min_k = candidates["_abs_k"].min()
    ties = candidates[candidates["_abs_k"] == min_k]
    oi = _oi_series(ties).fillna(-1)
    sort_keys = ties.assign(_oi=-oi.astype(float), _spread=ties["rel_spread"].astype(float))
    return sort_keys.sort_values(["_abs_k", "_oi", "_spread"]).iloc[0]


def _select_anchor_with_quality(
    df: pd.DataFrame,
    belly_mask_arr: np.ndarray,
) -> tuple[float, str]:
    if df.empty:
        raise AnchorError("No valid strikes for anchor selection")

    if len(belly_mask_arr) != len(df):
        raise ValueError("belly_mask length must match DataFrame length")

    valid = _valid_row_mask(df)
    work = df.loc[valid].copy()
    if work.empty:
        raise AnchorError("No valid strikes for anchor selection")

    valid_idx = valid.to_numpy()
    std_belly = np.asarray(belly_mask_arr, dtype=bool)[valid_idx]
    relaxed = relaxed_belly_mask(work)

    row = _pick_nearest_k_row(work, std_belly)
    if row is not None:
        abs_k = abs(float(row["log_moneyness"]))
        quality = (
            "EXACT_ATM" if abs_k <= cfg.ANCHOR_K_STAR_TOL else "NEAREST_BELLY"
        )
        return float(row["log_moneyness"]), quality

    row = _pick_nearest_k_row(work, relaxed)
    if row is not None:
        return float(row["log_moneyness"]), "WIDENED_GATES"

    row = _pick_nearest_k_row(work, np.ones(len(work), dtype=bool))
    if row is not None:
        return float(row["log_moneyness"]), "NEAREST_ANY"

    raise AnchorError("No valid strikes for anchor selection")


def select_anchor_k_star(
    df: pd.DataFrame,
    belly_mask_arr: np.ndarray,
) -> float:
    """Fallback ladder: EXACT_ATM -> NEAREST_BELLY -> WIDENED_GATES -> NEAREST_ANY."""
    k_star, _ = _select_anchor_with_quality(df, belly_mask_arr)
    return k_star


def compute_theta_star(w_star: float, k_star: float, phi: float, rho: float) -> float:
    """
    Compute theta* from market total variance w* at k* given (phi, rho).

    This is the INVERSE function: given w* (market ATM total variance),
    compute the theta* parameter that makes the eSSVI slice pass through (k*, w*).
    
    w(k*) = theta/2 * (1 + rho*phi*k* + sqrt((phi*k* + rho)^2 + (1 - rho^2)))
    => theta = 2*w* / (1 + rho*phi*k* + sqrt((phi*k* + rho)^2 + (1 - rho^2)))
    
    This function IS the inverse of eval_w at k*. It is used by the SOLVER
    when it needs to compute the theta that makes the slice pass through
    the market ATM point for a GIVEN candidate (phi, rho).
    
    WARNING: This is NOT the anchor extraction. The anchor (k*, theta_star)
    is INDEPENDENT of (phi, rho). Use extract_anchor_params() for that.
    """
    u = phi * k_star + rho
    d = u * u + (1.0 - rho * rho)
    denom = 1.0 + rho * phi * k_star + np.sqrt(d)
    return float(2.0 * w_star / denom)


def eval_w(k: float, theta: float, rho: float, phi: float) -> float:
    """Evaluate total variance w(k) under eSSVI with psi = theta * phi."""
    u = phi * k + rho
    d = u * u + (1.0 - rho * rho)
    return float(theta / 2.0 * (1.0 + rho * phi * k + np.sqrt(d)))


def _w_star_at_k(df: pd.DataFrame, k_star: float) -> float:
    match = df.loc[df["log_moneyness"] == k_star]
    if match.empty:
        raise AnchorError(f"No row found at k*={k_star}")
    row = match.iloc[0]
    return float(row["implied_vol"]) ** 2 * float(row["business_t"])


def extract_anchor_params(df_slice: pd.DataFrame) -> AnchorParams:
    """
    Extract market anchor (k*_t, theta*_t) from a single expiration slice.

    Anchor is INDEPENDENT of (rho, psi) — computed ONCE per slice.
    The solver will compute theta_t(psi, rho) using constraints.theta_from_psi.
    """
    # 1. Filter to standard belly region using the belly_flag column if available,
    #    otherwise compute it using belly_mask function
    if "belly_flag" in df_slice.columns:
        std_belly_mask_arr = df_slice["belly_flag"].astype(bool).to_numpy()
    else:
        std_belly_mask_arr = belly_mask(df_slice)
    std_belly = df_slice[std_belly_mask_arr].copy()

    # Apply valid row mask to filter out rows with invalid data (e.g., IV below MIN_IV)
    valid_mask = _valid_row_mask(std_belly)
    std_belly = std_belly[valid_mask].copy()

    # 2. Check if standard belly has >= 3 strikes
    has_std_belly = len(std_belly) >= 3

    if has_std_belly:
        belly = std_belly
    else:
        # Fallback: use relaxed belly criteria
        relaxed = relaxed_belly_mask(df_slice)
        has_relaxed = relaxed.any()
        if has_relaxed:
            belly = df_slice[relaxed].copy()
            valid_mask = _valid_row_mask(belly)
            belly = belly[valid_mask].copy()
        else:
            # Final fallback: use all OTM or all available
            belly = df_slice[df_slice.get("OTM", True)].copy() if "OTM" in df_slice.columns else df_slice.copy()
            valid_mask = _valid_row_mask(belly)
            belly = belly[valid_mask].copy()

    if len(belly) == 0:
        raise AnchorError("No OTM/belly options in slice")

    # 3. k* = strike minimizing |log_moneyness| (closest to forward)
    k_star_idx = belly["log_moneyness"].abs().idxmin()
    k_star = float(belly.loc[k_star_idx, "log_moneyness"])

    # 4. theta* = sigma*^2 * T at k* (interpolate if needed)
    # For exact ATM, use the option at k_star
    row = belly.loc[k_star_idx]
    iv = float(row["implied_vol"])
    T = float(row["business_t"])
    theta_star = iv * iv * T  # theta* = sigma*^2 * T

    # 5. Quality metrics
    n_belly = len(belly)
    avg_spread = float(belly["rel_spread"].mean())

    # Determine quality label using fallback ladder
    # EXACT_ATM -> NEAREST_BELLY (std belly >= 3) -> WIDENED_GATES (relaxed belly) -> NEAREST_ANY
    if abs(k_star) <= cfg.ANCHOR_K_STAR_TOL:
        quality_label = "EXACT_ATM"
    elif has_std_belly:
        quality_label = "NEAREST_BELLY"
    elif has_relaxed:
        quality_label = "WIDENED_GATES"
    else:
        quality_label = "NEAREST_ANY"

    return AnchorParams(
        k_star=k_star,
        theta_star=theta_star,
        quality=quality_label,
        n_belly=n_belly,
    )


def compute_theta_t(psi: float, rho: float, anchor: AnchorParams) -> float:
    """
    Compute slice parameter theta_t for given (psi, rho) using EXACT closed form.

    Delegates to constraints.theta_from_psi to ensure single source of truth.
    """
    from essvi.constraints import theta_from_psi

    return theta_from_psi(psi, rho, anchor.k_star, anchor.theta_star)


__all__ = [
    "AnchorParams",
    "extract_anchor_params",
    "compute_theta_t",
    "compute_theta_star",  # Kept for solver use (inverse function)
    "eval_w",
    "select_anchor_k_star",
    "belly_mask",
    "relaxed_belly_mask",
    "AnchorError",
]