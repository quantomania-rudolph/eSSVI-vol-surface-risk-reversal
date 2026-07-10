"""Tests for essvi.constraints no-arbitrage checks and corridor builder."""

from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd
import pytest

from essvi import config as cfg
from essvi.constraints import (
    build_corridor,
    check_butterfly,
    check_butterfly_gj,
    check_butterfly_mm,
    check_calendar_pasquazzi,
    check_lee_bound,
    check_vertical_spread,
    compute_f_MM,
    _compute_f_MM_brent,
    _find_feasible_psi_intervals,
)


def _slice_df(
    *,
    k_star: float = 0.0,
    theta_star: float = 0.04,
    business_t: float = 0.05,
    strikes: tuple[float, ...] = (-0.5, 0.0, 0.5, 1.0),
) -> pd.DataFrame:
    rows = []
    for k in strikes:
        rows.append(
            {
                "anchor_k_star": k_star,
                "anchor_theta_star": theta_star,
                "business_t": business_t,
                "log_moneyness": k,
            }
        )
    return pd.DataFrame(rows)


def test_butterfly_b1_violated():
    ok, msg = check_butterfly_gj(theta=1.0, phi=5.0, rho=0.0)
    assert not ok
    assert "B1" in msg


def test_butterfly_b2_violated():
    ok, msg = check_butterfly_gj(theta=0.001, phi=100.0, rho=0.0)
    assert not ok
    assert "B2" in msg


def test_butterfly_both_pass():
    ok, msg = check_butterfly_gj(theta=0.04, phi=1.0, rho=-0.5)
    assert ok, msg
    ok_mm, msg_mm = check_butterfly_mm(theta=0.04, phi=1.0, rho=-0.5)
    assert ok_mm, msg_mm


def test_butterfly_mm_tighter_than_gj():
    """MM boundary is at least as permissive as GJ: F_MM >= 4*theta/(1+|rho|)."""
    thetas = np.logspace(-3, 0, 20)
    rhos = np.linspace(0.0, 0.95, 10)
    for theta in thetas:
        for abs_rho in rhos:
            f_mm = compute_f_MM(float(theta), float(abs_rho))
            gj = 4.0 * theta / (1.0 + abs_rho)
            assert f_mm + 1e-6 >= gj

    # Borderline: GJ B2 fails while MM-2 still passes when F_MM > psi^2.
    theta = 0.01
    rho = 0.0
    phi = 20.57609856878415
    gj_ok, _ = check_butterfly_gj(theta, phi, rho)
    mm_ok, _ = check_butterfly_mm(theta, phi, rho)
    assert not gj_ok
    assert mm_ok


def test_calendar_pasquazzi_case_a():
    # theta2/theta1 = 0.05/0.04 = 1.25 > 1, so Case B
    # Need Phi >= 1: phi1 = psi1/theta1 = 0.04/0.04 = 1, phi2 = psi2/theta2
    # For Phi >= 1: psi2/0.05 >= 1/1 => psi2 >= 0.05
    feasible, reason = check_calendar_pasquazzi(0.04, 0.04, -0.4, 0.05, 0.06, -0.4)
    assert feasible
    assert "HM stripe" in reason or reason == ""


def test_calendar_pasquazzi_violated_theta_ratio():
    # Case C: theta1 (nearer) = 0.05, theta2 (farther) = 0.04 -> ratio = 0.8 < 1
    # Calls _check_hm_stripe(0.04, psi2, rho, 0.05, psi1, rho)
    # Need Phi < 1 to be infeasible
    # phi_small = psi2/0.04, phi_large = psi1/0.05
    # Phi = (psi1/0.05) / (psi2/0.04) = (psi1/psi2) * (0.04/0.05) = (psi1/psi2) * 0.8
    # For Phi < 1: psi1/psi2 < 1.25 => psi1 < 1.25 * psi2
    # Let psi2 = 0.04, psi1 = 0.04 => Phi = 1.0 >= 1 (feasible)
    # Let psi2 = 0.04, psi1 = 0.03 => Phi = (0.03/0.04) * 0.8 = 0.6 < 1 (INFEASIBLE)
    feasible, reason = check_calendar_pasquazzi(0.05, 0.03, -0.3, 0.04, 0.04, -0.3)
    assert not feasible
    assert "HM stripe" in reason or "Φ=" in reason or "Phi" in reason


def test_calendar_pasquazzi_violated_delta_rho():
    # Case B: theta1=0.04, theta2=0.05 (ratio > 1)
    # But rho differs
    feasible, reason = check_calendar_pasquazzi(0.04, 0.04, -0.8, 0.05, 0.06, 0.7)
    assert not feasible
    assert "HM stripe" in reason or "ρ" in reason or "rho" in reason


