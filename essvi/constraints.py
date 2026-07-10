"""No-arbitrage constraints and corridor construction for eSSVI slices."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from essvi import config as cfg

_RHO_BOUND_TOL = 1e-12


def _psi(theta: float, phi: float) -> float:
    return theta * phi


def _abs_rho(rho: float) -> float:
    return abs(rho)


def theta_from_psi(
    psi: float,
    rho: float,
    k_star: float,
    theta_star: float,
) -> float:
    """Exact anchor relation θ_t(ψ) from plan §8.1 (Corbetta Eq 3.12)."""
    return (
        theta_star
        - rho * psi * k_star
        - psi * psi * k_star * k_star * (1.0 - rho * rho) / (4.0 * theta_star)
    )


def solve_anchor_theta(
    phi: float,
    rho: float,
    k_star: float,
    theta_star: float,
) -> float:
    """Solve θ from anchor constraint with ψ = θφ (quadratic in θ)."""
    if phi <= 0.0:
        return float("nan")

    coeff_a = phi * phi * k_star * k_star * (1.0 - rho * rho) / (4.0 * theta_star)
    coeff_b = -(1.0 + rho * phi * k_star)
    coeff_c = theta_star

    if abs(coeff_a) < 1e-15:
        if abs(coeff_b) < 1e-15:
            return theta_star
        return -coeff_c / coeff_b

    disc = coeff_b * coeff_b - 4.0 * coeff_a * coeff_c
    if disc < 0.0:
        return float("nan")

    sqrt_disc = math.sqrt(disc)
    root_lo = (-coeff_b - sqrt_disc) / (2.0 * coeff_a)
    root_hi = (-coeff_b + sqrt_disc) / (2.0 * coeff_a)
    positive = [r for r in (root_lo, root_hi) if r > 0.0]
    if not positive:
        return float("nan")
    return min(positive, key=lambda r: abs(r - theta_star))


def _mm_l2(abs_rho: float) -> float:
    if abs_rho >= 1.0:
        return 0.0
    return 1.0 / math.tan(math.acos(-abs_rho) / 3.0)


def _mm_objective_grid(
    l: np.ndarray,
    theta: float,
    abs_rho: float,
) -> np.ndarray:
    """Vectorized ℱ_MM integrand over l-grid."""
    if abs_rho >= 1.0:
        return np.full_like(l, np.inf, dtype=float)

    sqrt_1mr2 = math.sqrt(1.0 - abs_rho * abs_rho)
    n_val = sqrt_1mr2 + abs_rho * l + np.sqrt(l * l + 1.0)
    n_prime = abs_rho + l / np.sqrt(l * l + 1.0)
    n_double_prime = 1.0 / (l * l + 1.0) ** 1.5

    g_val = n_prime / 4.0
    h_val = 1.0 - (l - abs_rho / sqrt_1mr2) * n_prime / (2.0 * n_val)
    g2_val = n_double_prime - n_prime * n_prime / (2.0 * n_val)

    denom = theta * sqrt_1mr2 * g_val * g_val - g2_val
    with np.errstate(divide="ignore", invalid="ignore"):
        numer = 4.0 * theta * sqrt_1mr2 * h_val * h_val
        out = np.where(denom > 0.0, numer / denom, np.inf)
    return out


def compute_f_MM(
    theta: float,
    abs_rho: float,
    n_grid: int | None = None,
) -> float:
    """
    Compute ℱ_MM(θ, |ρ|) = inf_{l > l₂(|ρ|)} f(θ, |ρ|, l) via dense l-grid scan.
    """
    if theta <= 0.0:
        return 0.0

    if abs_rho >= 1.0:
        return 4.0 * theta / (1.0 + abs_rho)

    grid_points = n_grid if n_grid is not None else cfg.MM_L_GRID_POINTS
    l2 = _mm_l2(abs_rho)
    l_start = l2 + max(cfg.MM_L2_TOL, 1e-8)
    l_end = cfg.MM_L_MAX
    if l_start >= l_end:
        return 4.0 * theta / (1.0 + abs_rho)

    l_grid = np.linspace(l_start, l_end, grid_points)
    values = _mm_objective_grid(l_grid, theta, abs_rho)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 4.0 * theta / (1.0 + abs_rho)
    return float(np.min(finite))


def check_butterfly_gj(theta: float, phi: float, rho: float) -> tuple[bool, str]:
    """Gatheral-Jacquier sufficient butterfly conditions B1 and B2."""
    if theta <= 0.0 or phi <= 0.0:
        return False, "non-positive theta or phi"

    psi = _psi(theta, phi)
    abs_r = _abs_rho(rho)
    one_plus = 1.0 + abs_r
    tol = cfg.KILL_TOL_BUTTERFLY

    if psi * one_plus > cfg.U_BF1_FACTOR + tol:
        return False, f"B1 violated: psi*(1+|rho|)={psi * one_plus:.6g} > {cfg.U_BF1_FACTOR}"

    # B2: psi^2 * (1+|rho|) / theta <= 4  (plan §7.1)
    b2_lhs = psi * psi * one_plus / theta
    if b2_lhs > cfg.U_BF1_FACTOR + tol:
        return False, f"B2 violated: psi^2*(1+|rho|)/theta={b2_lhs:.6g} > {cfg.U_BF1_FACTOR}"

    return True, ""


def check_butterfly_mm(
    theta: float,
    phi: float,
    rho: float,
    n_grid: int | None = None,
) -> tuple[bool, str]:
    """Martini-Mingone exact butterfly conditions MM-1 and MM-2."""
    if theta <= 0.0 or phi <= 0.0:
        return False, "non-positive theta or phi"

    psi = _psi(theta, phi)
    abs_r = _abs_rho(rho)
    one_plus = 1.0 + abs_r
    tol = cfg.KILL_TOL_BUTTERFLY

    mm1_bound = cfg.U_BF1_FACTOR / one_plus
    if psi > mm1_bound + tol:
        return False, f"MM-1 violated: psi={psi:.6g} > {mm1_bound:.6g}"

    f_mm = compute_f_MM(theta, abs_r, n_grid=n_grid)
    psi_sq = psi * psi
    if psi_sq > f_mm + tol:
        return False, f"MM-2 violated: psi^2={psi_sq:.6g} > F_MM={f_mm:.6g}"

    return True, ""


def check_butterfly(theta: float, phi: float, rho: float) -> tuple[bool, str]:
    """Dispatch butterfly check per cfg.BUTTERFLY_BOUND_MODE."""
    mode = cfg.BUTTERFLY_BOUND_MODE
    if mode == "gj_conservative":
        return check_butterfly_gj(theta, phi, rho)
    if mode == "mm_exact":
        return check_butterfly_mm(theta, phi, rho)
    if mode == "both":
        gj_ok, gj_msg = check_butterfly_gj(theta, phi, rho)
        mm_ok, mm_msg = check_butterfly_mm(theta, phi, rho)
        if gj_ok and mm_ok:
            return True, ""
        reasons = [m for m in (gj_msg, mm_msg) if m]
        return False, "; ".join(reasons)

    raise AssertionError(f"Unhandled BUTTERFLY_BOUND_MODE: {mode}")


def check_calendar_pasquazzi(
    params1: dict[str, float],
    params2: dict[str, float],
) -> tuple[bool, str]:
    """
    Pasquazzi 2023 Proposition 13 calendar condition.

    params1 = nearer maturity (T1), params2 = farther maturity (T2).
    """
    theta1 = float(params1["theta"])
    phi1 = float(params1["phi"])
    rho1 = float(params1["rho"])
    theta2 = float(params2["theta"])
    phi2 = float(params2["phi"])
    rho2 = float(params2["rho"])

    if theta1 <= 0.0 or theta2 <= 0.0 or phi1 <= 0.0 or phi2 <= 0.0:
        return False, "non-positive slice parameters"

    theta_ratio = theta2 / theta1
    phi_ratio = phi2 / phi1
    tol = cfg.KILL_TOL_CALENDAR

    if theta_ratio < 1.0 - tol:
        return False, f"Theta ratio violated: Theta={theta_ratio:.6g} < 1"

    theta_phi = theta_ratio * phi_ratio
    delta_rho = abs(rho2 - rho1)
    stripe = theta_phi * delta_rho
    abs_one_minus = abs(1.0 - theta_phi)
    upper = theta_phi - 1.0

    if stripe < abs_one_minus - tol:
        return False, (
            f"calendar stripe lower violated: Theta*Phi*|drho|={stripe:.6g} "
            f"< |1-Theta*Phi|={abs_one_minus:.6g}"
        )
    if stripe > upper + tol:
        return False, (
            f"calendar stripe upper violated: Theta*Phi*|drho|={stripe:.6g} "
            f"> Theta*Phi-1={upper:.6g}"
        )

    return True, ""


def w_prime(k: np.ndarray, theta: float, phi: float, rho: float) -> np.ndarray:
    """Closed-form w'(k) for eSSVI (plan §0)."""
    u = phi * k + rho
    d_val = u * u + (1.0 - rho * rho)
    return (theta * phi / 2.0) * (rho + u / np.sqrt(d_val))


