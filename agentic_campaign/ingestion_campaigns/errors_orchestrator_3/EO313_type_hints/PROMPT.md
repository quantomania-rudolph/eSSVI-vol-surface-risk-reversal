# EO313: Type Hints on All Private Functions

## Persona

You are a **type safety advocate** who knows that 17 private functions without type hints defeats the purpose of using a statically typed language and makes refactoring dangerous.

## Core Objective

**Add complete type hints to all private functions in `orchestrator.py` and new modules.**

## Functions Missing Type Hints

| Function | File | Current |
|----------|------|---------|
| `_acquire_conn` | orchestrator.py | `async def _acquire_conn(pool):` |
| `_release_conn` | orchestrator.py | `async def _release_conn(pool, conn):` |
| `_heartbeat_once` | orchestrator.py | `async def _heartbeat_once() -> None:` |
| `_get_calendar` | orchestrator.py | `async def _get_calendar() -> mcal.MarketCalendar:` |
| `_get_rates` | orchestrator.py | `async def _get_rates(...):` |
| `_get_stock_ohlc_cached` | orchestrator.py | `async def _get_stock_ohlc_cached(...):` |
| `_join_spot` | orchestrator.py/joins.py | `def _join_spot(...):` |
| `_join_oi` | orchestrator.py/joins.py | `def _join_oi(...):` |
| `_attach_rates` | orchestrator.py/joins.py | `def _attach_rates(...):` |
| `_month_chunks` | orchestrator.py/chunking.py | `def _month_chunks(...):` |
| `_dte_window` | orchestrator.py/chunking.py | `def _dte_window(...):` |
| `_process_chunk` | orchestrator.py | `async def _process_chunk(...):` |
| `fetch_with_retry` | retry.py | `async def fetch_with_retry(...):` |
| `_is_retryable_error` | retry.py | `def _is_retryable_error(...):` |

## Required Format

```python
async def _acquire_conn(pool: asyncpg.Pool) -> asyncpg.Connection:
    ...

async def _release_conn(pool: asyncpg.Pool, conn: asyncpg.Connection) -> None:
    ...

async def _heartbeat_once() -> None:
    ...

async def _get_calendar() -> "mcal.MarketCalendar":
    ...

async def _get_rates(
    client: AsyncThetaClient,
    start_date: dt.date,
    end_date: dt.date,
    cache: BoundedCache,
) -> pd.DataFrame:
    ...

async def _get_stock_ohlc_cached(
    client: AsyncThetaClient,
    symbol: str,
    chunk_start: dt.date,
    chunk_end: dt.date,
    cache: BoundedCache,
) -> pd.DataFrame:
    ...

def _join_spot(opt_df: pd.DataFrame, stk_df: pd.DataFrame) -> pd.DataFrame:
    ...

def _join_oi(opt_df: pd.DataFrame, oi_df: pd.DataFrame) -> pd.DataFrame:
    ...

def _attach_rates(df: pd.DataFrame, rates_df: pd.DataFrame) -> pd.DataFrame:
    ...

def _month_chunks(
    start: dt.date, 
    end: dt.date, 
    max_days: int = cfg.MAX_CHUNK_DAYS
) -> list[tuple[dt.date, dt.date]]:
    ...

def _dte_window(
    exp: dt.date, 
    dte_min: int = cfg.DTE_WINDOW_MIN, 
    dte_max: int = cfg.DTE_WINDOW_MAX
) -> tuple[dt.date, dt.date]:
    ...

async def _process_chunk(
    client: AsyncThetaClient,
    exp: dt.date,
    chunk_start: dt.date,
    chunk_end: dt.date,
    conn: asyncpg.Connection,
    run_id: int,
    cal: "mcal.MarketCalendar",
    rates_df: pd.DataFrame,
    completed_chunks: set[tuple[str, dt.date]],
    schedule_cache: dict,
    ohlc_cache: BoundedCache,
    underlying: str,
) -> tuple[int, int, int]:
    ...
```

## Invariants

- ✅ `mypy dataingestion/orchestrator.py` passes
- ✅ All 77 tests pass
- ✅ Type hints use `from __future__ import annotations` for forward refs

## Success Criteria

```bash
mypy dataingestion/orchestrator.py dataingestion/retry.py dataingestion/joins.py dataingestion/chunking.py
# Should pass with no errors
```