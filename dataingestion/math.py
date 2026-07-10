"""Pure computation module for option chain enrichment.

Computes business time T, forward prices (F = S * exp(r*T) for AMD with q=0),
and Black-76 vega via Numba JIT. No HTTP, no database, no cleaning logic.

Input:  Clean DataFrame from cleaning.py (COLUMNS.md Section II.A)
Output: Enriched DataFrame with business_t, forward_price, r, q, vega,
        log_moneyness columns (COLUMNS.md Section III).
"""

from __future__ import annotations

import datetime as dt
import logging
import math

import numpy as np
import pandas as pd
from numba import njit

from dataingestion import config as cfg

log = logging.getLogger("dataingestion.math")


# ---------------------------------------------------------------------------
# Numba kernel — Black-76 vega (forward convention)
# ---------------------------------------------------------------------------

@njit(fastmath=False, parallel=True)
def _vega_kernel(F_vec, K_vec, sigma_vec, T_vec, r_vec, sigma_eps, t_eps):
    """Vectorized vega computation. All inputs float64 arrays.

    Returns float64 array: element i is NaN for degenerate inputs
    (sigma <= sigma_eps, T <= t_eps, K <= 0, or F <= 0).

    Units: ∂Price/∂σ for a 1.00 vol move (σ in decimals, e.g. 0.25).
    """
    n = len(F_vec)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        F = F_vec[i]
        K = K_vec[i]
        sigma = sigma_vec[i]
        T = T_vec[i]
        r = r_vec[i]

        if sigma <= sigma_eps or T <= t_eps or K <= 0 or F <= 0:
            out[i] = np.nan
            continue

        sqrt_T = np.sqrt(T)
        d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_T)
        phi_d1 = np.exp(-0.5 * d1 * d1) / np.sqrt(2.0 * np.pi)
        out[i] = np.exp(-r * T) * F * phi_d1 * sqrt_T

    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assert_no_dividend_ex_dates(
    df: pd.DataFrame,
    underlying: str = "AMD",
) -> None:
    """Assert no dividend ex-dates fall in [bar_date, expiration] for *underlying*.

    For AMD: q is hardcoded to 0 because AMD has not paid dividends in the
    historical backfill window.  This function is a **documented no-op** for
    AMD — it logs a warning-level check that passes unconditionally.

    For other tickers (future use):
    1. Fetch dividend calendar (e.g. from ThetaData `list/dividends` endpoint).
    2. For each row, check whether any ex-date falls in
       [bar_date, expiration].  If yes, raise an AssertionError or log a
       critical warning — q=0 is invalid for that period.
    3. Compute an effective q from the dividend yield and pass it into the
       forward formula instead.

    Args:
        df: DataFrame with 'timestamp' (tz-aware UTC) and 'expiration' columns.
        underlying: Ticker symbol (default "AMD").

    Raises:
        AssertionError: If dividends are detected and q=0 assertion fails
            (only raised when dividend checking is actively enabled for this
            underlying — currently a no-op for AMD).
    """
    if underlying == "AMD":
        # AMD has no dividend history — assertion is a documented no-op.
        log.info(
            "Dividend assertion: no-op for AMD (underlying=%s, rows=%d). "
            "Enable dividend calendar fetch for other tickers.",
            underlying, len(df),
        )
        return

    # TODO: For non-AMD tickers, fetch dividend calendar and assert no
    # ex-dates in [bar_date, expiration] for each row.
    raise NotImplementedError(
        f"Dividend assertion not implemented for {underlying}. "
        f"Fetch dividend calendar and compute effective q."
    )


def compute_forward(
    df: pd.DataFrame,
    dividends_map: dict[dt.date, float] | None = None,
) -> pd.DataFrame:
    """Add columns ``forward_price``, ``r``, ``q``, ``log_moneyness``.

    Delegates to :func:`compute_forward_with_dividends`. For AMD with no
    dividend map, ``q`` is set to ``AMD_Q_OVERRIDE`` (0.0).
    """
    return compute_forward_with_dividends(df, dividends_map=dividends_map)


