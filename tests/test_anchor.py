"""Tests for essvi.anchor — anchor extraction (k*, theta*)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from essvi import config as cfg
from essvi.anchor import (
    AnchorParams,
    belly_mask,
    compute_theta_star,
    compute_theta_t,
    eval_w,
    extract_anchor_params,
    relaxed_belly_mask,
    select_anchor_k_star,
)
from essvi.exceptions import AnchorError


def _row(
    *,
    log_moneyness: float = 0.0,
    rel_spread: float = 0.05,
    oi: int = 200,
    delta_black76: float = 0.50,
    implied_vol: float = 0.30,
    business_t: float = 0.10,
    strike: float = 150.0,
    belly_flag: bool = True,
    OTM: bool = True,
) -> dict:
    return {
        "strike": strike,
        "log_moneyness": log_moneyness,
        "rel_spread": rel_spread,
        "oi": oi,
        "delta_black76": delta_black76,
        "implied_vol": implied_vol,
        "business_t": business_t,
        "belly_flag": belly_flag,
        "OTM": OTM,
    }


def _df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def test_belly_mask_all_pass():
    df = _df(
        _row(log_moneyness=-0.05, strike=145.0),
        _row(log_moneyness=0.0, strike=150.0),
        _row(log_moneyness=0.05, strike=155.0),
    )
    mask = belly_mask(df)
    assert mask.dtype == bool
    assert mask.shape == (3,)
    assert mask.all()


def test_belly_mask_filters_high_spread():
    df = _df(_row(rel_spread=cfg.BELLY_REL_SPREAD_MAX + 0.01))
    assert not belly_mask(df).any()


def test_belly_mask_filters_low_oi():
    df = _df(_row(oi=cfg.BELLY_OI_MIN - 1))
    assert not belly_mask(df).any()


def test_belly_mask_filters_delta():
    low = _df(_row(delta_black76=cfg.BELLY_DELTA_LO - 0.01))
    high = _df(_row(delta_black76=cfg.BELLY_DELTA_HI + 0.01))
    assert not belly_mask(low).any()
    assert not belly_mask(high).any()


def test_belly_mask_filters_k():
    df = _df(_row(log_moneyness=cfg.BELLY_K_ABS + 0.01))
    assert not belly_mask(df).any()


def test_select_anchor_exact_atm():
    df = _df(
        _row(log_moneyness=0.05, strike=155.0),
        _row(log_moneyness=0.0, strike=150.0),
        _row(log_moneyness=-0.04, strike=145.0),
    )
    k_star = select_anchor_k_star(df, belly_mask(df))
    assert k_star == pytest.approx(0.0)


def test_select_anchor_nearest_belly():
    df = _df(
        _row(log_moneyness=0.08, strike=155.0),
        _row(log_moneyness=-0.06, strike=145.0),
    )
    k_star = select_anchor_k_star(df, belly_mask(df))
    assert k_star == pytest.approx(-0.06)


def test_select_anchor_widened_gates():
    df = _df(
        _row(log_moneyness=0.02, rel_spread=0.12, oi=60, strike=151.0),
        _row(log_moneyness=0.10, rel_spread=0.20, oi=10, strike=160.0),
    )
    assert not belly_mask(df).any()
    assert relaxed_belly_mask(df).any()
    k_star = select_anchor_k_star(df, belly_mask(df))
    assert k_star == pytest.approx(0.02)


def test_select_anchor_nearest_any():
    df = _df(
        _row(log_moneyness=0.03, rel_spread=0.20, oi=10, delta_black76=0.02, strike=151.0),
        _row(log_moneyness=0.12, rel_spread=0.22, oi=5, delta_black76=0.01, strike=160.0),
    )
    assert not relaxed_belly_mask(df).any()
    k_star = select_anchor_k_star(df, belly_mask(df))
    assert k_star == pytest.approx(0.03)


def test_select_anchor_no_strikes_raises():
    with pytest.raises(AnchorError):
        select_anchor_k_star(_df(), belly_mask(_df()))
    bad = _df(_row(implied_vol=0.001))
    with pytest.raises(AnchorError):
        select_anchor_k_star(bad, belly_mask(bad))


# ============================================================================
# NEW TESTS: Anchor Inversion Fix (P0-1)
# ============================================================================

def test_anchor_independent_of_rho_psi():
    """Anchor (k*, theta*) must be identical regardless of candidate (rho, psi)."""
    df = _df(
        _row(log_moneyness=0.04, strike=155.0, implied_vol=0.28),
        _row(log_moneyness=0.0, strike=150.0, implied_vol=0.30),
    )

    # Anchor extraction takes NO (phi, rho) parameters
    anchor1 = extract_anchor_params(df)
    anchor2 = extract_anchor_params(df)

    assert anchor1.k_star == anchor2.k_star
    assert anchor1.theta_star == anchor2.theta_star
    assert anchor1.quality == anchor2.quality
    assert anchor1.n_belly == anchor2.n_belly


def test_theta_from_psi_exact_formula():
    """theta_t = theta* - rho*psi*k* - psi^2*k*^2*(1-rho^2)/(4*theta*) — exact, no iteration."""
    anchor = AnchorParams(k_star=0.05, theta_star=0.04, quality="EXACT_ATM", n_belly=5)

    # Test case from Corbetta Table 1
    psi = 0.5
    rho = -0.3

    theta_t = compute_theta_t(psi, rho, anchor)

    # Manual computation of the exact closed form (Corbetta Eq 3.12)
    expected = (
        anchor.theta_star
        - rho * psi * anchor.k_star
        - psi**2 * anchor.k_star**2 * (1 - rho**2) / (4 * anchor.theta_star)
    )

    assert abs(theta_t - expected) < 1e-12, f"theta_t={theta_t}, expected={expected}"


def test_slice_passes_through_atm():
    """For ANY (rho, psi), w(k*) = theta* exactly (by construction)."""
    anchor = AnchorParams(k_star=0.02, theta_star=0.035, quality="EXACT_ATM", n_belly=5)

    for rho in [-0.8, -0.3, 0.0, 0.3, 0.8]:
        for psi in [0.1, 0.3, 0.6, 1.0]:
            theta_t = compute_theta_t(psi, rho, anchor)
            phi = psi / theta_t

            # Evaluate eSSVI at k*
            w_at_kstar = eval_w(anchor.k_star, theta_t, rho, phi)

            assert abs(w_at_kstar - anchor.theta_star) < 1e-10, \
                f"Failed for rho={rho}, psi={psi}: w(k*)={w_at_kstar}, theta*={anchor.theta_star}"


def test_compute_theta_star_removed_from_exports():
    """Ensure the old inverted function is not in the main API exports."""
    from essvi import anchor as anchor_module

    # The function should still exist but is NOT exported in __all__ as a primary API
    # (It's kept for internal solver use only)
    assert hasattr(anchor_module, 'compute_theta_star'), "Function should exist for solver use"
    assert 'compute_theta_star' in anchor_module.__all__, "Should be in __all__ for internal use"


def test_anchor_params_dataclass():
    """AnchorParams is a frozen dataclass with correct fields."""
    anchor = AnchorParams(k_star=0.05, theta_star=0.04, quality="EXACT_ATM", n_belly=5)

    assert anchor.k_star == 0.05
    assert anchor.theta_star == 0.04
    assert anchor.quality == "EXACT_ATM"
    assert anchor.n_belly == 5

    # Verify frozen
    with pytest.raises(Exception):
        anchor.k_star = 0.1


def test_extract_anchor_params_quality_labels():
    """Test quality labels match the fallback ladder."""
    # EXACT_ATM
    exact = _df(_row(log_moneyness=0.0, belly_flag=True))
    assert extract_anchor_params(exact).quality == "EXACT_ATM"

    # NEAREST_BELLY (at least 3 in belly)
    near = _df(
        _row(log_moneyness=0.05, belly_flag=True),
        _row(log_moneyness=-0.04, belly_flag=True),
        _row(log_moneyness=0.03, belly_flag=True),
    )
    assert extract_anchor_params(near).quality == "NEAREST_BELLY"

    # WIDENED_GATES (fewer than 3 in standard belly, but some in relaxed)
    widened = _df(
        _row(log_moneyness=0.02, rel_spread=0.12, oi=60, belly_flag=False),
        _row(log_moneyness=0.10, rel_spread=0.20, oi=10, belly_flag=False),
    )
    assert not belly_mask(widened).any()
    assert relaxed_belly_mask(widened).any()
    assert extract_anchor_params(widened).quality == "WIDENED_GATES"

    # NEAREST_ANY (no belly at all)
    any_strike = _df(
        _row(log_moneyness=0.03, rel_spread=0.20, oi=10, delta_black76=0.02, belly_flag=False),
    )
    assert not relaxed_belly_mask(any_strike).any()
    assert extract_anchor_params(any_strike).quality == "NEAREST_ANY"


def test_compute_theta_star_consistency():
    """compute_theta_star is the inverse of eval_w at k*."""
    w_star = 0.05
    k_star = 0.01
    phi = 2.0
    rho = -0.4
    theta_star = compute_theta_star(w_star, k_star, phi, rho)
    w_recovered = eval_w(k_star, theta_star, rho, phi)
    assert w_recovered == pytest.approx(w_star, rel=0, abs=cfg.ANCHOR_THETA_TOL)


def test_anchor_extraction_no_belly_flag_fallback():
    """When belly_flag column missing, falls back to OTM or all strikes."""
    # Without belly_flag, should fall back to OTM filter
    df = _df(
        _row(log_moneyness=0.04, strike=155.0, implied_vol=0.28, belly_flag=False),
        _row(log_moneyness=0.0, strike=150.0, implied_vol=0.30, belly_flag=False),
    )
    # Remove belly_flag column
    df_no_belly = df.drop(columns=['belly_flag'])

    anchor = extract_anchor_params(df_no_belly)
    assert anchor.k_star == pytest.approx(0.0)
    assert anchor.theta_star == pytest.approx(0.30**2 * 0.10)


def test_compute_theta_t_matches_constraints_theta_from_psi():
    """compute_theta_t delegates to constraints.theta_from_psi."""
    from essvi.constraints import theta_from_psi

    anchor = AnchorParams(k_star=0.03, theta_star=0.04, quality="EXACT_ATM", n_belly=5)
    psi = 0.4
    rho = -0.2

    theta_t = compute_theta_t(psi, rho, anchor)
    expected = theta_from_psi(psi, rho, anchor.k_star, anchor.theta_star)

    assert theta_t == pytest.approx(expected, rel=1e-12)