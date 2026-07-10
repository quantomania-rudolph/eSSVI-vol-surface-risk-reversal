"""Tests for essvi.audit — post-calibration dense k-grid audit and kill-switch."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from essvi import config as cfg
from essvi.audit import (
    audit_butterfly,
    audit_calendar,
    audit_lee_bound,
    audit_monotonicity,
    audit_result_to_kill_switch,
    audit_vertical_spread,
    build_audit_grid,
    compute_durrleman_g,
    is_surface_safe,
    run_full_audit,
)
from essvi.objective import w_slice_derivatives

# Calendar-compatible chain (same rho, monotone theta/psi) — mirrors test_surface.
VALID_SLICES = [
    {"T": 7 / 252.0, "theta": 0.06, "phi": 0.9, "psi": 0.06 * 0.9, "rho": -0.40},
    {"T": 30 / 252.0, "theta": 0.08, "phi": 0.675, "psi": 0.08 * 0.675, "rho": -0.40},
    {"T": 60 / 252.0, "theta": 0.10, "phi": 0.54, "psi": 0.10 * 0.54, "rho": -0.40},
]

K_COARSE = np.linspace(-2.0, 2.0, 81)


def _minute_result_from_slices(slices: list[dict]) -> dict:
    """Build a minimal minute_result dict without running calibration."""
    ordered = sorted(slices, key=lambda s: s["T"])
    rho_grid = np.array([s["rho"] for s in ordered], dtype=float)
    theta_grid = np.array([s["theta"] for s in ordered], dtype=float)
    psi_grid = np.array([s["psi"] for s in ordered], dtype=float)
    enriched = []
    for i, sl in enumerate(ordered):
        enriched.append(
            {
                "dte": int(round(sl["T"] * 252)),
                "rho": sl["rho"],
                "theta": sl["theta"],
                "phi": sl["phi"],
                "psi": sl["psi"],
                "anchor_k_star": 0.0,
                "anchor_theta_star": sl["theta"],
                "objective_value": 0.0,
                "is_valid": True,
                "n_strikes": 17,
                "n_belly": 9,
                "quality_flag": "VALID",
                "violations": [],
            }
        )
    return {
        "timestamp": pd.Timestamp("2024-06-03 14:30:00", tz="UTC"),
        "slices": enriched,
        "rho_grid": rho_grid,
        "theta_grid": theta_grid,
        "psi_grid": psi_grid,
        "n_slices": len(enriched),
        "n_valid": len(enriched),
        "any_invalid": False,
        "is_total_kill": False,
    }


def test_durrleman_g_nonnegative_for_valid_params():
    k = K_COARSE
    w, wp, wpp = w_slice_derivatives(k, theta=0.04, phi=1.0, rho=-0.5)
    g = compute_durrleman_g(k, w, wp, wpp)
    assert np.all(g >= -cfg.KILL_TOL_BUTTERFLY)


def test_durrleman_g_negative_for_arbitrageable_params():
    k = K_COARSE
    w, wp, wpp = w_slice_derivatives(k, theta=1.0, phi=5.0, rho=0.0)
    g = compute_durrleman_g(k, w, wp, wpp)
    assert np.any(g < -cfg.KILL_TOL_BUTTERFLY)


def test_butterfly_audit_flags_violation():
    bad = [{"T": 0.25, "theta": 1.0, "phi": 5.0, "rho": 0.0}]
    violations = audit_butterfly(bad, K_COARSE)
    assert len(violations) >= 1
    assert violations[0]["severity"] > 0.0


def test_butterfly_audit_clean_for_valid():
    violations = audit_butterfly(VALID_SLICES, K_COARSE)
    assert violations == []


def test_calendar_audit_flags_violation():
  # Near slice has much higher variance than far slice → calendar inversion.
    near = {"T": 0.05, "theta": 0.20, "phi": 1.0, "rho": -0.40}
    far = {"T": 0.25, "theta": 0.04, "phi": 1.0, "rho": -0.40}
    violations = audit_calendar([near, far], K_COARSE)
    assert len(violations) >= 1


def test_calendar_audit_clean_for_monotonic():
    violations = audit_calendar(VALID_SLICES, K_COARSE)
    assert violations == []


def test_vertical_spread_audit_flags_violation():
    # Extreme φ drives |w'| well above 2/T (bound = 40 at T = 0.05).
    bad = [{"T": 0.05, "theta": 1.0, "phi": 100.0, "rho": 0.0}]
    violations = audit_vertical_spread(bad, K_COARSE)
    assert len(violations) >= 1


def test_lee_bound_audit_flags_violation():
    bad = [{"T": 0.25, "theta": 0.08, "phi": 50.0, "rho": 0.0}]
    violations = audit_lee_bound(bad)
    assert len(violations) >= 1
    assert violations[0]["slope"] > cfg.TAIL_SLOPE_CAP


def test_monotonicity_audit_flags_violation():
    s1 = {"T": 0.05, "theta": 0.12, "phi": 1.0, "rho": -0.3}
    s2 = {"T": 0.25, "theta": 0.04, "phi": 1.0, "rho": -0.3}
    violations = audit_monotonicity([s1, s2])
    assert len(violations) == 1
    assert violations[0]["severity"] > 0.0


def test_full_audit_on_valid_minute_result():
    minute = _minute_result_from_slices(VALID_SLICES)
    report = run_full_audit(minute)
    ks = report["kill_switch"]
    assert ks["kill_triggered"] is False
    assert ks["total_violations"] == 0
    assert is_surface_safe(report)


def test_full_audit_report_structure():
    minute = _minute_result_from_slices(VALID_SLICES)
    report = run_full_audit(minute)
    for key in (
        "timestamp",
        "n_slices",
        "k_grid",
        "butterfly",
        "calendar",
        "vertical_spread",
        "lee",
        "monotonicity",
        "kill_switch",
    ):
        assert key in report

    ks = report["kill_switch"]
    for key in (
        "surface_usable",
        "butterfly_violations",
        "calendar_violations",
        "slope_violations",
        "lee_violations",
        "monotonicity_violations",
        "total_violations",
        "worst_severity",
        "kill_triggered",
    ):
        assert key in ks


def test_kill_switch_triggered_by_butterfly():
    minute = _minute_result_from_slices(
        [{"T": 0.25, "theta": 1.0, "phi": 5.0, "psi": 5.0, "rho": 0.0}]
    )
    report = run_full_audit(minute)
    ks = report["kill_switch"]
    assert ks["kill_triggered"] is True
    assert len(ks["butterfly_violations"]) >= 1
    assert not ks["surface_usable"]


def test_kill_switch_not_triggered_within_tolerance(monkeypatch):
    # Smallest negative g on the audit grid is ~−2.3e-5; widen tol so it stays sub-threshold.
    monkeypatch.setattr(cfg, "KILL_TOL_BUTTERFLY", 1e-4)
    theta, phi, rho = 0.14183673469387756, 7.456140350877193, 0.6
    minute = _minute_result_from_slices(
        [
            {
                "T": 0.25,
                "theta": theta,
                "phi": phi,
                "psi": theta * phi,
                "rho": rho,
            }
        ]
    )
    report = run_full_audit(minute)
    ks = report["kill_switch"]
    assert ks["kill_triggered"] is False
    assert ks["total_violations"] == 0


def test_audit_grid_correct_bounds():
    grid = build_audit_grid()
    assert grid.size == cfg.AUDIT_GRID_POINTS
    assert grid[0] == pytest.approx(-cfg.K_AUDIT, rel=0, abs=1e-12)
    assert grid[-1] == pytest.approx(cfg.K_AUDIT, rel=0, abs=1e-12)


def test_is_surface_safe_convenience():
    clean = run_full_audit(_minute_result_from_slices(VALID_SLICES))
    assert is_surface_safe(clean) is True

    dirty = run_full_audit(
        _minute_result_from_slices(
            [{"T": 0.25, "theta": 1.0, "phi": 5.0, "psi": 5.0, "rho": 0.0}]
        )
    )
    assert is_surface_safe(dirty) is False

    ks_only = audit_result_to_kill_switch({"butterfly": [], "calendar": [], "vertical_spread": [], "lee": [], "monotonicity": []})
    assert is_surface_safe({"kill_switch": ks_only}) is True
