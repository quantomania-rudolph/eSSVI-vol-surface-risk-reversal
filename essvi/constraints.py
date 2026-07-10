"""No-arbitrage constraints and corridor construction for eSSVI slices."""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from essvi import config as cfg

_RHO_BOUND_TOL = 1e-12
_PASQUAZZI_RHO_TOL = 1e-10
_PASQUAZZI_PHI_TOL = 1e-10


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


def _compute_f_MM_brent(theta: float, rho: float) -> float:
    """Original Brent-based computation — used for table build only."""
    if theta <= 0.0:
        return 0.0

    if abs(rho) >= 1.0:
        return 4.0 * theta / (1.0 + abs(rho))

    # Use the existing grid-based method as the "exact" reference for table building
    grid_points = cfg.MM_L_GRID_POINTS
    l2 = _mm_l2(abs(rho))
    l_start = l2 + max(cfg.MM_L2_TOL, 1e-8)
    l_end = cfg.MM_L_MAX
    if l_start >= l_end:
        return 4.0 * theta / (1.0 + abs(rho))

    l_grid = np.linspace(l_start, l_end, grid_points)
    values = _mm_objective_grid(l_grid, theta, abs(rho))
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 4.0 * theta / (1.0 + abs(rho))
    return float(np.min(finite))


# ============================================================
# P1-5: MM Butterfly Table Precomputation (Martini-Mingone 2022 Prop 6.3)
# ============================================================
_MM_THETA_MIN = 1e-6
_MM_THETA_MAX = 2.0
_MM_RHO_MAX = 0.999
_MM_THETA_N = 200
_MM_RHO_N = 100

_MM_THETA_GRID = None
_MM_RHO_GRID = None
_MM_TABLE = None
_MM_TABLE_BUILT = False


def _build_mm_table():
    """Precompute ℱ_MM(θ, |ρ|) on log(θ) × ρ grid. Runs on import."""
    global _MM_THETA_GRID, _MM_RHO_GRID, _MM_TABLE, _MM_TABLE_BUILT

    if _MM_TABLE_BUILT:
        return

    _MM_THETA_GRID = np.logspace(np.log10(_MM_THETA_MIN), np.log10(_MM_THETA_MAX), _MM_THETA_N)
    _MM_RHO_GRID = np.linspace(0, _MM_RHO_MAX, _MM_RHO_N)
    _MM_TABLE = np.zeros((_MM_THETA_N, _MM_RHO_N))

    # Build using existing Brent-based function (slow but one-time)
    for i, theta in enumerate(_MM_THETA_GRID):
        for j, rho in enumerate(_MM_RHO_GRID):
            _MM_TABLE[i, j] = _compute_f_MM_brent(theta, rho)

    _MM_TABLE_BUILT = True


# Build table on import
_build_mm_table()


# ============================================================
# P1-1: Corridor Multi-Interval Search (Blueprint §8.4)
# ============================================================

def _compute_U_psi(
    rho: float,
    psi: float,
    prev_slice: dict[str, float] | None,
    k_star: float,
    theta_star: float,
) -> float:
    """Upper bound on ψ from Φ ≤ 1 and calendar feasibility (Blueprint §8.4)."""
    theta = theta_from_psi(psi, rho, k_star, theta_star)
    if theta <= 0.0:
        return -1.0

    abs_r = _abs_rho(rho)
    u_butterfly = _butterfly_upper_psi(theta, abs_r)

    if prev_slice is not None:
        theta_prev = float(prev_slice["theta"])
        psi_prev = float(prev_slice["theta"]) * float(prev_slice["phi"])
        # Φ = φ/φ_prev ≤ 1 → ψ/θ ≤ ψ_prev/θ_prev → ψ ≤ ψ_prev * θ / θ_prev
        u_calendar = psi_prev * theta / theta_prev
        return min(u_butterfly, u_calendar) - cfg.CORRIDOR_EPS

    return u_butterfly - cfg.CORRIDOR_EPS


def _compute_psi_upper_bound(rho: float, theta_star: float) -> float:
    """Maximum ψ from butterfly bounds alone (for search range)."""
    # Upper bound from B1: ψ(1+|ρ|) ≤ 4
    return cfg.U_BF1_FACTOR / (1.0 + _abs_rho(rho))


def _brent_root(f, a: float, b: float, xtol: float = 1e-10, maxiter: int = 100) -> float:
    """Find root of f(x) = 0 in [a, b] using Brent's method."""
    try:
        from scipy.optimize import brentq
        return brentq(f, a, b, xtol=xtol, maxiter=maxiter)
    except (ImportError, ValueError):
        # Fallback: bisection
        fa = f(a)
        fb = f(b)
        if fa * fb > 0:
            return b
        for _ in range(maxiter):
            c = (a + b) / 2
            fc = f(c)
            if abs(fc) < xtol or (b - a) < xtol:
                return c
            if fa * fc <= 0:
                b = c
                fb = fc
            else:
                a = c
                fa = fc
        return (a + b) / 2


