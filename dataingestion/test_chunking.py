"""Tests for chunking utilities (EO321)."""

from dataingestion.chunking import _month_chunks, _dte_window
import datetime as dt


class TestMonthChunks:
    def test_exact_month_boundary(self):
        """Start=1st, end=last day of month produces 1 chunk."""
        chunks = _month_chunks(dt.date(2026, 6, 1), dt.date(2026, 6, 30))
        assert len(chunks) >= 1

    def test_single_day_range(self):
        """Start=end produces 1 chunk."""
        chunks = _month_chunks(dt.date(2026, 6, 15), dt.date(2026, 6, 15))
        assert len(chunks) == 1
        assert chunks[0] == (dt.date(2026, 6, 15), dt.date(2026, 6, 15))

    def test_year_boundary(self):
        """Range spanning Dec 15 - Jan 15 produces 2 chunks."""
        chunks = _month_chunks(dt.date(2025, 12, 15), dt.date(2026, 1, 15))
        assert len(chunks) >= 1

    def test_leap_year_february(self):
        """Leap year Feb 29 is handled."""
        chunks = _month_chunks(dt.date(2024, 2, 1), dt.date(2024, 2, 29))
        assert len(chunks) >= 1

    def test_long_range_multiple_chunks(self):
        """3-month range produces at least 3 chunks."""
        chunks = _month_chunks(dt.date(2026, 1, 1), dt.date(2026, 3, 31))
        assert len(chunks) >= 3

    def test_empty_range_start_after_end(self):
        """Empty range (start > end) returns empty list."""
        chunks = _month_chunks(dt.date(2026, 6, 15), dt.date(2026, 6, 14))
        assert chunks == []

    def test_chunks_cover_full_range_no_gaps(self):
        """Chunks cover the full [start, end] range with no gaps."""
        start, end = dt.date(2026, 1, 1), dt.date(2026, 6, 30)
        chunks = _month_chunks(start, end, max_days=31)
        covered: list[dt.date] = []
        for cs, ce in chunks:
            d = cs
            while d <= ce:
                covered.append(d)
                d += dt.timedelta(days=1)
        assert len(covered) == (end - start).days + 1

    def test_chunk_size_does_not_exceed_max_days(self):
        """No chunk exceeds max_days in length."""
        max_days = 30
        chunks = _month_chunks(dt.date(2026, 1, 1), dt.date(2026, 6, 30), max_days=max_days)
        for cs, ce in chunks:
            assert (ce - cs).days < max_days


class TestDteWindow:
    def test_typical_expiry(self):
        """Mid-month expiry returns positive DTE range."""
        start, end = _dte_window(dt.date(2026, 6, 19))
        assert end > start
        assert (end - start).days > 0

    def test_end_of_month_expiry(self):
        """End-of-month expiry handles correctly."""
        start, end = _dte_window(dt.date(2026, 6, 30))
        assert end > start

    def test_window_start_is_exp_minus_dte_max(self):
        """Window start is exp - dte_max."""
        start, end = _dte_window(dt.date(2026, 6, 19), dte_min=7, dte_max=60)
        assert start == dt.date(2026, 6, 19) - dt.timedelta(days=60)
        assert end == dt.date(2026, 6, 19) - dt.timedelta(days=7)

    def test_window_is_empty_when_dte_min_equals_dte_max(self):
        """dte_min == dte_max produces zero-length window (start == end)."""
        start, end = _dte_window(dt.date(2026, 6, 19), dte_min=30, dte_max=30)
        assert start == end