"""In-memory quality and arbitrage cleaning for option chain DataFrames.

Pure pandas/numpy — no HTTP, no Theta, no database, no file I/O.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dataingestion import config as cfg


def clean_option_chain(df: pd.DataFrame, run_id: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply all quality and arbitrage checks per dataingestion.md Sections 4-5.

    Order: cheap structural rejects first, cross-strike checks last.
    DTE and delta bands applied first as safety nets (pre-filter also
    applied in orchestrator at fetch time). Monotonicity (the most
    expensive cross-strike check) runs last.

    Args:
        df: Raw DataFrame from fetchers.py (must satisfy COLUMNS.md Section I).
        run_id: Optional ingest_run_id to attach to quarantine rows for traceability.

    Returns:
        (clean_df, quarantine_df) tuple.
        clean_df has additional columns: mid_price, spread, rel_spread,
          quality_flags, dte_calendar, and _phase="clean".
        quarantine_df has all original columns plus reject_code, reject_detail,
          _phase="quarantine", and ingest_run_id (if run_id provided).
    """
    result = df.copy()
    quar_parts: list[pd.DataFrame] = []

    # ------------------------------------------------------------------
    # Safety-net pre-filter: DTE band only (delta/OI run post-join in joins.py)
    # ------------------------------------------------------------------
    bar_dates = pd.to_datetime(result["timestamp"].dt.date)
    exp_dates = pd.to_datetime(result["expiration"].dt.date)
    dte_val = (exp_dates - bar_dates).dt.days
    dte_ok = (dte_val >= cfg.MIN_DTE) & (dte_val <= cfg.MAX_DTE)
    failing = result.loc[~dte_ok].copy()
    failing["reject_code"] = "DTE_BAND"
    failing["reject_detail"] = (
        "DTE=" + dte_val.loc[failing.index].astype(str)
        + f", min={cfg.MIN_DTE}, max={cfg.MAX_DTE}"
    )
    quar_parts.append(failing)
    result = result.loc[dte_ok].copy()

    # ------------------------------------------------------------------
    # Compute derived columns on remaining rows
    # ------------------------------------------------------------------
    result["mid_price"] = (result["bid"] + result["ask"]) / 2.0
    result["spread"] = result["ask"] - result["bid"]
    result["rel_spread"] = np.where(
        result["mid_price"] > 0,
        result["spread"] / result["mid_price"],
        0.0,
    )
    result["quality_flags"] = 0
    result["dte_calendar"] = (
        pd.to_datetime(result["expiration"].dt.date)
        - pd.to_datetime(result["timestamp"].dt.date)
    ).dt.days

    imminent = result["dte_calendar"] == cfg.EXPIRY_IMMINENT_DTE
    result.loc[imminent, "quality_flags"] = (
        result.loc[imminent, "quality_flags"] | cfg.QUALITY_EXPIRY_IMMINENT
    )

    # ------------------------------------------------------------------
    # Quality check 3: No-quote — bid > 0 AND ask > 0
    # ------------------------------------------------------------------
    no_quote_ok = (result["bid"] > 0) & (result["ask"] > 0)
    failing = result.loc[~no_quote_ok].copy()
    failing["reject_code"] = "NO_QUOTE"
    failing["reject_detail"] = "bid=" + result.loc[failing.index, "bid"].astype(str) + ", ask=" + result.loc[failing.index, "ask"].astype(str)
    quar_parts.append(failing)
    result = result.loc[no_quote_ok].copy()

    # ------------------------------------------------------------------
    # Quality check 4: Crossed — ask > bid
    # ------------------------------------------------------------------
    crossed_ok = result["ask"] > result["bid"]
    failing = result.loc[~crossed_ok].copy()
    failing["reject_code"] = "CROSSED"
    failing["reject_detail"] = "bid=" + result.loc[failing.index, "bid"].astype(str) + ", ask=" + result.loc[failing.index, "ask"].astype(str)
    quar_parts.append(failing)
    result = result.loc[crossed_ok].copy()

    # ------------------------------------------------------------------
    # Quality check 5: Subpenny — bid and ask on penny grid
    # Tolerance-based: |round(x*100) - x*100| < SUBPENNY_EPS
    # ------------------------------------------------------------------
    bid_on_grid = np.abs(np.round(result["bid"].values * 100) - result["bid"].values * 100) < cfg.SUBPENNY_EPS
    ask_on_grid = np.abs(np.round(result["ask"].values * 100) - result["ask"].values * 100) < cfg.SUBPENNY_EPS
    subpenny_ok = bid_on_grid & ask_on_grid
    failing = result.loc[~subpenny_ok].copy()
    failing["reject_code"] = "SUBPENNY"
    failing["reject_detail"] = "bid=" + result.loc[failing.index, "bid"].astype(str) + ", ask=" + result.loc[failing.index, "ask"].astype(str)
    quar_parts.append(failing)
    result = result.loc[subpenny_ok].copy()

    # ------------------------------------------------------------------
    # Quality check 6: Spread — hard reject > 0.25, belly flag > 0.10
    # ------------------------------------------------------------------
    spread_hard = result["rel_spread"] > cfg.MAX_REL_SPREAD_HARD
    failing = result.loc[spread_hard].copy()
    failing["reject_code"] = "SPREAD_HARD"
    failing["reject_detail"] = "rel_spread=" + result.loc[failing.index, "rel_spread"].round(4).astype(str) + ", limit=0.25"
    quar_parts.append(failing)
    result = result.loc[~spread_hard].copy()

    # Belly flag (bit 0) — rows stay in clean
    belly_mask = result["rel_spread"] > cfg.MAX_REL_SPREAD_BELLY
    result.loc[belly_mask, "quality_flags"] = result.loc[belly_mask, "quality_flags"] | cfg.QUALITY_BELLY_SPREAD

    # ------------------------------------------------------------------
    # Quality check: Bad mid — mid_price <= 0 cannot form valid spread
    # (catches rows where rel_spread was clamped to 0.0 via np.where)
    # ------------------------------------------------------------------
    bad_mid = result["mid_price"] <= 0
    failing = result.loc[bad_mid].copy()
    failing["reject_code"] = "BAD_MID"
    failing["reject_detail"] = "mid_price=" + result.loc[failing.index, "mid_price"].astype(str) + ", must be > 0"
    quar_parts.append(failing)
    result = result.loc[~bad_mid].copy()

    # ------------------------------------------------------------------
    # Quality check 7: Zero-IV — implied_vol > 0.005
    # ------------------------------------------------------------------
    zero_iv_ok = result["implied_vol"] > cfg.MIN_IV
    failing = result.loc[~zero_iv_ok].copy()
    failing["reject_code"] = "ZERO_IV"
    failing["reject_detail"] = "iv=" + result.loc[failing.index, "implied_vol"].astype(str) + ", min=0.005"
    quar_parts.append(failing)
    result = result.loc[zero_iv_ok].copy()

    # OI and monotonicity filters run post-join (joins.apply_post_join_filters)
    # after forward_price and local delta are computed.

    # ------------------------------------------------------------------
    # Quality check 8: Intrinsic value
    #   calls: mid >= max(0, spot_close - strike)
    #   puts:  mid >= max(0, strike - spot_close)
    #   SKIP for belly-flagged rows (rel_spread > 0.10):
    #     wide spreads make mids unreliable for intrinsic checks.
    # ------------------------------------------------------------------
    spot_na_ok = result["spot_close"].notna().values
    failing = result.loc[~spot_na_ok].copy()
    failing["reject_code"] = "SPOT_NA"
    failing["reject_detail"] = "spot_close is NaN"
    quar_parts.append(failing)
    result = result.loc[spot_na_ok].copy()

    is_belly = (result["quality_flags"].values & cfg.QUALITY_BELLY_SPREAD) != 0
    is_call_intr = result["option_type"].values == "C"
    intrinsic = np.where(
        is_call_intr,
        np.maximum(0, result["spot_close"].values - result["strike"].values),
        np.maximum(0, result["strike"].values - result["spot_close"].values),
    )
    intrinsic_violation = result["mid_price"].values < intrinsic
    # Only quarantine non-belly rows that violate intrinsic
    intrinsic_quar = intrinsic_violation & ~is_belly
    failing = result.loc[intrinsic_quar].copy()
    failing["reject_code"] = "INTRINSIC"
    failing["reject_detail"] = "mid=" + result.loc[failing.index, "mid_price"].round(4).astype(str) + ", intrinsic=" + pd.Series(intrinsic[failing.index.values]).round(4).astype(str)
    quar_parts.append(failing)
    result = result.loc[~intrinsic_quar].copy()

    # ------------------------------------------------------------------
    # Final assembly
    # ------------------------------------------------------------------
    result["_phase"] = "clean"
    result["quality_flags"] = result["quality_flags"].astype("int32")
    result["mid_price"] = result["mid_price"].astype("float64")
    result["spread"] = result["spread"].astype("float64")
    result["rel_spread"] = result["rel_spread"].astype("float64")
    result["dte_calendar"] = result["dte_calendar"].astype(int)

    # Assemble quarantine DataFrame
    if quar_parts:
        quar_df = pd.concat(quar_parts)
        quar_df["_phase"] = "quarantine"
        if run_id is not None:
            quar_df["ingest_run_id"] = run_id
    else:
        quar_df = pd.DataFrame(
            columns=list(df.columns) + ["reject_code", "reject_detail", "_phase"]
        )
        if run_id is not None:
            quar_df["ingest_run_id"] = run_id

    return result, quar_df