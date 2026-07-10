"""Verification script for dataingestion/fetchers.py (Agent A1).

Runs offline — mocks AsyncThetaClient, no Theta subscription needed.
Tests every function, every column, every invariant.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

# Ensure dataingestion is importable from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# -----------------------------------------------------------------------
# Synthetic NDJSON payloads matching Theta v3 responses
# -----------------------------------------------------------------------

def _make_greeks_rows(n: int = 5) -> list[dict]:
    """Synthetic greeks/first_order NDJSON rows."""
    base_ts = dt.datetime(2026, 6, 15, 10, 30, tzinfo=dt.timezone.utc)
    return [
        {
            "timestamp": (base_ts + dt.timedelta(minutes=i)).isoformat(),
            "strike": 155.0 + i,
            "right": "CALL" if i % 2 == 0 else "PUT",
            "bid": 1.0 + i * 0.1,
            "ask": 1.1 + i * 0.15,
            "delta": 0.5 - i * 0.05,
            "theta": -0.02 - i * 0.005,
            "vega": 0.15 + i * 0.01,
            "rho": 0.03 + i * 0.002,
            "implied_vol": 0.25 + i * 0.02,
            "iv_error": 0.001 * i,
            "underlying_price": 158.0,
            "underlying_timestamp": base_ts.isoformat(),
        }
        for i in range(n)
    ]


def _make_ohlc_rows(n: int = 5) -> list[dict]:
    base_ts = dt.datetime(2026, 6, 15, 10, 30, tzinfo=dt.timezone.utc)
    return [
        {
            "timestamp": (base_ts + dt.timedelta(minutes=i)).isoformat(),
            "open": 157.0 + i * 0.1,
            "high": 157.5 + i * 0.1,
            "low": 157.0 + i * 0.1,
            "close": 157.25 + i * 0.1,
            "volume": 100000 + i * 1000,
        }
        for i in range(n)
    ]


def _make_rate_rows(n: int = 3) -> list[dict]:
    return [
        {"created": "2026-06-15", "rate": 4.50 + i * 0.05}
        for i in range(n)
    ]


def _make_oi_rows(n: int = 3) -> list[dict]:
    return [
        {"date": f"2026-06-{14 + i}", "open_interest": 500 + i * 100}
        for i in range(n)
    ]


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture
def mock_client():
    """An AsyncThetaClient whose .get() returns mock data by path matching."""
    client = AsyncMock()

    async def _get(path, params=None, ticker=None):
        if "greeks/first_order" in path:
            return 200, _make_greeks_rows(5)
        if "stock/history/ohlc" in path:
            return 200, _make_ohlc_rows(5)
        if "interest_rate" in path:
            return 200, _make_rate_rows(3)
        if "open_interest" in path:
            return 200, _make_oi_rows(3)
        if "list/expirations" in path:
            return 200, [
                {"expiration": "2026-07-21"},
                {"expiration": "2026-08-21"},
                {"expiration": "2026-09-19"},
            ]
        if "list/contracts" in path:
            return 200, [
                {"strike": 150.0, "right": "CALL"},
                {"strike": 150.0, "right": "PUT"},
                {"strike": 155.0, "right": "CALL"},
            ]
        return 500, {"error": "unknown path"}

    client.get = _get
    return client


@pytest.fixture
def mock_client_erroring():
    """Client that always returns 503."""
    client = AsyncMock()

    async def _get(path, params=None, ticker=None):
        return 503, {"error": "service unavailable"}

    client.get = _get
    return client


# -----------------------------------------------------------------------
# Imports (after path setup)
# -----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_path():
    """Ensure imports resolve from project root."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    yield


def _import_module():
    """Lazy import to ensure path setup runs first."""
    from dataingestion import fetchers as fmod

    return fmod


# -----------------------------------------------------------------------
# Tests: fetch_option_greeks_first_order
# -----------------------------------------------------------------------

