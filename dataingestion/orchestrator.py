"""Orchestrator for AMD eSSVI backfill pipeline.

Pure orchestration: ties together fetchers, cleaning, math, joins, cache, retry, and DB writer.
"""

from __future__ import annotations

import aiohttp
import asyncio
import datetime as dt
import time
from dataclasses import dataclass

import asyncpg
import pandas as pd
import pandas_market_calendars as mcal

from core_engine.shared.theta_client import AsyncThetaClient, heartbeat

from dataingestion import config as cfg
from dataingestion.fetchers import async_validate_theta_port

# Local aliases for commonly-used config constants
MIN_DTE, MAX_DTE = cfg.MIN_DTE, cfg.MAX_DTE
MIN_DELTA_ABS, MAX_DELTA_ABS = cfg.MIN_DELTA_ABS, cfg.MAX_DELTA_ABS
from dataingestion.fetchers import (
    async_fetch_interest_rate_eod,
    async_fetch_option_greeks_first_order,
    async_fetch_option_list_contracts,
    async_fetch_option_list_expirations,
    async_fetch_option_open_interest,
    async_fetch_stock_ohlc,
)
from dataingestion.cleaning import clean_option_chain
from dataingestion.math import (
    _build_business_time_schedule,
)
from dataingestion.db_writer import (
    advance_watermark,
    disable_compression,
    enable_compression,
    get_completed_chunks,
    get_pool,
    init_schema,
    load_from_staging,
    next_run_id,
    write_quarantine_batch,
    write_staging_batch,
)
from dataingestion.logging import (
    chunk_var,
    exp_var,
    run_id_var,
)
from dataingestion.cache import BoundedCache
from dataingestion.chunking import _dte_window, _month_chunks
from dataingestion.dividends import (
    compute_dividend_yield,
    get_dividend_fetcher,
)
from dataingestion.joins import (
    apply_post_join_filters,
    attach_rates_and_math,
    finalize_slice_metadata,
    join_spot_and_oi,
)
from dataingestion.retry import _is_retryable_error, fetch_with_retry
from core_engine.shared.constants import normalize_right

import logging

log = logging.getLogger("dataingestion.orchestrator")


def _prefilter_band(df: pd.DataFrame) -> pd.DataFrame:
    """Apply DTE pre-filter immediately after fetch (calendar membership only)."""
    result = df.copy()
    bar_dates = pd.to_datetime(result["timestamp"].dt.date)
    exp_dates = pd.to_datetime(result["expiration"].dt.date)
    dte = (exp_dates - bar_dates).dt.days
    dte_ok = (dte >= MIN_DTE) & (dte <= MAX_DTE)
    return result.loc[dte_ok].copy()

# Two semaphores for different tier limits
OPT_SEM = asyncio.Semaphore(cfg.OPT_SEM_LIMIT)   # Standard tier: greeks, OI, contracts
STK_SEM = asyncio.Semaphore(cfg.STK_SEM_LIMIT)   # Value tier: stock OHLC, rates, calendar

# Backward compatibility aliases for tests
OPT = OPT_SEM
STK = STK_SEM

# Re-export retry constants and functions for test compatibility
FETCH_MAX_RETRIES = cfg.FETCH_MAX_RETRIES
FETCH_BASE_DELAY = cfg.FETCH_BASE_DELAY
FETCH_MAX_DELAY = cfg.FETCH_MAX_DELAY
FETCH_RETRYABLE_STATUS = cfg.FETCH_RETRYABLE_STATUS
FETCH_NON_RETRYABLE_STATUS = cfg.FETCH_NON_RETRYABLE_STATUS


@dataclass
class ChunkResult:
    clean_rows: int = 0
    quar_rows: int = 0
    fetch_error: bool = False
    db_error: bool = False
    skipped: bool = False
    skip_reason: str = ""


async def _acquire_conn(pool: asyncpg.Pool) -> asyncpg.Connection:
    """Acquire a database connection from the pool.

    Handles both real asyncpg pools and test mocks (AsyncMock).
    Real pools return a coroutine from acquire(); mocks may return directly.

    Args:
        pool: asyncpg.Pool or mock pool object with acquire() method.

    Returns:
        asyncpg.Connection or mock connection object.

    Raises:
        Exception: Propagates any exception from pool.acquire().
    """
    acquired = pool.acquire()
    if asyncio.iscoroutine(acquired):
        acquired = await acquired
    return acquired


