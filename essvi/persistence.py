"""Persist calibrated eSSVI parameters and audit summaries (plan §17)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from essvi.audit import build_audit_grid, compute_durrleman_g
from essvi.objective import w_slice_derivatives

PARAMS_TABLE = "essvi_surface_params"
AUDIT_TABLE = "essvi_surface_audit"

PARAMS_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "dte",
    "theta",
    "phi",
    "rho",
    "psi",
    "anchor_k_star",
    "anchor_theta_star",
    "anchor_quality",
    "objective_value",
    "n_strikes",
    "n_belly",
    "is_valid",
    "quality_flag",
    "audit_butterfly_ok",
    "audit_calendar_ok",
    "audit_vertical_ok",
    "audit_lee_ok",
    "durrleman_g_min",
    "max_calendar_violation",
    "created_at",
)

AUDIT_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "surface_id",
    "calibrated",
    "n_slices",
    "n_valid",
    "n_invalid",
    "butterfly_violations",
    "calendar_violations",
    "vertical_violations",
    "lee_violations",
    "monotonicity_violations",
    "worst_severity",
    "kill_triggered",
    "session_phase",
    "cold_start",
    "computation_ms",
)


def _is_sqlite(conn: Any) -> bool:
    return isinstance(conn, sqlite3.Connection)


def _now_default_sql(conn: Any) -> str:
    if _is_sqlite(conn):
        return "DEFAULT CURRENT_TIMESTAMP"
    return "DEFAULT NOW()"


def _bool_type(conn: Any) -> str:
    return "INTEGER" if _is_sqlite(conn) else "BOOLEAN"


def _execute(conn: Any, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
    if params is None:
        params = ()
    if _is_sqlite(conn):
        return conn.execute(sql, params)
    cursor = conn.cursor()
    cursor.execute(sql, params)
    return cursor


def _commit(conn: Any) -> None:
    if hasattr(conn, "commit"):
        conn.commit()


def _fetchall(conn: Any, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[Any]:
    cur = _execute(conn, sql, params)
    return cur.fetchall()


def _fetchone(conn: Any, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
    cur = _execute(conn, sql, params)
    return cur.fetchone()


def _table_exists(conn: Any, table: str) -> bool:
    if _is_sqlite(conn):
        row = _fetchone(
            conn,
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        )
        return row is not None
    row = _fetchone(
        conn,
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    )
    return row is not None


def _try_create_hypertable(conn: Any, table: str) -> None:
    sql = f"SELECT create_hypertable('{table}', 'timestamp', if_not_exists => TRUE)"
    try:
        _execute(conn, sql)
        _commit(conn)
    except Exception:
        if _is_sqlite(conn):
            return
        try:
            conn.rollback()
        except Exception:
            pass


def _params_ddl(conn: Any) -> str:
    bool_t = _bool_type(conn)
    ts_type = "TEXT" if _is_sqlite(conn) else "TIMESTAMPTZ"
    dbl = "REAL" if _is_sqlite(conn) else "DOUBLE PRECISION"
    now_def = _now_default_sql(conn)
    return f"""
        CREATE TABLE IF NOT EXISTS {PARAMS_TABLE} (
            timestamp       {ts_type} NOT NULL,
            dte             INTEGER NOT NULL,
            theta           {dbl} NOT NULL,
            phi             {dbl} NOT NULL,
            rho             {dbl} NOT NULL,
            psi             {dbl} NOT NULL,
            anchor_k_star   {dbl},
            anchor_theta_star {dbl},
            anchor_quality  TEXT,
            objective_value {dbl},
            n_strikes       INTEGER,
            n_belly         INTEGER,
            is_valid        {bool_t} NOT NULL DEFAULT 1,
            quality_flag    TEXT,
            audit_butterfly_ok      {bool_t},
            audit_calendar_ok       {bool_t},
            audit_vertical_ok       {bool_t},
            audit_lee_ok            {bool_t},
            durrleman_g_min         {dbl},
            max_calendar_violation  {dbl},
            created_at      {ts_type} NOT NULL {now_def},
            PRIMARY KEY (timestamp, dte)
        )
    """


def _audit_ddl(conn: Any) -> str:
    bool_t = _bool_type(conn)
    ts_type = "TEXT" if _is_sqlite(conn) else "TIMESTAMPTZ"
    dbl = "REAL" if _is_sqlite(conn) else "DOUBLE PRECISION"
    return f"""
        CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
            timestamp       {ts_type} NOT NULL,
            surface_id      TEXT NOT NULL,
            calibrated      {bool_t} NOT NULL,
            n_slices        INTEGER,
            n_valid         INTEGER,
            n_invalid       INTEGER,
            butterfly_violations    INTEGER DEFAULT 0,
            calendar_violations     INTEGER DEFAULT 0,
            vertical_violations     INTEGER DEFAULT 0,
            lee_violations          INTEGER DEFAULT 0,
            monotonicity_violations INTEGER DEFAULT 0,
            worst_severity  {dbl},
            kill_triggered  {bool_t} NOT NULL,
            session_phase   TEXT,
            cold_start      {bool_t},
            computation_ms  {dbl},
            PRIMARY KEY (timestamp)
        )
    """


def init_schema(conn: Any) -> None:
    """Create params and audit tables if missing; idempotent."""
    _execute(conn, _params_ddl(conn))
    _execute(conn, _audit_ddl(conn))
    _try_create_hypertable(conn, PARAMS_TABLE)
    _try_create_hypertable(conn, AUDIT_TABLE)

    if _is_sqlite(conn):
        _execute(
            conn,
            f"""
            CREATE INDEX IF NOT EXISTS idx_essvi_params_dte
                ON {PARAMS_TABLE} (dte, timestamp DESC)
            """,
        )
        _execute(
            conn,
            f"""
            CREATE INDEX IF NOT EXISTS idx_essvi_params_valid
                ON {PARAMS_TABLE} (timestamp, is_valid)
                WHERE is_valid = 1
            """,
        )
    else:
        _execute(
            conn,
            f"""
            CREATE INDEX IF NOT EXISTS idx_essvi_params_dte
                ON {PARAMS_TABLE} (dte, timestamp DESC)
            """,
        )
        _execute(
            conn,
            f"""
            CREATE INDEX IF NOT EXISTS idx_essvi_params_valid
                ON {PARAMS_TABLE} (timestamp, is_valid)
                WHERE is_valid = TRUE
            """,
        )
    _commit(conn)


def _format_ts(ts: pd.Timestamp, conn: Any) -> str | datetime:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    if _is_sqlite(conn):
        return stamp.isoformat()
    return stamp.to_pydatetime()


def _parse_ts(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def _to_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return bool(int(value))


def _slice_t_from_dte(dte: int) -> float:
    return float(dte) / 252.0


def _t_matches(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(float(a) - float(b)) <= tol


def _durrleman_g_min(theta: float, phi: float, rho: float, k_grid: np.ndarray) -> float:
    w, wp, wpp = w_slice_derivatives(k_grid, theta, phi, rho)
    g = compute_durrleman_g(k_grid, w, wp, wpp)
    finite = g[np.isfinite(g)]
    if finite.size == 0:
        return float("nan")
    return float(np.min(finite))


def _per_slice_audit(
    slice_row: dict[str, Any],
    audit_report: dict[str, Any],
    k_grid: np.ndarray,
) -> dict[str, Any]:
    t_val = _slice_t_from_dte(int(slice_row["dte"]))
    butterfly = audit_report.get("butterfly", [])
    calendar = audit_report.get("calendar", [])
    vertical = audit_report.get("vertical_spread", audit_report.get("slope", []))
    lee = audit_report.get("lee", [])

    bf_hits = [v for v in butterfly if _t_matches(v["T"], t_val)]
    cal_hits = [
        v
        for v in calendar
        if _t_matches(v["T_near"], t_val) or _t_matches(v["T_far"], t_val)
    ]
    vert_hits = [v for v in vertical if _t_matches(v["T"], t_val)]
    lee_hits = [v for v in lee if _t_matches(v["T"], t_val)]

    max_cal = max((float(v["severity"]) for v in cal_hits), default=None)

    theta = float(slice_row["theta"])
    phi = float(slice_row["phi"])
    rho = float(slice_row["rho"])

    return {
        "audit_butterfly_ok": len(bf_hits) == 0,
        "audit_calendar_ok": len(cal_hits) == 0,
        "audit_vertical_ok": len(vert_hits) == 0,
        "audit_lee_ok": len(lee_hits) == 0,
        "durrleman_g_min": _durrleman_g_min(theta, phi, rho, k_grid),
        "max_calendar_violation": max_cal,
    }


def _anchor_quality(slice_row: dict[str, Any]) -> str | None:
    if "anchor_quality" in slice_row and slice_row["anchor_quality"] is not None:
        return str(slice_row["anchor_quality"])
    return None


def _bool_db(value: bool | None, conn: Any) -> int | bool | None:
    if value is None:
        return None
    if _is_sqlite(conn):
        return 1 if value else 0
    return bool(value)


def _placeholder(conn: Any) -> str:
    return "?" if _is_sqlite(conn) else "%s"


def insert_minute_params(
    conn: Any,
    minute_result: dict[str, Any],
    audit_report: dict[str, Any],
    surface_id: str,
) -> int:
    """Insert all slices for one minute; upsert on (timestamp, dte)."""
    _ = surface_id
    ts = _format_ts(minute_result["timestamp"], conn)
    slices = list(minute_result.get("slices", []))
    if not slices:
        return 0

    k_grid = audit_report.get("k_grid")
    if k_grid is None:
        k_grid = build_audit_grid()

    ph = _placeholder(conn)
    update_cols = [
        "theta",
        "phi",
        "rho",
        "psi",
        "anchor_k_star",
        "anchor_theta_star",
        "anchor_quality",
        "objective_value",
        "n_strikes",
        "n_belly",
        "is_valid",
        "quality_flag",
        "audit_butterfly_ok",
        "audit_calendar_ok",
        "audit_vertical_ok",
        "audit_lee_ok",
        "durrleman_g_min",
        "max_calendar_violation",
    ]
    update_clause = ", ".join(f"{col} = excluded.{col}" for col in update_cols)

    sql = f"""
        INSERT INTO {PARAMS_TABLE} (
            timestamp, dte, theta, phi, rho, psi,
            anchor_k_star, anchor_theta_star, anchor_quality,
            objective_value, n_strikes, n_belly, is_valid, quality_flag,
            audit_butterfly_ok, audit_calendar_ok, audit_vertical_ok, audit_lee_ok,
            durrleman_g_min, max_calendar_violation
        ) VALUES (
            {ph}, {ph}, {ph}, {ph}, {ph}, {ph},
            {ph}, {ph}, {ph},
            {ph}, {ph}, {ph}, {ph}, {ph},
            {ph}, {ph}, {ph}, {ph},
            {ph}, {ph}
        )
        ON CONFLICT (timestamp, dte) DO UPDATE SET
            {update_clause}
    """

    count = 0
    for sl in slices:
        audit_fields = _per_slice_audit(sl, audit_report, np.asarray(k_grid, dtype=float))
        anchor_k = sl.get("anchor_k_star")
        anchor_theta = sl.get("anchor_theta_star")
        params = (
            ts,
            int(sl["dte"]),
            float(sl["theta"]),
            float(sl["phi"]),
            float(sl["rho"]),
            float(sl["psi"]),
            None if anchor_k is None or (isinstance(anchor_k, float) and np.isnan(anchor_k)) else float(anchor_k),
            None
            if anchor_theta is None or (isinstance(anchor_theta, float) and np.isnan(anchor_theta))
            else float(anchor_theta),
            _anchor_quality(sl),
            float(sl.get("objective_value", float("inf"))),
            int(sl.get("n_strikes", 0)),
            int(sl.get("n_belly", 0)),
            _bool_db(bool(sl.get("is_valid", False)), conn),
            sl.get("quality_flag"),
            _bool_db(audit_fields["audit_butterfly_ok"], conn),
            _bool_db(audit_fields["audit_calendar_ok"], conn),
            _bool_db(audit_fields["audit_vertical_ok"], conn),
            _bool_db(audit_fields["audit_lee_ok"], conn),
            audit_fields["durrleman_g_min"],
            audit_fields["max_calendar_violation"],
        )
        _execute(conn, sql, params)
        count += 1

    _commit(conn)
    return count


def insert_audit_report(conn: Any, audit_report: dict[str, Any], surface_id: str) -> int:
    """Insert one audit summary row; upsert on timestamp."""
    ts = _format_ts(audit_report["timestamp"], conn)
    kill = audit_report.get("kill_switch", {})

    n_slices = int(audit_report.get("n_slices", 0))
    n_valid = int(audit_report.get("n_valid", n_slices))
    n_invalid = int(audit_report.get("n_invalid", max(n_slices - n_valid, 0)))

    ph = _placeholder(conn)
    sql = f"""
        INSERT INTO {AUDIT_TABLE} (
            timestamp, surface_id, calibrated,
            n_slices, n_valid, n_invalid,
            butterfly_violations, calendar_violations, vertical_violations,
            lee_violations, monotonicity_violations,
            worst_severity, kill_triggered,
            session_phase, cold_start, computation_ms
        ) VALUES (
            {ph}, {ph}, {ph},
            {ph}, {ph}, {ph},
            {ph}, {ph}, {ph},
            {ph}, {ph},
            {ph}, {ph},
            {ph}, {ph}, {ph}
        )
        ON CONFLICT (timestamp) DO UPDATE SET
            surface_id = excluded.surface_id,
            calibrated = excluded.calibrated,
            n_slices = excluded.n_slices,
            n_valid = excluded.n_valid,
            n_invalid = excluded.n_invalid,
            butterfly_violations = excluded.butterfly_violations,
            calendar_violations = excluded.calendar_violations,
            vertical_violations = excluded.vertical_violations,
            lee_violations = excluded.lee_violations,
            monotonicity_violations = excluded.monotonicity_violations,
            worst_severity = excluded.worst_severity,
            kill_triggered = excluded.kill_triggered,
            session_phase = excluded.session_phase,
            cold_start = excluded.cold_start,
            computation_ms = excluded.computation_ms
    """

    params = (
        ts,
        surface_id,
        _bool_db(bool(audit_report.get("calibrated", True)), conn),
        n_slices,
        n_valid,
        n_invalid,
        len(audit_report.get("butterfly", [])),
        len(audit_report.get("calendar", [])),
        len(audit_report.get("vertical_spread", audit_report.get("slope", []))),
        len(audit_report.get("lee", [])),
        len(audit_report.get("monotonicity", [])),
        float(kill.get("worst_severity", audit_report.get("worst_severity", 0.0))),
        _bool_db(bool(kill.get("kill_triggered", audit_report.get("kill_triggered", False))), conn),
        audit_report.get("session_phase"),
        _bool_db(audit_report.get("cold_start"), conn) if audit_report.get("cold_start") is not None else None,
        audit_report.get("computation_ms"),
    )
    _execute(conn, sql, params)
    _commit(conn)
    return 1


def _rows_to_dataframe(rows: list[Any], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _fetch_params_rows(conn: Any, sql: str, params: tuple[Any, ...]) -> pd.DataFrame:
    cur = _execute(conn, sql, params)
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=list(PARAMS_COLUMNS))
    cols = [d[0] for d in cur.description]
    return _rows_to_dataframe(rows, cols)


def _slice_from_row(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, pd.Series):
        data = row.to_dict()
    else:
        data = dict(row)
    dte = int(data["dte"])
    return {
        "dte": dte,
        "T": _slice_t_from_dte(dte),
        "rho": float(data["rho"]),
        "theta": float(data["theta"]),
        "phi": float(data["phi"]),
        "psi": float(data["psi"]),
        "anchor_k_star": data.get("anchor_k_star"),
        "anchor_theta_star": data.get("anchor_theta_star"),
        "anchor_quality": data.get("anchor_quality"),
        "objective_value": data.get("objective_value"),
        "is_valid": _to_bool(data.get("is_valid")),
        "n_strikes": int(data.get("n_strikes") or 0),
        "n_belly": int(data.get("n_belly") or 0),
        "quality_flag": data.get("quality_flag"),
        "violations": [],
    }


def _minute_from_slices(ts: pd.Timestamp, slices: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(slices, key=lambda sl: int(sl["dte"]))
    rho_grid = np.asarray([float(sl["rho"]) for sl in ordered], dtype=float)
    theta_grid = np.asarray([float(sl["theta"]) for sl in ordered], dtype=float)
    psi_grid = np.asarray([float(sl["psi"]) for sl in ordered], dtype=float)
    n_valid = sum(1 for sl in ordered if sl.get("is_valid"))
    return {
        "timestamp": pd.Timestamp(ts),
        "slices": ordered,
        "rho_grid": rho_grid,
        "theta_grid": theta_grid,
        "psi_grid": psi_grid,
        "n_slices": len(ordered),
        "n_valid": n_valid,
        "any_invalid": n_valid < len(ordered),
        "is_total_kill": len(ordered) > 0 and n_valid == 0,
    }


def load_prior_minute_params(conn: Any, timestamp: pd.Timestamp) -> dict[str, Any] | None:
    """Most recent valid calibration strictly before *timestamp*."""
    ph = _placeholder(conn)
    valid_flag = 1 if _is_sqlite(conn) else True
    row = _fetchone(
        conn,
        f"""
        SELECT MAX(timestamp) AS ts
        FROM {PARAMS_TABLE}
        WHERE timestamp < {ph} AND is_valid = {ph}
        """,
        (_format_ts(timestamp, conn), valid_flag),
    )
    if row is None or row[0] is None:
        return None

    prior_ts = _parse_ts(row[0])
    df = _fetch_params_rows(
        conn,
        f"""
        SELECT * FROM {PARAMS_TABLE}
        WHERE timestamp = {ph} AND is_valid = {ph}
        ORDER BY dte
        """,
        (_format_ts(prior_ts, conn), valid_flag),
    )
    if df.empty:
        return None

    slices = [_slice_from_row(df.iloc[i]) for i in range(len(df))]
    return _minute_from_slices(prior_ts, slices)


def load_surface_by_timestamp(conn: Any, timestamp: pd.Timestamp) -> dict[str, Any]:
    """Load all slices for one timestamp (surface evaluation format)."""
    ph = _placeholder(conn)
    df = _fetch_params_rows(
        conn,
        f"SELECT * FROM {PARAMS_TABLE} WHERE timestamp = {ph} ORDER BY dte",
        (_format_ts(timestamp, conn),),
    )
    slices = [_slice_from_row(df.iloc[i]) for i in range(len(df))]
    return {
        "timestamp": pd.Timestamp(timestamp),
        "slices": slices,
        "n_slices": len(slices),
    }


def load_surface_history(
    conn: Any,
    start: pd.Timestamp,
    end: pd.Timestamp,
    dte: int | None = None,
) -> pd.DataFrame:
    """Historical params between *start* and *end*, optionally filtered by DTE."""
    ph = _placeholder(conn)
    params: list[Any] = [_format_ts(start, conn), _format_ts(end, conn)]
    dte_clause = ""
    if dte is not None:
        dte_clause = f" AND dte = {ph}"
        params.append(int(dte))

    return _fetch_params_rows(
        conn,
        f"""
        SELECT * FROM {PARAMS_TABLE}
        WHERE timestamp >= {ph} AND timestamp <= {ph}{dte_clause}
        ORDER BY timestamp, dte
        """,
        tuple(params),
    )