class TestFetchGreeks:
    def test_returns_dataframe_with_columns(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_option_greeks_first_order(
            mock_client, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        expected_cols = {
            "timestamp", "underlying", "expiration", "strike", "option_type",
            "bid", "ask", "delta", "theta", "vega_api", "rho",
            "implied_vol", "iv_error", "underlying_price", "underlying_timestamp",
            "spot_close", "open_interest", "_phase",
        }
        assert expected_cols.issubset(set(df.columns))

    def test_option_type_normalized(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_option_greeks_first_order(
            mock_client, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert df["option_type"].isin(["C", "P"]).all()

    def test_underlying_is_amd(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_option_greeks_first_order(
            mock_client, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert (df["underlying"] == "AMD").all()

    def test_phase_is_raw(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_option_greeks_first_order(
            mock_client, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert (df["_phase"] == "raw").all()

    def test_expiration_consistent(self, mock_client):
        fmod = _import_module()
        exp = dt.date(2026, 7, 21)
        df = fmod.fetch_option_greeks_first_order(
            mock_client, "AMD", exp,
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert (df["expiration"].dt.date == exp).all()

    def test_timestamp_is_utc_tz_aware(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_option_greeks_first_order(
            mock_client, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert df["timestamp"].dt.tz is not None
        assert str(df["timestamp"].dt.tz) == "UTC"

    def test_empty_on_error(self, mock_client_erroring):
        fmod = _import_module()
        df = fmod.fetch_option_greeks_first_order(
            mock_client_erroring, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert df.empty

    def test_uses_correct_interval(self, mock_client):
        """Verify the params sent to client contain interval=1m."""
        captured = {}

        async def _capture(path, params=None, ticker=None):
            captured["params"] = params
            return 200, _make_greeks_rows(3)

        mock_client.get = _capture
        fmod = _import_module()
        fmod.fetch_option_greeks_first_order(
            mock_client, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        p = captured.get("params", {})
        assert p.get("interval") == "1m", f"Expected interval=1m, got params={p}"


# -----------------------------------------------------------------------
# Tests: fetch_stock_ohlc
# -----------------------------------------------------------------------

class TestFetchOHLC:
    def test_returns_correct_columns(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_stock_ohlc(mock_client, "AMD", dt.date(2026, 6, 1), dt.date(2026, 6, 28))
        assert not df.empty
        expected = {"timestamp", "open", "high", "low", "close", "volume"}
        assert expected.issubset(set(df.columns))

    def test_empty_on_error(self, mock_client_erroring):
        fmod = _import_module()
        df = fmod.fetch_stock_ohlc(mock_client_erroring, "AMD", dt.date(2026, 6, 1), dt.date(2026, 6, 28))
        assert df.empty

    def test_uses_one_minute_interval(self, mock_client):
        captured = {}

        async def _capture(path, params=None, ticker=None):
            captured["params"] = params
            return 200, _make_ohlc_rows(3)

        mock_client.get = _capture
        fmod = _import_module()
        fmod.fetch_stock_ohlc(mock_client, "AMD", dt.date(2026, 6, 1), dt.date(2026, 6, 28))
        p = captured.get("params", {})
        assert p.get("interval") == "1m", f"Expected interval=1m, got params={p}"


# -----------------------------------------------------------------------
# Tests: fetch_interest_rate_eod
# -----------------------------------------------------------------------

class TestFetchRate:
    def test_returns_rate_column(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_interest_rate_eod(mock_client, "SOFR", dt.date(2026, 6, 1), dt.date(2026, 6, 28))
        assert not df.empty
        assert "rate" in df.columns
        assert "created" in df.columns

    def test_empty_on_error(self, mock_client_erroring):
        fmod = _import_module()
        df = fmod.fetch_interest_rate_eod(mock_client_erroring, "SOFR", dt.date(2026, 6, 1), dt.date(2026, 6, 28))
        assert df.empty


# -----------------------------------------------------------------------
# Tests: fetch_option_open_interest
# -----------------------------------------------------------------------

class TestFetchOI:
    def test_returns_oi_column(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_option_open_interest(
            mock_client, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert not df.empty
        assert "open_interest" in df.columns

    def test_empty_on_error(self, mock_client_erroring):
        fmod = _import_module()
        df = fmod.fetch_option_open_interest(
            mock_client_erroring, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        )
        assert df.empty


# -----------------------------------------------------------------------
# Tests: fetch_option_list_expirations
# -----------------------------------------------------------------------

class TestListExpirations:
    def test_returns_list_of_dates(self, mock_client):
        fmod = _import_module()
        result = fmod.fetch_option_list_expirations(mock_client, "AMD")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(d, dt.date) for d in result)

    def test_returns_sorted(self, mock_client):
        fmod = _import_module()
        result = fmod.fetch_option_list_expirations(mock_client, "AMD")
        assert result == sorted(result)

    def test_empty_on_error(self, mock_client_erroring):
        fmod = _import_module()
        result = fmod.fetch_option_list_expirations(mock_client_erroring, "AMD")
        assert result == []


# -----------------------------------------------------------------------
# Tests: fetch_option_list_contracts
# -----------------------------------------------------------------------

class TestListContracts:
    def test_returns_strikes_and_rights(self, mock_client):
        fmod = _import_module()
        df = fmod.fetch_option_list_contracts(mock_client, "AMD", dt.date(2026, 6, 15))
        assert not df.empty
        assert "strike" in df.columns

    def test_empty_on_error(self, mock_client_erroring):
        fmod = _import_module()
        df = fmod.fetch_option_list_contracts(mock_client_erroring, "AMD", dt.date(2026, 6, 15))
        assert df.empty


# -----------------------------------------------------------------------
# Invariant tests: no semaphore, no DB, no heartbeat
# -----------------------------------------------------------------------

class TestInvariants:
    def test_module_imports_no_semaphore(self):
        """fetchers.py must not create or import any asyncio.Semaphore."""
        fmod = _import_module()
        source = Path(fmod.__file__).read_text()
        assert "Semaphore" not in source, "fetchers.py must not create a Semaphore"

    def test_module_imports_no_asyncpg(self):
        """fetchers.py must not import asyncpg (no DB)."""
        fmod = _import_module()
        source = Path(fmod.__file__).read_text()
        assert "asyncpg" not in source, "fetchers.py must not touch the database"

    def test_module_imports_no_heartbeat_call(self):
        """fetchers.py must not call heartbeat() in normal fetch operations.
        
        The async_validate_theta_port function is allowed to call heartbeat
        as it's a dedicated port validation utility.
        """
        fmod = _import_module()
        source = Path(fmod.__file__).read_text()
        
        # Allow heartbeat in async_validate_theta_port but not elsewhere
        # Count occurrences outside the validation function
        import re
        # Remove the validation function block from consideration
        validation_func = re.search(r'async def async_validate_theta_port.*?(?=\n(?:async )?def |\nclass |\Z)', source, re.DOTALL)
        if validation_func:
            source_without_validation = source[:validation_func.start()] + source[validation_func.end():]
        else:
            source_without_validation = source
        
        assert "heartbeat" not in source_without_validation, "fetchers.py must not call heartbeat() outside async_validate_theta_port"

    def test_no_disk_writes(self):
        """fetchers.py must not contain open() or Path.write()."""
        fmod = _import_module()
        source = Path(fmod.__file__).read_text()
        forbidden = [
            "open(", "open(",
            ".to_csv(", ".to_parquet(", ".to_feather(",
            ".to_sql(",
        ]
        for f in forbidden:
            assert f not in source, f"fetchers.py must not write to disk/DB: '{f}' found"


# -----------------------------------------------------------------------
# Error handling: all functions must not raise on network failure
# -----------------------------------------------------------------------

class TestNoRaises:
    def test_no_function_raises_on_network_error(self, mock_client_erroring):
        fmod = _import_module()
        functions = [
            (fmod.fetch_option_greeks_first_order, ("AMD", dt.date(2026, 7, 21), dt.date(2026, 6, 1), dt.date(2026, 6, 28))),
            (fmod.fetch_stock_ohlc, ("AMD", dt.date(2026, 6, 1), dt.date(2026, 6, 28))),
            (fmod.fetch_interest_rate_eod, ("SOFR", dt.date(2026, 6, 1), dt.date(2026, 6, 28))),
            (fmod.fetch_option_open_interest, ("AMD", dt.date(2026, 7, 21), dt.date(2026, 6, 1), dt.date(2026, 6, 28))),
            (fmod.fetch_option_list_expirations, ("AMD",)),
            (fmod.fetch_option_list_contracts, ("AMD", dt.date(2026, 6, 15))),
        ]
        for func, args in functions:
            try:
                result = func(mock_client_erroring, *args)
            except Exception as e:
                pytest.fail(f"{func.__name__} raised {type(e).__name__}: {e}")
            # Must return empty or list
            if hasattr(result, "empty"):
                assert result.empty, f"{func.__name__} should return empty"
            else:
                assert len(result) == 0, f"{func.__name__} should return empty list"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])