def check_vertical_spread(
    slice_params: dict[str, float],
    df_slice: pd.DataFrame,
    tolerance: float | None = None,
) -> tuple[bool, str]:
    """Post-fit Roper audit: |w'(k)| * T_t <= 2 for all k in the slice."""
    tol = cfg.KILL_TOL_ROPER if tolerance is None else tolerance
    theta = float(slice_params["theta"])
    phi = float(slice_params["phi"])
    rho = float(slice_params["rho"])

    if "log_moneyness" not in df_slice.columns:
        return False, "missing log_moneyness column"
    if "business_t" not in df_slice.columns:
        return False, "missing business_t column"

    k = df_slice["log_moneyness"].to_numpy(dtype=float)
    t_val = float(df_slice["business_t"].iloc[0])
    if t_val <= 0.0:
        return False, "non-positive business_t"

    wp = w_prime(k, theta, phi, rho)
    bound = 2.0 / t_val
    max_abs = float(np.max(np.abs(wp)))
    if max_abs > bound + tol:
        return False, f"vertical spread violated: max|w'|={max_abs:.6g} > {bound:.6g}"

    return True, ""


def check_lee_bound(theta: float, phi: float, rho: float) -> tuple[bool, str]:
    """Roger Lee wing bound: psi*(1+|rho|) <= 4*(1 - KILL_TOL_LEE)."""
    if theta <= 0.0 or phi <= 0.0:
        return False, "non-positive theta or phi"

    psi = _psi(theta, phi)
    lhs = psi * (1.0 + _abs_rho(rho))
    rhs = cfg.U_BF1_FACTOR * (1.0 - cfg.KILL_TOL_LEE)
    if lhs > rhs:
        return False, f"Lee bound violated: psi*(1+|rho|)={lhs:.6g} > {rhs:.6g}"
    return True, ""


