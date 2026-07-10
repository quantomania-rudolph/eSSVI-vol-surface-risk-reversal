"""Minute-level calibration runtime loop (plan §14, §16)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import pandas as pd

from essvi import audit
from essvi import config as cfg
from essvi import loader
from essvi import sequential

logger = logging.getLogger(__name__)

_ET = "America/New_York"

_SESSION_PHASES = frozenset({"pre_open", "rth", "no_trade_window", "post_close"})


@dataclass
class RuntimeState:
    """Mutable state tracking across calibration cycles."""

    last_minute_params: dict[str, Any] | None = None
    last_calibration_time: pd.Timestamp | None = None
    last_surface_id: str | None = None
    last_audit_passed: bool = True
    stale_surface: bool = False
    cold_start: bool = True
    minute_count: int = 0
    total_calibrations: int = 0
    total_failures: int = 0


def _to_eastern(timestamp: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(timestamp)
    if ts.tzinfo is None:
        return ts.tz_localize(_ET)
    return ts.tz_convert(_ET)


def _session_bounds_for_date(
    bar_date,
    calendar: Any | None,
) -> tuple[pd.Timestamp, pd.Timestamp, float | None]:
    """Return (open_et, close_et, session_minutes) for a calendar date."""
    if calendar is not None:
        schedule = calendar.schedule(start_date=bar_date, end_date=bar_date)
        if schedule.empty:
            return (
                pd.Timestamp(bar_date).tz_localize(_ET).replace(
                    hour=cfg.SESSION_OPEN_HOUR, minute=cfg.SESSION_OPEN_MIN
                ),
                pd.Timestamp(bar_date).tz_localize(_ET).replace(
                    hour=cfg.SESSION_CLOSE_HOUR, minute=cfg.SESSION_CLOSE_MIN
                ),
                float(cfg.REGULAR_SESSION_MINUTES),
            )
        open_utc = schedule["market_open"].iloc[0]
        close_utc = schedule["market_close"].iloc[0]
        open_et = pd.Timestamp(open_utc).tz_convert(_ET)
        close_et = pd.Timestamp(close_utc).tz_convert(_ET)
        session_mins = (close_et - open_et).total_seconds() / 60.0
        return open_et, close_et, session_mins

    open_et = pd.Timestamp(bar_date).tz_localize(_ET).replace(
        hour=cfg.SESSION_OPEN_HOUR,
        minute=cfg.SESSION_OPEN_MIN,
        second=0,
        microsecond=0,
    )
    close_et = pd.Timestamp(bar_date).tz_localize(_ET).replace(
        hour=cfg.SESSION_CLOSE_HOUR,
        minute=cfg.SESSION_CLOSE_MIN,
        second=0,
        microsecond=0,
    )
    return open_et, close_et, float(cfg.REGULAR_SESSION_MINUTES)


def get_session_phase(timestamp: pd.Timestamp, calendar: Any | None = None) -> str:
    """
    Determine pre_open, rth, no_trade_window, or post_close for *timestamp*.

    Uses exchange calendar when provided; otherwise config session hours (ET).
    """
    ts_et = _to_eastern(timestamp)
    bar_date = ts_et.date()
    open_et, close_et, session_mins = _session_bounds_for_date(bar_date, calendar)

    if ts_et >= close_et:
        return "post_close"
    if ts_et < open_et:
        return "pre_open"

    mins_from_open = (ts_et - open_et).total_seconds() / 60.0
    mins_to_close = (close_et - ts_et).total_seconds() / 60.0

    is_half_day = (
        session_mins is not None
        and abs(session_mins - cfg.HALF_DAY_SESSION_MINUTES) < 1.0
    )
    if is_half_day and session_mins is not None:
        if session_mins <= cfg.NO_TRADE_OPEN_MIN + cfg.NO_TRADE_CLOSE_MIN:
            return "no_trade_window"

    if mins_from_open < cfg.NO_TRADE_OPEN_MIN:
        return "pre_open"
    if mins_to_close < cfg.NO_TRADE_CLOSE_MIN:
        return "no_trade_window"
    return "rth"


def should_calibrate(timestamp: pd.Timestamp, session_phase: str) -> tuple[bool, str]:
    """Return (proceed, reason). Only ``rth`` proceeds with calibration."""
    _ = timestamp
    if session_phase == "rth":
        return True, "rth"
    if session_phase in _SESSION_PHASES:
        return False, session_phase
    return False, session_phase


def _count_valid_expiry_slices(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    if "slice_strike_count" in df.columns:
        by_exp = df.groupby("expiration", sort=False)["slice_strike_count"].max()
        return int((by_exp >= cfg.MIN_STRIKES_PER_SLICE).sum())
    counts = df.groupby("expiration", sort=False).size()
    return int((counts >= cfg.MIN_STRIKES_PER_SLICE).sum())


def _make_surface_id(timestamp: pd.Timestamp) -> str:
    ts = pd.Timestamp(timestamp)
    return f"essvi-{ts.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _params_summary(minute_params: dict[str, Any] | None) -> dict[str, Any]:
    if minute_params is None:
        return {}
    slices = minute_params.get("slices", [])
    return {
        "n_slices": minute_params.get("n_slices", len(slices)),
        "n_valid": minute_params.get("n_valid", 0),
        "any_invalid": minute_params.get("any_invalid", False),
        "rho_grid": minute_params.get("rho_grid"),
        "theta_grid": minute_params.get("theta_grid"),
        "psi_grid": minute_params.get("psi_grid"),
    }


def _kill_triggered(audit_report: dict[str, Any]) -> bool:
    kill = audit_report.get("kill_switch")
    if kill is not None:
        return bool(kill.get("kill_triggered", False))
    return bool(audit_report.get("kill_triggered", False))


def _prior_not_too_stale(state: RuntimeState, current_time: pd.Timestamp) -> bool:
    if state.last_calibration_time is None:
        return False
    elapsed = pd.Timestamp(current_time) - pd.Timestamp(state.last_calibration_time)
    return elapsed <= pd.Timedelta(minutes=cfg.STALE_SLICE_MAX_MINUTES)


def is_surface_stale(state: RuntimeState, current_time: pd.Timestamp) -> bool:
    """True if time since last successful calibration exceeds STALE_SLICE_MAX_MINUTES."""
    if state.last_calibration_time is None:
        return True
    elapsed = pd.Timestamp(current_time) - pd.Timestamp(state.last_calibration_time)
    return elapsed > pd.Timedelta(minutes=cfg.STALE_SLICE_MAX_MINUTES)


def calibrate_minute(
    timestamp: pd.Timestamp,
    conn: Any = None,
    state: RuntimeState | None = None,
) -> dict[str, Any]:
    """
    One-minute calibration cycle: load → calibrate → audit → state update.
    """
    ts = pd.Timestamp(timestamp)
    if state is None:
        state = RuntimeState()

    state.minute_count += 1
    session_phase = get_session_phase(ts)
    proceed, reason = should_calibrate(ts, session_phase)

    if not proceed:
        logger.info(
            "skip_calibration ts=%s phase=%s reason=%s minute_count=%d",
            ts,
            session_phase,
            reason,
            state.minute_count,
        )
        return {
            "calibrated": False,
            "reason": reason,
            "timestamp": ts,
            "session_phase": session_phase,
        }

    df = loader.load_minute(ts, conn)
    n_valid_slices = _count_valid_expiry_slices(df)
    if n_valid_slices < cfg.MIN_STRIKES_PER_SLICE:
        logger.info(
            "skip_calibration ts=%s reason=too_few_slices n_valid_slices=%d",
            ts,
            n_valid_slices,
        )
        return {
            "calibrated": False,
            "reason": "too_few_slices",
            "timestamp": ts,
            "session_phase": session_phase,
            "n_valid_slices": n_valid_slices,
        }

    use_cold_start = cfg.COLD_START_AT_SESSION_OPEN and state.cold_start
    prior = None if use_cold_start else state.last_minute_params

    calibration_result = sequential.calibrate_one_minute(
        df,
        prior,
        warmstart=True,
    )

    audit_report = audit.run_full_audit(calibration_result)
    kill = _kill_triggered(audit_report)
    n_slices = int(calibration_result.get("n_slices", 0))
    n_valid = int(calibration_result.get("n_valid", 0))
    any_invalid = bool(calibration_result.get("any_invalid", False))

    if use_cold_start:
        state.cold_start = False

    if kill:
        violations = audit_report.get("kill_switch", audit_report)
        logger.warning(
            "kill_switch ts=%s total_violations=%s worst_severity=%s",
            ts,
            violations.get("total_violations"),
            violations.get("worst_severity"),
        )
        if state.last_minute_params is not None and _prior_not_too_stale(state, ts):
            state.last_calibration_time = ts
            state.stale_surface = True
            state.last_audit_passed = False
            surface_id = state.last_surface_id or _make_surface_id(ts)
            logger.info(
                "reuse_prior_surface ts=%s surface_id=%s stale=True",
                ts,
                surface_id,
            )
            return {
                "calibrated": True,
                "reused_prior": True,
                "timestamp": ts,
                "session_phase": session_phase,
                "n_slices": n_slices,
                "n_valid": n_valid,
                "any_invalid": any_invalid,
                "surface_id": surface_id,
                "audit_passed": False,
                "params": _params_summary(state.last_minute_params),
                "violations": audit_report,
            }

        state.total_failures += 1
        state.last_audit_passed = False
        return {
            "calibrated": False,
            "reason": "kill_switch",
            "timestamp": ts,
            "session_phase": session_phase,
            "n_slices": n_slices,
            "n_valid": n_valid,
            "any_invalid": any_invalid,
            "audit_passed": False,
            "violations": audit_report,
        }

    state.last_minute_params = calibration_result
    state.last_calibration_time = ts
    state.stale_surface = False
    state.last_audit_passed = True
    state.total_calibrations += 1
    surface_id = _make_surface_id(ts)
    state.last_surface_id = surface_id

    logger.info(
        "calibration_ok ts=%s surface_id=%s n_slices=%d n_valid=%d",
        ts,
        surface_id,
        n_slices,
        n_valid,
    )

    return {
        "calibrated": True,
        "timestamp": ts,
        "session_phase": session_phase,
        "n_slices": n_slices,
        "n_valid": n_valid,
        "any_invalid": any_invalid,
        "surface_id": surface_id,
        "audit_passed": True,
        "params": _params_summary(calibration_result),
    }


def calibrate_batch(
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    freq: str = "1min",
    conn: Any = None,
    state: RuntimeState | None = None,
) -> list[dict[str, Any]]:
    """Bulk calibration over a time range (backtesting driver)."""
    if state is None:
        state = RuntimeState()
    start = pd.Timestamp(start_time)
    end = pd.Timestamp(end_time)
    results: list[dict[str, Any]] = []
    for ts in pd.date_range(start, end, freq=freq):
        results.append(calibrate_minute(ts, conn=conn, state=state))
    return results


def get_runtime_summary(state: RuntimeState) -> str:
    """Human-readable summary of runtime state."""
    last_ts = (
        state.last_calibration_time.isoformat()
        if state.last_calibration_time is not None
        else "none"
    )
    lines = [
        "eSSVI Runtime Summary",
        "=" * 40,
        f"minute_count: {state.minute_count}",
        f"total_calibrations: {state.total_calibrations}",
        f"total_failures: {state.total_failures}",
        f"last_calibration_time: {last_ts}",
        f"last_surface_id: {state.last_surface_id or 'none'}",
        f"last_audit_passed: {state.last_audit_passed}",
        f"stale_surface: {state.stale_surface}",
        f"cold_start: {state.cold_start}",
        f"has_prior: {state.last_minute_params is not None}",
    ]
    return "\n".join(lines)
