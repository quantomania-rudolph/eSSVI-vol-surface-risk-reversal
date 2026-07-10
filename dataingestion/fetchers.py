"""HTTP fetchers for Theta Terminal v3. The ONLY module that speaks HTTP to Theta.

Each function returns a DataFrame (or list for expirations). No filtering,
no cleaning, no disk/DB I/O — just raw fetch, column contract, and return.

All functions have async variants (prefixed with `async_`) for use in
async pipelines. The sync wrappers are preserved for backward compatibility.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math

import aiohttp
import pandas as pd

import dataingestion.config as cfg
from core_engine.shared.constants import normalize_right
from core_engine.shared.parse import to_dataframe
from core_engine.shared.theta_client import AsyncThetaClient

log = logging.getLogger("dataingestion.fetchers")


async def async_validate_theta_port(
    client: AsyncThetaClient,
    host: str = "127.0.0.1",
    port: int = 25510,
) -> bool:
    """
    Validate Theta Terminal port by fetching heartbeat and checking actual port.
    
    Args:
        client: AsyncThetaClient instance
        host: Theta Terminal host
        port: Configured Theta Terminal port
        
    Returns:
        True if validation passes (port matches), False otherwise
    """
    try:
        hb = await heartbeat(client, timeout=5.0)
        actual_port = hb.get("websocket_port") or hb.get("port")
        if actual_port is not None:
            if str(actual_port) != str(port):
                log.warning(
                    "Theta port mismatch: configured=%s, terminal reports=%s",
                    port, actual_port
                )
                return False
            log.info("Theta port validation OK: %s", actual_port)
            return True
        else:
            log.warning("Theta heartbeat missing port field: %s", hb)
            return False
    except Exception as e:
        log.error("Theta port validation failed: %s", e)
        return False


def _run_async(coro):
    """Run a coroutine safely, handling both running and missing event loops."""
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _fmt(date_val: dt.date) -> str:
    return date_val.strftime("%Y%m%d")


# ============================================================================
# ASYNC VARIANTS (primary API for async pipelines)
# ============================================================================

async def async_fetch_option_greeks_first_order(
    client: AsyncThetaClient,
    symbol: str,
    expiration: dt.date,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Async variant: fetch option greeks (first order) from Theta v3."""
    status, payload = await client.get(
        "/v3/option/history/greeks/first_order",
        {
            "symbol": symbol,
            "expiration": _fmt(expiration),
            "strike": "*",
            "right": "both",
            "interval": cfg.THETA_INTERVAL,
            "start_date": _fmt(start_date),
            "end_date": _fmt(end_date),
            "annual_dividend": cfg.THETA_ANNUAL_DIVIDEND,
            "rate_type": cfg.THETA_RATE_TYPE,
            "version": cfg.THETA_VERSION,
            "format": cfg.THETA_FORMAT,
        },
        ticker=symbol,
    )
    if status != 200:
        return pd.DataFrame()

    df = to_dataframe(payload)
    if df.empty:
        return df

    # --- normalize greeks columns ---
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.floor("min")
    df["option_type"] = df["right"].map(normalize_right)
    df = df.drop(columns=["right"])

    df["underlying"] = symbol.upper()
    df["expiration"] = pd.Timestamp(expiration)

    if "vega" not in df.columns:
        log.error("Missing 'vega' column in greeks response — cannot rename to vega_api")
        return pd.DataFrame()
    df = df.rename(columns={"vega": "vega_api"})

    if "underlying_timestamp" in df.columns:
        df["underlying_timestamp"] = pd.to_datetime(
            df["underlying_timestamp"], utc=True
        )

    # --- join OHLC spot_close ---
    df["spot_close"] = float("nan")

    # --- open_interest placeholder ---
    df["open_interest"] = pd.NA
    df["open_interest"] = df["open_interest"].astype("Int64")

    # --- select and order output columns ---
    output_cols = [
        "timestamp",
        "underlying",
        "expiration",
        "strike",
        "option_type",
        "bid",
        "ask",
        "delta",
        "theta",
        "vega_api",
        "rho",
        "implied_vol",
        "iv_error",
        "underlying_price",
        "underlying_timestamp",
        "spot_close",
        "open_interest",
    ]
    # keep only columns that exist
    present = [c for c in output_cols if c in df.columns]
    df = df[present]

    df["_phase"] = "raw"
    return df