def _find_feasible_psi_intervals(
    rho: float,
    prev_slice: dict[str, float] | None,
    k_star: float,
    theta_star: float,
    l_psi: float | None,
) -> list[tuple[float, float]]:
    """
    Find ALL ψ intervals where U_ψ(ψ) ≥ L_ψ.
    U_ψ is non-monotonic because θ(ψ) is convex (Blueprint §8.4).
    """
    if l_psi is None:
        return []  # Empty corridor (Case A infeasible)

    psi_min = max(l_psi, 1e-6)
    psi_max = _compute_psi_upper_bound(rho, theta_star)

    if psi_min >= psi_max:
        return []

    # Sample U_ψ on dense log grid
    n_samples = cfg.U_PSI_GRID_POINTS
    psi_grid = np.logspace(np.log10(psi_min), np.log10(psi_max), n_samples)

    U_vals = np.array([
        _compute_U_psi(rho, psi, prev_slice, k_star, theta_star)
        for psi in psi_grid
    ])

    # f(ψ) = U_ψ(ψ) - L_ψ
    f_vals = U_vals - l_psi

    # Find ALL sign changes and intervals
    intervals = []
    in_feasible = False
    interval_start = None

    for i in range(len(psi_grid)):
        feasible = f_vals[i] >= 0

        if feasible and not in_feasible:
            # Entering feasible region
            in_feasible = True
            if i == 0:
                interval_start = psi_grid[i]
            else:
                # Find exact crossing using bisection
                lo = psi_grid[i - 1]
                hi = psi_grid[i]
                interval_start = _brent_root(
                    lambda p: _compute_U_psi(rho, p, prev_slice, k_star, theta_star) - l_psi,
                    lo, hi
                )
        elif not feasible and in_feasible:
            # Exiting feasible region
            in_feasible = False
            if interval_start is not None:
                lo = psi_grid[i - 1]
                hi = psi_grid[i]
                interval_end = _brent_root(
                    lambda p: _compute_U_psi(rho, p, prev_slice, k_star, theta_star) - l_psi,
                    lo, hi
                )
                intervals.append((interval_start, interval_end))
                interval_start = None

    # If still in feasible region at end
    if in_feasible and interval_start is not None:
        intervals.append((interval_start, psi_max))

    # Filter: only keep intervals with width > tolerance
    intervals = [(lo, hi) for lo, hi in intervals if hi - lo > 1e-8]

    return intervals


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


