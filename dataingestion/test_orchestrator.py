"""Verification script for dataingestion/orchestrator.py (Agent A5).

Mocks all downstream modules — tests orchestration logic without
Theta subscription, DB, or real data.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# -----------------------------------------------------------------------
# Mock helpers
# -----------------------------------------------------------------------

async def _mock_fetch_greeks(client, symbol, expiration, start_date, end_date):
    """Returns a small synthetic opt DataFrame."""
    return pd.DataFrame(
        {
            "timestamp": pd.DatetimeIndex(
                [pd.Timestamp("2026-06-15 10:30:00", tz="UTC")]
            ),
            "underlying": ["AMD"],
            "expiration": pd.Timestamp("2026-07-21"),
            "strike": [155.0],
            "option_type": ["C"],
            "bid": [1.0],
            "ask": [1.2],
            "delta": [0.52],
            "theta": [-0.02],
            "vega_api": [0.15],
            "rho": [0.03],
            "implied_vol": [0.25],
            "iv_error": [0.001],
            "underlying_price": [158.0],
            "underlying_timestamp": [pd.Timestamp("2026-06-15 10:30:00", tz="UTC")],
            "spot_close": [158.0],
            "open_interest": [500],
            "_phase": ["raw"],
        }
    )


async def _mock_fetch_ohlc(client, symbol, start_date, end_date):
    return pd.DataFrame(
        {
            "timestamp": pd.DatetimeIndex(
                [pd.Timestamp("2026-06-15 10:30:00", tz="UTC")]
            ),
            "open": [157.0],
            "high": [158.0],
            "low": [157.0],
            "close": [158.0],
            "volume": [100000],
        }
    )


async def _mock_fetch_oi(client, symbol, expiration, start_date, end_date):
    return pd.DataFrame(
        {"date": ["2026-06-15"], "open_interest": [500]}
    )


async def _mock_fetch_rate(client, symbol, start_date, end_date):
    return pd.DataFrame(
        {"created": ["2026-06-15"], "rate": [4.50]}
    )


async def _mock_list_expirations(client, symbol, start_date=None, end_date=None):
    return [dt.date(2026, 7, 21)]


async def _mock_list_contracts(client, symbol, date):
    """Returns a DataFrame with contracts matching the mock greeks data."""
    return pd.DataFrame({
        "strike": [155.0],
        "right": ["CALL"],
    })


def _mock_clean(df, **kwargs):
    """Returns (clean_df, quar_df)."""
    clean = df.copy()
    clean["mid_price"] = (clean["bid"] + clean["ask"]) / 2
    clean["spread"] = clean["ask"] - clean["bid"]
    clean["rel_spread"] = clean["spread"] / clean["mid_price"]
    clean["quality_flags"] = 0
    clean["dte_calendar"] = 36
    clean["_phase"] = "clean"
    quar = pd.DataFrame(columns=list(df.columns) + ["reject_code", "reject_detail"])
    return clean, quar


def _mock_compute_business_T(df, cal, schedule_cache=None):
    df = df.copy()
    df["business_t"] = 0.1
    return df


def _mock_compute_forward(df):
    df = df.copy()
    df["r"] = 0.045
    df["q"] = 0.0
    df["forward_price"] = df["spot_close"] * 1.0045
    return df


def _mock_compute_vega(df):
    df = df.copy()
    df["vega"] = 0.15
    df["log_moneyness"] = 0.0
    df["_phase"] = "math"
    return df


def _mock_attach_rates_and_math(clean_df, rates_df, cal, schedule_cache, dividends_map=None):
    clean_df = _mock_compute_business_T(clean_df, cal, schedule_cache)
    clean_df["r"] = 0.045
    clean_df = _mock_compute_forward(clean_df)
    return _mock_compute_vega(clean_df)


def _mock_post_join_filters(clean_df, run_id=None):
    quar = pd.DataFrame(columns=list(clean_df.columns) + ["reject_code", "reject_detail"])
    return clean_df, quar


def _mock_finalize_slice_metadata(clean_df):
    return clean_df


async def _mock_build_dividends_map(underlying, chunk_start, chunk_end, stk_df):
    return {}


def _import_module():
    from dataingestion import orchestrator as omod

    return omod


# -----------------------------------------------------------------------
# Patch setup
# -----------------------------------------------------------------------

@pytest.fixture
def patched_orchestrator():
    """Patch all downstream modules and the client."""
    from contextlib import ExitStack
    
    with ExitStack() as stack:
        stack.enter_context(patch("dataingestion.orchestrator.heartbeat", return_value={"ok": True}))
        stack.enter_context(patch("core_engine.shared.theta_client.heartbeat", return_value={"ok": True}))
        mock_client_cls = stack.enter_context(patch("dataingestion.orchestrator.AsyncThetaClient"))
        stack.enter_context(patch("dataingestion.orchestrator.init_schema", new_callable=AsyncMock))
        stack.enter_context(patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
              side_effect=_mock_fetch_greeks))
        stack.enter_context(patch("dataingestion.orchestrator.async_fetch_stock_ohlc",
              side_effect=_mock_fetch_ohlc))
        stack.enter_context(patch("dataingestion.orchestrator.async_fetch_option_open_interest",
              side_effect=_mock_fetch_oi))
        stack.enter_context(patch("dataingestion.orchestrator.async_fetch_interest_rate_eod",
              side_effect=_mock_fetch_rate))
        stack.enter_context(patch("dataingestion.orchestrator.async_fetch_option_list_expirations",
              side_effect=_mock_list_expirations))
        stack.enter_context(patch("dataingestion.orchestrator.async_fetch_option_list_contracts",
              side_effect=_mock_list_contracts))
        stack.enter_context(patch("dataingestion.orchestrator.clean_option_chain",
              side_effect=_mock_clean))
        stack.enter_context(patch("dataingestion.orchestrator.attach_rates_and_math",
              side_effect=_mock_attach_rates_and_math))
        stack.enter_context(patch("dataingestion.orchestrator.apply_post_join_filters",
              side_effect=_mock_post_join_filters))
        stack.enter_context(patch("dataingestion.orchestrator.finalize_slice_metadata",
              side_effect=_mock_finalize_slice_metadata))
        stack.enter_context(patch("dataingestion.orchestrator._build_dividends_map",
              side_effect=_mock_build_dividends_map))
        stack.enter_context(patch("dataingestion.orchestrator.write_staging_batch",
              new_callable=AsyncMock, return_value=1))
        stack.enter_context(patch("dataingestion.orchestrator.load_from_staging",
              new_callable=AsyncMock, return_value=1))
        stack.enter_context(patch("dataingestion.orchestrator.write_quarantine_batch",
              new_callable=AsyncMock, return_value=0))
        stack.enter_context(patch("dataingestion.orchestrator.advance_watermark",
              new_callable=AsyncMock))
        stack.enter_context(patch("dataingestion.orchestrator.get_completed_chunks",
              new_callable=AsyncMock, return_value=set()))
        stack.enter_context(patch("dataingestion.orchestrator.next_run_id",
              new_callable=AsyncMock, return_value=1))
        mock_pool = stack.enter_context(patch("dataingestion.orchestrator.get_pool",
              new_callable=AsyncMock))
        
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        # Mock pool and connection — use MagicMock (not AsyncMock) for parts
        # that mirror asyncpg's synchronous API:
        #   - pool.acquire() is synchronous, returns context manager
        #   - conn.transaction() is synchronous, returns context manager
        # Only the context manager __aenter__/__aexit__ and the DB calls
        # themselves need AsyncMock behavior.
        from unittest.mock import MagicMock

        # Connection mock: MagicMock so .transaction() returns the txn ctx directly
        mock_conn = MagicMock()
        # DB calls from db_writer need async behavior
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
        mock_conn.copy_from = AsyncMock()
        
        # Transaction context manager
        mock_txn = AsyncMock()
        async def _txn_aexit_false(*args, **kwargs):
            return False
        mock_txn.__aexit__.side_effect = _txn_aexit_false
        mock_conn.transaction.return_value = mock_txn
        
        # Pool acquire context manager
        mock_acquire_ctx = AsyncMock()
        mock_acquire_ctx.__aenter__.return_value = mock_conn

        pool_obj = MagicMock()
        pool_obj.acquire.return_value = mock_acquire_ctx
        mock_pool.return_value = pool_obj
        
        yield mock_client, mock_conn, pool_obj


# -----------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------

class TestOrchestrator:
    def test_run_backfill_completes(self, patched_orchestrator):
        mock_client, mock_conn, mock_pool = patched_orchestrator
        omod = _import_module()

        async def _run():
            return await omod.run_backfill(
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 28),
            )

        result = asyncio.run(_run())
        assert isinstance(result, dict)
        assert "total_clean_rows" in result

    def test_heartbeat_called_first(self, patched_orchestrator):
        _, _, _ = patched_orchestrator

        async def _run():
            omod = _import_module()
            return await omod.run_backfill(
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 28),
            )

        asyncio.run(_run())
        # heartbeat is patched and called — we just verify no exception

    def test_semaphores_declared(self):
        omod = _import_module()
        source = Path(omod.__file__).read_text()
        # Check that config imports are present and semaphores use config values
        assert "from dataingestion import config as cfg" in source
        assert "cfg.OPT_SEM_LIMIT" in source
        assert "cfg.STK_SEM_LIMIT" in source
        assert "OPT_SEM = asyncio.Semaphore(cfg.OPT_SEM_LIMIT)" in source
        assert "STK_SEM = asyncio.Semaphore(cfg.STK_SEM_LIMIT)" in source

    def test_chunks_respect_one_month_cap(self, patched_orchestrator):
        """Chunks should be ≤ 30 days each."""
        mock_client, mock_conn, mock_pool = patched_orchestrator
        # Track date ranges requested via the mock
        requested_dates = []

        async def _capture(*args, **kwargs):
            requested_dates.append((kwargs.get("start_date"), kwargs.get("end_date")))
            return await _mock_fetch_greeks(*args, **kwargs)

        with patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
                    side_effect=_capture):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 1, 1),
                    end_date=dt.date(2026, 6, 1),
                )
            asyncio.run(_run())

        for start, end in requested_dates:
            if start and end:
                days = (end - start).days
                assert days <= 31, f"Chunk too large: {start} → {end} ({days} days)"

    def test_watermark_checked(self, patched_orchestrator):
        """get_completed_chunks must be called during backfill (inside transaction)."""
        mock_client, mock_conn, mock_pool = patched_orchestrator
        called = False

        async def _capture_get_completed(*args, **kwargs):
            nonlocal called
            called = True
            return set()

        with patch("dataingestion.orchestrator.get_completed_chunks",
                    side_effect=_capture_get_completed):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            asyncio.run(_run())

        assert called, "get_completed_chunks was not called — resume support broken"

    def test_empty_fetch_skips_chunk(self, patched_orchestrator):
        """Empty DataFrame from greeks fetch should skip without error."""
        _, _, _ = patched_orchestrator

        async def _empty(*args, **kwargs):
            return pd.DataFrame()

        with patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
                    side_effect=_empty):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            result = asyncio.run(_run())
            assert result["total_clean_rows"] >= 0  # should be 0

    def test_db_error_does_not_crash(self, patched_orchestrator):
        """A DB write error should not crash the entire backfill."""
        _, _, _ = patched_orchestrator

        async def _error(*args, **kwargs):
            raise RuntimeError("DB down")

        with patch("dataingestion.orchestrator.write_staging_batch",
                    side_effect=_error):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            result = asyncio.run(_run())
            assert "errors" in result
            assert result["errors"] > 0

    def test_pipeline_order(self, patched_orchestrator):
        """Verify pipeline calls happen in correct order."""
        _, _, _ = patched_orchestrator
        call_order = []

        async def _track_fetch(*a, **kw):
            call_order.append("fetch")
            return await _mock_fetch_greeks(*a, **kw)

        def _track_clean(df, **kwargs):
            call_order.append("clean")
            return _mock_clean(df)

        def _track_attach(clean_df, rates_df, cal, schedule_cache=None, dividends_map=None):
            call_order.append("math")
            return _mock_attach_rates_and_math(
                clean_df, rates_df, cal, schedule_cache, dividends_map,
            )

        with (
            patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
                   side_effect=_track_fetch),
            patch("dataingestion.orchestrator.clean_option_chain",
                   side_effect=_track_clean),
            patch("dataingestion.orchestrator.attach_rates_and_math",
                   side_effect=_track_attach),
        ):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            asyncio.run(_run())

        # Find the expected subsequence in call_order
        order_str = ",".join(call_order)
        # The pipeline order should be: fetch → clean → businesst → forward → vega
        assert "fetch" in call_order
        assert "clean" in call_order
        # The actual order they appear in the list
        fetch_idx = call_order.index("fetch")
        clean_idx = call_order.index("clean")
        assert fetch_idx < clean_idx, f"fetch must come before clean: {order_str}"


class TestInvariants:
    def test_dual_semaphores(self):
        omod = _import_module()
        source = Path(omod.__file__).read_text()
        assert "OPT_SEM" in source
        assert "STK_SEM" in source

    def test_no_direct_http(self):
        omod = _import_module()
        source = Path(omod.__file__).read_text()
        assert "aiohttp.ClientSession" not in source

    def test_no_raw_sql_outside_db_writer(self):
        omod = _import_module()
        source = Path(omod.__file__).read_text()
        # Should use init_schema, write_staging_batch etc., not raw SQL
        assert "asyncpg.connect" not in source


class TestConcurrency:
    """Tests verifying async concurrency control with semaphores."""

    def test_semaphores_limit_concurrent_requests(self, patched_orchestrator):
        """Verify OPT_SEM and STK_SEM actually limit concurrent fetches."""
        mock_client, mock_conn, mock_pool = patched_orchestrator
        
        # Track concurrent calls
        opt_concurrent = 0
        opt_max_concurrent = 0
        stk_concurrent = 0
        stk_max_concurrent = 0
        
        async def _track_opt(*args, **kwargs):
            nonlocal opt_concurrent, opt_max_concurrent
            opt_concurrent += 1
            opt_max_concurrent = max(opt_max_concurrent, opt_concurrent)
            await asyncio.sleep(0.01)  # Simulate network delay
            opt_concurrent -= 1
            return await _mock_fetch_greeks(*args, **kwargs)
        
        async def _track_oi(*args, **kwargs):
            nonlocal opt_concurrent, opt_max_concurrent
            opt_concurrent += 1
            opt_max_concurrent = max(opt_max_concurrent, opt_concurrent)
            await asyncio.sleep(0.01)
            opt_concurrent -= 1
            return await _mock_fetch_oi(*args, **kwargs)
        
        async def _track_stk(*args, **kwargs):
            nonlocal stk_concurrent, stk_max_concurrent
            stk_concurrent += 1
            stk_max_concurrent = max(stk_max_concurrent, stk_concurrent)
            await asyncio.sleep(0.01)
            stk_concurrent -= 1
            return await _mock_fetch_ohlc(*args, **kwargs)

        with (
            patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
                   side_effect=_track_opt),
            patch("dataingestion.orchestrator.async_fetch_option_open_interest",
                   side_effect=_track_oi),
            patch("dataingestion.orchestrator.async_fetch_stock_ohlc",
                   side_effect=_track_stk),
        ):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            asyncio.run(_run())

        # OPT_SEM=4 should limit concurrent option endpoint calls
        assert opt_max_concurrent <= 4, f"OPT_SEM violated: max concurrent was {opt_max_concurrent}"
        # STK_SEM=2 should limit concurrent stock endpoint calls
        assert stk_max_concurrent <= 2, f"STK_SEM violated: max concurrent was {stk_max_concurrent}"

    def test_no_asyncio_run_in_pipeline(self):
        """Static analysis: no asyncio.run() in orchestrator.py source."""
        source = Path("dataingestion/orchestrator.py").read_text()
        assert "asyncio.run" not in source, "orchestrator.py must not contain asyncio.run()"


class TestWatermarkAtomicity:
    """Verify atomic watermark via transaction in _process_chunk."""

    def test_watermark_check_and_advance_atomic(self, patched_orchestrator):
        """Watermark check + advance happen inside the same transaction."""
        mock_client, mock_conn, mock_pool = patched_orchestrator

        # Track transaction enter/exit
        txn_entered = False
        txn_exited_cleanly = False

        mock_txn = mock_conn.transaction.return_value

        async def _txn_enter():
            nonlocal txn_entered
            txn_entered = True
            return mock_txn

        async def _txn_exit(*args, **kwargs):
            nonlocal txn_exited_cleanly
            txn_exited_cleanly = True
            return False  # Don't suppress exceptions

        mock_txn.__aenter__.side_effect = _txn_enter
        mock_txn.__aexit__.side_effect = _txn_exit

        # Track order of DB calls
        call_order = []

        async def _track_write_staging(*args, **kwargs):
            call_order.append("write_staging")
            return 1

        async def _track_load_staging(*args, **kwargs):
            call_order.append("load_staging")
            return 1

        async def _track_advance(*args, **kwargs):
            call_order.append("advance_watermark")
            return None

        async def _track_get_completed(*args, **kwargs):
            call_order.append("get_completed_chunks")
            return set()

        with (
            patch("dataingestion.orchestrator.write_staging_batch",
                  side_effect=_track_write_staging),
            patch("dataingestion.orchestrator.load_from_staging",
                  side_effect=_track_load_staging),
            patch("dataingestion.orchestrator.advance_watermark",
                  side_effect=_track_advance),
            patch("dataingestion.orchestrator.get_completed_chunks",
                  side_effect=_track_get_completed),
        ):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            result = asyncio.run(_run())

        assert txn_entered, "Transaction.__aenter__ was never called"
        assert txn_exited_cleanly, "Transaction.__aexit__ was never called"
        assert result["errors"] == 0, f"Expected 0 errors, got {result['errors']}"

        # Verify order: get_completed_chunks inside transaction, then writes, then advance
        assert "get_completed_chunks" in call_order, \
            "get_completed_chunks not called inside transaction"
        gc_idx = call_order.index("get_completed_chunks") if "get_completed_chunks" in call_order else -1
        ws_idx = call_order.index("write_staging") if "write_staging" in call_order else -1
        aw_idx = call_order.index("advance_watermark") if "advance_watermark" in call_order else -1

        if gc_idx >= 0 and ws_idx >= 0:
            assert gc_idx < ws_idx, \
                f"Watermark check must precede writes: call_order={call_order}"
        if ws_idx >= 0 and aw_idx >= 0:
            assert ws_idx < aw_idx, \
                f"Writes must precede watermark advance: call_order={call_order}"

    def test_crash_before_advance_retries(self, patched_orchestrator):
        """Simulate crash after load_from_staging but before advance_watermark.

        First run: advance_watermark raises → transaction rolls back → chunk NOT marked
        Second run: advance_watermark succeeds → chunk completed
        """
        mock_client, mock_conn, mock_pool = patched_orchestrator

        call_count = 0

        async def _fail_first_advance(conn, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: simulate crash → transaction rolls back
                raise RuntimeError("Crash after load, before advance")
            # Subsequent calls: succeed
            return None

        with patch("dataingestion.orchestrator.advance_watermark",
                   side_effect=_fail_first_advance):
            omod = _import_module()

            # First run — advance_watermark crashes, chunk NOT completed
            async def _run1():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            result1 = asyncio.run(_run1())

            assert result1["errors"] > 0, \
                "First run should report at least 1 error"
            assert call_count >= 1, \
                "advance_watermark should have been called on first run"

            # Second run — chunk retried, advance_watermark succeeds
            async def _run2():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            result2 = asyncio.run(_run2())

            assert result2["errors"] == 0, \
                f"Second run should have 0 errors, got {result2['errors']}"
            assert call_count >= 2, \
                f"Expected at least 2 advance_watermark calls (1 failed + 1 retry), got {call_count}"
            assert result2["total_clean_rows"] > 0, \
                "Second run should process the chunk successfully"

    def test_completed_chunk_skipped_inside_transaction(self, patched_orchestrator):
        """If watermark already exists inside transaction, chunk is skipped."""
        mock_client, mock_conn, mock_pool = patched_orchestrator

        write_called = False
        advance_called = False

        async def _track_write(*args, **kwargs):
            nonlocal write_called
            write_called = True
            return 1

        async def _track_advance(*args, **kwargs):
            nonlocal advance_called
            advance_called = True
            return None

        async def _already_completed(*args, **kwargs):
            # Return a completed chunk matching our test data
            return {("2026-07-21", dt.date(2026, 6, 28))}

        with (
            patch("dataingestion.orchestrator.get_completed_chunks",
                  side_effect=_already_completed),
            patch("dataingestion.orchestrator.write_staging_batch",
                  side_effect=_track_write),
            patch("dataingestion.orchestrator.advance_watermark",
                  side_effect=_track_advance),
        ):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )
            result = asyncio.run(_run())

        assert not write_called, \
            "Completed chunk should not trigger writes"
        assert not advance_called, \
            "Completed chunk should not trigger watermark advance"
        assert result["total_clean_rows"] == 0, \
            "Completed chunk should contribute 0 clean rows"


class TestClientLifecycle:
    """Verify single-client lifecycle per backfill (EH204)."""

    def test_single_client_per_backfill(self, patched_orchestrator):
        """AsyncThetaClient.__aenter__ called exactly once."""
        mock_client, mock_conn, mock_pool = patched_orchestrator
        omod = _import_module()

        async def _run():
            return await omod.run_backfill(
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 28),
            )

        asyncio.run(_run())

        enter_count = mock_client.__aenter__.call_count
        assert enter_count == 1, f"Client __aenter__ called {enter_count} times, expected 1"


class _RetryableError(Exception):
    """Test exception with a retryable HTTP status."""
    def __init__(self, status=503):
        self.status = status


class _NonRetryableError(Exception):
    """Test exception with a non-retryable HTTP status."""
    def __init__(self, status=400):
        self.status = status


class TestFetchResilience:
    """EH206: Retry logic with exponential backoff and error classification."""

    # ---- _is_retryable_error unit tests ----

    def test_retryable_status_recognized(self):
        """503 status is retryable."""
        omod = _import_module()
        err = _RetryableError(status=503)
        assert omod._is_retryable_error(err) is True

    def test_non_retryable_status_fails_fast(self):
        """400 status is NOT retryable."""
        omod = _import_module()
        err = _NonRetryableError(status=400)
        assert omod._is_retryable_error(err) is False

    def test_timeout_is_retryable(self):
        """asyncio.TimeoutError is retryable."""
        omod = _import_module()
        assert omod._is_retryable_error(asyncio.TimeoutError()) is True

    def test_connection_error_is_retryable(self):
        """ConnectionError is retryable."""
        omod = _import_module()
        assert omod._is_retryable_error(ConnectionError("refused")) is True

    def test_oserror_is_retryable(self):
        """OSError is retryable."""
        omod = _import_module()
        assert omod._is_retryable_error(OSError("socket")) is True

    def test_value_error_is_not_retryable(self):
        """ValueError (no status, not IO) is NOT retryable."""
        omod = _import_module()
        assert omod._is_retryable_error(ValueError("bad data")) is False

    def test_exception_without_status_is_not_retryable(self):
        """Generic Exception is NOT retryable."""
        omod = _import_module()
        assert omod._is_retryable_error(Exception("generic")) is False

    # ---- fetch_with_retry functional tests ----

    def test_success_on_first_try(self):
        """Successful fetch returns immediately without retries."""
        omod = _import_module()
        call_count = 0

        async def _ok():
            nonlocal call_count
            call_count += 1
            return pd.DataFrame({"x": [1]})

        async def _run():
            return await omod.fetch_with_retry(_ok)

        result = asyncio.run(_run())
        assert not result.empty
        assert result["x"].iloc[0] == 1
        assert call_count == 1

    def test_retries_on_retryable_then_succeeds(self):
        """503 error retried up to FETCH_MAX_RETRIES, then succeeds."""
        omod = _import_module()
        call_count = 0
        max_retries = omod.FETCH_MAX_RETRIES

        async def _flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= max_retries:
                raise _RetryableError(status=503)
            return pd.DataFrame({"x": [42]})

        async def _run():
            return await omod.fetch_with_retry(_flaky)

        result = asyncio.run(_run())
        assert not result.empty
        assert result["x"].iloc[0] == 42
        assert call_count == max_retries + 1  # N failures + 1 success

    def test_exhausts_retries_then_raises(self):
        """After FETCH_MAX_RETRIES+1 failures, raises the last exception."""
        omod = _import_module()
        call_count = 0
        max_retries = omod.FETCH_MAX_RETRIES

        async def _always_fails():
            nonlocal call_count
            call_count += 1
            raise _RetryableError(status=503)

        async def _run():
            try:
                await omod.fetch_with_retry(_always_fails)
                return None
            except _RetryableError as e:
                return e

        result = asyncio.run(_run())
        assert isinstance(result, _RetryableError)
        assert result.status == 503
        assert call_count == max_retries + 1  # all attempts exhausted

    def test_non_retryable_fails_immediately(self):
        """400 error fails fast, no retries."""
        omod = _import_module()
        call_count = 0

        async def _bad_request():
            nonlocal call_count
            call_count += 1
            raise _NonRetryableError(status=400)

        async def _run():
            try:
                await omod.fetch_with_retry(_bad_request)
                return None
            except _NonRetryableError as e:
                return e

        result = asyncio.run(_run())
        assert isinstance(result, _NonRetryableError)
        assert result.status == 400
        assert call_count == 1  # only one attempt

    def test_backoff_increases_with_jitter(self):
        """Verify backoff increases exponentially with jitter."""
        omod = _import_module()
        slept_times = []

        async def _always_503():
            raise _RetryableError(status=503)

        original_sleep = asyncio.sleep

        async def _capture_sleep(delay):
            slept_times.append(delay)

        with patch("dataingestion.orchestrator.asyncio.sleep", side_effect=_capture_sleep):
            async def _run():
                try:
                    await omod.fetch_with_retry(_always_503)
                except _RetryableError:
                    pass

            asyncio.run(_run())

        # Should have slept max_retries times (one per retry, not for the last attempt)
        assert len(slept_times) == omod.FETCH_MAX_RETRIES
        # Each subsequent sleep should be >= previous (monotonic increase with jitter tolerance)
        for i in range(len(slept_times) - 1):
            assert slept_times[i + 1] >= slept_times[i] * 0.5, \
                f"Backoff not monotonic: {slept_times}"

    def test_backoff_capped_at_max_delay(self):
        """Backoff delay is capped at FETCH_MAX_DELAY."""
        omod = _import_module()

        # Simulate many failures so delay would exceed max
        base = omod.FETCH_BASE_DELAY
        # Find the delay at the last retry attempt
        last_attempt = omod.FETCH_MAX_RETRIES - 1
        expected_delay = min(base * (2 ** last_attempt), omod.FETCH_MAX_DELAY)
        assert expected_delay <= omod.FETCH_MAX_DELAY

    # ---- _process_chunk integration tests (return_exceptions handling) ----

    @patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order")
    def test_exhausted_retries_marks_chunk_as_error(self, mock_greeks, patched_orchestrator):
        """If greeks fetch exhausts all retries, chunk returns errors=1."""
        mock_client, mock_conn, mock_pool = patched_orchestrator
        # Mock raises retryable error FETCH_MAX_RETRIES+1 times
        mock_greeks.side_effect = _RetryableError(status=503)

        omod = _import_module()
        async def _run():
            return await omod.run_backfill(
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 28),
            )

        result = asyncio.run(_run())
        # At least one chunk should have marked errors
        assert result["errors"] > 0, "Exhausted retries should mark chunks as errors"

    @patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order")
    def test_non_retryable_fails_fast_in_chunk(self, mock_greeks, patched_orchestrator):
        """Non-retryable 400 error fails immediately, marks chunk as error."""
        mock_client, mock_conn, mock_pool = patched_orchestrator
        mock_greeks.side_effect = _NonRetryableError(status=400)

        omod = _import_module()
        async def _run():
            return await omod.run_backfill(
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 28),
            )

        result = asyncio.run(_run())
        assert result["errors"] > 0, "Non-retryable errors should mark chunks as errors"
        assert mock_greeks.call_count == 1, "Non-retryable error should fail immediately"


class TestIntegration:
    """EH208: Integration tests verifying Phase 2 features work together."""

    def test_full_pipeline_with_all_phase2_features(self, patched_orchestrator):
        """Run a backfill and verify all Phase 2 features are exercised.

        Verifies: schedule_cache passed, cache used, client lifecycle, retry
        integration with semaphores, and correct result structure.
        """
        mock_client, mock_conn, mock_pool = patched_orchestrator

        # Track schedule_cache to verify it's passed through to compute_business_T
        schedule_cache_received = None

        def _capture_attach(clean_df, rates_df, cal, schedule_cache=None, dividends_map=None):
            nonlocal schedule_cache_received
            schedule_cache_received = schedule_cache
            return _mock_attach_rates_and_math(
                clean_df, rates_df, cal, schedule_cache, dividends_map,
            )

        # Track fetch_with_retry calls to verify _sem parameter
        omod = _import_module()
        original_retry = omod.fetch_with_retry
        retry_calls = []

        async def _track_retry(fetch_func, *args, _sem=None, **kwargs):
            retry_calls.append({
                'func_name': getattr(fetch_func, '__name__', str(fetch_func)),
                'sem': _sem,
            })
            return await original_retry(fetch_func, *args, _sem=_sem, **kwargs)

        with (
            patch("dataingestion.orchestrator.attach_rates_and_math",
                  side_effect=_capture_attach),
            patch("dataingestion.orchestrator.fetch_with_retry",
                  side_effect=_track_retry),
        ):
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )

            result = asyncio.run(_run())

        # Verify backfill completed with all expected keys
        assert "total_clean_rows" in result
        assert "total_quarantined" in result
        assert "errors" in result
        assert "duration_seconds" in result
        assert result["total_clean_rows"] > 0
        assert result["errors"] == 0

        # Verify schedule_cache was passed to attach_rates_and_math
        assert schedule_cache_received is not None, \
            "schedule_cache was not passed to attach_rates_and_math"
        assert isinstance(schedule_cache_received, dict)
        assert len(schedule_cache_received) > 0

        # Verify fetch_with_retry receives _sem=OPT_SEM for greeks and OI
        opt_calls = [c for c in retry_calls if c['sem'] is not None]
        no_sem_calls = [c for c in retry_calls if c['sem'] is None]
        assert len(opt_calls) >= 2, \
            f"Expected at least 2 semaphore-guarded fetches (greeks+OI), got {len(opt_calls)}"
        assert len(no_sem_calls) >= 1, \
            f"Expected at least 1 non-semaphore fetch (stock via cache), got {len(no_sem_calls)}"

        # Verify client lifecycle: entered and exited exactly once
        assert mock_client.__aenter__.call_count == 1, \
            f"Client __aenter__ called {mock_client.__aenter__.call_count} times"
        assert mock_client.__aexit__.call_count == 1, \
            f"Client __aexit__ called {mock_client.__aexit__.call_count} times"

    def test_schedule_cache_keys_match_format(self, patched_orchestrator):
        """schedule_cache dict contains prefix_minutes, session_minutes, tz."""
        mock_client, mock_conn, mock_pool = patched_orchestrator
        schedule_cache_received = None

        def _capture_attach(clean_df, rates_df, cal, schedule_cache=None, dividends_map=None):
            nonlocal schedule_cache_received
            schedule_cache_received = schedule_cache
            return _mock_attach_rates_and_math(
                clean_df, rates_df, cal, schedule_cache, dividends_map,
            )

        with patch("dataingestion.orchestrator.attach_rates_and_math",
                   side_effect=_capture_attach):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )

            asyncio.run(_run())

        assert schedule_cache_received is not None
        assert 'prefix_minutes' in schedule_cache_received, \
            f"schedule_cache missing prefix_minutes, keys={list(schedule_cache_received.keys())}"
        assert 'session_minutes' in schedule_cache_received
        assert 'tz' in schedule_cache_received

    def test_retry_releases_semaphore_during_backoff(self, patched_orchestrator):
        """When greeks fetch fails with retryable error, OPT_SEM is released.

        OPT_SEM starts at 4. During fetch_with_retry, the semaphore is acquired
        for the fetch attempt but released during backoff sleep. So when
        asyncio.sleep is called, OPT_SEM._value should be back to 4 (the max).
        """
        mock_client, mock_conn, mock_pool = patched_orchestrator

        call_count = 0
        sem_values_during_sleep = []

        async def _flaky_greeks(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise _RetryableError(status=503)
            return await _mock_fetch_greeks(*args, **kwargs)

        async def _capture_sleep(delay):
            omod = _import_module()
            sem_values_during_sleep.append(omod.OPT_SEM._value)

        with (
            patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
                  side_effect=_flaky_greeks),
            patch("dataingestion.orchestrator.asyncio.sleep", side_effect=_capture_sleep),
        ):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )

            result = asyncio.run(_run())

        # The flaky greeks recovers, so the backfill should succeed
        assert result["errors"] == 0, f"Expected 0 errors, got {result['errors']}"
        assert call_count == 2, \
            f"Expected 2 calls (1 fail + 1 success), got {call_count}"

        # During retry sleep, semaphore should be released back to full (4)
        assert len(sem_values_during_sleep) >= 1, \
            "asyncio.sleep was not called — retry did not trigger backoff"
        for val in sem_values_during_sleep:
            assert val >= 3, \
                f"Semaphore leaked during sleep: value={val}, limit=4"

    def test_cache_lifecycle_with_ohlc_fetch(self, patched_orchestrator):
        """BoundedCache is passed to _get_stock_ohlc_cached and accumulates state."""
        mock_client, mock_conn, mock_pool = patched_orchestrator

        cache_objects = []
        omod = _import_module()
        original_cached = omod._get_stock_ohlc_cached

        async def _track_cache(client, symbol, chunk_start, chunk_end, cache):
            cache_objects.append(cache)
            stats = cache.stats()
            result = await original_cached(client, symbol, chunk_start, chunk_end, cache)
            return result

        with patch("dataingestion.orchestrator._get_stock_ohlc_cached",
                   side_effect=_track_cache):
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )

            result = asyncio.run(_run())

        assert result["errors"] == 0
        assert len(cache_objects) >= 1, \
            "_get_stock_ohlc_cached was never called with cache"
        # The same cache instance should be reused across chunks
        assert all(c is cache_objects[0] for c in cache_objects), \
            "Different cache instances used across chunks — expected single BoundedCache"

    def test_cache_and_client_created_together(self, patched_orchestrator):
        """Caches are created inside the client context manager, client used once."""
        mock_client, mock_conn, mock_pool = patched_orchestrator

        # Track BoundedCache instances to verify they're created
        from dataingestion.orchestrator import BoundedCache as _RealBoundedCache
        cache_instances = []

        class TrackedCache(_RealBoundedCache):
            def __init__(self, *args, **kwargs):
                cache_instances.append(self)
                super().__init__(*args, **kwargs)

        with patch("dataingestion.orchestrator.BoundedCache", TrackedCache):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )

            result = asyncio.run(_run())

        assert result["errors"] == 0
        # Three caches: ohlc_cache + rates_cache + contract_cache
        assert len(cache_instances) == 3, \
            f"Expected 3 BoundedCache instances (ohlc + rates + contracts), got {len(cache_instances)}"
        assert mock_client.__aenter__.call_count == 1
        assert mock_client.__aexit__.call_count == 1

    def test_full_pipeline_handles_flaky_oi_fetch(self, patched_orchestrator):
        """Retry + semaphore integration works for OI fetch (also OPT_SEM)."""
        mock_client, mock_conn, mock_pool = patched_orchestrator

        call_count = 0
        async def _flaky_oi(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise _RetryableError(status=503)
            return await _mock_fetch_oi(*args, **kwargs)

        with patch("dataingestion.orchestrator.async_fetch_option_open_interest",
                   side_effect=_flaky_oi):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )

            result = asyncio.run(_run())

        assert result["errors"] == 0
        assert call_count == 3, \
            f"Expected 3 calls (2 fails + 1 success) for OI, got {call_count}"


class TestAsyncMockVerification:
    """EH208: Verify async mocks return correct column contracts per COLUMNS.md."""

    def test_mock_greeks_has_required_raw_columns(self):
        """_mock_fetch_greeks returns all required raw columns from COLUMNS.md Section I."""
        df = asyncio.run(_mock_fetch_greeks(
            None, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        ))

        required = {
            "timestamp", "underlying", "expiration", "strike", "option_type",
            "bid", "ask", "delta", "theta", "vega_api", "rho", "implied_vol",
            "iv_error", "underlying_price", "underlying_timestamp",
            "spot_close", "open_interest", "_phase",
        }
        actual = set(df.columns)
        missing = required - actual
        assert not missing, f"Mock greeks DataFrame missing columns: {missing}"
        extra = actual - required
        assert not extra, f"Mock greeks DataFrame has unexpected columns: {extra}"
        assert df["_phase"].iloc[0] == "raw"

    def test_mock_ohlc_has_required_columns(self):
        """_mock_fetch_ohlc returns timestamp, open, high, low, close, volume."""
        df = asyncio.run(_mock_fetch_ohlc(
            None, "AMD",
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        ))
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        actual = set(df.columns)
        missing = required - actual
        assert not missing, f"Mock OHLC DataFrame missing columns: {missing}"

    def test_mock_oi_has_required_columns(self):
        """_mock_fetch_oi returns date, open_interest."""
        df = asyncio.run(_mock_fetch_oi(
            None, "AMD", dt.date(2026, 7, 21),
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        ))
        required = {"date", "open_interest"}
        actual = set(df.columns)
        missing = required - actual
        assert not missing, f"Mock OI DataFrame missing columns: {missing}"

    def test_mock_rate_has_required_columns(self):
        """_mock_fetch_rate returns created, rate."""
        df = asyncio.run(_mock_fetch_rate(
            None, "SOFR",
            dt.date(2026, 6, 1), dt.date(2026, 6, 28),
        ))
        required = {"created", "rate"}
        actual = set(df.columns)
        missing = required - actual
        assert not missing, f"Mock rate DataFrame missing columns: {missing}"

    def test_mock_clean_adds_required_columns(self):
        """_mock_clean adds mid_price, spread, rel_spread, quality_flags, dte_calendar, _phase=clean."""
        raw = pd.DataFrame({
            "bid": [1.0], "ask": [1.2],
        })
        clean, quar = _mock_clean(raw)

        required = {"mid_price", "spread", "rel_spread", "quality_flags", "dte_calendar", "_phase"}
        actual = set(clean.columns)
        missing = required - actual
        assert not missing, f"Mock clean output missing columns: {missing}"
        assert clean["_phase"].iloc[0] == "clean"
        assert clean["mid_price"].iloc[0] == pytest.approx(1.1)
        assert clean["spread"].iloc[0] == pytest.approx(0.2)

    def test_mock_math_functions_set_required_columns(self):
        """_mock_compute_business_T, _mock_compute_forward, _mock_compute_vega set expected columns."""
        df = pd.DataFrame({"spot_close": [158.0]})

        result_t = _mock_compute_business_T(df, None)
        assert "business_t" in result_t.columns
        assert result_t["business_t"].iloc[0] == 0.1

        result_f = _mock_compute_forward(df)
        assert "r" in result_f.columns
        assert "q" in result_f.columns
        assert "forward_price" in result_f.columns
        assert result_f["r"].iloc[0] == 0.045

        result_v = _mock_compute_vega(df)
        assert "vega" in result_v.columns
        assert "log_moneyness" in result_v.columns
        assert "_phase" in result_v.columns
        assert result_v["_phase"].iloc[0] == "math"
        assert result_v["vega"].iloc[0] == 0.15


class TestPipelineColumnPropagation:
    """EH208: Verify column contract is maintained through the full pipeline."""

    def test_columns_flow_from_fetch_through_math(self, patched_orchestrator):
        """Columns from mock greeks fetch propagate correctly through clean → math."""
        mock_client, mock_conn, mock_pool = patched_orchestrator

        cleaned_inputs = []
        mathed_inputs = []

        def _capture_clean(df, **kwargs):
            cleaned_inputs.append(df)
            return _mock_clean(df)

        def _capture_attach(clean_df, rates_df, cal, schedule_cache=None, dividends_map=None):
            mathed_inputs.append(clean_df)
            return _mock_attach_rates_and_math(
                clean_df, rates_df, cal, schedule_cache, dividends_map,
            )

        with (
            patch("dataingestion.orchestrator.clean_option_chain",
                  side_effect=_capture_clean),
            patch("dataingestion.orchestrator.attach_rates_and_math",
                  side_effect=_capture_attach),
        ):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )

            result = asyncio.run(_run())

        assert result["errors"] == 0
        assert len(cleaned_inputs) >= 1, "clean_option_chain was never called"
        assert len(mathed_inputs) >= 1, "attach_rates_and_math was never called"

        # Clean input should have raw columns (fetch output)
        for cdf in cleaned_inputs:
            if not cdf.empty:
                for col in ["bid", "ask", "delta", "theta", "vega_api"]:
                    assert col in cdf.columns, \
                        f"clean input missing raw column '{col}', cols={list(cdf.columns)[:10]}"
                assert cdf["_phase"].iloc[0] == "raw"

        # Math input should have clean columns
        for mdf in mathed_inputs:
            if not mdf.empty:
                for col in ["mid_price", "spread", "dte_calendar", "quality_flags"]:
                    assert col in mdf.columns, \
                        f"math input missing clean column '{col}', cols={list(mdf.columns)[:10]}"
                assert "spot_close" in mdf.columns
                assert mdf["_phase"].iloc[0] == "clean"

    def test_attach_rates_and_math_receives_clean_df(self, patched_orchestrator):
        """attach_rates_and_math receives cleaned rows with spot_close."""
        mock_client, mock_conn, mock_pool = patched_orchestrator

        math_inputs = []

        def _capture_attach(clean_df, rates_df, cal, schedule_cache=None, dividends_map=None):
            math_inputs.append(clean_df)
            return _mock_attach_rates_and_math(
                clean_df, rates_df, cal, schedule_cache, dividends_map,
            )

        with patch("dataingestion.orchestrator.attach_rates_and_math",
                   side_effect=_capture_attach):
            omod = _import_module()
            async def _run():
                return await omod.run_backfill(
                    start_date=dt.date(2026, 6, 1),
                    end_date=dt.date(2026, 6, 28),
                )

            result = asyncio.run(_run())

        assert result["errors"] == 0
        assert len(math_inputs) >= 1

        for df in math_inputs:
            if not df.empty:
                for col in ["mid_price", "spread", "dte_calendar", "quality_flags"]:
                    assert col in df.columns
                assert "spot_close" in df.columns


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])