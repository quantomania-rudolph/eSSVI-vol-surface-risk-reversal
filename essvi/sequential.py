"""Sequential slice-by-slice eSSVI calibration coordinator (plan §4, §14)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from essvi import config as cfg
from essvi.anchor import belly_mask
from essvi.constraints import check_calendar_pasquazzi
from essvi.exceptions import AnchorError
from essvi.regularize import temporal_reg_penalty
from essvi.solver import build_rho_grid, refine_rho_grid, solve_single_slice

_REQUIRED_MINUTE_KEYS = frozenset(
    {
        "timestamp",
        "slices",
        "rho_grid",
        "theta_grid",
        "psi_grid",
        "n_slices",
        "n_valid",
        "any_invalid",
        "is_total_kill",
    }
)

_REQUIRED_SLICE_KEYS = frozenset(
    {
        "dte",
        "rho",
        "theta",
        "phi",
        "psi",
        "anchor_k_star",
        "anchor_theta_star",
        "objective_value",
        "is_valid",
        "n_strikes",
        "n_belly",
        "quality_flag",
        "violations",
    }
)


def _minute_timestamp(df_minute: pd.DataFrame) -> pd.Timestamp:
    if "minute_timestamp" in df_minute.columns and df_minute["minute_timestamp"].notna().any():
        return pd.Timestamp(df_minute["minute_timestamp"].iloc[0])
    if "timestamp" in df_minute.columns and df_minute["timestamp"].notna().any():
        return pd.Timestamp(df_minute["timestamp"].iloc[0])
    return pd.NaT


def _safe_solve_single_slice(
    df_slice: pd.DataFrame,
    prev_slice_params: dict[str, float] | None,
    rho_grid: np.ndarray | None = None,
) -> dict[str, Any]:
    try:
        return solve_single_slice(df_slice, prev_slice_params, rho_grid=rho_grid)
    except AnchorError:
        return {
            "rho": float("nan"),
            "theta": float("nan"),
            "phi": float("nan"),
            "objective_value": float("inf"),
            "corridor": {},
            "is_valid": False,
            "violations": [("ANCHOR", "no valid strikes for anchor")],
            "n_iterations": 0,
            "anchor_k_star": float("nan"),
            "anchor_theta_star": float("nan"),
        }


def _slice_dte(df_slice: pd.DataFrame) -> int:
    if "dte" in df_slice.columns and df_slice["dte"].notna().any():
        return int(df_slice["dte"].iloc[0])
    if "dte_calendar" in df_slice.columns and df_slice["dte_calendar"].notna().any():
        return int(df_slice["dte_calendar"].iloc[0])
    raise KeyError("slice requires dte or dte_calendar")


def _partition_slices(df_minute: pd.DataFrame) -> list[tuple[int, pd.DataFrame]]:
    group_col = "dte" if "dte" in df_minute.columns else "dte_calendar"
    if group_col not in df_minute.columns:
        group_col = "expiration"
    groups: list[tuple[int, pd.DataFrame]] = []
    for key, df_slice in df_minute.groupby(group_col, sort=True):
        dte = int(key) if group_col in ("dte", "dte_calendar") else int(df_slice["dte"].iloc[0])
        groups.append((dte, df_slice.copy()))
    groups.sort(key=lambda item: item[0])
    return groups


def _slice_counts(df_slice: pd.DataFrame) -> tuple[int, int]:
    n_strikes = int(df_slice["strike"].nunique()) if "strike" in df_slice.columns else len(df_slice)
    if "belly_flag" in df_slice.columns:
        n_belly = int(df_slice["belly_flag"].sum())
    else:
        mask = belly_mask(df_slice)
        n_belly = int(mask.sum())
    return n_strikes, n_belly


def _is_stale_slice(df_slice: pd.DataFrame, minute_ts: pd.Timestamp) -> bool:
    if "timestamp" not in df_slice.columns or cfg.STALE_SLICE_MAX_MINUTES <= 0:
        return False
    slice_ts = pd.to_datetime(df_slice["timestamp"], utc=True)
    if slice_ts.isna().all():
        return False
    age_minutes = (minute_ts - slice_ts.max()).total_seconds() / 60.0
    return age_minutes > cfg.STALE_SLICE_MAX_MINUTES


def _lookup_prior_slice(
    prior_minute_params: dict[str, Any] | None,
    dte: int,
) -> dict[str, float] | None:
    if prior_minute_params is None:
        return None

    slices = prior_minute_params.get("slices")
    if slices:
        for sl in slices:
            if int(sl["dte"]) == dte:
                return {
                    "rho": float(sl["rho"]),
                    "theta": float(sl["theta"]),
                    "phi": float(sl["phi"]),
                }

    dte_grid = prior_minute_params.get("dte_grid")
    if dte_grid is None:
        return None

    dte_arr = np.asarray(dte_grid, dtype=int)
    idx = np.where(dte_arr == dte)[0]
    if idx.size == 0:
        return None
    i = int(idx[0])
    theta = prior_minute_params.get("theta_grid", prior_minute_params.get("theta"))
    rho = prior_minute_params.get("rho_grid", prior_minute_params.get("rho"))
    psi = prior_minute_params.get("psi_grid", prior_minute_params.get("psi"))
    if theta is None or rho is None:
        return None
    theta_v = float(np.asarray(theta, dtype=float)[i])
    rho_v = float(np.asarray(rho, dtype=float)[i])
    if psi is not None:
        psi_v = float(np.asarray(psi, dtype=float)[i])
        phi_v = psi_v / max(theta_v, cfg.THETA_PROJECTION_EPS)
    elif "phi_grid" in prior_minute_params:
        phi_v = float(np.asarray(prior_minute_params["phi_grid"], dtype=float)[i])
    else:
        phi_v = 1.0
    return {"rho": rho_v, "theta": theta_v, "phi": phi_v}


def copy_prior_params(
    df_slice: pd.DataFrame,
    dte: int,
    prev_slice_params: dict[str, float] | None,
    prior_minute_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Carry forward prior params when calibration is infeasible."""
    source = _lookup_prior_slice(prior_minute_params, dte)
    if source is None and prev_slice_params is not None:
        source = {
            "rho": float(prev_slice_params["rho"]),
            "theta": float(prev_slice_params["theta"]),
            "phi": float(prev_slice_params["phi"]),
        }
    if source is None:
        anchor_theta = (
            float(df_slice["anchor_theta_star"].iloc[0])
            if "anchor_theta_star" in df_slice.columns
            else 0.08
        )
        source = {
            "rho": cfg.SHORT_MATURITY_RHO_PRIOR,
            "theta": anchor_theta,
            "phi": 1.0,
        }

    rho = float(source["rho"])
    theta = float(source["theta"])
    phi = float(source["phi"])
    psi = theta * phi
    n_strikes, n_belly = _slice_counts(df_slice)
    anchor_k = (
        float(df_slice["anchor_k_star"].iloc[0])
        if "anchor_k_star" in df_slice.columns
        else 0.0
    )
    anchor_theta = (
        float(df_slice["anchor_theta_star"].iloc[0])
        if "anchor_theta_star" in df_slice.columns
        else theta
    )

    return {
        "dte": dte,
        "rho": rho,
        "theta": theta,
        "phi": phi,
        "psi": psi,
        "anchor_k_star": anchor_k,
        "anchor_theta_star": anchor_theta,
        "objective_value": float("inf"),
        "is_valid": False,
        "n_strikes": n_strikes,
        "n_belly": n_belly,
        "quality_flag": "DEGENERATE",
        "violations": [("DEGENERATE", "empty corridor — copied prior params")],
    }


