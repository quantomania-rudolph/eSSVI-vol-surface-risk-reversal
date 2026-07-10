"""Tests for essvi.anchor — anchor extraction (k*, theta*)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from essvi import config as cfg
from essvi.anchor import (
    belly_mask,
    compute_theta_star,
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
) -> dict:
    return {
        "strike": strike,
        "log_moneyness": log_moneyness,
        "rel_spread": rel_spread,
        "oi": oi,
        "delta_black76": delta_black76,
        "implied_vol": implied_vol,
        "business_t": business_t,
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


def test_compute_theta_star_exact():
    w_star = 0.04
    k_star = 0.02
    phi = 1.5
    rho = -0.3
    u = phi * k_star + rho
    d = u * u + (1.0 - rho * rho)
    expected = 2.0 * w_star / (1.0 + rho * phi * k_star + np.sqrt(d))
    assert compute_theta_star(w_star, k_star, phi, rho) == pytest.approx(expected)


def test_compute_theta_star_consistency():
    w_star = 0.05
    k_star = 0.01
    phi = 2.0
    rho = -0.4
    theta_star = compute_theta_star(w_star, k_star, phi, rho)
    w_recovered = eval_w(k_star, theta_star, rho, phi)
    assert w_recovered == pytest.approx(w_star, rel=0, abs=cfg.ANCHOR_THETA_TOL)


def test_extract_anchor_params_integration():
    df = _df(
        _row(log_moneyness=0.04, strike=155.0, implied_vol=0.28),
        _row(log_moneyness=0.0, strike=150.0, implied_vol=0.30),
    )
    phi = 1.2
    rho = -0.25
    result = extract_anchor_params(df, phi=phi, rho=rho)

    assert result["k_star"] == pytest.approx(0.0)
    assert result["w_star"] == pytest.approx(0.30**2 * 0.10)
    assert result["theta_star"] == pytest.approx(
        compute_theta_star(result["w_star"], result["k_star"], phi, rho)
    )
    assert result["belly_mask"].shape == (2,)
    assert result["n_belly"] == 2
    assert result["quality"] == "EXACT_ATM"


def test_theta_star_positive():
    cases = [
        (0.04, 0.0, 1.0, -0.2),
        (0.06, 0.05, 2.5, -0.5),
        (0.03, -0.03, 0.8, 0.1),
    ]
    for w_star, k_star, phi, rho in cases:
        theta_star = compute_theta_star(w_star, k_star, phi, rho)
        assert theta_star > 0


def test_anchor_quality_label():
    exact = _df(_row(log_moneyness=0.0))
    near = _df(_row(log_moneyness=0.05))
    widened = _df(_row(log_moneyness=0.02, rel_spread=0.12, oi=60))
    any_strike = _df(
        _row(log_moneyness=0.03, rel_spread=0.20, oi=10, delta_black76=0.02)
    )

    assert extract_anchor_params(exact, phi=1.0, rho=-0.2)["quality"] == "EXACT_ATM"
    assert extract_anchor_params(near, phi=1.0, rho=-0.2)["quality"] == "NEAREST_BELLY"
    assert extract_anchor_params(widened, phi=1.0, rho=-0.2)["quality"] == "WIDENED_GATES"
    assert extract_anchor_params(any_strike, phi=1.0, rho=-0.2)["quality"] == "NEAREST_ANY"
