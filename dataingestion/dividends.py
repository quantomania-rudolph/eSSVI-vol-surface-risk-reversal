"""Dividend data fetcher for point-in-time dividend yield computation.

Fetches dividend calendar from external providers (Alpha Vantage, Polygon)
and computes trailing 12-month dividend yield q for forward price calculation.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from dataclasses import dataclass
from typing import Optional

import aiohttp
import pandas as pd

import dataingestion.config as cfg

log = logging.getLogger("dataingestion.dividends")


@dataclass
class DividendEvent:
    """Single dividend event."""
    ex_date: dt.date
    cash_amount: float
    declared_date: dt.date
    record_date: Optional[dt.date] = None
    pay_date: Optional[dt.date] = None


class DividendFetcher:
    """Fetch dividend events from external providers."""
    
    def __init__(self, provider: str = "none"):
        self.provider = provider.lower()
        self._cache: dict[str, list[DividendEvent]] = {}
    
    async def fetch_dividends(self, symbol: str) -> list[DividendEvent]:
        """Fetch all dividend events for a symbol."""
        if self.provider == "none":
            return []
        
        if symbol in self._cache:
            return self._cache[symbol]
        
        if self.provider == "alphavantage":
            events = await self._fetch_alphavantage(symbol)
        elif self.provider == "polygon":
            events = await self._fetch_polygon(symbol)
        else:
            log.warning("Unknown dividend provider: %s", self.provider)
            return []
        
        self._cache[symbol] = events
        return events
    
    async def _fetch_alphavantage(self, symbol: str) -> list[DividendEvent]:
        """Fetch dividends from Alpha Vantage."""
        api_key = cfg.ALPHAVANTAGE_API_KEY
        if not api_key:
            log.warning("ALPHAVANTAGE_API_KEY not set, skipping dividend fetch")
            return []
        
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "DIVIDENDS",
            "symbol": symbol,
            "apikey": api_key,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=30) as resp:
                    if resp.status != 200:
                        log.error("Alpha Vantage error: %s", resp.status)
                        return []
                    data = await resp.json()
            
            # Alpha Vantage returns: {"data": [{"ex_dividend_date": "...", "amount": "...", "declaration_date": "...", ...}]}
            events = []
            for item in data.get("data", []):
                try:
                    ex_date = dt.datetime.strptime(item.get("ex_dividend_date", ""), "%Y-%m-%d").date()
                    declared = dt.datetime.strptime(item.get("declaration_date", ""), "%Y-%m-%d").date()
                    amount = float(item.get("amount", 0))
                    events.append(DividendEvent(
                        ex_date=ex_date,
                        cash_amount=amount,
                        declared_date=declared,
                    ))
                except (ValueError, KeyError) as e:
                    log.warning("Failed to parse dividend item: %s", e)
                    continue
            
            return events
        
        except Exception as e:
            log.error("Alpha Vantage fetch failed: %s", e)
            return []
    
    async def _fetch_polygon(self, symbol: str) -> list[DividendEvent]:
        """Fetch dividends from Polygon.io."""
        api_key = cfg.POLYGON_API_KEY
        if not api_key:
            log.warning("POLYGON_API_KEY not set, skipping dividend fetch")
            return []
        
        url = f"https://api.polygon.io/v3/reference/dividends"
        params = {
            "ticker": symbol,
            "limit": 1000,
            "apiKey": api_key,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=30) as resp:
                    if resp.status != 200:
                        log.error("Polygon error: %s", resp.status)
                        return []
                    data = await resp.json()
            
            events = []
            for item in data.get("results", []):
                try:
                    ex_date = dt.datetime.strptime(item.get("ex_dividend_date", ""), "%Y-%m-%d").date()
                    declared = dt.datetime.strptime(item.get("declaration_date", ""), "%Y-%m-%d").date()
                    amount = float(item.get("cash_amount", 0))
                    record = item.get("record_date")
                    pay = item.get("pay_date")
                    events.append(DividendEvent(
                        ex_date=ex_date,
                        cash_amount=amount,
                        declared_date=declared,
                        record_date=dt.datetime.strptime(record, "%Y-%m-%d").date() if record else None,
                        pay_date=dt.datetime.strptime(pay, "%Y-%m-%d").date() if pay else None,
                    ))
                except (ValueError, KeyError) as e:
                    log.warning("Failed to parse Polygon dividend item: %s", e)
                    continue
            
            return events
        
        except Exception as e:
            log.error("Polygon fetch failed: %s", e)
            return []


def compute_dividend_yield(
    dividends: list[DividendEvent],
    spot_price: float,
    as_of_date: dt.date,
    lookback_days: int = 365,
) -> float:
    """
    Compute trailing dividend yield q as of a specific date.
    
    Point-in-time: only uses dividends with declaration_date <= as_of_date.
    Trailing window: sums cash amounts over lookback_days prior to as_of_date.
    
    Args:
        dividends: List of dividend events
        spot_price: Current spot price (for yield denominator)
        as_of_date: Date to compute yield as of (point-in-time cutoff)
        lookback_days: Lookback window in days (default 365)
    
    Returns:
        Continuous dividend yield q (decimal, e.g., 0.02 for 2%)
    """
    if spot_price <= 0 or not dividends:
        return 0.0
    
    cutoff = as_of_date - dt.timedelta(days=lookback_days)
    total_cash = 0.0
    
    for div in dividends:
        # Point-in-time: only use dividends declared on or before as_of_date
        if div.declared_date <= as_of_date:
            # Only count if ex-date falls in lookback window
            if cutoff <= div.ex_date <= as_of_date:
                total_cash += div.cash_amount
    
    # Simple yield: cash / spot. For continuous q in BS, this is an approximation.
    # More precise: q = -ln(1 - cash/spot) / T but T varies. Standard practice uses simple yield.
    return total_cash / spot_price


def get_dividend_yield_for_symbol(
    symbol: str,
    spot_price: float,
    as_of_date: dt.date,
    fetcher: Optional[DividendFetcher] = None,
) -> float:
    """
    Get dividend yield q for a symbol as of a specific date.
    
    For AMD: returns AMD_Q_OVERRIDE (0.0) directly.
    For other symbols: fetches dividends and computes trailing yield.
    """
    if symbol.upper() == "AMD":
        return cfg.AMD_Q_OVERRIDE
    
    if fetcher is None:
        fetcher = DividendFetcher(cfg.DIVIDEND_PROVIDER)
    
    # This is async in reality; for sync call we need to run the event loop
    try:
        loop = asyncio.get_running_loop()
        # Can't await in sync context; user must call async version
        raise RuntimeError("Use async_get_dividend_yield in async context")
    except RuntimeError:
        # No running loop, we can create one
        dividends = asyncio.run(fetcher.fetch_dividends(symbol))
        return compute_dividend_yield(dividends, spot_price, as_of_date)


async def async_get_dividend_yield_for_symbol(
    symbol: str,
    spot_price: float,
    as_of_date: dt.date,
    fetcher: Optional[DividendFetcher] = None,
) -> float:
    """Async version of get_dividend_yield_for_symbol."""
    if symbol.upper() == "AMD":
        return cfg.AMD_Q_OVERRIDE
    
    if fetcher is None:
        fetcher = DividendFetcher(cfg.DIVIDEND_PROVIDER)
    
    dividends = await fetcher.fetch_dividends(symbol)
    return compute_dividend_yield(dividends, spot_price, as_of_date)


# Module-level fetcher instance for reuse
_default_fetcher: Optional[DividendFetcher] = None

def get_dividend_fetcher() -> DividendFetcher:
    """Get or create the default dividend fetcher."""
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = DividendFetcher(cfg.DIVIDEND_PROVIDER)
    return _default_fetcher