def _butterfly_upper_psi(theta: float, abs_rho: float) -> float:
    """Upper ψ from butterfly bounds (plan §8.3)."""
    one_plus = 1.0 + abs_rho
    u_bf1 = cfg.U_BF1_FACTOR / one_plus

    mode = cfg.BUTTERFLY_BOUND_MODE
    if mode == "gj_conservative":
        u_bf2 = cfg.U_BF2_FACTOR * math.sqrt(theta / one_plus)
        return min(u_bf1, u_bf2)
    if mode == "mm_exact":
        u_bf_mm = math.sqrt(compute_f_MM(theta, abs_rho))
        return min(u_bf1, u_bf_mm)
    if mode == "both":
        u_bf2 = cfg.U_BF2_FACTOR * math.sqrt(theta / one_plus)
        u_bf_mm = math.sqrt(compute_f_MM(theta, abs_rho))
        return min(u_bf1, u_bf2, u_bf_mm)

    raise AssertionError(f"Unhandled BUTTERFLY_BOUND_MODE: {mode}")


def _upper_psi_of_psi(
    psi: float,
    rho: float,
    k_star: float,
    theta_star: float,
    prev_slice: dict[str, float] | None,
) -> float:
    """U_ψ(ψ) from plan §8.3."""
    theta = theta_from_psi(psi, rho, k_star, theta_star)
    if theta <= 0.0:
        return -1.0

    abs_r = _abs_rho(rho)
    u_butterfly = _butterfly_upper_psi(theta, abs_r)

    if prev_slice is not None:
        theta_prev = float(prev_slice["theta"])
        psi_prev = float(prev_slice["theta"]) * float(prev_slice["phi"])
        u_calendar = psi_prev * theta / theta_prev
        u_val = min(u_butterfly, u_calendar) - cfg.CORRIDOR_EPS
    else:
        u_val = u_butterfly - cfg.CORRIDOR_EPS

    return u_val