async def async_fetch_stock_ohlc(
    client: AsyncThetaClient,
    symbol: str,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Async variant: fetch stock OHLC from Theta v3."""
    status, payload = await client.get(
        "/v3/stock/history/ohlc",
        {
            "symbol": symbol,
            "interval": "1m",
            "start_date": _fmt(start_date),
            "end_date": _fmt(end_date),
            "format": "ndjson",
        },
        ticker=symbol,
    )
    if status != 200:
        return pd.DataFrame()

    df = to_dataframe(payload)
    if df.empty:
        return df

    keep = ["timestamp", "open", "high", "low", "close", "volume"]
    present = [c for c in keep if c in df.columns]
    return df[present]


async def async_fetch_interest_rate_eod(
    client: AsyncThetaClient,
    symbol: str,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Async variant: fetch interest rate EOD from Theta v3.

    Theta returns ``rate`` as a percent (e.g. 4.50 = 4.5%).
    This function converts it to decimal (``rate / 100``, e.g. 0.045)
    so downstream consumers receive ready-to-use decimal form.
    
    If ``cfg.SIMPLE_TO_CC`` is True, converts simple money-market rates
    to continuously compounded rates using the appropriate tenor.
    """
    status, payload = await client.get(
        "/v3/interest_rate/history/eod",
        {
            "symbol": symbol,
            "start_date": _fmt(start_date),
            "end_date": _fmt(end_date),
            "format": "ndjson",
        },
        ticker=symbol,
    )
    if status != 200:
        return pd.DataFrame()

    df = to_dataframe(payload)
    if df.empty:
        return df

    keep = ["created", "rate"]
    present = [c for c in keep if c in df.columns]
    df = df[present]
    # Percent→decimal: 4.50 → 0.045
    df["rate"] = df["rate"].astype(float) / 100.0
    
    # Convert simple to continuously compounded if configured
    if cfg.SIMPLE_TO_CC:
        tenor_years = get_rate_tenor_years(symbol)
        df["rate"] = df["rate"].apply(lambda r: simple_to_cc_rate(r, tenor_years))
    
    return df


def simple_to_cc_rate(rate_simple: float, tenor_years: float) -> float:
    """
    Convert simple money-market rate to continuously compounded rate.
    
    Args:
        rate_simple: Simple rate (e.g., 0.05 for 5%)
        tenor_years: Tenor in years (e.g., 90/360 = 0.25 for 90-day T-bill)
    
    Returns:
        Continuously compounded rate: r_cc = ln(1 + r_simple * τ) / τ
    """
    if tenor_years <= 0:
        return rate_simple
    return math.log1p(rate_simple * tenor_years) / tenor_years


def get_rate_tenor_years(symbol: str) -> float:
    """Get the tenor in years for a rate symbol."""
    tenors = {
        "SOFR": 1.0 / 360.0,   # overnight money-market
        "TREASURY_M1": 1 / 12,  # 1 month ≈ 30/360
        "TREASURY_M3": 3 / 12,   # 3 months ≈ 90/360
        "TREASURY_M6": 6 / 12,  # 6 months
        "TREASURY_Y1": 1.0,     # 1 year
    }
    return tenors.get(symbol, 1.0)


async def async_fetch_option_open_interest(
    client: AsyncThetaClient,
    symbol: str,
    expiration: dt.date,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Async variant: fetch option open interest from Theta v3."""
    status, payload = await client.get(
        "/v3/option/history/open_interest",
        {
            "symbol": symbol,
            "expiration": _fmt(expiration),
            "start_date": _fmt(start_date),
            "end_date": _fmt(end_date),
            "format": "ndjson",
        },
        ticker=symbol,
    )
    if status != 200:
        return pd.DataFrame()

    df = to_dataframe(payload)
    if df.empty:
        return df

    keep = ["date", "open_interest"]
    present = [c for c in keep if c in df.columns]
    return df[present]


async def async_fetch_option_list_expirations(
    client: AsyncThetaClient,
    symbol: str,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
) -> list[dt.date]:
    """Async variant: fetch option expirations list from Theta v3.

    Optionally accepts start_date/end_date to filter the expiration list
    to a specific date range, reducing data transferred.
    """
    params: dict = {"symbol": symbol, "format": "ndjson"}
    if start_date is not None:
        params["start_date"] = _fmt(start_date)
    if end_date is not None:
        params["end_date"] = _fmt(end_date)
    status, payload = await client.get(
        "/v3/option/list/expirations",
        params,
        ticker=symbol,
    )
    if status != 200:
        return []

    df = to_dataframe(payload)
    if df.empty:
        return []

    dates: list[dt.date] = []
    for val in df.get("expiration", []):
        d = _parse_date(str(val))
        if d is not None:
            dates.append(d)

    return sorted(dates)


async def async_fetch_option_list_contracts(
    client: AsyncThetaClient,
    symbol: str,
    date: dt.date,
) -> pd.DataFrame:
    """Async variant: fetch option contracts list from Theta v3."""
    status, payload = await client.get(
        "/v3/option/list/contracts",
        {"symbol": symbol, "date": _fmt(date), "format": "ndjson"},
        ticker=symbol,
    )
    if status != 200:
        return pd.DataFrame()

    df = to_dataframe(payload)
    if df.empty:
        return df

    keep = ["strike", "right"]
    present = [c for c in keep if c in df.columns]
    return df[present]


# ============================================================================
# SYNC WRAPPERS (backward compatibility)
# ============================================================================

def fetch_option_greeks_first_order(
    client: AsyncThetaClient,
    symbol: str,
    expiration: dt.date,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Sync wrapper for backward compatibility."""
    return _run_async(async_fetch_option_greeks_first_order(
        client, symbol, expiration, start_date, end_date
    ))


def fetch_stock_ohlc(
    client: AsyncThetaClient,
    symbol: str,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Sync wrapper for backward compatibility."""
    return _run_async(async_fetch_stock_ohlc(
        client, symbol, start_date, end_date
    ))


def fetch_interest_rate_eod(
    client: AsyncThetaClient,
    symbol: str,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Sync wrapper for backward compatibility."""
    return _run_async(async_fetch_interest_rate_eod(
        client, symbol, start_date, end_date
    ))


def fetch_option_open_interest(
    client: AsyncThetaClient,
    symbol: str,
    expiration: dt.date,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Sync wrapper for backward compatibility."""
    return _run_async(async_fetch_option_open_interest(
        client, symbol, expiration, start_date, end_date
    ))


def fetch_option_list_expirations(
    client: AsyncThetaClient,
    symbol: str,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
) -> list[dt.date]:
    """Sync wrapper for backward compatibility."""
    return _run_async(async_fetch_option_list_expirations(
        client, symbol, start_date=start_date, end_date=end_date
    ))


def fetch_option_list_contracts(
    client: AsyncThetaClient,
    symbol: str,
    date: dt.date,
) -> pd.DataFrame:
    """Sync wrapper for backward compatibility."""
    return _run_async(async_fetch_option_list_contracts(
        client, symbol, date
    ))


def _parse_date(value: str) -> dt.date | None:
    """Parse a date string in YYYYMMDD or YYYY-MM-DD format."""
    s = value.strip()[:10]
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None