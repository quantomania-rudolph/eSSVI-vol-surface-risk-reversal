"""Anchor extraction (k*, theta*) for eSSVI calibration slices.

Recomputes anchors from slice market data; DB anchor columns are not ground truth.
Uses psi = theta * phi convention and exact closed-form theta* inversion.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from essvi import config as cfg
from essvi.exceptions import AnchorError

AnchorQuality = Literal[
    "EXACT_ATM",
    "NEAREST_BELLY",
    "WIDENED_GATES",
    "NEAREST_ANY",
]

_MIN_IV = 0.005


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
        & (df["implied_vol"] > _MIN_IV)
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
) -> tuple[float, AnchorQuality]:
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
        quality: AnchorQuality = (
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


def select_anchor_k_star(df: pd.DataFrame, belly_mask_arr: np.ndarray) -> float:
    """
    Fallback ladder:
    EXACT_ATM -> NEAREST_BELLY -> WIDENED_GATES -> NEAREST_ANY.
    Returns k* (log_moneyness at anchor).
    Raises AnchorError if no strike passes.
    """
    k_star, _ = _select_anchor_with_quality(df, belly_mask_arr)
    return k_star


def compute_theta_star(
    w_star: float,
    k_star: float,
    phi: float,
    rho: float,
) -> float:
    """
    Exact closed-form inversion of w(k*; theta, rho, phi) for theta.

    psi = theta * phi convention:
    theta = 2 * w* / (1 + rho*phi*k* + sqrt((phi*k* + rho)^2 + (1 - rho^2)))
    """
    u = phi * k_star + rho
    d = u * u + (1.0 - rho * rho)
    denom = 1.0 + rho * phi * k_star + np.sqrt(d)
    if denom <= 0:
        raise AnchorError(f"Invalid theta* denominator {denom}")
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


def extract_anchor_params(
    df_slice: pd.DataFrame,
    phi: float,
    rho: float,
) -> dict:
    """
    Extract anchor parameters for one maturity slice.

    Returns dict with k_star, w_star, theta_star, belly_mask, quality, n_belly.
    """
    bm = belly_mask(df_slice)
    k_star, quality = _select_anchor_with_quality(df_slice, bm)
    w_star = _w_star_at_k(df_slice.loc[_valid_row_mask(df_slice)], k_star)
    theta_star = compute_theta_star(w_star, k_star, phi, rho)
    return {
        "k_star": k_star,
        "w_star": w_star,
        "theta_star": theta_star,
        "belly_mask": bm,
        "quality": quality,
        "n_belly": int(bm.sum()),
    }