async def _release_conn(pool: asyncpg.Pool, conn: asyncpg.Connection) -> None:
    """Release a database connection back to the pool.

    Handles both real asyncpg pools (release() returns a coroutine) and
    test mocks (release() may return None). Swallows exceptions to avoid
    masking upstream failures.

    Args:
        pool: asyncpg.Pool or mock pool object with release() method.
        conn: Connection object to release, obtained from pool.acquire().

    Side Effects:
        Returns the connection to the pool for reuse.
    """
    released = pool.release(conn)
    if asyncio.iscoroutine(released):
        await released


async def _heartbeat_once() -> None:
    """Verify ThetaData terminal connectivity by sending a single heartbeat.

    Runs the synchronous heartbeat() in a background thread so it does not
    block the async event loop. Called once at the start of each backfill.

    Raises:
        Exception: Propagates any exception from the heartbeat call,
            typically indicating the terminal is unreachable.
    """
    await asyncio.to_thread(heartbeat, cfg)


async def _get_calendar() -> mcal.MarketCalendar:
    """Fetch the NYSE (XNYS) market calendar from pandas_market_calendars.

    The calendar is used throughout the pipeline for business-day and
    business-time calculations (e.g. compute_business_T, DTE windows).

    Returns:
        mcal.MarketCalendar instance for the NYSE exchange.
    """
    return mcal.get_calendar("XNYS")


