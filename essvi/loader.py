"""Load minute-level calibration panels from amd_surface_min."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from essvi import config as cfg
from essvi.exceptions import DataNotFoundError, MissingColumnError


# Actual columns in amd_surface_min (per dataingestion.md schema)
_REQUIRED_DB_COLUMNS = (
    "ts",
    "underlying",
    "expiration",
    "strike",
    "option_type",
    "spot_price",
    "forward_price",
    "implied_vol",
    "option_mid",
    "spread",
    "vega",
    "bid",
    "ask",
    "delta",
    "r",
    "q",
    "business_t",
    "dte_calendar",
    "log_moneyness",
    "open_interest",
    "quality_flags",
    "ingest_run_id",
    "underlying_timestamp",
)

# Column rename map (DB -> loader internal names)
_COLUMN_RENAME_MAP = {
    "ts": "timestamp",
    "underlying": "root",
    "option_type": "right",
    "dte_calendar": "dte",
    "delta": "delta_black76",
    "open_interest": "oi",
    "option_mid": "mid_price",      # Already in DB!
    "log_moneyness": "log_moneyness",  # Already in DB!
}

# Final required columns after rename + derived computation
_REQUIRED_COLUMNS = (
    "timestamp",
    "root",
    "expiration",
    "strike",
    "right",
    "bid",
    "ask",
    "mid_price",
    "rel_spread",
    "oi",
    "spot_price",
    "forward_price",
    "r",
    "q",
    "business_t",
    "log_moneyness",
    "vega",
    "delta_black76",
    "session_phase",
    "parity_skew",
    "anchor_k_star",
    "anchor_theta_star",
    "anchor_quality",
    "slice_strike_count",
    "OTM",
    "belly_flag",
)


def _normalize_db_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Rename DB columns to loader internal names."""
    out = df.copy()
    out = out.rename(columns=_COLUMN_RENAME_MAP)
    if "root" not in out.columns and "underlying" in out.columns:
        out["root"] = out["underlying"]
    return out


def _compute_session_phase(timestamps: pd.Series) -> pd.Series:
    """Classify each row by trading session phase."""
    et = timestamps.dt.tz_convert("US/Eastern")
    hour = et.dt.hour + et.dt.minute / 60.0
    
    phase = pd.Series("regular", index=timestamps.index)
    phase[hour < 9.5] = "premarket"
    phase[(hour >= 9.5) & (hour < 16.0)] = "regular"
    phase[hour >= 16.0] = "postmarket"
    return phase.astype("category")


def _compute_otm_flag(df: pd.DataFrame) -> pd.Series:
    """OTM flag: |delta| < 0.5 (standard definition)."""
    return (df["delta_black76"].abs() < 0.5).astype("bool")


def _compute_anchor_quality(df: pd.DataFrame) -> pd.Series:
    """Per-expiration anchor quality metric.
    
    Quality combines belly strike metrics: 
    - inverse of average relative spread in belly
    - log of total open interest in belly
    - sqrt of belly strike count
    """
    quality_map = {}
    for exp, grp in df.groupby("expiration"):
        belly_rows = grp.nsmallest(3, "log_moneyness")
        if len(belly_rows) > 0:
            avg_rel_spread = belly_rows["rel_spread"].mean() if "rel_spread" in belly_rows.columns else 0.1
            total_oi = belly_rows["oi"].sum() if "oi" in belly_rows.columns else 100
            strike_count = len(belly_rows)
            # Quality = (1 - avg_spread) * log(1 + OI) * sqrt(strike_count)
            quality = max(0.0, 1 - avg_rel_spread) * np.log1p(total_oi) * np.sqrt(strike_count)
        else:
            quality = 1.0
        quality_map[exp] = quality
    
    return df["expiration"].map(quality_map)


def _compute_parity_skew(df: pd.DataFrame) -> pd.Series:
    """Put-call parity skew per expiration.
    
    For each expiration & strike, pair put/call and compute:
    skew = (call_mid - put_mid) / forward - (strike/forward - 1)
    """
    if df.empty:
        return pd.Series(0.0, index=df.index)
    
    # Pivot to get call/put mid prices by expiration and strike
    pivot = df.pivot_table(
        index=["expiration", "strike"],
        columns="right",
        values="mid_price",
        aggfunc="first"
    ).reset_index()
    
    if "C" not in pivot.columns or "P" not in pivot.columns:
        return pd.Series(0.0, index=df.index)
    
    # Merge forward prices
    fwd_map = df.drop_duplicates("expiration").set_index("expiration")["forward_price"]
    pivot["forward"] = pivot["expiration"].map(fwd_map)
    
    # Compute parity skew
    pivot["parity_skew"] = (
        (pivot["C"] - pivot["P"]) / pivot["forward"] - (pivot["strike"] / pivot["forward"] - 1)
    )
    
    # Merge back to original dataframe
    skew_map = pivot.set_index(["expiration", "strike"])["parity_skew"]
    return df.set_index(["expiration", "strike"]).index.map(skew_map).fillna(0.0)