def should_cold_start(
    df_minute: pd.DataFrame,
    prior_minute_params: dict | None,
) -> bool:
    """Check COLD_START_AT_SESSION_OPEN and session_phase."""
    if prior_minute_params is None:
        return True
    if "session_phase" in df_minute.columns:
        phases = df_minute["session_phase"].dropna().astype(str).unique()
        if "pre_open" in phases:
            return True
    if cfg.COLD_START_AT_SESSION_OPEN and prior_minute_params.get("cold_start", False):
        return True
    return False


def build_correlation_grid(slice_results: list[dict[str, Any]]) -> np.ndarray:
    """Extract rho values ordered by DTE."""
    ordered = sorted(slice_results, key=lambda sl: int(sl["dte"]))
    return np.asarray([float(sl["rho"]) for sl in ordered], dtype=float)


def _enrich_slice_result(
    solver_result: dict[str, Any],
    dte: int,
    df_slice: pd.DataFrame,
    *,
    quality_flag: str = "VALID",
) -> dict[str, Any]:
    n_strikes, n_belly = _slice_counts(df_slice)
    theta = float(solver_result["theta"])
    phi = float(solver_result["phi"])
    return {
        "dte": dte,
        "rho": float(solver_result["rho"]),
        "theta": theta,
        "phi": phi,
        "psi": theta * phi,
        "anchor_k_star": float(solver_result.get("anchor_k_star", float("nan"))),
        "anchor_theta_star": float(solver_result.get("anchor_theta_star", float("nan"))),
        "objective_value": float(solver_result.get("objective_value", float("inf"))),
        "is_valid": bool(solver_result.get("is_valid", False)),
        "n_strikes": n_strikes,
        "n_belly": n_belly,
        "quality_flag": quality_flag,
        "violations": list(solver_result.get("violations", [])),
    }


