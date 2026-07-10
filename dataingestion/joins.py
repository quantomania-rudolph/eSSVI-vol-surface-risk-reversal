"""DataFrame join utilities for spot, OI, rate attachment, and post-join filters."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Optional

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from dataingestion.cache import BoundedCache
    import pandas_market_calendars as mcal

from dataingestion import config as cfg
from dataingestion.anchors import attach_anchor_columns, attach_slice_strike_count
from dataingestion.math import (
    compute_business_T,
    compute_delta_black76,
    compute_forward_with_dividends,
    compute_vega,
    tag_session_phase,
)


def _join_spot(opt_df: pd.DataFrame, stk_df: pd.DataFrame) -> pd.DataFrame:
    """Join spot_close from stock OHLC onto the option DataFrame."""
    if opt_df.empty or stk_df.empty:
        opt_df["spot_close"] = float("nan")
        return opt_df

    opt_df = opt_df.copy()
    opt_df["timestamp"] = pd.to_datetime(opt_df["timestamp"], utc=True).dt.floor("min")
    stk_df = stk_df.copy()
    stk_df["timestamp"] = pd.to_datetime(stk_df["timestamp"], utc=True).dt.floor("min")

    merged = opt_df.merge(stk_df, on="timestamp", how="left")

    if "spot_close" not in merged.columns:
        merged["spot_close"] = float("nan")

    return merged


def _join_oi(
    opt_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    schedule_cache: Optional[dict] = None,
) -> pd.DataFrame:
    """Join daily open_interest according to ``cfg.OI_MODE``."""
    if opt_df.empty:
        opt_df["open_interest"] = pd.NA
        return opt_df

    if oi_df.empty:
        if "open_interest" not in opt_df.columns:
            opt_df["open_interest"] = pd.NA
        return opt_df

    opt_df = opt_df.copy()
    if "open_interest" in opt_df.columns:
        opt_df = opt_df.drop(columns=["open_interest"])

    opt_df["bar_date"] = pd.to_datetime(opt_df["timestamp"]).dt.date
    oi_df = oi_df.copy()
    oi_df["date"] = pd.to_datetime(oi_df["date"]).dt.date

    if cfg.OI_MODE == "strict":
        if schedule_cache is not None and "session_minutes" in schedule_cache:
            trading_dates = sorted(schedule_cache["session_minutes"].keys())
            date_to_prior: dict[dt.date, dt.date] = {}
            for i, td in enumerate(trading_dates):
                if i > 0:
                    date_to_prior[td] = trading_dates[i - 1]
            opt_df["oi_join_date"] = opt_df["bar_date"].map(date_to_prior)
        else:
            opt_df["oi_join_date"] = opt_df["bar_date"] - dt.timedelta(days=1)
        join_left = "oi_join_date"
    else:
        join_left = "bar_date"

    merged = opt_df.merge(
        oi_df[["date", "open_interest"]],
        left_on=join_left,
        right_on="date",
        how="left",
    ).drop(columns=["bar_date", "date"])

    if "oi_join_date" in merged.columns:
        merged = merged.drop(columns=["oi_join_date"])

    merged["open_interest"] = merged["open_interest"].astype("Int64")
    return merged


def _linear_interp_rate(dte: float, rates: dict[str, float]) -> float:
    """Linearly interpolate r by DTE across SOFR / M1 / M3 knots."""
    knots = cfg.RATE_DTE_KNOTS
    symbols = cfg.RATE_SYMBOL_KNOTS
    values = [rates.get(sym, float("nan")) for sym in symbols]

    if any(np.isnan(v) for v in values):
        # Fall back to first available rate column
        for sym in symbols:
            if sym in rates and not np.isnan(rates[sym]):
                return rates[sym]
        return float("nan")

    if dte <= knots[0]:
        return values[0]
    if dte >= knots[-1]:
        return values[-1]

    for i in range(len(knots) - 1):
        lo, hi = knots[i], knots[i + 1]
        if lo <= dte <= hi:
            w = (dte - lo) / (hi - lo)
            return (1.0 - w) * values[i] + w * values[i + 1]

    return values[-1]


def _attach_rates(df: pd.DataFrame, rates_df: pd.DataFrame) -> pd.DataFrame:
    """Attach tenor-matched risk-free rate ``r`` (decimal, cc) by calendar date."""
    import logging

    log = logging.getLogger("dataingestion.joins")

    if df.empty or rates_df.empty:
        df["r"] = float("nan")
        log.warning(
            "No rates data available — r set to NaN",
            extra={"rows_affected": len(df), "rates_df_empty": rates_df.empty},
        )
        return df

    df = df.copy()
    df["bar_date"] = pd.to_datetime(df["timestamp"]).dt.date

    rate_cols = [c for c in rates_df.columns if c.startswith("r_")]
    present = ["date"] + (["r"] if "r" in rates_df.columns else []) + rate_cols
    present = [c for c in present if c in rates_df.columns]
    merged = df.merge(rates_df[present], left_on="bar_date", right_on="date", how="left")

    has_dte = "dte_calendar" in merged.columns
    knot_cols = [f"r_{sym}" for sym in cfg.RATE_SYMBOL_KNOTS]
    has_knots = has_dte and all(c in merged.columns for c in knot_cols)

    if has_knots and cfg.RATE_INTERPOLATION_METHOD == "linear":
        r_interp = []
        for _, row in merged.iterrows():
            rates_map = {sym: row[f"r_{sym}"] for sym in cfg.RATE_SYMBOL_KNOTS}
            r_interp.append(_linear_interp_rate(float(row["dte_calendar"]), rates_map))
        merged["r"] = r_interp
    elif has_dte and has_knots:
        # Bucket fallback (legacy)
        dte = merged["dte_calendar"].values
        mask_short = dte <= cfg.DTE_BUCKET_SHORT_MAX
        mask_medium = (dte > cfg.DTE_BUCKET_SHORT_MAX) & (dte <= cfg.DTE_BUCKET_MEDIUM_MAX)
        mask_long = (dte > cfg.DTE_BUCKET_MEDIUM_MAX) & (dte <= cfg.DTE_BUCKET_LONG_MAX)

        r_short_col = f"r_{cfg.RATE_SYMBOLS_SHORT[0]}"
        r_med1_col = f"r_{cfg.RATE_SYMBOLS_MEDIUM[0]}"
        r_med2_col = (
            f"r_{cfg.RATE_SYMBOLS_MEDIUM[1]}"
            if len(cfg.RATE_SYMBOLS_MEDIUM) >= 2
            else r_med1_col
        )
        r_long1_col = f"r_{cfg.RATE_SYMBOLS_LONG[0]}"
        r_long2_col = (
            f"r_{cfg.RATE_SYMBOLS_LONG[1]}"
            if len(cfg.RATE_SYMBOLS_LONG) >= 2
            else r_long1_col
        )

        r_tenor = merged["r"].copy()
        if mask_short.any() and r_short_col in merged.columns:
            r_tenor = r_tenor.where(~mask_short, merged[r_short_col].values)
        if mask_medium.any():
            med_avg = (merged[r_med1_col].values + merged[r_med2_col].values) / 2.0
            r_tenor = r_tenor.where(~mask_medium, med_avg)
        if mask_long.any():
            long_avg = (merged[r_long1_col].values + merged[r_long2_col].values) / 2.0
            r_tenor = r_tenor.where(~mask_long, long_avg)
        merged["r"] = r_tenor

    merged = merged.drop(columns=["bar_date", "date"], errors="ignore")

    missing = merged["r"].isna().sum()
    if missing > 0:
        log.warning(
            "Missing rates for some dates",
            extra={"missing_count": int(missing), "total_rows": len(merged)},
        )

    merged["r"] = merged["r"].astype(float)
    return merged


def compute_parity_skew(df: pd.DataFrame) -> pd.DataFrame:
    """Attach per-row put-call IV skew diagnostic (call_iv - put_iv at same strike)."""
    if df.empty:
        df["parity_skew"] = pd.Series(dtype=float)
        return df

    work = df.copy()
    pivot = work.pivot_table(
        index=["timestamp", "expiration", "strike"],
        columns="option_type",
        values="implied_vol",
        aggfunc="first",
    )
    if "C" not in pivot.columns or "P" not in pivot.columns:
        work["parity_skew"] = float("nan")
        return work

    skew = (pivot["C"] - pivot["P"]).reset_index(name="parity_skew")
    merged = work.merge(skew, on=["timestamp", "expiration", "strike"], how="left")
    return merged


def apply_post_join_filters(
    df: pd.DataFrame,
    run_id: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Post-join filters using locally recomputed delta and prior-session OI.

    Delta band, OI liquidity, and monotonicity run here (after forward is known)
    so belly partition is consistent with ``F = S·e^{(r-q)T}``.
    """
    if df.empty:
        quar = pd.DataFrame(columns=list(df.columns) + ["reject_code", "reject_detail", "_phase"])
        if run_id is not None:
            quar["ingest_run_id"] = run_id
        return df, quar

    result = compute_delta_black76(df.copy())
    quar_parts: list[pd.DataFrame] = []

    # Delta band
    delta_abs = result["delta"].abs()
    delta_ok = delta_abs.between(cfg.MIN_DELTA_ABS, cfg.MAX_DELTA_ABS)
    failing = result.loc[~delta_ok].copy()
    if not failing.empty:
        failing["reject_code"] = "DELTA_BAND"
        failing["reject_detail"] = (
            "delta=" + delta_abs.loc[failing.index].astype(str)
            + f", min={cfg.MIN_DELTA_ABS}, max={cfg.MAX_DELTA_ABS}"
        )
        quar_parts.append(failing)
    result = result.loc[delta_ok].copy()

    # OI liquidity
    if "open_interest" not in result.columns:
        result["open_interest"] = 0
    oi_ok = result["open_interest"].notna() & (result["open_interest"] > cfg.MIN_OI)
    failing = result.loc[~oi_ok].copy()
    if not failing.empty:
        failing["reject_code"] = "LOW_OI"
        failing["reject_detail"] = (
            "oi=" + result.loc[failing.index, "open_interest"].astype(str)
            + f", min={cfg.MIN_OI}"
        )
        quar_parts.append(failing)
    result = result.loc[oi_ok].copy()

    # Monotonicity (same logic as cleaning.py)
    if not result.empty:
        result = result.sort_values(
            ["expiration", "timestamp", "option_type", "strike"]
        ).copy()
        prev_mid = result.groupby(
            ["expiration", "timestamp", "option_type"]
        )["mid_price"].shift(1)
        prev_mid_filled = prev_mid.fillna(result["mid_price"]).values
        mid_arr = result["mid_price"].values
        is_call = result["option_type"].values == "C"
        violations = np.where(
            is_call, mid_arr > prev_mid_filled, mid_arr < prev_mid_filled
        )
        failing = result.loc[violations].copy()
        if not failing.empty:
            failing["reject_code"] = "MONOTONICITY"
            failing["reject_detail"] = (
                "strike=" + result.loc[failing.index, "strike"].astype(str)
                + ", option_type=" + result.loc[failing.index, "option_type"]
            )
            quar_parts.append(failing)
        result = result.loc[~violations].copy()

    # Expiry-imminent flag (DTE=1)
    if "dte_calendar" in result.columns:
        imminent = result["dte_calendar"] == cfg.EXPIRY_IMMINENT_DTE
        result.loc[imminent, "quality_flags"] = (
            result.loc[imminent, "quality_flags"].astype(int) | cfg.QUALITY_EXPIRY_IMMINENT
        )

    result["_phase"] = "math"

    if quar_parts:
        quar_df = pd.concat(quar_parts)
        quar_df["_phase"] = "quarantine"
        if run_id is not None:
            quar_df["ingest_run_id"] = run_id
    else:
        quar_df = pd.DataFrame(columns=list(df.columns) + ["reject_code", "reject_detail", "_phase"])
        if run_id is not None:
            quar_df["ingest_run_id"] = run_id

    return result, quar_df