def _norm_cdf(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF without scipy."""
    vec_erf = np.vectorize(math.erf)
    return 0.5 * (1.0 + vec_erf(x / np.sqrt(2.0)))


def compute_delta_black76(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute Black-76 delta from local forward, IV, and business_t.

    Overwrites ``delta`` so belly-band filters use the same forward as F.
    """
    F = df["forward_price"].values.astype(np.float64)
    K = df["strike"].values.astype(np.float64)
    sigma = df["implied_vol"].values.astype(np.float64)
    T = df["business_t"].values.astype(np.float64)
    r = df["r"].values.astype(np.float64)
    is_call = (df["option_type"].values == "C")

    delta = np.full(len(df), np.nan, dtype=np.float64)
    valid = (sigma > cfg.NUMBA_SIGMA_EPS) & (T > cfg.NUMBA_T_EPS) & (K > 0) & (F > 0)
    if valid.any():
        sqrt_T = np.sqrt(T[valid])
        d1 = (np.log(F[valid] / K[valid]) + 0.5 * sigma[valid] ** 2 * T[valid]) / (
            sigma[valid] * sqrt_T
        )
        disc = np.exp(-r[valid] * T[valid])
        nd1 = _norm_cdf(d1)
        call_delta = disc * nd1
        put_delta = disc * (nd1 - 1.0)
        delta[valid] = np.where(is_call[valid], call_delta, put_delta)

    df["delta"] = delta
    return df


def compute_vega(
    df: pd.DataFrame,
    mode: str | None = None,
) -> pd.DataFrame:
    """Add column ``vega`` using Black-76 forward convention (Numba JIT).

    ``mode`` controls the stored weight (default ``cfg.VEGA_WEIGHT_MODE``):
      - ``vol`` / ``vol_vega1``: ∂Price/∂σ (Black-76 vega)
      - ``var_vega1``: variance-space vega ν_var = ν_vol / (2σ√T)
      - ``var_vega2``: (ν_var)² — eSSVI objective weight (recommended)
    """
    weight_mode = mode or cfg.VEGA_WEIGHT_MODE

    F_vec = df["forward_price"].values.astype(np.float64)
    K_vec = df["strike"].values.astype(np.float64)
    sigma_vec = df["implied_vol"].values.astype(np.float64)
    T_vec = df["business_t"].values.astype(np.float64)
    r_vec = df["r"].values.astype(np.float64)

    nu_vol = _vega_kernel(
        F_vec, K_vec, sigma_vec, T_vec, r_vec,
        cfg.NUMBA_SIGMA_EPS, cfg.NUMBA_T_EPS,
    )

    if weight_mode in ("vol", "vol_vega1"):
        vega_out = nu_vol
    elif weight_mode == "vol_vega2":
        vega_out = nu_vol ** 2
    elif weight_mode in ("var_vega1", "var_vega2"):
        nu_var = np.full_like(nu_vol, np.nan)
        valid = (
            ~np.isnan(nu_vol)
            & (sigma_vec > cfg.NUMBA_SIGMA_EPS)
            & (T_vec > cfg.NUMBA_T_EPS)
        )
        denom = 2.0 * sigma_vec[valid] * np.sqrt(T_vec[valid])
        nu_var[valid] = nu_vol[valid] / denom
        vega_out = nu_var if weight_mode == "var_vega1" else nu_var ** 2
    else:
        raise ValueError(f"Unknown vega mode: {weight_mode}")

    df["vega"] = vega_out
    df["_phase"] = "math"
    return df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _session_minutes(session):
    """Return session length in minutes for a (market_open, market_close) pair."""
    return (session[1] - session[0]).total_seconds() / 60.0


def _get_tz(cal):
    """Extract the timezone from the calendar object."""
    tz = getattr(cal, "tz", None)
    if tz is not None:
        return tz

    # Fallback: try to infer from the schedule itself.
    import pytz

    sample = cal.schedule(start_date="2026-01-01", end_date="2026-01-10")
    if len(sample) > 0:
        tz_info = sample["market_open"].iloc[0].tz
        if tz_info is not None:
            return tz_info

    return pytz.timezone("US/Eastern")


def _to_eastern(timestamps, tz_eastern):
    """Convert a timestamp series to US/Eastern timezone for session math."""
    try:
        return pd.DatetimeIndex(timestamps).tz_convert(tz_eastern)
    except TypeError:
        # Timestamps may be tz-naive; assume UTC and localize.
        return (
            pd.DatetimeIndex(timestamps).tz_localize("UTC").tz_convert(tz_eastern)
        )


# ---------------------------------------------------------------------------
# Schedule cache for O(1) business time lookups
# ---------------------------------------------------------------------------

def _build_business_time_schedule(cal, start_date: pd.Timestamp, end_date: pd.Timestamp) -> dict:
    """
    Build prefix-sum schedule cache for O(1) business time computation.
    
    Args:
        cal: pandas_market_calendars calendar object
        start_date: Start date for schedule (inclusive)
        end_date: End date for schedule (inclusive)
        
    Returns:
        dict with keys:
        - 'prefix_minutes': dict[date] -> cumulative minutes from start_date to date (exclusive)
        - 'session_minutes': dict[date] -> session minutes for that date
        - 'tz': timezone (US/Eastern)
    """
    # Get schedule for the full range
    schedule = cal.schedule(
        start_date=start_date - pd.Timedelta(days=5),
        end_date=end_date + pd.Timedelta(days=5),
    )
    
    if len(schedule) == 0:
        # Empty schedule - return empty caches
        return {
            'prefix_minutes': {},
            'session_minutes': {},
            'tz': _get_tz(cal),
        }
    
    # Extract timezone
    tz = schedule.index.tz if schedule.index.tz is not None else _get_tz(cal)
    
    # Build session minutes dict and open/close time caches
    session_minutes = {}
    open_times = {}
    close_times = {}
    for i in range(len(schedule)):
        d = schedule.index[i].date()
        open_t = schedule["market_open"].iloc[i]
        close_t = schedule["market_close"].iloc[i]
        session_minutes[d] = (close_t - open_t).total_seconds() / 60.0
        open_times[d] = open_t
        close_times[d] = close_t
        expected = {cfg.REGULAR_SESSION_MINUTES, cfg.HALF_DAY_SESSION_MINUTES}
        if round(session_minutes[d]) not in expected:
            log.warning(
                "Unexpected session length %s min on %s (expected 390 or 210)",
                round(session_minutes[d]), d,
            )
    
    # Build prefix sum: cumulative minutes up to (but not including) each date
    sorted_dates = sorted(session_minutes.keys())
    prefix_minutes = {}
    cumulative = 0.0
    for d in sorted_dates:
        prefix_minutes[d] = cumulative
        cumulative += session_minutes[d]
    
    # Add one past the end for easy lookups
    if sorted_dates:
        cumulative = prefix_minutes.get(sorted_dates[-1], 0) + session_minutes[sorted_dates[-1]]
        # Find next trading day after the last date in the schedule.
        # Use a robust while loop (no artificial iteration cap) to handle
        # any gap length (e.g. long holiday weekends, market closures).
        # Fall back to date + N days if no trading day found within
        # MAX_GAP_DAYS days (avoids infinite loop with mock calendars).
        MAX_GAP_DAYS = 60
        next_date = sorted_dates[-1] + pd.Timedelta(days=1)
        gap_days = 0
        while next_date not in session_minutes and gap_days < MAX_GAP_DAYS:
            next_date += pd.Timedelta(days=1)
            gap_days += 1
        if next_date in session_minutes:
            prefix_minutes[next_date] = cumulative
    
    return {
        'prefix_minutes': prefix_minutes,
        'session_minutes': session_minutes,
        'open_times': open_times,
        'close_times': close_times,
        'tz': tz,
    }


def compute_business_T(
    df: pd.DataFrame,
    cal,
    schedule_cache: dict | None = None,
) -> pd.DataFrame:
    """Add column ``business_t`` in years per dataingestion.md Section 6.

    Formula::

        T_years = (minutes_remaining_today + sum_of_session_minutes_between)
                  / (390 * 252)

    Uses the ``cal`` schedule (pandas_market_calendars XNYS style) to determine
    exact session lengths including half-days (210 min) and holidays (0 min).

    Args:
        df: Clean DataFrame containing ``timestamp`` (tz-aware) and ``expiration``.
        cal: An object with a ``schedule(start_date, end_date)`` method that
             returns a DataFrame with ``market_open`` / ``market_close`` columns
             indexed by trading days.  Also needs a ``tz`` attribute or the
             schedule columns must already be tz-aware in US/Eastern.
        schedule_cache: Pre-built cache from _build_business_time_schedule for O(1) lookups.
                        If not provided, will be built on-demand.

    Returns:
        Same DataFrame reference with ``business_t`` column added (float64, in years).
    """
    timestamps = df["timestamp"]
    expirations = df["expiration"]

    # Build or use provided schedule cache
    if schedule_cache is None:
        min_ts_date = timestamps.min().date()
        max_exp = (
            pd.Timestamp(expirations.max()).date()
            if len(expirations) > 0
            else min_ts_date
        )
        schedule_cache = _build_business_time_schedule(cal, min_ts_date, max_exp)

    prefix_minutes = schedule_cache['prefix_minutes']
    session_minutes = schedule_cache['session_minutes']
    open_times = schedule_cache.get('open_times', {})
    close_times = schedule_cache.get('close_times', {})
    tz_eastern = schedule_cache['tz']

    # Pre-compute the last prefix value and date for fast fallback lookups
    # (avoids per-row sorted(prefix_minutes.keys()) call — Medium #42)
    sorted_prefix_dates = sorted(prefix_minutes.keys())
    last_prefix_date = sorted_prefix_dates[-1] if sorted_prefix_dates else None
    last_prefix_value = prefix_minutes.get(last_prefix_date, 0) if last_prefix_date else 0

    # Convert timestamps to Eastern for session math
    ts_eastern = _to_eastern(timestamps, tz_eastern)

    business_t_arr = np.empty(len(df), dtype=np.float64)

    for i in range(len(df)):
        bar_ts = ts_eastern[i]
        bar_date = bar_ts.date()
        exp_date = pd.Timestamp(expirations.iloc[i]).date()

        # Minutes remaining today
        minutes_remaining = 0.0
        session_today = session_minutes.get(bar_date)
        if session_today is not None and bar_date in open_times:
            open_t = open_times[bar_date]
            close_t = close_times[bar_date]
            if open_t <= bar_ts < close_t:
                # Bar is within RTH — count remaining minutes
                minutes_remaining = max(0.0, (close_t - bar_ts).total_seconds() / 60.0)
            # else: bar is outside RTH (pre-open or after-close) → minutes_remaining = 0

        # Sum session minutes for trading days strictly between bar_date and exp_date
        # Using prefix sums: prefix[exp_date] - prefix[bar_date] - session[bar_date]
        # prefix_minutes[exp_date] already counts minutes up to (not including) exp day
        prefix_exp = prefix_minutes.get(exp_date, last_prefix_value if last_prefix_date != exp_date else 0)
        prefix_bar = prefix_minutes.get(bar_date, 0)
        session_bar = session_minutes.get(bar_date, 0)

        between_minutes = max(0.0, prefix_exp - prefix_bar - session_bar)

        business_t_arr[i] = (minutes_remaining + between_minutes) / (390.0 * 252.0)

    df["business_t"] = business_t_arr
    return df


# ---------------------------------------------------------------------------
# Session tagging
# ---------------------------------------------------------------------------

def tag_session_phase(
    df: pd.DataFrame,
    cal,
    schedule_cache: dict | None = None,
) -> pd.DataFrame:
    """
    Tag each row with session phase for no-trade window handling.
    
    Phases:
    - 'pre_open': before market open
    - 'rth': regular trading hours (after open window, before close window)
    - 'no_trade_open': first NO_TRADE_OPEN_MIN minutes after open
    - 'no_trade_close': last NO_TRADE_CLOSE_MIN minutes before close
    - 'post_close': after market close
    - 'half_day_no_trade': half-day session entirely in no-trade window
    
    Plan §14: "First hour 09:30-10:30 and last hour 15:00-16:00... 
    engine still calibrates but tags rows no_trade=True... half-days auto-shift."
    """
    timestamps = df["timestamp"]
    
    # Build or use provided schedule cache
    if schedule_cache is None:
        min_ts_date = timestamps.min().date()
        max_exp = (
            pd.Timestamp(df["expiration"].max()).date()
            if len(df) > 0
            else min_ts_date
        )
        schedule_cache = _build_business_time_schedule(cal, min_ts_date, max_exp)
    
    open_times = schedule_cache.get('open_times', {})
    close_times = schedule_cache.get('close_times', {})
    session_minutes = schedule_cache.get('session_minutes', {})
    tz_eastern = schedule_cache['tz']
    
    ts_eastern = _to_eastern(timestamps, tz_eastern)
    
    session_phase = []
    
    for i in range(len(df)):
        bar_ts = ts_eastern[i]
        bar_date = bar_ts.date()
        
        session_today = session_minutes.get(bar_date)
        if session_today is None:
            # Non-trading day
            session_phase.append('non_trading_day')
            continue
        
        open_t = open_times.get(bar_date)
        close_t = close_times.get(bar_date)
        
        if open_t is None or close_t is None:
            session_phase.append('unknown')
            continue
        
        # Total session minutes
        total_mins = session_today
        
        # Half-day handling: if session is half-day (210 min), the whole session
        # is flagged as no-trade if it falls within the no-trade windows
        is_half_day = abs(total_mins - cfg.HALF_DAY_SESSION_MINUTES) < 1.0
        
        # Minutes from open
        if bar_ts < open_t:
            session_phase.append('pre_open')
            continue
        elif bar_ts >= close_t:
            session_phase.append('post_close')
            continue
        
        # Bar is within RTH
        mins_from_open = (bar_ts - open_t).total_seconds() / 60.0
        mins_to_close = (close_t - bar_ts).total_seconds() / 60.0
        
        # Check no-trade windows
        in_open_window = mins_from_open < cfg.NO_TRADE_OPEN_MIN
        in_close_window = mins_to_close < cfg.NO_TRADE_CLOSE_MIN
        
        if is_half_day:
            # On half-days, if the entire session is within no-trade windows,
            # flag as half_day_no_trade
            if total_mins <= cfg.NO_TRADE_OPEN_MIN + cfg.NO_TRADE_CLOSE_MIN:
                session_phase.append('half_day_no_trade')
            elif in_open_window:
                session_phase.append('no_trade_open')
            elif in_close_window:
                session_phase.append('no_trade_close')
            else:
                session_phase.append('rth')
        else:
            if in_open_window:
                session_phase.append('no_trade_open')
            elif in_close_window:
                session_phase.append('no_trade_close')
            else:
                session_phase.append('rth')
    
    df["session_phase"] = session_phase
    return df


# ---------------------------------------------------------------------------
# Dividend-aware forward computation
# ---------------------------------------------------------------------------

def compute_forward_with_dividends(
    df: pd.DataFrame,
    dividends_map: dict[dt.date, float] | None = None,
) -> pd.DataFrame:
    """
    Add columns forward_price, r, q, log_moneyness with dividend yield support.
    
    For AMD: q = AMD_Q_OVERRIDE (0.0).
    For other symbols: uses dividends_map[bar_date] if provided, else 0.0.
    
    Args:
        df: DataFrame with spot_close, r, business_t, strike, timestamp, expiration
        dividends_map: Optional dict mapping bar_date -> q (continuous yield)
    
    Returns:
        DataFrame with forward_price, q, r, log_moneyness added/updated
    """
    r = df["r"].values.astype(np.float64)
    spot = df["spot_close"].values.astype(np.float64)
    T = df["business_t"].values.astype(np.float64)
    
    # Get q for each row
    if dividends_map is not None:
        # Bar dates from timestamp
        ts_eastern = _to_eastern(df["timestamp"], df["timestamp"].dt.tz if df["timestamp"].dt.tz is not None else 'US/Eastern')
        bar_dates = [ts.date() for ts in ts_eastern]
        q = np.array([dividends_map.get(d, 0.0) for d in bar_dates], dtype=np.float64)
    else:
        q = np.zeros(len(df), dtype=np.float64)
    
    # AMD override
    if "underlying" in df.columns:
        amd_mask = df["underlying"].str.upper() == "AMD"
        q[amd_mask] = cfg.AMD_Q_OVERRIDE
    
    forward = spot * np.exp((r - q) * T)
    
    df["forward_price"] = forward.astype(np.float64)
    df["q"] = q
    df["r"] = r
    
    # log_moneyness = ln(strike / forward)
    with np.errstate(divide="ignore", invalid="ignore"):
        log_m = np.log(df["strike"].values.astype(np.float64) / forward)
    df["log_moneyness"] = log_m.astype(np.float64)

    return df