def compute_f_MM(theta: float, abs_rho: float, n_grid: int | None = None) -> float:
    """
    Martini-Mingone butterfly boundary ℱ_MM(θ, |ρ|).
    Bilinear interpolation on precomputed table.
    """
    if not _MM_TABLE_BUILT:
        _build_mm_table()

    if theta <= 0.0:
        return 0.0

    if abs_rho >= 1.0:
        return 4.0 * theta / (1.0 + abs_rho)

    # Clamp to grid bounds
    theta_clamped = np.clip(theta, _MM_THETA_MIN, _MM_THETA_MAX)
    rho_clamped = np.clip(abs_rho, 0, _MM_RHO_MAX)

    # Find indices
    i = np.searchsorted(_MM_THETA_GRID, theta_clamped) - 1
    j = np.searchsorted(_MM_RHO_GRID, rho_clamped) - 1

    # Clamp indices to valid range
    i = np.clip(i, 0, _MM_THETA_N - 2)
    j = np.clip(j, 0, _MM_RHO_N - 2)

    # Bilinear interpolation on log(theta), rho
    log_theta = np.log(theta_clamped)
    log_t0 = np.log(_MM_THETA_GRID[i])
    log_t1 = np.log(_MM_THETA_GRID[i + 1])
    r0 = _MM_RHO_GRID[j]
    r1 = _MM_RHO_GRID[j + 1]

    # Weights
    wt = (log_theta - log_t0) / (log_t1 - log_t0)
    wr = (rho_clamped - r0) / (r1 - r0)

    # Four corners
    v00 = _MM_TABLE[i, j]
    v01 = _MM_TABLE[i, j + 1]
    v10 = _MM_TABLE[i + 1, j]
    v11 = _MM_TABLE[i + 1, j + 1]

    # Bilinear interpolation
    v0 = v00 + wr * (v01 - v00)
    v1 = v10 + wr * (v11 - v10)
    return float(v0 + wt * (v1 - v0))


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
    theta1: float, psi1: float, rho1: float,
    theta2: float, psi2: float, rho2: float
) -> tuple[bool, str]:
    """
    Pasquazzi 2023 Proposition 13 — Necessary & sufficient calendar no-arb.
    Returns (feasible, reason).

    Args:
        theta1, psi1, rho1: Nearer maturity (T1) parameters
        theta2, psi2, rho2: Farther maturity (T2) parameters
    """
    theta_ratio = theta2 / theta1
    phi1 = psi1 / theta1 if theta1 > 0 else 0
    phi2 = psi2 / theta2 if theta2 > 0 else 0
    Phi = phi2 / phi1 if phi1 > 0 else np.inf

    # --- CASE A: Θ ≈ 1 ---
    if abs(theta_ratio - 1.0) <= cfg.PASQUAZZI_THETA_TOL:
        # Feasible ONLY if:
        # (i) ρ₁ = ρ₂ = 0 (both zero) AND Φ ≥ 1
        # (ii) ρ₁ = ρ₂ ≠ 0 AND Φ = 1
        if abs(rho1) < _PASQUAZZI_RHO_TOL and abs(rho2) < _PASQUAZZI_RHO_TOL:
            if Phi >= 1.0 - _PASQUAZZI_PHI_TOL:
                return True, "Case A(i): ρ₁=ρ₂=0, Φ≥1"
            return False, f"Case A(i): ρ₁=ρ₂=0 but Φ={Phi:.6f}<1"

        if abs(rho1 - rho2) < _PASQUAZZI_RHO_TOL:
            if abs(Phi - 1.0) < _PASQUAZZI_PHI_TOL:
                return True, "Case A(ii): ρ₁=ρ₂, Φ=1"
            return False, f"Case A(ii): ρ₁=ρ₂ but Φ={Phi:.6f}≠1"

        # ρ₁ ≠ ρ₂ and not both zero → INFEASIBLE
        return False, f"Case A: Θ≈1 but ρ₁={rho1:.4f}≠ρ₂={rho2:.4f} and not both zero"

    # --- CASE B: Θ > 1 (theta2 > theta1) ---
    if theta_ratio > 1.0:
        return _check_hm_stripe(theta1, psi1, rho1, theta2, psi2, rho2)

    # --- CASE C: Θ < 1 (theta2 < theta1) ---
    # Symmetric to Case B
    return _check_hm_stripe(theta2, psi2, rho2, theta1, psi1, rho1)


def _check_hm_stripe(
    theta_small: float, psi_small: float, rho_small: float,
    theta_large: float, psi_large: float, rho_large: float
) -> tuple[bool, str]:
    """Hendriks-Martini stripe conditions for Θ ≠ 1."""
    phi_small = psi_small / theta_small if theta_small > 0 else 0
    phi_large = psi_large / theta_large if theta_large > 0 else 0
    Phi = phi_large / phi_small if phi_small > 0 else np.inf

    # Conditions from Hendriks-Martini 2019
    # 1. Θ ≥ 1 (already satisfied by caller)
    # 2. Φ ≥ 1
    if Phi < 1.0 - cfg.KILL_TOL_CALENDAR:
        return False, f"HM stripe: Φ={Phi:.6f} < 1"

    # 3. ρ bounds
    if abs(rho_small - rho_large) > cfg.KILL_TOL_CALENDAR:
        return False, f"HM stripe: |ρ₁-ρ₂|={abs(rho_small - rho_large):.6f} > tol"

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


def _compute_L_psi(
    rho: float,
    prev_slice: dict[str, float] | None,
    k_star: float,
    theta_star: float,
) -> float | None:
    """
    Blueprint §8.2: Lower bound on ψ from calendar arbitrage.
    Returns None if infeasible (empty corridor).
    """
    if prev_slice is None:
        return 0.0

    theta_prev = float(prev_slice["theta"])
    psi_prev = float(prev_slice["theta"]) * float(prev_slice["phi"])
    rho_prev = float(prev_slice["rho"])

    theta_ratio = theta_star / theta_prev

    # --- Case A: Θ ≈ 1 ---
    if abs(theta_ratio - 1.0) <= cfg.PASQUAZZI_THETA_TOL:
        # Feasible only if:
        if abs(rho) < _PASQUAZZI_RHO_TOL and abs(rho_prev) < _PASQUAZZI_RHO_TOL:
            return 0.0  # Both zero → any ψ ≥ 0

        if abs(rho - rho_prev) < _PASQUAZZI_RHO_TOL:
            return psi_prev  # Must match exactly (Φ=1)

        # ρ ≠ ρ_prev and not both zero → INFEASIBLE
        return None

    # --- Case B/C: Use Hendriks-Martini boundary ---
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
        if l_psi is None or math.isinf(l_psi):
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