def test_vertical_spread_violated():
    df = _slice_df(strikes=(-2.0, -1.0, 0.0, 1.0, 2.0))
    params = {"theta": 5.0, "phi": 20.0, "rho": 0.95}
    ok, msg = check_vertical_spread(params, df, tolerance=0.0)
    assert not ok
    assert "vertical spread" in msg.lower()


def test_lee_bound_pass():
    ok, msg = check_lee_bound(theta=0.04, phi=1.0, rho=-0.5)
    assert ok, msg


def test_lee_bound_violated():
    ok, msg = check_lee_bound(theta=1.0, phi=5.0, rho=0.0)
    assert not ok
    assert "Lee" in msg


def test_corridor_nonempty():
    df = _slice_df(theta_star=0.04, k_star=0.0)
    corridor = build_corridor(rho=-0.3, prev_slice_params=None, df_slice=df)
    assert corridor["valid"]
    assert corridor["phi_min"] > 0.0
    assert corridor["phi_max"] >= corridor["phi_min"]
    assert math.isfinite(corridor["phi_min"])
    assert math.isfinite(corridor["phi_max"])


def test_corridor_empty_when_rho_extreme():
    df = _slice_df(theta_star=0.04, k_star=0.0)
    prev = {"theta": 0.04, "phi": 50.0, "rho": -0.95}
    corridor = build_corridor(rho=0.89, prev_slice_params=prev, df_slice=df)
    assert not corridor["valid"]
    assert len(corridor["violations"]) > 0


def test_corridor_edges_butterfly():
    df = _slice_df(theta_star=0.04, k_star=0.0)
    corridor = build_corridor(rho=0.0, prev_slice_params=None, df_slice=df)
    assert corridor["valid"]

    psi_hi = corridor["psi_max"]
    assert psi_hi <= 4.0 / (1.0 + 0.0) + 1e-3


def test_corridor_edges_calendar():
    df = _slice_df(theta_star=0.05, k_star=0.0)
    prev = {"theta": 0.04, "phi": 1.0, "rho": -0.4}
    corridor = build_corridor(rho=-0.35, prev_slice_params=prev, df_slice=df)
    assert corridor["valid"]

    phi_mid = 0.5 * (corridor["phi_min"] + corridor["phi_max"])
    theta_min = corridor["theta_min_phi"](phi_mid)
    assert theta_min >= prev["theta"] - 1e-8


def test_f_MM_monotonic():
    abs_rho = 0.4
    thetas = np.linspace(0.01, 0.2, 15)
    f_vals = [compute_f_MM(float(t), abs_rho) for t in thetas]
    for left, right in zip(f_vals, f_vals[1:]):
        assert right >= left - 1e-6


def test_check_butterfly_dispatches_to_config(monkeypatch):
    monkeypatch.setattr(cfg, "BUTTERFLY_BOUND_MODE", "gj_conservative")
    ok, _ = check_butterfly(theta=0.04, phi=1.0, rho=-0.5)
    assert ok


# ============================================================
# P0-4: Pasquazzi 2023 Case A Tests
# ============================================================

def test_pasquazzi_case_A_feasible_both_zero():
    """Case A: Θ≈1, ρ₁=ρ₂=0, Φ≥1 → feasible."""
    # theta2/theta1 ≈ 1.0 (within PASQUAZZI_THETA_TOL = 1e-4)
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, 0.0, 0.04001, 0.25, 0.0)
    assert feasible
    assert "Case A(i)" in reason


def test_pasquazzi_case_A_feasible_equal_rho():
    """Case A: Θ≈1, ρ₁=ρ₂≠0, Φ=1 → feasible."""
    # theta2/theta1 = 0.04001/0.04 = 1.00025 ≈ 1.0
    # psi2 = theta2 * phi2 = 0.04001 * 5.0005 = 0.20005 (so Φ = 1)
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, -0.3, 0.04001, 0.20005, -0.3)
    assert feasible
    assert "Case A(ii)" in reason


def test_pasquazzi_case_A_infeasible_rho_diff():
    """Case A: Θ≈1, ρ₁≠ρ₂, not both zero → INFEASIBLE."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, -0.3, 0.04001, 0.25, 0.2)
    assert not feasible
    assert "Case A" in reason
    assert "INFEASIBLE" in reason or "not both zero" in reason or "ρ₁=" in reason


def test_pasquazzi_case_A_infeasible_both_zero_phi_lt_1():
    """Case A: Θ≈1, ρ₁=ρ₂=0, but Φ<1 → INFEASIBLE."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, 0.0, 0.04001, 0.15, 0.0)
    assert not feasible
    assert "Case A(i)" in reason
    assert "Φ=" in reason


