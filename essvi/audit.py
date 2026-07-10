"""Post-calibration audit on a dense k-grid with kill-switch reporting (plan §12)."""

from __future__ import annotations

from typing import Any

import numpy as np

from essvi import config as cfg
from essvi.objective import w_slice, w_slice_derivatives


def build_audit_grid(
    n_points: int = cfg.AUDIT_GRID_POINTS,
    k_max: float = cfg.K_AUDIT,
) -> np.ndarray:
    """linspace(-k_max, +k_max, n_points)."""
    return np.linspace(-float(k_max), float(k_max), int(n_points))


def compute_durrleman_g(
    k: np.ndarray,
    w: np.ndarray,
    wp: np.ndarray,
    wpp: np.ndarray,
) -> np.ndarray:
    """
    Full Durrleman g-function:
    g = (1 − k*w'/(2w))² − (w')²·(w⁻¹ + 1/4)/4 + w''/2
    """
    k_arr = np.asarray(k, dtype=float)
    w_arr = np.asarray(w, dtype=float)
    wp_arr = np.asarray(wp, dtype=float)
    wpp_arr = np.asarray(wpp, dtype=float)

    with np.errstate(divide="ignore", invalid="ignore"):
        term1 = 1.0 - k_arr * wp_arr / (2.0 * w_arr)
        term2 = (wp_arr**2) * (1.0 / w_arr + 0.25) / 4.0
        g = term1**2 - term2 + wpp_arr / 2.0
    return g


def _slice_T(entry: dict[str, Any]) -> float:
    if "T" in entry:
        return float(entry["T"])
    if "business_t" in entry:
        return float(entry["business_t"])
    if "dte" in entry:
        return float(entry["dte"]) / 252.0
    raise KeyError("slice entry requires 'T', 'business_t', or 'dte'")