def join_spot_and_oi(
    opt_df: pd.DataFrame,
    stk_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    schedule_cache: Optional[dict] = None,
) -> pd.DataFrame:
    """Join spot prices and open interest onto the option DataFrame."""
    opt_df = _join_spot(opt_df, stk_df)
    opt_df = _join_oi(opt_df, oi_df, schedule_cache=schedule_cache)
    return opt_df


def attach_rates_and_math(
    clean_df: pd.DataFrame,
    rates_df: pd.DataFrame,
    cal,
    schedule_cache: dict,
    dividends_map: dict[dt.date, float] | None = None,
) -> pd.DataFrame:
    """Attach rates and compute T, forward, vega, session, anchors, parity skew."""
    clean_df = compute_business_T(clean_df, cal, schedule_cache=schedule_cache)
    clean_df = _attach_rates(clean_df, rates_df)
    clean_df = compute_forward_with_dividends(clean_df, dividends_map=dividends_map)
    clean_df = compute_vega(clean_df)
    clean_df = tag_session_phase(clean_df, cal, schedule_cache=schedule_cache)
    clean_df = compute_parity_skew(clean_df)
    return clean_df


def finalize_slice_metadata(clean_df: pd.DataFrame) -> pd.DataFrame:
    """Attach anchor and slice strike counts after post-join filters."""
    if clean_df.empty:
        return attach_slice_strike_count(attach_anchor_columns(clean_df))
    clean_df = attach_anchor_columns(clean_df)
    return attach_slice_strike_count(clean_df)