def _compute_L_psi(
    rho: float,
    prev_slice: dict[str, float],
    k_star: float,
    theta_star: float,
) -> float:
    """Lower ψ bound from calendar + θ-monotonicity (plan §8.4)."""
    theta_prev = float(prev_slice["theta"])
    rho_prev = float(prev_slice["rho"])
    psi_prev = float(prev_slice["theta"]) * float(prev_slice["phi"])

    bound1 = (
        psi_prev * (1.0 - rho_prev) / (1.0 - rho)
        if rho < 1.0 - _RHO_BOUND_TOL
        else float("inf")
    )
    bound2 = (
        psi_prev * (1.0 + rho_prev) / (1.0 + rho)
        if rho > -1.0 + _RHO_BOUND_TOL
        else float("inf")
    )
    l_cal_skew = max(bound1, bound2, psi_prev)

    coeff_a = k_star * k_star * (1.0 - rho * rho) / (4.0 * theta_star)
    coeff_b = rho * k_star
    coeff_c = theta_prev - theta_star

    if abs(coeff_a) < 1e-15:
        if theta_star + 1e-15 < theta_prev:
            return float("inf")
        l_theta_mono = cfg.CORRIDOR_EPS
    else:
        disc = coeff_b * coeff_b - 4.0 * coeff_a * coeff_c
        if disc < 0.0:
            return float("inf")
        root2 = (coeff_b + math.sqrt(disc)) / (2.0 * coeff_a)
        l_theta_mono = max(root2, cfg.CORRIDOR_EPS)

    return max(l_cal_skew, l_theta_mono, cfg.CORRIDOR_EPS)


def _find_feasible_psi_intervals(
    rho: float,
    prev_slice: dict[str, float] | None,
    k_star: float,
    theta_star: float,
    l_psi: float,
) -> list[tuple[float, float]]:
    """Find intervals where U_ψ(ψ) >= L_ψ (plan §8.4)."""
    if l_psi >= cfg.U_PSI_MAX:
        return []

    psi_start = max(l_psi, cfg.CORRIDOR_EPS)
    psi_grid = np.logspace(
        math.log10(psi_start),
        math.log10(cfg.U_PSI_MAX),
        cfg.U_PSI_GRID_POINTS,
    )

    intervals: list[tuple[float, float]] = []
    in_feasible = False
    interval_start: float | None = None

    for psi in psi_grid:
        upper = _upper_psi_of_psi(psi, rho, k_star, theta_star, prev_slice)
        feasible = upper > 0.0 and l_psi <= psi <= upper
        if feasible and not in_feasible:
            in_feasible = True
            interval_start = float(psi)
        elif not feasible and in_feasible:
            in_feasible = False
            if interval_start is not None:
                intervals.append((interval_start, float(psi)))
            interval_start = None

    if in_feasible and interval_start is not None:
        intervals.append((interval_start, float(psi_grid[-1])))

    return intervals


