"""Tests for essvi.persistence — params/audit storage and warmstart loaders."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from essvi import audit
from essvi.persistence import (
    AUDIT_COLUMNS,
    PARAMS_COLUMNS,
    init_schema,
    insert_audit_report,
    insert_minute_params,
    load_prior_minute_params,
    load_surface_by_timestamp,
    load_surface_history,
)

TS1 = pd.Timestamp("2024-06-03 14:30:00", tz="UTC")
TS2 = pd.Timestamp("2024-06-03 14:31:00", tz="UTC")
TS3 = pd.Timestamp("2024-06-03 14:32:00", tz="UTC")


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_schema(connection)
    return connection


def _slice(
    dte: int,
    *,
    theta: float,
    phi: float,
    rho: float = -0.4,
    is_valid: bool = True,
    anchor_k_star: float | None = 0.0,
    anchor_theta_star: float | None = None,
    anchor_quality: str | None = "EXACT_ATM",
    quality_flag: str = "VALID",
) -> dict:
    psi = theta * phi
    return {
        "dte": dte,
        "rho": rho,
        "theta": theta,
        "phi": phi,
        "psi": psi,
        "anchor_k_star": anchor_k_star,
        "anchor_theta_star": anchor_theta_star if anchor_theta_star is not None else theta,
        "anchor_quality": anchor_quality,
        "objective_value": 0.01,
        "is_valid": is_valid,
        "n_strikes": 12,
        "n_belly": 6,
        "quality_flag": quality_flag,
        "violations": [],
    }


def _minute_result(
    ts: pd.Timestamp,
    slices: list[dict],
    *,
    cold_start: bool = False,
) -> dict:
    ordered = sorted(slices, key=lambda sl: int(sl["dte"]))
    rho_grid = np.array([sl["rho"] for sl in ordered], dtype=float)
    theta_grid = np.array([sl["theta"] for sl in ordered], dtype=float)
    psi_grid = np.array([sl["psi"] for sl in ordered], dtype=float)
    n_valid = sum(1 for sl in ordered if sl["is_valid"])
    return {
        "timestamp": ts,
        "slices": ordered,
        "rho_grid": rho_grid,
        "theta_grid": theta_grid,
        "psi_grid": psi_grid,
        "n_slices": len(ordered),
        "n_valid": n_valid,
        "any_invalid": n_valid < len(ordered),
        "is_total_kill": len(ordered) > 0 and n_valid == 0,
        "cold_start": cold_start,
    }


def _three_slice_minute(ts: pd.Timestamp = TS1) -> dict:
    slices = [
        _slice(7, theta=0.06, phi=0.9),
        _slice(30, theta=0.08, phi=0.675),
        _slice(60, theta=0.10, phi=0.54),
    ]
    return _minute_result(ts, slices)


def _audit_for(minute_result: dict) -> dict:
    report = audit.run_full_audit(minute_result)
    report["n_valid"] = minute_result["n_valid"]
    report["n_invalid"] = minute_result["n_slices"] - minute_result["n_valid"]
    report["calibrated"] = True
    report["session_phase"] = "rth"
    report["cold_start"] = minute_result.get("cold_start", False)
    report["computation_ms"] = 42.5
    return report


def test_init_schema_creates_tables(conn: sqlite3.Connection):
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='essvi_surface_params'"
    ).fetchone() is not None
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='essvi_surface_audit'"
    ).fetchone() is not None


def test_init_schema_idempotent(conn: sqlite3.Connection):
    init_schema(conn)
    init_schema(conn)


def test_insert_minute_params(conn: sqlite3.Connection):
    minute = _three_slice_minute()
    report = _audit_for(minute)
    n = insert_minute_params(conn, minute, report, "surf-1")
    assert n == 3
    count = conn.execute("SELECT COUNT(*) FROM essvi_surface_params").fetchone()[0]
    assert count == 3


def test_insert_minute_params_idempotent(conn: sqlite3.Connection):
    minute = _three_slice_minute()
    report = _audit_for(minute)
    insert_minute_params(conn, minute, report, "surf-1")

    minute["slices"][0]["theta"] = 0.07
    minute["slices"][0]["psi"] = minute["slices"][0]["theta"] * minute["slices"][0]["phi"]
    minute["theta_grid"] = np.array([sl["theta"] for sl in minute["slices"]])
    minute["psi_grid"] = np.array([sl["psi"] for sl in minute["slices"]])
    report2 = _audit_for(minute)

    n = insert_minute_params(conn, minute, report2, "surf-1")
    assert n == 3
    count = conn.execute("SELECT COUNT(*) FROM essvi_surface_params").fetchone()[0]
    assert count == 3
    theta7 = conn.execute(
        "SELECT theta FROM essvi_surface_params WHERE dte = 7"
    ).fetchone()[0]
    assert theta7 == pytest.approx(0.07)


def test_insert_and_load_roundtrip(conn: sqlite3.Connection):
    minute = _three_slice_minute()
    report = _audit_for(minute)
    insert_minute_params(conn, minute, report, "surf-rt")

    loaded = load_surface_by_timestamp(conn, TS1)
    assert loaded["n_slices"] == 3
    for orig, got in zip(minute["slices"], loaded["slices"], strict=True):
        assert got["dte"] == orig["dte"]
        assert got["theta"] == pytest.approx(orig["theta"])
        assert got["phi"] == pytest.approx(orig["phi"])
        assert got["rho"] == pytest.approx(orig["rho"])
        assert got["psi"] == pytest.approx(orig["psi"])


def test_load_prior_minute_params_returns_most_recent(conn: sqlite3.Connection):
    m1 = _three_slice_minute(TS1)
    m2 = _three_slice_minute(TS2)
    m2["slices"][0]["theta"] = 0.065
    m2["slices"][0]["psi"] = m2["slices"][0]["theta"] * m2["slices"][0]["phi"]
    m2["theta_grid"] = np.array([sl["theta"] for sl in m2["slices"]])

    insert_minute_params(conn, m1, _audit_for(m1), "s1")
    insert_minute_params(conn, m2, _audit_for(m2), "s2")

    prior = load_prior_minute_params(conn, TS3)
    assert prior is not None
    assert pd.Timestamp(prior["timestamp"]) == TS2
    assert prior["slices"][0]["theta"] == pytest.approx(0.065)


def test_load_prior_minute_params_none_when_no_valid(conn: sqlite3.Connection):
    invalid = _minute_result(
        TS1,
        [
            _slice(7, theta=0.06, phi=0.9, is_valid=False),
            _slice(30, theta=0.08, phi=0.675, is_valid=False),
        ],
    )
    insert_minute_params(conn, invalid, _audit_for(invalid), "bad")
    assert load_prior_minute_params(conn, TS2) is None


def test_load_prior_minute_params_filters_invalid(conn: sqlite3.Connection):
    valid = _three_slice_minute(TS1)
    invalid = _minute_result(
        TS2,
        [
            _slice(7, theta=0.06, phi=0.9, is_valid=False),
            _slice(30, theta=0.08, phi=0.675, is_valid=False),
        ],
    )
    insert_minute_params(conn, valid, _audit_for(valid), "ok")
    insert_minute_params(conn, invalid, _audit_for(invalid), "bad")

    prior = load_prior_minute_params(conn, TS3)
    assert prior is not None
    assert pd.Timestamp(prior["timestamp"]) == TS1


def test_insert_audit_report(conn: sqlite3.Connection):
    minute = _three_slice_minute()
    report = _audit_for(minute)
    surface_id = "essvi-20240603T143000-deadbeef"
    assert insert_audit_report(conn, report, surface_id) == 1

    row = conn.execute(
        "SELECT * FROM essvi_surface_audit WHERE timestamp = ?",
        (TS1.isoformat(),),
    ).fetchone()
    assert row is not None
    assert row["surface_id"] == surface_id
    assert row["n_slices"] == 3
    assert row["n_valid"] == 3
    assert row["session_phase"] == "rth"
    assert row["computation_ms"] == pytest.approx(42.5)
    assert row["kill_triggered"] == 0


def test_load_surface_by_timestamp(conn: sqlite3.Connection):
    minute = _three_slice_minute()
    insert_minute_params(conn, minute, _audit_for(minute), "surf")

    surface = load_surface_by_timestamp(conn, TS1)
    assert surface["timestamp"] == TS1
    assert len(surface["slices"]) == 3
    assert all("T" in sl for sl in surface["slices"])
    assert surface["slices"][0]["T"] == pytest.approx(7 / 252.0)


def test_load_surface_history_time_range(conn: sqlite3.Connection):
    insert_minute_params(conn, _three_slice_minute(TS1), _audit_for(_three_slice_minute(TS1)), "a")
    insert_minute_params(conn, _three_slice_minute(TS2), _audit_for(_three_slice_minute(TS2)), "b")
    insert_minute_params(conn, _three_slice_minute(TS3), _audit_for(_three_slice_minute(TS3)), "c")

    hist = load_surface_history(conn, TS1, TS2)
    timestamps = sorted(hist["timestamp"].unique())
    assert len(timestamps) == 2
    assert TS1.isoformat() in timestamps
    assert TS2.isoformat() in timestamps
    assert TS3.isoformat() not in timestamps


def test_load_surface_history_dte_filter(conn: sqlite3.Connection):
    minute = _three_slice_minute(TS1)
    insert_minute_params(conn, minute, _audit_for(minute), "surf")

    hist = load_surface_history(conn, TS1, TS1, dte=30)
    assert len(hist) == 1
    assert int(hist.iloc[0]["dte"]) == 30


def test_schema_has_all_required_columns(conn: sqlite3.Connection):
    params_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(essvi_surface_params)").fetchall()
    }
    audit_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(essvi_surface_audit)").fetchall()
    }
    for col in PARAMS_COLUMNS:
        assert col in params_cols, f"missing params column {col}"
    for col in AUDIT_COLUMNS:
        assert col in audit_cols, f"missing audit column {col}"


def test_null_anchor_on_fallback(conn: sqlite3.Connection):
    minute = _minute_result(
        TS1,
        [
            {
                "dte": 7,
                "rho": -0.4,
                "theta": 0.06,
                "phi": 0.9,
                "psi": 0.054,
                "anchor_k_star": None,
                "anchor_theta_star": None,
                "anchor_quality": None,
                "objective_value": float("inf"),
                "is_valid": False,
                "n_strikes": 3,
                "n_belly": 1,
                "quality_flag": "DEGENERATE",
                "violations": [("DEGENERATE", "empty corridor — copied prior params")],
            },
        ],
    )
    insert_minute_params(conn, minute, _audit_for(minute), "fallback")

    row = conn.execute(
        "SELECT anchor_k_star, anchor_theta_star, anchor_quality FROM essvi_surface_params"
    ).fetchone()
    assert row["anchor_k_star"] is None
    assert row["anchor_theta_star"] is None
    assert row["anchor_quality"] is None


def test_durrleman_g_stored(conn: sqlite3.Connection):
    minute = _three_slice_minute()
    report = _audit_for(minute)
    insert_minute_params(conn, minute, report, "surf")

    rows = conn.execute(
        "SELECT dte, durrleman_g_min FROM essvi_surface_params ORDER BY dte"
    ).fetchall()
    assert len(rows) == 3
    for row in rows:
        assert row["durrleman_g_min"] is not None
        assert np.isfinite(row["durrleman_g_min"])