def _rho_grid_for_slice(
    prev_slice_params: dict[str, float] | None,
    prior_minute_params: dict[str, Any] | None,
    dte: int,
    *,
    warmstart: bool,
    temporal_active: bool,
    widen: bool = False,
) -> np.ndarray:
    rho_prev = None if prev_slice_params is None else float(prev_slice_params["rho"])
    if widen:
        base = build_rho_grid(None)
    else:
        base = build_rho_grid(rho_prev)

    if not warmstart or not temporal_active or prior_minute_params is None:
        return base

    prior = _lookup_prior_slice(prior_minute_params, dte)
    if prior is None:
        return base

    prior_rho = float(prior["rho"])
    if rho_prev is not None and abs(prior_rho - rho_prev) > cfg.RHO_MAX_STEP + 1e-12:
        return base

    hint_grid = refine_rho_grid(
        prior_rho,
        cfg.RHO_GRID_STEP,
        max(cfg.RHO_GRID_REFINE_FACTOR, 5),
    )
    combined = np.unique(np.concatenate([base, hint_grid, np.array([prior_rho])]))
    if rho_prev is not None:
        combined = combined[np.abs(combined - rho_prev) <= cfg.RHO_MAX_STEP + 1e-12]
    return combined


def _is_short_maturity_degenerate(df_slice: pd.DataFrame, dte: int) -> bool:
    if dte > cfg.EXPIRY_IMMINENT_DTE:
        return False
    n_strikes, n_belly = _slice_counts(df_slice)
    return n_strikes < cfg.MIN_STRIKES_PER_SLICE or n_belly < cfg.MIN_STRIKES_PER_SLICE


def handle_short_maturity(
    df_slice: pd.DataFrame,
    prev_minute_params: dict | None,
    *,
    prev_slice_params: dict[str, float] | None = None,
    next_slice_rho: float | None = None,
    dte: int | None = None,
) -> dict[str, Any]:
    """Apply SHORT_MATURITY_RHO_FALLBACK strategy."""
    dte_val = dte if dte is not None else _slice_dte(df_slice)
    strategy = cfg.SHORT_MATURITY_RHO_FALLBACK

    if strategy == "next_slice":
        if next_slice_rho is None:
            return {"action": "defer", "quality_flag": "EXPIRY_IMMINENT_DEFERRED"}
        rho_fixed = float(next_slice_rho)
    elif strategy == "prior":
        prior = _lookup_prior_slice(prev_minute_params, dte_val)
        rho_fixed = float(prior["rho"]) if prior is not None else cfg.SHORT_MATURITY_RHO_PRIOR
    elif strategy == "fixed":
        rho_fixed = float(cfg.SHORT_MATURITY_RHO_PRIOR)
    elif strategy == "fit_psi_only":
        prior = _lookup_prior_slice(prev_minute_params, dte_val)
        if prior is not None:
            rho_fixed = float(prior["rho"])
        elif prev_slice_params is not None:
            rho_fixed = float(prev_slice_params["rho"])
        else:
            rho_fixed = float(cfg.SHORT_MATURITY_RHO_PRIOR)
    else:
        msg = f"unsupported SHORT_MATURITY_RHO_FALLBACK: {strategy}"
        raise ValueError(msg)

    solver_result = _safe_solve_single_slice(
        df_slice,
        prev_slice_params,
        rho_grid=np.array([rho_fixed], dtype=float),
    )
    quality = "EXPIRY_IMMINENT"
    if strategy == "fit_psi_only":
        quality = "EXPIRY_IMMINENT_PSI_ONLY"
    return {
        "action": "solved",
        "result": _enrich_slice_result(solver_result, dte_val, df_slice, quality_flag=quality),
    }