def _sorted_slices(slice_params_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(slice_params_list, key=_slice_T)


def _slice_params_at_k(
    k_grid: np.ndarray,
    theta: float,
    phi: float,
    rho: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return w_slice_derivatives(k_grid, theta, phi, rho)


def audit_butterfly(
    slice_params_list: list[dict[str, Any]],
    k_grid: np.ndarray,
) -> list[dict[str, Any]]:
    """For each slice, evaluate g(k) on grid, flag negatives beyond tolerance."""
    violations: list[dict[str, Any]] = []
    tol = cfg.KILL_TOL_BUTTERFLY

    for sl in _sorted_slices(slice_params_list):
        t_val = _slice_T(sl)
        theta = float(sl["theta"])
        phi = float(sl["phi"])
        rho = float(sl["rho"])
        w, wp, wpp = _slice_params_at_k(k_grid, theta, phi, rho)
        g = compute_durrleman_g(k_grid, w, wp, wpp)

        bad = g < -tol
        if not np.any(bad):
            continue

        idx = int(np.argmin(g))
        violations.append(
            {
                "T": t_val,
                "k": float(k_grid[idx]),
                "g": float(g[idx]),
                "severity": float(-g[idx]),
                "n_points": int(np.sum(bad)),
            }
        )

    return violations


def audit_calendar(
    slice_params_list: list[dict[str, Any]],
    k_grid: np.ndarray,
) -> list[dict[str, Any]]:
    """For each adjacent pair, check w₁(k) ≤ w₂(k) on the audit grid."""
    violations: list[dict[str, Any]] = []
    tol = cfg.KILL_TOL_CALENDAR
    ordered = _sorted_slices(slice_params_list)

    for i in range(len(ordered) - 1):
        sl1, sl2 = ordered[i], ordered[i + 1]
        t1, t2 = _slice_T(sl1), _slice_T(sl2)
        w1 = w_slice(k_grid, float(sl1["theta"]), float(sl1["phi"]), float(sl1["rho"]))
        w2 = w_slice(k_grid, float(sl2["theta"]), float(sl2["phi"]), float(sl2["rho"]))
        excess = w1 - w2
        bad = excess > tol
        if not np.any(bad):
            continue

        idx = int(np.argmax(excess))
        violations.append(
            {
                "T_near": t1,
                "T_far": t2,
                "k": float(k_grid[idx]),
                "w_near": float(w1[idx]),
                "w_far": float(w2[idx]),
                "severity": float(excess[idx]),
                "n_points": int(np.sum(bad)),
            }
        )

    return violations


def audit_vertical_spread(
    slice_params_list: list[dict[str, Any]],
    k_grid: np.ndarray,
) -> list[dict[str, Any]]:
    """Check |w'(k)|·T ≤ 2 (+ tolerance) for every (k, T) on the grid."""
    violations: list[dict[str, Any]] = []
    tol = cfg.KILL_TOL_ROPER

    for sl in _sorted_slices(slice_params_list):
        t_val = _slice_T(sl)
        if t_val <= 0.0:
            continue
        theta = float(sl["theta"])
        phi = float(sl["phi"])
        rho = float(sl["rho"])
        _, wp, _ = _slice_params_at_k(k_grid, theta, phi, rho)
        bound = 2.0 / t_val
        abs_wp = np.abs(wp)
        excess = abs_wp - bound
        bad = excess > tol
        if not np.any(bad):
            continue

        idx = int(np.argmax(excess))
        violations.append(
            {
                "T": t_val,
                "k": float(k_grid[idx]),
                "w_prime": float(wp[idx]),
                "bound": bound,
                "severity": float(excess[idx]),
                "n_points": int(np.sum(bad)),
            }
        )

    return violations


def audit_lee_bound(slice_params_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check asymptotic wing slope at k = ±K_AUDIT."""
    violations: list[dict[str, Any]] = []
    cap = cfg.TAIL_SLOPE_CAP + cfg.KILL_TOL_LEE
    k_min = -cfg.K_AUDIT
    k_max = cfg.K_AUDIT

    for sl in _sorted_slices(slice_params_list):
        t_val = _slice_T(sl)
        theta = float(sl["theta"])
        phi = float(sl["phi"])
        rho = float(sl["rho"])

        w_neg = float(w_slice(k_min, theta, phi, rho))
        w_pos = float(w_slice(k_max, theta, phi, rho))
        slope_neg = w_neg / abs(k_min)
        slope_pos = w_pos / abs(k_max)
        slope = max(slope_neg, slope_pos)

        if slope > cap:
            violations.append(
                {
                    "T": t_val,
                    "slope": slope,
                    "cap": cap,
                    "slope_neg": slope_neg,
                    "slope_pos": slope_pos,
                    "severity": float(slope - cap),
                }
            )

    return violations


def audit_monotonicity(slice_params_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check θ_{i+1} ≥ θ_i for adjacent calibrated expiries."""
    violations: list[dict[str, Any]] = []
    tol = cfg.THETA_MONOTONICITY_EPS
    ordered = _sorted_slices(slice_params_list)

    for i in range(len(ordered) - 1):
        sl1, sl2 = ordered[i], ordered[i + 1]
        t1, t2 = _slice_T(sl1), _slice_T(sl2)
        theta1 = float(sl1["theta"])
        theta2 = float(sl2["theta"])
        if theta2 + tol >= theta1:
            continue
        violations.append(
            {
                "T_near": t1,
                "T_far": t2,
                "theta_near": theta1,
                "theta_far": theta2,
                "severity": float(theta1 - theta2),
            }
        )

    return violations


def audit_result_to_kill_switch(report: dict[str, Any]) -> dict[str, Any]:
    """
    Convert audit report to runtime decision.

    Returns dict with surface_usable, per-type violation tuples, totals, and kill flag.
    """
    butterfly = report.get("butterfly", [])
    calendar = report.get("calendar", [])
    slope = report.get("vertical_spread", report.get("slope", []))
    lee = report.get("lee", [])
    mono = report.get("monotonicity", [])

    butterfly_violations = [(v["T"], v["k"], v["severity"]) for v in butterfly]
    calendar_violations = [
        (v["T_near"], v["T_far"], v["k"], v["severity"]) for v in calendar
    ]
    slope_violations = [(v["T"], v["k"], v["severity"]) for v in slope]
    lee_violations = [(v["T"], v["slope"], v["cap"]) for v in lee]
    monotonicity_violations = [
        (v["T_near"], v["T_far"], (v["theta_near"], v["theta_far"])) for v in mono
    ]

    severities = [v["severity"] for v in butterfly + calendar + slope + lee + mono]
    total_violations = (
        len(butterfly_violations)
        + len(calendar_violations)
        + len(slope_violations)
        + len(lee_violations)
        + len(monotonicity_violations)
    )
    worst_severity = max(severities) if severities else 0.0
    kill_triggered = total_violations > 0

    return {
        "surface_usable": not kill_triggered,
        "butterfly_violations": butterfly_violations,
        "calendar_violations": calendar_violations,
        "slope_violations": slope_violations,
        "lee_violations": lee_violations,
        "monotonicity_violations": monotonicity_violations,
        "total_violations": total_violations,
        "worst_severity": float(worst_severity),
        "kill_triggered": kill_triggered,
    }


def run_full_audit(minute_result: dict[str, Any]) -> dict[str, Any]:
    """
    MAIN FUNCTION. Runs all 5 audits and produces a structured report.

    Input minute_result from sequential.calibrate_one_minute.
    """
    slice_params = list(minute_result.get("slices", []))
    k_grid = build_audit_grid()

    butterfly = audit_butterfly(slice_params, k_grid)
    calendar = audit_calendar(slice_params, k_grid)
    vertical_spread = audit_vertical_spread(slice_params, k_grid)
    lee = audit_lee_bound(slice_params)
    monotonicity = audit_monotonicity(slice_params)

    report: dict[str, Any] = {
        "timestamp": minute_result.get("timestamp"),
        "n_slices": len(slice_params),
        "k_grid": k_grid,
        "butterfly": butterfly,
        "calendar": calendar,
        "vertical_spread": vertical_spread,
        "lee": lee,
        "monotonicity": monotonicity,
    }
    report["kill_switch"] = audit_result_to_kill_switch(report)
    return report


def is_surface_safe(audit_report: dict[str, Any]) -> bool:
    """Convenience: True iff kill_triggered is False."""
    kill = audit_report.get("kill_switch")
    if kill is not None:
        return not bool(kill.get("kill_triggered", True))
    return not bool(audit_result_to_kill_switch(audit_report).get("kill_triggered", True))
