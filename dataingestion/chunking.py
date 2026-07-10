"""Date chunking utilities for backfill pipeline."""

from __future__ import annotations

import datetime as dt

from dataingestion.config import DTE_WINDOW_MAX, DTE_WINDOW_MIN, MAX_CHUNK_DAYS, MAX_TRADING_DAYS_PER_CHUNK


def _month_chunks(start: dt.date, end: dt.date, max_days: int = MAX_CHUNK_DAYS) -> list[tuple[dt.date, dt.date]]:
    """Split a date range into contiguous chunks of at most max_days.

    Each chunk is a half-open interval [start, end] suitable for use as
    API fetch bounds. Useful for breaking large backfill windows into
    manageable, cacheable pieces.

    The default max_days (31 calendar days) corresponds to approximately
    MAX_TRADING_DAYS_PER_CHUNK=21 trading days, which is the blueprint's
    intended "≤1 month" chunk size in terms of actual market days.

    Args:
        start: First date of the range (inclusive).
        end: Last date of the range (inclusive).
        max_days: Maximum number of calendar days per chunk
            (default from config).

    Returns:
        List of (chunk_start, chunk_end) tuples covering the full range.
        Each chunk_end - chunk_start < max_days. Returns empty list when
        start > end.
    """
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=max_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + dt.timedelta(days=1)
    return chunks


def _dte_window(exp: dt.date, dte_min: int = DTE_WINDOW_MIN, dte_max: int = DTE_WINDOW_MAX) -> tuple[dt.date, dt.date]:
    """Calculate the days-to-expiration (DTE) trading window for an option.

    Returns the date range [exp - dte_max, exp - dte_min] that defines
    when the option is actively traded for the backfill. Options outside
    this window are not processed.

    Args:
        exp: Option expiration date.
        dte_min: Minimum days before expiration to start trading
            (default from config).
        dte_max: Maximum days before expiration to include
            (default from config).

    Returns:
        Tuple of (window_start, window_end) as dates, where the window
        is [exp - dte_max, exp - dte_min].
    """
    start = exp - dt.timedelta(days=dte_max)
    end = exp - dt.timedelta(days=dte_min)
    return start, end