def _theta_lower_bounds_at_phi(
    phi: float,
    rho: float,
    prev_slice: dict[str, float] | None,
    k_star: float,
    theta_star: float,
) -> float:
    """Maximum of algebraic lower bounds on θ at fixed φ."""
    if phi <= 0.0:
        return float("inf")

    lower = cfg.CORRIDOR_EPS / phi

    if prev_slice is not None:
        theta_prev = float(prev_slice["theta"])
        psi_prev = float(prev_slice["theta"]) * float(prev_slice["phi"])
        rho_prev = float(prev_slice["rho"])

        lower = max(lower, theta_prev)

        skew1 = psi_prev * (1.0 - rho_prev) / ((1.0 - rho) * phi)
        skew2 = psi_prev * (1.0 + rho_prev) / ((1.0 + rho) * phi)
        lower = max(lower, skew1, skew2, psi_prev / phi)

        coeff_a = k_star * k_star * (1.0 - rho * rho) / (4.0 * theta_star)
        coeff_b = rho * k_star
        coeff_c = theta_prev - theta_star
        if abs(coeff_a) < 1e-15:
            if theta_star + 1e-15 < theta_prev:
                return float("inf")
        else:
            disc = coeff_b * coeff_b - 4.0 * coeff_a * coeff_c
            if disc >= 0.0:
                root2 = (coeff_b + math.sqrt(disc)) / (2.0 * coeff_a)
                psi_mono = max(root2, cfg.CORRIDOR_EPS)
                lower = max(lower, psi_mono / phi)

    return lower


def _empty_corridor(violations: list[str]) -> dict[str, Any]:
    def _invalid_theta_min(_phi: float) -> float:
        return float("inf")

    return {
        "phi_min": float("nan"),
        "phi_max": float("nan"),
        "theta_min_phi": _invalid_theta_min,
        "valid": False,
        "violations": violations,
    }


def build_corridor(
    rho: float,
    prev_slice_params: dict[str, float] | None,
    df_slice: pd.DataFrame,
) -> dict[str, Any]:
    """
    Construct feasible (φ, θ) corridor for candidate ρ (plan §8).

    Returns phi_min, phi_max, theta_min_phi callable, valid flag, violations.
    """
    violations: list[str] = []

    if df_slice.empty:
        return _empty_corridor(["empty df_slice"])

    k_star = float(df_slice["anchor_k_star"].iloc[0])
    theta_star = float(df_slice["anchor_theta_star"].iloc[0])
    if theta_star <= 0.0:
        return _empty_corridor(["non-positive anchor_theta_star"])

    if prev_slice_params is None:
        l_psi = cfg.CORRIDOR_EPS
    else:
        l_psi = _compute_L_psi(rho, prev_slice_params, k_star, theta_star)
        if math.isinf(l_psi):
            return _empty_corridor(["calendar_lower_infeasible"])

    intervals = _find_feasible_psi_intervals(
        rho, prev_slice_params, k_star, theta_star, l_psi
    )
    if not intervals:
        if prev_slice_params is not None:
            violations.append("calendar_or_butterfly_infeasible")
        else:
            violations.append("butterfly_infeasible")
        return _empty_corridor(violations)

    psi_lo, psi_hi = intervals[0]

    phi_samples: list[float] = []
    psi_scan = np.linspace(psi_lo, psi_hi, max(50, cfg.U_PSI_GRID_POINTS // 10))
    for psi in psi_scan:
        theta = theta_from_psi(float(psi), rho, k_star, theta_star)
        if theta > 0.0:
            phi_samples.append(float(psi) / theta)

    if not phi_samples:
        return _empty_corridor(violations + ["no_positive_theta_in_corridor"])

    phi_min = float(min(phi_samples))
    phi_max = float(max(phi_samples))

    def theta_min_phi(phi: float) -> float:
        return _theta_lower_bounds_at_phi(
            phi, rho, prev_slice_params, k_star, theta_star
        )

    return {
        "phi_min": phi_min,
        "phi_max": phi_max,
        "theta_min_phi": theta_min_phi,
        "valid": True,
        "violations": violations,
        "psi_min": psi_lo,
        "psi_max": psi_hi,
    }
