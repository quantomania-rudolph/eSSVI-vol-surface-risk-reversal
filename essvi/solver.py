"""Per-slice eSSVI solver: two-stage rho grid search + Brent inner on phi."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from essvi import config as cfg
from essvi.anchor import extract_anchor_params
from essvi.constraints import (
    build_corridor,
    check_butterfly,
    check_calendar_pasquazzi,
    check_lee_bound,
    check_vertical_spread,
)
from essvi.objective import objective_slice
from essvi.regularize import spatial_reg_penalty

_TOP_RHO_CANDIDATES = 3


def build_rho_grid(
    rho_prev: float | None,
    lo: float | None = None,
    hi: float | None = None,
    step: float | None = None,
    max_step: float | None = None,
) -> np.ndarray:
    """Coarse rho grid, constrained by |rho - rho_prev| <= max_step if rho_prev given."""
    lo_v = cfg.RHO_GRID_LO if lo is None else lo
    hi_v = cfg.RHO_GRID_HI if hi is None else hi
    step_v = cfg.RHO_GRID_STEP if step is None else step
    max_step_v = cfg.RHO_MAX_STEP if max_step is None else max_step

    n = int(math.floor((hi_v - lo_v) / step_v)) + 1
    grid = lo_v + step_v * np.arange(n, dtype=float)
    grid = grid[grid <= hi_v + step_v * 0.5]

    if rho_prev is not None:
        grid = grid[np.abs(grid - rho_prev) <= max_step_v + 1e-12]

    return grid


def refine_rho_grid(
    rho_center: float,
    step: float,
    refine_factor: int,
) -> np.ndarray:
    """Subdivide around best rho candidate."""
    half_span = step / 2.0
    lo = rho_center - half_span
    hi = rho_center + half_span
    return np.linspace(lo, hi, refine_factor, dtype=float)


def _slice_arrays(df_slice: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    k = df_slice["log_moneyness"].to_numpy(dtype=float)
    w = (df_slice["implied_vol"].to_numpy(dtype=float) ** 2) * df_slice[
        "business_t"
    ].to_numpy(dtype=float)
    if "vega" in df_slice.columns:
        vega = df_slice["vega"].to_numpy(dtype=float)
    else:
        vega = np.full_like(k, 0.3, dtype=float)
    return k, w, vega


def _spatial_penalty(
    rho: float,
    theta: float,
    phi: float,
    prev_slice_params: dict[str, float] | None,
) -> float:
    if prev_slice_params is None:
        return 0.0
    rho_prev = float(prev_slice_params["rho"])
    psi_prev = float(prev_slice_params["theta"]) * float(prev_slice_params["phi"])
    psi = theta * phi
    return spatial_reg_penalty(
        np.array([rho_prev, rho], dtype=float),
        np.array([psi_prev, psi], dtype=float),
    )


def _theta_max_at_phi(phi: float, corridor: dict[str, Any]) -> float:
    psi_max = corridor.get("psi_max")
    if psi_max is None or phi <= 0.0:
        return float("inf")
    return float(psi_max) / phi


def _evaluate_at_phi(
    phi: float,
    rho: float,
    df_slice: pd.DataFrame,
    corridor: dict[str, Any],
    prev_slice_params: dict[str, float] | None,
    k: np.ndarray,
    w: np.ndarray,
    vega: np.ndarray,
) -> tuple[float, float, dict[str, Any]]:
    if phi < corridor["phi_min"] or phi > corridor["phi_max"]:
        return float("inf"), float("nan"), {}

    anchor = extract_anchor_params(df_slice, phi, rho)
    theta = float(anchor["theta_star"])
    theta_min = corridor["theta_min_phi"](phi)
    if theta < theta_min:
        theta = theta_min

    theta_max = _theta_max_at_phi(phi, corridor)
    if math.isfinite(theta_max):
        theta = min(theta, theta_max)

    if theta <= 0.0 or not math.isfinite(theta):
        return float("inf"), float("nan"), anchor

    obj = objective_slice((theta, phi, rho), k, w, vega)
    obj += _spatial_penalty(rho, theta, phi, prev_slice_params)
    return obj, theta, anchor


def _phi_scan_points(corridor: dict[str, Any], n_points: int = 3) -> np.ndarray:
    phi_min = corridor["phi_min"]
    phi_max = corridor["phi_max"]
    if n_points <= 1 or phi_max <= phi_min:
        return np.array([phi_min], dtype=float)
    return np.linspace(phi_min, phi_max, n_points, dtype=float)


def _score_rho_candidate(
    rho: float,
    df_slice: pd.DataFrame,
    prev_slice_params: dict[str, float] | None,
    k: np.ndarray,
    w: np.ndarray,
    vega: np.ndarray,
    *,
    use_brent: bool = False,
    n_phi_scan: int = 3,
) -> tuple[float, float, float, float, dict[str, Any], dict[str, Any], int]:
    corridor = build_corridor(rho, prev_slice_params, df_slice)
    if not corridor["valid"]:
        return float("inf"), rho, float("nan"), float("nan"), corridor, {}, 0

    if use_brent:
        score, theta, phi, anchor, n_eval = _brent_phi_solve(
            rho,
            df_slice,
            corridor,
            prev_slice_params,
            k,
            w,
            vega,
        )
        return score, rho, theta, phi, corridor, anchor, n_eval

    best_score = float("inf")
    best_theta = float("nan")
    best_phi = float("nan")
    best_anchor: dict[str, Any] = {}
    n_eval = 0

    for phi in _phi_scan_points(corridor, n_phi_scan):
        score, theta, anchor = _evaluate_at_phi(
            float(phi),
            rho,
            df_slice,
            corridor,
            prev_slice_params,
            k,
            w,
            vega,
        )
        n_eval += 1
        if score < best_score:
            best_score = score
            best_theta = theta
            best_phi = float(phi)
            best_anchor = anchor

    return best_score, rho, best_theta, best_phi, corridor, best_anchor, n_eval


def _brent_phi_solve(
    rho: float,
    df_slice: pd.DataFrame,
    corridor: dict[str, Any],
    prev_slice_params: dict[str, float] | None,
    k: np.ndarray,
    w: np.ndarray,
    vega: np.ndarray,
) -> tuple[float, float, float, dict[str, Any], int]:
    phi_min = corridor["phi_min"]
    phi_max = corridor["phi_max"]
    n_eval = 0

    def objective_phi(phi: float) -> float:
        nonlocal n_eval
        n_eval += 1
        score, _, _ = _evaluate_at_phi(
            phi,
            rho,
            df_slice,
            corridor,
            prev_slice_params,
            k,
            w,
            vega,
        )
        return score

    result = minimize_scalar(
        objective_phi,
        bounds=(phi_min, phi_max),
        method="bounded",
        options={"xatol": cfg.BRENT_XTOL, "maxiter": cfg.BRENT_MAX_ITER},
    )

    phi_opt = float(result.x)
    score, theta, anchor = _evaluate_at_phi(
        phi_opt,
        rho,
        df_slice,
        corridor,
        prev_slice_params,
        k,
        w,
        vega,
    )
    return score, theta, phi_opt, anchor, n_eval


def clamp_params(
    rho: float,
    theta: float,
    phi: float,
    corridor: dict[str, Any],
    prev_slice_params: dict[str, float] | None,
) -> tuple[float, float, float]:
    """Clamp all params to corridor + rho bounds + calendar constraints."""
    rho_out = max(cfg.RHO_GRID_LO, min(cfg.RHO_GRID_HI, rho))

    if not corridor.get("valid", False):
        return rho_out, theta, phi

    phi_out = max(corridor["phi_min"], min(corridor["phi_max"], phi))
    theta_min = corridor["theta_min_phi"](phi_out)
    theta_max = _theta_max_at_phi(phi_out, corridor)
    if math.isfinite(theta_max):
        theta_out = max(theta_min, min(theta_max, theta))
    else:
        theta_out = max(theta_min, theta)

    if prev_slice_params is not None:
        rho_prev = float(prev_slice_params["rho"])
        if abs(rho_out - rho_prev) <= 1e-12:
            theta_prev = float(prev_slice_params["theta"])
            phi_prev = float(prev_slice_params["phi"])
            phi_calendar = phi_prev * theta_prev / max(theta_out, cfg.THETA_PROJECTION_EPS)
            phi_out = max(corridor["phi_min"], min(corridor["phi_max"], phi_calendar))

    return rho_out, theta_out, phi_out


def kill_switch(params_dict: dict[str, Any]) -> tuple[bool, list[tuple[str, str]]]:
    """
    Run all 4 no-arb checks with per-type tolerances.

    Returns (is_valid, list_of_violations) where each violation is (type, message).
    """
    theta = float(params_dict["theta"])
    phi = float(params_dict["phi"])
    rho = float(params_dict["rho"])
    violations: list[tuple[str, str]] = []

    ok, msg = check_butterfly(theta, phi, rho)
    if not ok:
        violations.append(("BUTTERFLY", msg))

    prev = params_dict.get("prev_slice_params")
    if prev is not None:
        current = {"theta": theta, "phi": phi, "rho": rho}
        ok, msg = check_calendar_pasquazzi(prev, current)
        if not ok:
            violations.append(("CALENDAR", msg))

    df_slice = params_dict.get("df_slice")
    if df_slice is not None:
        ok, msg = check_vertical_spread(
            {"theta": theta, "phi": phi, "rho": rho},
            df_slice,
            tolerance=cfg.KILL_TOL_ROPER,
        )
        if not ok:
            violations.append(("ROPER", msg))

    ok, msg = check_lee_bound(theta, phi, rho)
    if not ok:
        violations.append(("LEE", msg))

    return len(violations) == 0, violations


def solve_single_slice(
    df_slice: pd.DataFrame,
    prev_slice_params: dict | None,
    rho_grid: np.ndarray | None = None,
) -> dict[str, Any]:
    """
    Calibrate one expiry slice via two-stage rho search and Brent on phi.

    Returns rho, theta, phi, objective_value, corridor, is_valid, violations,
    n_iterations, anchor_k_star, anchor_theta_star.
    """
    empty_result: dict[str, Any] = {
        "rho": float("nan"),
        "theta": float("nan"),
        "phi": float("nan"),
        "objective_value": float("inf"),
        "corridor": build_corridor(0.0, prev_slice_params, df_slice),
        "is_valid": False,
        "violations": [("SOLVER", "no feasible rho in grid")],
        "n_iterations": 0,
        "anchor_k_star": float("nan"),
        "anchor_theta_star": float("nan"),
    }

    if df_slice.empty:
        empty_result["violations"] = [("SOLVER", "empty df_slice")]
        return empty_result

    rho_prev = None if prev_slice_params is None else float(prev_slice_params["rho"])
    if rho_grid is None:
        rho_grid = build_rho_grid(rho_prev)

    if rho_grid.size == 0:
        return empty_result

    k, w, vega = _slice_arrays(df_slice)
    n_iterations = 0
    candidates: list[tuple[float, float, float, float, dict[str, Any], dict[str, Any]]] = []

    for rho in rho_grid:
        if rho_prev is not None and abs(float(rho) - rho_prev) > cfg.RHO_MAX_STEP + 1e-12:
            continue

        score, rho_val, theta, phi, corridor, anchor, n_eval = _score_rho_candidate(
            float(rho),
            df_slice,
            prev_slice_params,
            k,
            w,
            vega,
            use_brent=True,
        )
        n_iterations += n_eval
        if math.isfinite(score):
            candidates.append((score, rho_val, theta, phi, corridor, anchor))

    candidates.sort(key=lambda item: item[0])
    top_candidates = candidates[:_TOP_RHO_CANDIDATES]

    refined: list[tuple[float, float, float, float, dict[str, Any], dict[str, Any]]] = []
    for score, rho_val, theta, phi, corridor, anchor in top_candidates:
        refined.append((score, rho_val, theta, phi, corridor, anchor))
        for rho_ref in refine_rho_grid(
            rho_val, cfg.RHO_GRID_STEP, cfg.RHO_GRID_REFINE_FACTOR
        ):
            if rho_prev is not None and abs(rho_ref - rho_prev) > cfg.RHO_MAX_STEP + 1e-12:
                continue
            r_score, r_rho, r_theta, r_phi, r_corridor, r_anchor, n_eval = (
                _score_rho_candidate(
                    float(rho_ref),
                    df_slice,
                    prev_slice_params,
                    k,
                    w,
                    vega,
                    use_brent=True,
                )
            )
            n_iterations += n_eval
            if math.isfinite(r_score):
                refined.append((r_score, r_rho, r_theta, r_phi, r_corridor, r_anchor))

    if not refined:
        empty_result["violations"] = [("SOLVER", "empty corridor for all rho candidates")]
        return empty_result

    refined.sort(key=lambda item: item[0])

    best_score = float("inf")
    best_rho = float("nan")
    best_theta = float("nan")
    best_phi = float("nan")
    best_corridor: dict[str, Any] = {}
    best_anchor: dict[str, Any] = {}

    for score, rho_val, theta, phi, corridor, anchor in refined:
        if not corridor.get("valid", False) or not math.isfinite(score):
            continue
        if score < best_score:
            best_score = score
            best_rho = rho_val
            best_theta = theta
            best_phi = phi
            best_corridor = corridor
            best_anchor = anchor

    best_rho, best_theta, best_phi = clamp_params(
        best_rho,
        best_theta,
        best_phi,
        best_corridor,
        prev_slice_params,
    )

    params_for_kill = {
        "theta": best_theta,
        "phi": best_phi,
        "rho": best_rho,
        "prev_slice_params": prev_slice_params,
        "df_slice": df_slice,
    }
    is_valid, violations = kill_switch(params_for_kill)

    anchor_k_star = float(best_anchor.get("k_star", float("nan")))
    anchor_theta_star = float(best_anchor.get("w_star", float("nan")))

    return {
        "rho": best_rho,
        "theta": best_theta,
        "phi": best_phi,
        "objective_value": best_score,
        "corridor": best_corridor,
        "is_valid": is_valid,
        "violations": violations,
        "n_iterations": n_iterations,
        "anchor_k_star": anchor_k_star,
        "anchor_theta_star": anchor_theta_star,
    }