def handle_degenerate_slice(
    df_slice: pd.DataFrame,
    prev_params: dict[str, float] | None,
    strategy: str,
    *,
    prior_minute_params: dict | None = None,
    dte: int | None = None,
) -> dict[str, Any]:
    """Apply EMPTY_CORRIDOR_STRATEGY fallback."""
    dte_val = dte if dte is not None else _slice_dte(df_slice)

    if strategy == "widen_rho_first":
        wide_result = _safe_solve_single_slice(
            df_slice,
            prev_params,
            rho_grid=_rho_grid_for_slice(
                prev_params,
                prior_minute_params,
                dte_val,
                warmstart=False,
                temporal_active=False,
                widen=True,
            ),
        )
        if wide_result.get("is_valid") or math.isfinite(wide_result.get("rho", float("nan"))):
            if not math.isnan(wide_result["rho"]):
                return _enrich_slice_result(
                    wide_result,
                    dte_val,
                    df_slice,
                    quality_flag="DEGENERATE_RECOVERED",
                )

    rho_fixed = cfg.SHORT_MATURITY_RHO_PRIOR
    if prev_params is not None:
        rho_fixed = float(prev_params["rho"])
    else:
        prior = _lookup_prior_slice(prior_minute_params, dte_val)
        if prior is not None:
            rho_fixed = float(prior["rho"])

    psi_only = _safe_solve_single_slice(
        df_slice,
        prev_params,
        rho_grid=np.array([rho_fixed], dtype=float),
    )
    if psi_only.get("is_valid") or (
        math.isfinite(psi_only.get("rho", float("nan")))
        and not math.isnan(psi_only["rho"])
    ):
        return _enrich_slice_result(
            psi_only,
            dte_val,
            df_slice,
            quality_flag="DEGENERATE_RECOVERED",
        )

    return copy_prior_params(df_slice, dte_val, prev_params, prior_minute_params)


def validate_minute_result(result: dict) -> bool:
    """
    Check required keys, slice ordering, calendar condition, and no NaN params.
    """
    if not _REQUIRED_MINUTE_KEYS <= set(result.keys()):
        return False

    slices = result.get("slices", [])
    if not slices:
        return False

    if not all(_REQUIRED_SLICE_KEYS <= set(sl.keys()) for sl in slices):
        return False

    dtes = [int(sl["dte"]) for sl in slices]
    if dtes != sorted(dtes):
        return False

    thetas = [float(sl["theta"]) for sl in slices]
    if thetas != sorted(thetas):
        return False

    for sl in slices:
        for key in ("rho", "theta", "phi", "psi"):
            val = float(sl[key])
            if not math.isfinite(val):
                return False

    for i in range(len(slices) - 1):
        psi_i = float(slices[i]["psi"])
        psi_j = float(slices[i + 1]["psi"])
        if psi_i > psi_j + cfg.THETA_MONOTONICITY_EPS:
            return False
        prev = {
            "theta": float(slices[i]["theta"]),
            "phi": float(slices[i]["phi"]),
            "rho": float(slices[i]["rho"]),
        }
        curr = {
            "theta": float(slices[i + 1]["theta"]),
            "phi": float(slices[i + 1]["phi"]),
            "rho": float(slices[i + 1]["rho"]),
        }
        ok, _ = check_calendar_pasquazzi(prev, curr)
        if not ok:
            return False

    rho_grid = np.asarray(result["rho_grid"], dtype=float)
    theta_grid = np.asarray(result["theta_grid"], dtype=float)
    psi_grid = np.asarray(result["psi_grid"], dtype=float)
    if rho_grid.size != len(slices):
        return False
    if theta_grid.size != len(slices) or psi_grid.size != len(slices):
        return False
    if np.any(~np.isfinite(rho_grid)) or np.any(~np.isfinite(theta_grid)) or np.any(~np.isfinite(psi_grid)):
        return False

    return True


