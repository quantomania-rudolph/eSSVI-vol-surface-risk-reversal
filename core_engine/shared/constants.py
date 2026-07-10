"""Shared constants and timezone handles."""
from __future__ import annotations

import datetime as dt

import pytz

ET = pytz.timezone("America/New_York")
UTC = pytz.UTC

REGULAR_SECONDS = 6 * 3600 + 30 * 60
EARLY_SECONDS = 3 * 3600 + 30 * 60
TRADING_DAYS_PER_YEAR = 252
TRADING_SECONDS_PER_YEAR = TRADING_DAYS_PER_YEAR * REGULAR_SECONDS


def parse_expiration(value) -> dt.date | None:
    if value is None:
        return None
    s = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalize_right(value) -> str:
    s = str(value or "C").strip().lower()
    if s in ("c", "call"):
        return "C"
    if s in ("p", "put"):
        return "P"
    return "C" if s.startswith("c") else "P"
