# EO311: Parameterize Ticker and Rate Symbol

## Persona

You are a **pragmatic engineer** who knows that while the pipeline only runs AMD/SOFR today, hardcoding ticker symbols throughout the orchestrator makes future reuse painful and violates the Open/Closed Principle.

## Core Objective

**Add `underlying` and `rate_symbol` parameters to `run_backfill()` and propagate them through the call chain.**

## Current Hardcoded Values

| Location | Line | Hardcoded |
|----------|------|-----------|
| `run_backfill` signature | 391 | `underlying="AMD"` missing |
| Expirations fetch | 428 | `"AMD"` |
| Greeks fetch | 485 | `"AMD"` |
| OI fetch | 490 | `"AMD"` |
| Stock OHLC fetch | 495 | `"AMD"` |
| Rates fetch | 471 | `"SOFR"` |

## Required Changes

```python
# run_backfill signature
async def run_backfill(
    start_date: dt.date = dt.date(2018, 1, 1),
    end_date: dt.date | None = None,
    underlying: str = "AMD",           # NEW
    rate_symbol: str = "SOFR",         # NEW
) -> dict:

# Propagate to _process_chunk
async def _process_chunk(
    client: AsyncThetaClient,
    exp: dt.date,
    chunk_start: dt.date,
    chunk_end: dt.date,
    conn,
    run_id: int,
    cal,
    rates_df: pd.DataFrame,
    completed_chunks: set[tuple[str, dt.date]],
    schedule_cache: dict,
    ohlc_cache: BoundedCache,
    underlying: str,      # NEW
) -> tuple[int, int, int]:

# In fetch calls:
async_fetch_option_greeks_first_order(client, underlying, exp, chunk_start, chunk_end)
async_fetch_option_open_interest(client, underlying, exp, chunk_start, chunk_end)
async_fetch_stock_ohlc(client, underlying, chunk_start, chunk_end)
async_fetch_interest_rate_eod(client, rate_symbol, start_date, end_date)
```

## Invariants

- ✅ Default values preserve AMD/SOFR behavior
- ✅ All 77 tests pass with defaults
- ✅ No hardcoded "AMD" or "SOFR" in orchestrator logic

## Success Criteria

### Functional
1. `run_backfill(underlying="NVDA", rate_symbol="TREASURY_M1")` works
2. All existing tests pass without modification
3. Config constants for defaults (optional)

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
# Add test: test_parameterized_ticker_and_rate
```