async def _fetch_single_rate(
    client: AsyncThetaClient,
    symbol: str,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Fetch one rate symbol and format columns for DTE-aware merging.

    The fetcher already converts percent→decimal so ``rate`` is ready to
    use.  This wrapper renames the column to ``r_<symbol>`` for the
    multi-symbol merge step.

    Args:
        client: Authenticated ThetaData async client.
        symbol: Interest rate symbol (e.g. "SOFR", "TREASURY_M1").
        start_date: Start of the date range (inclusive).
        end_date: End of the date range (inclusive).

    Returns:
        DataFrame with columns ['date', 'r_<symbol>'] with rate in decimal
        form (e.g. 0.05 for 5%). Returns empty DataFrame on fetch failure.
    """
    async with STK:
        df = await async_fetch_interest_rate_eod(client, symbol, start_date, end_date)

    if df.empty:
        return df

    df = df.rename(columns={"created": "date", "rate": f"r_{symbol}"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[["date", f"r_{symbol}"]].copy()


async def _get_rates(
    client: AsyncThetaClient,
    start_date: dt.date,
    end_date: dt.date,
    cache: BoundedCache,
    rate_symbol: str = "SOFR",
    rate_symbols: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Fetch and cache risk-free interest rates for the backfill window.

    Supports multiple rate symbols for DTE-aware tenor matching (Section 7).
    All symbols are fetched in parallel and merged into a single DataFrame.

    Each rate is converted from percent to decimal and stored in a per-symbol
    column ``r_<symbol>`` (e.g. ``r_SOFR``, ``r_TREASURY_M1``, ``r_TREASURY_M3``).
    A default ``r`` column is set to the ``rate_symbol`` (SOFR) rate for
    backward compatibility.

    Results are cached by (symbols, start_date, end_date) to avoid redundant
    API calls across chunks.

    Args:
        client: Authenticated ThetaData async client.
        start_date: Start of the date range (inclusive).
        end_date: End of the date range (inclusive).
        cache: BoundedCache instance keyed by (symbol, start, end).
        rate_symbol: Default rate symbol for the ``r`` column (default "SOFR").
        rate_symbols: Tuple of rate symbols to fetch. If None, fetches only
            ``rate_symbol`` (backward compatible mode).

    Returns:
        DataFrame with columns ['date', 'r_<symbol>'..., 'r'] where all rates
        are in decimal form (e.g. 0.05 for 5%). Returns empty DataFrame on
        fetch failure or missing cache entry.
    """
    symbols_to_fetch = rate_symbols if rate_symbols is not None else (rate_symbol,)
    cache_key = (symbols_to_fetch, start_date, end_date)
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    # Fetch all rate symbols in parallel
    results = await asyncio.gather(
        *[_fetch_single_rate(client, sym, start_date, end_date) for sym in symbols_to_fetch],
        return_exceptions=True,
    )

    # Collect non-empty, non-exception results
    parts = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("Rate fetch failed for one symbol: %s", r)
            continue
        if not r.empty:
            parts.append(r)

    if not parts:
        log.warning("All rate fetches failed — returning empty DataFrame")
        await cache.set(cache_key, pd.DataFrame())
        return pd.DataFrame()

    # Merge all rate columns on date
    merged = parts[0]
    for rdf in parts[1:]:
        merged = merged.merge(rdf, on="date", how="outer")

    # Set default 'r' column = rate_symbol's rate (backward compat)
    default_r_col = f"r_{rate_symbol}"
    if default_r_col in merged.columns:
        merged["r"] = merged[default_r_col]
    else:
        r_cols = [c for c in merged.columns if c.startswith("r_")]
        if r_cols:
            merged["r"] = merged[r_cols[0]]
        else:
            merged["r"] = float("nan")

    await cache.set(cache_key, merged)
    return merged


async def _get_stock_ohlc_cached(
    client: AsyncThetaClient,
    symbol: str,
    chunk_start: dt.date,
    chunk_end: dt.date,
    cache: BoundedCache,
) -> pd.DataFrame:
    """Fetch stock OHLC data with per-chunk caching.

    Floors timestamps to minute precision and renames the close column to
    'spot_close' for downstream joins. Results are cached by (chunk_start, chunk_end)
    to avoid redundant API calls across overlapping chunks.

    Args:
        client: Authenticated ThetaData async client.
        symbol: Ticker symbol (e.g. "AMD").
        chunk_start: Start date of the chunk (inclusive).
        chunk_end: End date of the chunk (inclusive).
        cache: BoundedCache instance keyed by (chunk_start, chunk_end).

    Returns:
        DataFrame with columns ['timestamp', 'spot_close']; timestamps are
        tz-aware UTC floored to the minute. Returns empty DataFrame on
        fetch failure or missing cache entry.
    """
    cache_key = (chunk_start, chunk_end)
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    async with STK:
        df = await async_fetch_stock_ohlc(client, symbol, chunk_start, chunk_end)

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.floor("min")
        df = df.rename(columns={"close": "spot_close"})
        await cache.set(cache_key, df[["timestamp", "spot_close"]].copy())

    result = await cache.get(cache_key)
    return result if result is not None else pd.DataFrame()


async def _get_contracts_by_date(
    client: AsyncThetaClient,
    underlying: str,
    dates: set[dt.date],
    cache: BoundedCache,
) -> dict[dt.date, set[tuple[float, str]]]:
    """Fetch the as-of contract universe for each date in the set.

    Builds a mapping from date → {(strike, option_type)} so greeks rows can
    be filtered to only contracts that actually existed on that date
    (survivorship-safe universe, leakage rule 7).

    Args:
        client: Authenticated ThetaData async client.
        underlying: Ticker symbol (e.g. "AMD").
        dates: Set of dates to fetch contract universes for.
        cache: BoundedCache keyed by (underlying, date).

    Returns:
        Dict mapping each date to a set of (strike, option_type) pairs.
    """
    result: dict[dt.date, set[tuple[float, str]]] = {}

    async def _fetch_one(date: dt.date) -> tuple[dt.date, set[tuple[float, str]]]:
        """Fetch contracts for a single date, using cache if available."""
        cache_key = ("contracts", underlying, date)
        cached = await cache.get(cache_key)
        if cached is not None:
            return date, cached

        async with OPT:
            df = await async_fetch_option_list_contracts(client, underlying, date)

        contracts: set[tuple[float, str]] = set()
        if not df.empty and "strike" in df.columns and "right" in df.columns:
            for _, row in df.iterrows():
                opt_type = normalize_right(row.get("right", "C"))
                contracts.add((float(row["strike"]), opt_type))

        await cache.set(cache_key, contracts)
        return date, contracts

    tasks = [_fetch_one(d) for d in dates]
    fetched = await asyncio.gather(*tasks)
    for date, contracts in fetched:
        result[date] = contracts

    return result


async def _build_dividends_map(
    underlying: str,
    chunk_start: dt.date,
    chunk_end: dt.date,
    stk_df: pd.DataFrame,
) -> dict[dt.date, float]:
    """Build point-in-time dividend yield map for bar dates in the chunk."""
    if underlying.upper() == "AMD" and cfg.DIVIDEND_PROVIDER == "none":
        return {}

    fetcher = get_dividend_fetcher()
    events = await fetcher.fetch_dividends(underlying)

    spot_by_date: dict[dt.date, float] = {}
    if not stk_df.empty and "timestamp" in stk_df.columns and "spot_close" in stk_df.columns:
        for _, row in stk_df.iterrows():
            d = pd.to_datetime(row["timestamp"]).date()
            if chunk_start <= d <= chunk_end:
                spot_by_date[d] = float(row["spot_close"])

    dividends_map: dict[dt.date, float] = {}
    day = chunk_start
    while day <= chunk_end:
        spot = spot_by_date.get(day, 1.0)
        dividends_map[day] = compute_dividend_yield(
            events, spot, day, cfg.DIVIDEND_LOOKBACK_DAYS,
        )
        day += dt.timedelta(days=1)

    return dividends_map


async def _process_chunk(
    client: AsyncThetaClient,
    underlying: str,
    exp: dt.date,
    chunk_start: dt.date,
    chunk_end: dt.date,
    pool: asyncpg.Pool,
    run_id: int,
    cal: mcal.MarketCalendar,
    rates_df: pd.DataFrame,
    schedule_cache: dict,
    ohlc_cache: BoundedCache,
    contract_cache: BoundedCache | None = None,
    dividends_map: dict[dt.date, float] | None = None,
) -> ChunkResult:
    """Process a single (expiration, chunk) unit of the backfill pipeline.

    Orchestrates the full data pipeline for one chunk: parallel fetch of
    greeks, OI, and stock OHLC; survivorship-safe contract filtering;
    join; clean; math (T, forward, vega); then two-phase load to staging
    + load into final table + watermark advance — all inside a single
    database transaction for atomic exactly-once semantics.

    Args:
        client: Authenticated ThetaData async client.
        underlying: Ticker symbol (e.g. "AMD").
        exp: Option expiration date.
        chunk_start: Start date of the chunk (inclusive).
        chunk_end: End date of the chunk (inclusive).
        pool: asyncpg Pool for database operations.
        run_id: Current backfill run identifier.
        cal: NYSE market calendar for business time calculations.
        rates_df: DataFrame with columns ['date', 'r'] — risk-free rates.
        schedule_cache: Pre-computed business-time schedule dict (from
            _build_business_time_schedule).
        ohlc_cache: BoundedCache for stock OHLC data.
        contract_cache: BoundedCache for contract list data. If provided,
            survivorship-safe filtering is applied using list/contracts per date.

    Returns:
        ChunkResult with unambiguous status fields:
            clean_rows: Number of rows successfully written to the final table.
            quar_rows: Number of rows moved to quarantine.
            fetch_error: True if a fetch failed permanently.
            db_error: True if a database operation failed.
            skipped: True if the chunk was skipped (empty data, already completed, or race).

    Raises:
        asyncpg.UniqueViolationError: Caught internally; returns
            ChunkResult(skipped=True) to handle concurrent-runner races gracefully.
        Exception: Caught internally for transient failures; error logged and
            returned via ChunkResult(db_error=True) rather than propagated.
    """
    log.info("Processing exp=%s chunk [%s, %s]", exp, chunk_start, chunk_end)

    # 1. Fetch in parallel (OPT sem for options, STK sem for stock)
    async def _fetch_opt():
        """Fetch greeks with retry and OPT semaphore."""
        return await fetch_with_retry(
            async_fetch_option_greeks_first_order,
            client, underlying, exp, chunk_start, chunk_end,
            _sem=OPT,
        )

    async def _fetch_oi():
        """Fetch open interest with retry and OPT semaphore."""
        return await fetch_with_retry(
            async_fetch_option_open_interest,
            client, underlying, exp, chunk_start, chunk_end,
            _sem=OPT,
        )

    async def _fetch_stk():
        """Fetch stock OHLC via cache (no semaphore)."""
        return await fetch_with_retry(
            _get_stock_ohlc_cached,
            client, underlying, chunk_start, chunk_end, ohlc_cache,
        )

    opt_result, oi_result, stk_result = await asyncio.gather(
        _fetch_opt(), _fetch_oi(), _fetch_stk(),
        return_exceptions=True,
    )

    for name, result in [("greeks", opt_result), ("oi", oi_result), ("stock", stk_result)]:
        if isinstance(result, Exception):
            log.error("Fetch %s failed permanently: %s", name, result)
            return ChunkResult(fetch_error=True)

    opt_df, oi_df, stk_df = opt_result, oi_result, stk_result

    if opt_df.empty:
        log.info("Empty greeks fetch for exp=%s chunk [%s, %s], skipping", exp, chunk_start, chunk_end)
        return ChunkResult(skipped=True, skip_reason="no_data")
    if contract_cache is not None and not opt_df.empty:
        unique_bar_dates = set(pd.to_datetime(opt_df["timestamp"]).dt.date.unique())
        contracts_by_date = await _get_contracts_by_date(
            client, underlying, unique_bar_dates, contract_cache,
        )

        before_len = len(opt_df)

        # Vectorized survivorship filter via merge on (bar_date, strike, option_type)
        valid_rows: list[dict[str, object]] = []
        for bar_date, contracts in contracts_by_date.items():
            for strike, opt_type in contracts:
                valid_rows.append({
                    "bar_date": bar_date,
                    "strike": float(strike),
                    "option_type": opt_type,
                })
        if valid_rows:
            valid_df = pd.DataFrame(valid_rows)
            opt_df = opt_df.copy()
            opt_df["bar_date"] = pd.to_datetime(opt_df["timestamp"]).dt.date
            opt_df["strike"] = opt_df["strike"].astype(float)
            opt_df = opt_df.merge(
                valid_df,
                on=["bar_date", "strike", "option_type"],
                how="inner",
            ).drop(columns=["bar_date"])
        else:
            opt_df = opt_df.iloc[0:0].copy()

        after_len = len(opt_df)
        removed = before_len - after_len
        if removed > 0:
            log.info(
                "Survivorship filter removed %d/%d rows for exp=%s chunk [%s, %s]",
                removed, before_len, exp, chunk_start, chunk_end,
            )

        if opt_df.empty:
            log.info(
                "All rows filtered by survivorship check for exp=%s chunk [%s, %s], skipping",
                exp, chunk_start, chunk_end,
            )
            return ChunkResult(skipped=True, skip_reason="no_data_survivorship")
    opt_df = join_spot_and_oi(opt_df, stk_df, oi_df, schedule_cache=schedule_cache)

    # 2b. Pre-filter (fetch time) — Section 4: DTE band only
    opt_df = _prefilter_band(opt_df)

    # 3. Clean (structural checks; delta/OI/monotonicity deferred to post-join)
    clean_df, quar_df = clean_option_chain(opt_df, run_id=run_id)

    clean_len = len(clean_df) if not clean_df.empty else 0
    quar_len = len(quar_df) if not quar_df.empty else 0

    # 4. Math + post-join filters (forward-consistent delta/OI/monotonicity)
    post_quar_parts: list[pd.DataFrame] = []
    if not clean_df.empty:
        clean_df = attach_rates_and_math(
            clean_df, rates_df, cal, schedule_cache,
            dividends_map=dividends_map,
        )
        clean_df, post_quar_df = apply_post_join_filters(clean_df, run_id=run_id)
        if not post_quar_df.empty:
            post_quar_parts.append(post_quar_df)
        clean_df = finalize_slice_metadata(clean_df)
        clean_len = len(clean_df)

    if post_quar_parts:
        extra_quar = pd.concat(post_quar_parts)
        quar_df = pd.concat([quar_df, extra_quar], ignore_index=True) if not quar_df.empty else extra_quar
        quar_len = len(quar_df)

    # 5. Two-phase load + watermark advance in a SINGLE database transaction
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Re-check watermark inside transaction for fresh, serialized read
                completed = await get_completed_chunks(conn, underlying)
                chunk_key = (exp.isoformat(), chunk_end)
                if chunk_key in completed:
                    log.info(
                        "Skipping completed chunk (txn re-check): exp=%s end=%s",
                        exp, chunk_end,
                    )
                    return ChunkResult(skipped=True, skip_reason="already_completed")

                if not clean_df.empty:
                    await write_staging_batch(conn, clean_df)
                    await load_from_staging(conn, run_id)
                if not quar_df.empty:
                    await write_quarantine_batch(conn, quar_df, run_id)

                await advance_watermark(
                    conn, underlying, exp, chunk_end, "completed", clean_len, run_id,
                )
        return ChunkResult(clean_len, quar_len)
    except asyncpg.UniqueViolationError:
        log.warning(
            "Watermark race detected — chunk already completed by concurrent runner",
            extra={
                "expiration": exp.isoformat(),
                "chunk_end": chunk_end.isoformat(),
                "run_id": run_id,
            }
        )
        return ChunkResult(skipped=True, skip_reason="race_condition")
    except Exception as e:
        log.error(
            "Chunk processing failed for exp=%s chunk [%s, %s]: %s",
            exp, chunk_start, chunk_end, e,
        )
        return ChunkResult(clean_len, quar_len, db_error=True)


async def run_backfill(
    start_date: dt.date = dt.date(2018, 1, 1),
    end_date: dt.date | None = None,
    underlying: str = "AMD",
    rate_symbol: str = "SOFR",
) -> dict:
    """Run the full AMD eSSVI backfill pipeline.

    Args:
        start_date: Start date for backfill (default 2018-01-01)
        end_date: End date for backfill (default today)
        underlying: Ticker symbol for the underlying asset (default "AMD")
        rate_symbol: Interest rate symbol for rates fetch (default "SOFR")

    Returns:
        dict with stats: total_clean_rows, total_quarantined, errors, duration_seconds
    """
    start_time = time.monotonic()

    if end_date is None:
        end_date = dt.datetime.now(dt.timezone.utc).date()

    # 1. Initialize DB schema
    await init_schema()

    # 2. Get calendar for business time
    cal = await _get_calendar()

    # 3. Validate Theta terminal port and run entire backfill within client context
    async with AsyncThetaClient(cfg.THETA_CFG) as client:
        await _heartbeat_once()
        await async_validate_theta_port(client, cfg.THETA_HOST, cfg.THETA_PORT)
        # Pass date range to list/expirations if API supports it (High #55)
        expirations = await async_fetch_option_list_expirations(
            client, underlying,
            start_date=start_date, end_date=end_date,
        )

        max_exp = max(expirations) if expirations else end_date
        # Schedule buffer increased from 5 to 14 calendar days (High #46)
        earliest_needed = start_date - dt.timedelta(days=cfg.DTE_WINDOW_MAX + cfg.SCHEDULE_BUFFER_DAYS)
        latest_needed = max_exp + dt.timedelta(days=cfg.SCHEDULE_BUFFER_DAYS)
        schedule_cache = _build_business_time_schedule(
            cal,
            pd.Timestamp(earliest_needed, tz="US/Eastern"),
            pd.Timestamp(latest_needed, tz="US/Eastern"),
        )

        # 4. Get run ID
        pool = await get_pool()
        conn = await _acquire_conn(pool)
        run_id = await next_run_id(conn)
        await _release_conn(pool, conn)

        run_id_var.set(run_id)
        try:
            # 5. Filter expirations to those with DTE window overlapping [start_date, end_date]
            valid_expirations = []
            for exp in expirations:
                dte_start, dte_end = _dte_window(exp)
                if dte_end < start_date or dte_start > end_date:
                    continue
                chunk_start = max(dte_start, start_date)
                chunk_end = min(dte_end, end_date)
                if chunk_start <= chunk_end:
                    valid_expirations.append((exp, chunk_start, chunk_end))

            log.info("backfill_started", extra={
                "start_date": str(start_date),
                "end_date": str(end_date),
                "total_expirations": len(valid_expirations),
            })

            if not valid_expirations:
                log.info("No valid expirations in range [%s, %s]", start_date, end_date)
                total_clean = total_quar = total_errors = 0
            else:
                # 6. Create fresh caches per backfill run
                ohlc_cache = BoundedCache(
                    max_size=cfg.OHLC_CACHE_MAX_CHUNKS,
                    ttl_hours=cfg.OHLC_CACHE_TTL_HOURS
                )
                rates_cache = BoundedCache(
                    max_size=cfg.OHLC_CACHE_MAX_CHUNKS,
                    ttl_hours=cfg.RATES_CACHE_TTL_HOURS
                )
                contract_cache = BoundedCache(
                    max_size=cfg.OHLC_CACHE_MAX_CHUNKS,
                    ttl_hours=cfg.RATES_CACHE_TTL_HOURS,
                )

                # 7. Pre-fetch rates for entire range is no longer done.
                #    Rates are fetched per-chunk so cache keys include chunk
                #    boundaries (High #14).
                all_rate_symbols = tuple(sorted(set(
                    list(cfg.RATE_SYMBOLS_SHORT)
                    + list(cfg.RATE_SYMBOLS_MEDIUM)
                    + list(cfg.RATE_SYMBOLS_LONG)
                )))

                # 8. Process each expiration and its chunks
                total_clean = total_quar = total_errors = 0
                total_chunks = 0

                for exp, exp_start, exp_end in valid_expirations:
                    chunks = _month_chunks(exp_start, exp_end)
                    total_chunks += len(chunks)

                done_chunks = 0

                for exp, exp_start, exp_end in valid_expirations:
                    exp_var.set(exp.isoformat())
                    try:
                        chunks = _month_chunks(exp_start, exp_end)

                        for chunk_start, chunk_end in chunks:
                            chunk_key = f"{chunk_start}_to_{chunk_end}"
                            chunk_var.set(chunk_key)
                            try:
                                chunk_start_time = time.monotonic()

                                log.info("chunk_started", extra={
                                    "chunk_start": str(chunk_start),
                                    "chunk_end": str(chunk_end),
                                })

                                # Fetch rates per chunk so the cache key
                                # includes chunk boundaries (High #14)
                                chunk_rates_df = await _get_rates(
                                    client, chunk_start, chunk_end, rates_cache,
                                    rate_symbol=rate_symbol,
                                    rate_symbols=all_rate_symbols,
                                )

                                stk_for_div = await _get_stock_ohlc_cached(
                                    client, underlying, chunk_start, chunk_end, ohlc_cache,
                                )
                                dividends_map = await _build_dividends_map(
                                    underlying, chunk_start, chunk_end, stk_for_div,
                                )

                                result = await _process_chunk(
                                    client, underlying, exp, chunk_start, chunk_end,
                                    pool, run_id, cal, chunk_rates_df,
                                    schedule_cache, ohlc_cache, contract_cache,
                                    dividends_map=dividends_map,
                                )

                                chunk_duration = time.monotonic() - chunk_start_time

                                total_clean += result.clean_rows
                                total_quar += result.quar_rows
                                if result.fetch_error or result.db_error:
                                    total_errors += 1
                                done_chunks += 1

                                pct = (done_chunks / total_chunks * 100) if total_chunks > 0 else 100
                                log.info("chunk_completed", extra={
                                    "clean_rows": result.clean_rows,
                                    "quarantined_rows": result.quar_rows,
                                    "errors": 1 if result.fetch_error or result.db_error else 0,
                                    "duration_seconds": round(chunk_duration, 4),
                                    "progress_pct": round(pct, 1),
                                    "done_chunks": done_chunks,
                                    "total_chunks": total_chunks,
                                })
                            finally:
                                chunk_var.set(None)

                    finally:
                        exp_var.set(None)

            log.info("backfill_completed", extra={
                "total_clean_rows": total_clean,
                "total_quarantined": total_quar,
                "total_errors": total_errors,
            })
        finally:
            run_id_var.set(None)

    elapsed = time.monotonic() - start_time
    log.info("backfill_done", extra={
        "total_clean_rows": total_clean,
        "total_quarantined": total_quar,
        "total_errors": total_errors,
        "duration_seconds": round(elapsed, 1),
    })

    return {
        "total_clean_rows": total_clean,
        "total_quarantined": total_quar,
        "errors": total_errors,
        "duration_seconds": elapsed,
    }