def _compute_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add all columns that are NOT in the DB but needed by calibration engine."""
    df = df.copy()
    
    # rel_spread = spread / mid_price
    df["rel_spread"] = df["spread"] / df["mid_price"].replace(0, np.nan)
    
    # session_phase from timestamp (US/Eastern market hours)
    df["session_phase"] = _compute_session_phase(df["timestamp"])
    
    # OTM flag: |delta| < 0.5
    df["OTM"] = _compute_otm_flag(df)
    
    # Slice-level aggregations (per expiration)
    # slice_strike_count: count unique strikes per expiration
    slice_stats = df.groupby("expiration").agg(
        slice_strike_count=("strike", "nunique"),
    ).reset_index()
    
    # anchor_k_star: belly strike = log_moneyness closest to forward (min |log_moneyness|)
    idx_min_abs_logm = df.groupby("expiration")["log_moneyness"].apply(lambda x: x.abs().idxmin())
    slice_stats["anchor_k_star"] = df.loc[idx_min_abs_logm.values, "log_moneyness"].values
    
    # anchor_theta_star: ATM total variance per slice
    # For each expiration, find row with min |log_moneyness|, get w = iv^2 * business_t
    idx_min = df.groupby("expiration")["log_moneyness"].apply(lambda x: x.abs().idxmin())
    belly_rows = df.loc[idx_min.values]
    belly_map = belly_rows.set_index("expiration").apply(
        lambda row: (row["implied_vol"] ** 2) * row["business_t"], axis=1
    )
    slice_stats["anchor_theta_star"] = slice_stats["expiration"].map(belly_map)
    
    # anchor_quality: per-expiration belly quality metric
    slice_stats["anchor_quality"] = _compute_anchor_quality(df)
    
    # Merge slice stats back
    df = df.merge(slice_stats, on="expiration", how="left")
    
    # belly_flag: |log_moneyness - anchor_k_star| < 0.1
    df["belly_flag"] = (df["log_moneyness"] - df["anchor_k_star"]).abs() < 0.1
    
    # parity_skew: put-call parity skew per expiration
    df["parity_skew"] = _compute_parity_skew(df)
    
    return df


def _validate_computed_columns(df: pd.DataFrame) -> None:
    """Ensure all required computed columns exist and have no NaN in critical fields."""
    required_computed = [
        "rel_spread", "session_phase", "OTM", "slice_strike_count",
        "anchor_k_star", "anchor_theta_star", "anchor_quality",
        "belly_flag", "parity_skew"
    ]
    missing = [c for c in required_computed if c not in df.columns]
    if missing:
        raise MissingColumnError(f"Computed columns missing: {missing}")
    
    # Critical columns must not be NaN
    critical = ["anchor_k_star", "anchor_theta_star", "slice_strike_count"]
    for c in critical:
        if df[c].isna().any():
            raise ValueError(f"Critical column {c} has NaN values")


def _validate_contract(df: pd.DataFrame) -> None:
    missing = set(_REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise MissingColumnError(sorted(missing))


def _select_contract(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[:, list(_REQUIRED_COLUMNS)].copy()


def _query_minute(timestamp: pd.Timestamp, conn: Any, underlying: str = "AMD") -> pd.DataFrame:
    ts = pd.Timestamp(timestamp)
    if isinstance(conn, pd.DataFrame):
        if conn.empty:
            return conn.copy()
        ts_col = "timestamp" if "timestamp" in conn.columns else "ts"
        mask = pd.to_datetime(conn[ts_col]) == ts
        if "underlying" in conn.columns:
            mask = mask & (conn["underlying"] == underlying)
        return conn.loc[mask].copy()
    if hasattr(conn, "execute") and not hasattr(conn, "cursor"):
        raise TypeError("Use a sync connection or pass a pre-fetched DataFrame via conn")
    cursor = conn.cursor()
    cols = ", ".join(_REQUIRED_DB_COLUMNS)
    cursor.execute(
        f"SELECT {cols} FROM {cfg.SURFACE_TABLE} WHERE ts = %s AND underlying = %s",
        (ts.to_pydatetime(), underlying),
    )
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    return pd.DataFrame(rows, columns=cols)


def load_minute_slice(
    timestamp: pd.Timestamp,
    conn: Any = None,
    config: Any | None = None,
    underlying: str = "AMD",
) -> pd.DataFrame:
    """Load one minute slice from amd_surface_min, rename columns, compute derived fields."""
    config = config or cfg
    if conn is None:
        raise DataNotFoundError(timestamp)

    # 1. Query with ONLY db columns
    df = _query_minute(timestamp, conn, underlying)
    
    if df.empty:
        raise DataNotFoundError(f"No data for {underlying} at {timestamp}")
    
    # 2. Rename DB columns to loader internal names
    df = _normalize_db_frame(df)
    
    # 3. Compute derived columns
    df = _compute_derived_columns(df)
    
    # 4. Validate required computed columns exist
    _validate_computed_columns(df)
    
    # 5. DTE filter
    if "dte" in df.columns:
        df = df[(df["dte"] >= config.MIN_DTE) & (df["dte"] <= config.MAX_DTE)]
    
    # 6. Validate final contract
    _validate_contract(df)
    
    # 7. Select required columns
    return df[list(_REQUIRED_COLUMNS)].reset_index(drop=True)