def calibrate_one_minute(
    df_minute: pd.DataFrame,
    prior_minute_params: dict | None,
    warmstart: bool = True,
) -> dict:
    """
    Sequential calibration for one minute snapshot (ascending DTE).
    """
    if df_minute.empty:
        ts = pd.NaT
    else:
        ts = _minute_timestamp(df_minute)

    cold_start = should_cold_start(df_minute, prior_minute_params)
    temporal_active = warmstart and not cold_start and prior_minute_params is not None

    slice_groups = _partition_slices(df_minute)
    deferred: list[tuple[int, pd.DataFrame]] = []
    slice_results: list[dict[str, Any]] = []
    prev_locked: dict[str, float] | None = None

    for dte, df_slice in slice_groups:
        quality_flag = "VALID"
        if _is_stale_slice(df_slice, ts):
            quality_flag = "STALE_SLICE"

        if _is_short_maturity_degenerate(df_slice, dte):
            short = handle_short_maturity(
                df_slice,
                prior_minute_params,
                prev_slice_params=prev_locked,
            )
            if short.get("action") == "defer":
                deferred.append((dte, df_slice))
                continue
            sl = short["result"]
            if quality_flag == "STALE_SLICE":
                sl["quality_flag"] = "STALE_SLICE"
            slice_results.append(sl)
            if sl["is_valid"]:
                prev_locked = {
                    "rho": sl["rho"],
                    "theta": sl["theta"],
                    "phi": sl["phi"],
                }
            continue

        rho_grid = _rho_grid_for_slice(
            prev_locked,
            prior_minute_params,
            dte,
            warmstart=warmstart,
            temporal_active=temporal_active,
        )
        solver_result = _safe_solve_single_slice(df_slice, prev_locked, rho_grid=rho_grid)

        if not solver_result.get("is_valid") and (
            not math.isfinite(solver_result.get("rho", float("nan")))
            or math.isnan(solver_result["rho"])
        ):
            sl = handle_degenerate_slice(
                df_slice,
                prev_locked,
                cfg.EMPTY_CORRIDOR_STRATEGY,
                prior_minute_params=prior_minute_params,
                dte=dte,
            )
        else:
            sl = _enrich_slice_result(solver_result, dte, df_slice, quality_flag=quality_flag)

        if quality_flag == "STALE_SLICE" and sl["quality_flag"] == "VALID":
            sl["quality_flag"] = "STALE_SLICE"

        slice_results.append(sl)
        if sl["is_valid"]:
            prev_locked = {"rho": sl["rho"], "theta": sl["theta"], "phi": sl["phi"]}

    for dte, df_slice in deferred:
        next_rho = None
        for sl in slice_results:
            if int(sl["dte"]) > dte:
                next_rho = float(sl["rho"])
                break
        short = handle_short_maturity(
            df_slice,
            prior_minute_params,
            prev_slice_params=prev_locked,
            next_slice_rho=next_rho,
            dte=dte,
        )
        if short.get("action") == "defer":
            sl = copy_prior_params(df_slice, dte, prev_locked, prior_minute_params)
            sl["quality_flag"] = "EXPIRY_IMMINENT_DEFERRED"
            slice_results.append(sl)
            continue

        sl = short["result"]
        if _is_stale_slice(df_slice, ts):
            sl["quality_flag"] = "STALE_SLICE"
        slice_results.append(sl)
        if sl["is_valid"]:
            prev_locked = {"rho": sl["rho"], "theta": sl["theta"], "phi": sl["phi"]}

    slice_results.sort(key=lambda sl: int(sl["dte"]))

    rho_grid = build_correlation_grid(slice_results)
    theta_grid = np.asarray([float(sl["theta"]) for sl in slice_results], dtype=float)
    psi_grid = np.asarray([float(sl["psi"]) for sl in slice_results], dtype=float)

    n_valid = sum(1 for sl in slice_results if sl["is_valid"])
    any_invalid = n_valid < len(slice_results)
    is_total_kill = len(slice_results) > 0 and n_valid == 0

    temporal_penalty = 0.0
    if temporal_active and prior_minute_params is not None:
        prior_thetas = []
        prior_rhos = []
        prior_psis = []
        for sl in slice_results:
            prior_sl = _lookup_prior_slice(prior_minute_params, int(sl["dte"]))
            if prior_sl is None:
                continue
            prior_thetas.append(prior_sl["theta"])
            prior_rhos.append(prior_sl["rho"])
            prior_psis.append(prior_sl["theta"] * prior_sl["phi"])
        if prior_thetas:
            temporal_penalty = temporal_reg_penalty(
                theta_grid[: len(prior_thetas)],
                rho_grid[: len(prior_rhos)],
                psi_grid[: len(prior_psis)],
                np.asarray(prior_thetas, dtype=float),
                np.asarray(prior_rhos, dtype=float),
                np.asarray(prior_psis, dtype=float),
            )

    return {
        "timestamp": ts,
        "slices": slice_results,
        "rho_grid": rho_grid,
        "theta_grid": theta_grid,
        "psi_grid": psi_grid,
        "n_slices": len(slice_results),
        "n_valid": n_valid,
        "any_invalid": any_invalid,
        "is_total_kill": is_total_kill,
        "cold_start": cold_start,
        "temporal_active": temporal_active,
        "temporal_penalty": temporal_penalty,
    }
