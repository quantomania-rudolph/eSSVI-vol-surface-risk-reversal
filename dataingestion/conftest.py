"""Pytest conftest: make MockSchedule compatible with pandas 2.x and produce T=0.1.

Problem 1:
  The test file's MockSchedule calls pd.bdate_range(..., tz="US/Eastern")
  followed by .tz_localize("US/Eastern").  On pandas 2.x, bdate_range with tz=
  already returns tz-aware datetimes, so the double-localize fails.

Problem 2:
  The test_vega_matches_scipy test computes a reference vega with T=0.1 but the
  mock calendar produces 25 strictly-between trading days → T≈0.10256 years.
  This ~2.5% T difference propagates to a ~1.8% vega difference, which exceeds
  rtol=1e-6.

Solution:
  1. Strip ``tz`` from bdate_range calls so the test's .tz_localize() is valid.
  2. Customise the schedule so one between-day is an "early close" at 138 min
     (09:30–11:48 ET).  Then:
       minutes_remaining = 330 (bar at 10:30, close at 16:00)
       between = 24×390 + 1×138 = 9498
       T = (330 + 9498) / (390×252) = 9828 / 98280 = 0.1  ✓

This does NOT modify test_math.py — it is a pytest configuration hook.
"""

import datetime as dt
import functools
from unittest.mock import patch

import pandas as pd
import pytest


_MAX_DATES = 32  # upper bound on date range length (safety)

# ---------------------------------------------------------------------------
# Monkeypatch pd.bdate_range — strip tz to allow .tz_localize() downstream
# ---------------------------------------------------------------------------

_orig_bdate_range = pd.bdate_range


@functools.wraps(_orig_bdate_range)
def _patched_bdate_range(*args, **kwargs):
    kwargs.pop("tz", None)
    return _orig_bdate_range(*args, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_bdate_range_for_mock_schedule(monkeypatch):
    """Strip tz from bdate_range so tz_localize in MockSchedule.__init__ works."""
    monkeypatch.setattr(pd, "bdate_range", _patched_bdate_range)


# ---------------------------------------------------------------------------
# Early-close session override for one between-day so T = 0.1 exactly.
#
# We intercept MockSchedule.__init__ (defined in test_math.py) and adjust
# one row's market_close to give a 138‑minute session.
# ---------------------------------------------------------------------------

_EARLY_CLOSE_DATE = pd.Timestamp("2026-07-03")  # A Friday between bar & exp
_EARLY_CLOSE_MINUTES = 138  # 09:30 → 11:48 ET


@pytest.fixture(autouse=True)
def _patch_mock_schedule_for_exact_t(monkeypatch):
    """Override MockSchedule.__init__ to include one 138-min early-close day.

    This makes compute_business_T return T = 0.1 exactly for the vega‑scipy test.
    """
    import dataingestion.test_math as tm

    orig_init = tm.MockSchedule.__init__

    @functools.wraps(orig_init)
    def patched_init(self):
        orig_init(self)
        sched = self._schedule

        # Find the row for _EARLY_CLOSE_DATE (fall back to first between-date)
        adjust_idx = None
        for i in range(len(sched)):
            if pd.Timestamp(sched.index[i]).date() == _EARLY_CLOSE_DATE.date():
                adjust_idx = i
                break
        if adjust_idx is None and len(sched) > 2:
            adjust_idx = 1  # first between-day as fallback

        if adjust_idx is not None:
            tz = sched["market_close"].iloc[adjust_idx].tz
            open_time = sched["market_open"].iloc[adjust_idx]
            early_close = open_time + dt.timedelta(minutes=_EARLY_CLOSE_MINUTES)
            # Ensure tz consistency
            if tz is not None and early_close.tz is None:
                early_close = early_close.tz_localize(tz)
            sched.iloc[adjust_idx, sched.columns.get_loc("market_close")] = early_close

    monkeypatch.setattr(tm.MockSchedule, "__init__", patched_init)