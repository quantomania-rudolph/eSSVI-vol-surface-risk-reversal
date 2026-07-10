"""Anchor extraction for eSSVI calibration slices.

Computes (k*_t, theta*_t) per (timestamp, expiration) group from clean
minute rows with forward_price and business_t already attached.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from dataingestion import config as cfg

AnchorQuality = Literal[
    "EXACT_ATM",
    "NEAREST_BELLY",
    "WIDENED_GATES",
    "NEAREST_ANY",
    "DROP_SLICE",
]


def _belly_mask(
    df: pd.DataFrame,
    *,
    rel_spread_max: float,
    oi_min: int,
    delta_lo: float,
    delta_hi: float,
    k_abs: float | None = None,
) -> pd.Series:
    """Return boolean mask for belly-qualifying strikes."""
    if df.empty:
        return pd.Series(dtype=bool)

    delta_ok = df["delta"].abs().between(delta_lo, delta_hi)
    spread_ok = df["rel_spread"] <= rel_spread_max
    oi_ok = df["open_interest"].notna() & (df["open_interest"] > oi_min)
    mask = delta_ok & spread_ok & oi_ok

    if k_abs is not None and "log_moneyness" in df.columns:
        mask = mask & (df["log_moneyness"].abs() <= k_abs)

    return mask


def extract_anchor(slice_df: pd.DataFrame) -> tuple[float, float, AnchorQuality]:
    """Extract anchor (k*, theta*) for one maturity slice at one minute.

    Finds the market quote whose strike is closest to the forward; uses its
    log-moneyness and total implied variance theta* = sigma^2 * T.

    Fallback ladder:
    1. EXACT_ATM — belly-qualifying strike nearest |k|
    2. NEAREST_BELLY — relaxed belly gates
    3. WIDENED_GATES — wider spread/OI/delta for thin slices
    4. NEAREST_ANY — any strike with valid IV/T
    5. DROP_SLICE — no usable quote (returns NaN, NaN)
    """
    if slice_df.empty:
        return float("nan"), float("nan"), "DROP_SLICE"

    required = {"forward_price", "implied_vol", "business_t", "strike", "log_moneyness"}
    missing = required - set(slice_df.columns)
    if missing:
        raise ValueError(f"extract_anchor missing columns: {sorted(missing)}")

    work = slice_df.copy()
    work = work[
        work["forward_price"].notna()
        & work["implied_vol"].notna()
        & work["business_t"].notna()
        & (work["implied_vol"] > cfg.MIN_IV)
        & (work["business_t"] > 0)
    ]
    if work.empty:
        return float("nan"), float("nan"), "DROP_SLICE"

    work["_abs_k"] = work["log_moneyness"].abs()
    work["_theta_star"] = work["implied_vol"].astype(float) ** 2 * work["business_t"].astype(float)

    # Pass 1: standard belly gates
    belly = work.loc[_belly_mask(
        work,
        rel_spread_max=cfg.MAX_REL_SPREAD_BELLY,
        oi_min=cfg.MIN_OI,
        delta_lo=cfg.MIN_DELTA_ABS,
        delta_hi=cfg.MAX_DELTA_ABS,
        k_abs=cfg.BELLY_K_ABS,
    )]
    if not belly.empty:
        row = belly.loc[belly["_abs_k"].idxmin()]
        quality: AnchorQuality = "EXACT_ATM" if row["_abs_k"] < 1e-4 else "NEAREST_BELLY"
        return float(row["log_moneyness"]), float(row["_theta_star"]), quality

    # Pass 2: relaxed gates for thin slices (eSSVI plan §4.1)
    relaxed = work.loc[_belly_mask(
        work,
        rel_spread_max=0.15,
        oi_min=50,
        delta_lo=0.05,
        delta_hi=0.95,
        k_abs=cfg.BELLY_K_ABS,
    )]
    if not relaxed.empty:
        row = relaxed.loc[relaxed["_abs_k"].idxmin()]
        return float(row["log_moneyness"]), float(row["_theta_star"]), "WIDENED_GATES"

    # Pass 3: nearest strike with valid IV/T (no belly gates)
    row = work.loc[work["_abs_k"].idxmin()]
    return float(row["log_moneyness"]), float(row["_theta_star"]), "NEAREST_ANY"


def attach_anchor_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Attach anchor_k_star, anchor_theta_star, anchor_quality per slice."""
    if df.empty:
        df["anchor_k_star"] = pd.Series(dtype=float)
        df["anchor_theta_star"] = pd.Series(dtype=float)
        df["anchor_quality"] = pd.Series(dtype=str)
        return df

    anchors: list[dict[str, object]] = []
    group_cols = ["timestamp", "expiration"]
    for (ts, exp), grp in df.groupby(group_cols, sort=False):
        k_star, theta_star, quality = extract_anchor(grp)
        anchors.append({
            "timestamp": ts,
            "expiration": exp,
            "anchor_k_star": k_star,
            "anchor_theta_star": theta_star,
            "anchor_quality": quality,
        })

    anchor_df = pd.DataFrame(anchors)
    merged = df.merge(anchor_df, on=group_cols, how="left")
    return merged


def attach_slice_strike_count(df: pd.DataFrame) -> pd.DataFrame:
    """Attach belly-qualifying strike count per (timestamp, expiration) slice."""
    if df.empty:
        df["slice_strike_count"] = pd.Series(dtype=int)
        return df

    def _count_belly(grp: pd.DataFrame) -> int:
        return int(_belly_mask(
            grp,
            rel_spread_max=cfg.MAX_REL_SPREAD_BELLY,
            oi_min=cfg.MIN_OI,
            delta_lo=cfg.MIN_DELTA_ABS,
            delta_hi=cfg.MAX_DELTA_ABS,
            k_abs=cfg.BELLY_K_ABS,
        ).sum())

    counts = (
        df.groupby(["timestamp", "expiration"], sort=False)
        .apply(_count_belly)
        .reset_index(name="slice_strike_count")
    )
    return df.merge(counts, on=["timestamp", "expiration"], how="left")