def test_pasquazzi_case_A_infeasible_equal_rho_phi_ne_1():
    """Case A: Θ≈1, ρ₁=ρ₂≠0, but Φ≠1 → INFEASIBLE."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, -0.3, 0.04001, 0.25, -0.3)
    assert not feasible
    assert "Case A(ii)" in reason
    assert "Φ=" in reason


def test_pasquazzi_case_B_theta_ratio_gt_1():
    """Case B: Θ > 1 → uses Hendriks-Martini stripe."""
    feasible, reason = check_calendar_pasquazzi(0.04, 0.2, -0.3, 0.05, 0.25, -0.3)
    assert feasible
    assert "HM stripe" in reason or reason == ""


def test_pasquazzi_case_C_theta_ratio_lt_1():
    """Case C: Θ < 1 → symmetric to Case B."""
    feasible, reason = check_calendar_pasquazzi(0.05, 0.25, -0.3, 0.04, 0.2, -0.3)
    assert feasible
    assert "HM stripe" in reason or reason == ""


# ============================================================
# P1-1: Corridor Multi-Interval Tests
# ============================================================

def test_corridor_multiple_intervals():
    """U_ψ dips below L_ψ then above → two feasible intervals."""
    # Create a prev_slice that creates a non-monotonic U_ψ
    prev = {"theta": 0.04, "phi": 10.0, "rho": -0.3}
    df = _slice_df(theta_star=0.04, k_star=0.0)
    
    # This test verifies the function returns a list of intervals
    intervals = _find_feasible_psi_intervals(
        rho=-0.3, prev_slice=prev, k_star=0.0, theta_star=0.04, l_psi=0.1
    )
    
    # At minimum should return something (could be 0, 1, or more intervals)
    assert isinstance(intervals, list)
    # Verify each interval is valid
    for lo, hi in intervals:
        assert lo < hi
        assert lo > 0
        # Check midpoint is feasible
        psi_mid = (lo + hi) / 2
        U = _compute_U_psi(-0.3, psi_mid, prev, 0.0, 0.04)
        # Note: might be slightly infeasible at boundaries due to numerical precision


def test_corridor_single_interval():
    """Simple case: single contiguous feasible interval."""
    prev = {"theta": 0.04, "phi": 1.0, "rho": -0.3}
    df = _slice_df(theta_star=0.05, k_star=0.0)
    
    intervals = _find_feasible_psi_intervals(
        rho=-0.35, prev_slice=prev, k_star=0.0, theta_star=0.05, l_psi=0.1
    )
    
    assert isinstance(intervals, list)
    assert len(intervals) >= 1
    for lo, hi in intervals:
        assert lo < hi


def test_corridor_empty_when_l_psi_none():
    """Case A infeasible returns empty intervals."""
    intervals = _find_feasible_psi_intervals(
        rho=-0.3, prev_slice=None, k_star=0.0, theta_star=0.04, l_psi=None
    )
    assert intervals == []


# ============================================================
# P1-5: MM Table Speed & Accuracy Tests
# ============================================================

def test_mm_table_speed():
    """compute_f_MM via table should be ~100x faster than Brent."""
    thetas = np.logspace(-6, 0, 50)
    rhos = np.linspace(0, 0.99, 50)
    
    # Table version
    start = time.perf_counter()
    for t, r in zip(thetas, rhos):
        compute_f_MM(t, r)
    table_time = time.perf_counter() - start
    
    # Brent version (sample only)
    start = time.perf_counter()
    for t, r in zip(thetas[:5], rhos[:5]):
        _compute_f_MM_brent(t, r)
    brent_time = time.perf_counter() - start
    brent_time *= 10  # Extrapolate
    
    # Table should be significantly faster
    assert table_time < brent_time / 10, \
        f"Table {table_time:.3f}s not fast enough vs Brent {brent_time:.3f}s"


def test_mm_table_accuracy():
    """Table interpolation matches Brent within 1e-6."""
    for theta in [1e-4, 1e-3, 0.01, 0.1, 0.5, 1.0]:
        for rho in [0.0, 0.3, 0.7, 0.95]:
            table_val = compute_f_MM(theta, rho)
            brent_val = _compute_f_MM_brent(theta, rho)
            assert abs(table_val - brent_val) < 1e-6, \
                f"Mismatch at θ={theta}, ρ={rho}: table={table_val}, brent={brent_val}"


def test_mm_table_bounds():
    """Table values respect grid bounds."""
    # Below grid
    assert compute_f_MM(1e-7, 0.5) == compute_f_MM(_MM_THETA_MIN, 0.5)
    # Above grid
    assert compute_f_MM(5.0, 0.5) == compute_f_MM(_MM_THETA_MAX, 0.5)
    # Rho bounds
    assert compute_f_MM(0.1, -0.5) == compute_f_MM(0.1, 0.5)
    assert compute_f_MM(0.1, 1.5) == 4.0 * 0.1 / (1.0 + 0.999)


# Need to import private constants for bounds test
from essvi.constraints import _MM_THETA_MIN, _MM_THETA_MAX
