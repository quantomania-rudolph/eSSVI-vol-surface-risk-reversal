# A1 — Data Fetchers

**Role:** Senior Python async I/O engineer specializing in Theta Data v3 API integration.

## Your Mission

Build `dataingestion/fetchers.py` — the **only** module that speaks HTTP to Theta Terminal v3.
No other module in this pipeline touches the network. You own the fetch layer end-to-end.

## What You Build

One file: `dataingestion/fetchers.py`

It contains six async functions that each call `AsyncThetaClient.get()` with the correct
endpoint path, parameters, and error handling. Functions return `pd.DataFrame`.

### Functions to Implement

```python
async def fetch_option_greeks_first_order(
    client: AsyncThetaClient,
    symbol: str,
    expiration: date,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """GET /v3/option/history/greeks/first_order
    Required params: symbol, expiration (YYYYMMDD), strike="*", right="both",
    interval="1m", start_date, end_date, annual_dividend=0, rate_type="sofr",
    version="latest", format="ndjson"
    Capped at 1-month date range. Caller chunks before calling this.
    Returns empty DataFrame on failure or no data.
    """

async def fetch_stock_ohlc(
    client: AsyncThetaClient,
    symbol: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """GET /v3/stock/history/ohlc
    Required params: symbol, interval="1m", start_date, end_date, format="ndjson"
    Returns columns: timestamp, open, high, low, close, volume
    Returns empty DataFrame on failure or no data.
    """

async def fetch_interest_rate_eod(
    client: AsyncThetaClient,
    symbol: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """GET /v3/interest_rate/history/eod
    Required params: symbol (e.g. "SOFR", "TREASURY_M1"), start_date, end_date,
    format="ndjson"
    Returns columns: created, rate (PERCENT, e.g. 4.50 = 4.5%)
    Returns empty DataFrame on failure or no data.
    """

async def fetch_option_open_interest(
    client: AsyncThetaClient,
    symbol: str,
    expiration: date,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """GET /v3/option/history/open_interest
    Required params: symbol, expiration, start_date, end_date, format="ndjson"
    Returns daily OI. Returns empty DataFrame on failure or no data.
    """

async def fetch_option_list_expirations(
    client: AsyncThetaClient,
    symbol: str,
) -> list[date]:
    """GET /v3/option/list/expirations
    Required params: symbol, format="ndjson"
    Returns sorted list of expiration dates, or empty list on failure.
    """

async def fetch_option_list_contracts(
    client: AsyncThetaClient,
    symbol: str,
    date: date,
) -> pd.DataFrame:
    """GET /v3/option/list/contracts
    Required params: symbol, date, format="ndjson"
    Returns DataFrame of strikes/rights live on that date.
    Returns empty DataFrame on failure or no data.
    """
```

### Column Contract

Read `dataingestion/COLUMNS.md` Section I. Each function must return DataFrames
with exactly the columns specified. No extra columns, no missing columns.

Key details:
- `timestamp` must be **floored to the minute boundary** and **UTC timezone-aware**.
- `option_type` normalized to single-character "C" or "P" (Theta returns "CALL"/"PUT" in `right`).
- `underlying` column hardcoded to `"AMD"`.
- `annual_dividend` is always 0 for AMD.
- `interval` is always `"1m"`.
- Every response must use `format=ndjson` (Theta's NDJSON is line-delimited JSON objects).

### How to Use the Client

```python
from core_engine.shared.theta_client import AsyncThetaClient
from core_engine.shared.parse import parse_response_body

async with AsyncThetaClient(cfg) as client:
    # The CALLER (orchestrator) owns the semaphore. You do NOT create one.
    status, payload = await client.get(
        "/v3/option/history/greeks/first_order",
        {
            "symbol": "AMD",
            "expiration": "20260721",
            "strike": "*",
            "right": "both",
            "interval": "1m",
            "start_date": "20260601",
            "end_date": "20260628",
            "annual_dividend": 0,
            "rate_type": "sofr",
            "version": "latest",
            "format": "ndjson",
        },
        ticker="AMD",
    )
    if status != 200:
        return pd.DataFrame()  # Empty = no data, caller handles
    # payload is list[dict] from NDJSON parsing
    df = pd.DataFrame(payload)
```

### Invariants — NEVER Violate

1. **Never create a semaphore.** Concurrency is the orchestrator's job, not yours.
2. **Never call `heartbeat()`.** That's the orchestrator's job.
3. **Never write to disk or database.** You return DataFrames only.
4. **Never filter or clean data.** Raw data goes to `cleaning.py`.
5. **Never retry beyond the client's built-in 3 attempts.** The client handles retries on 429/5xx.
6. **Never request more than 1 calendar month per date range.** The caller chunks.
7. **Never use `format=json`.** Use `format=ndjson` for all history endpoints.
8. **Never import from `dataingestion.*`.** You only depend on `core_engine.shared.*` and `pandas`.
9. **Always set `_phase = "raw"`** in every returned DataFrame that has data.
10. **Empty DataFrame on failure** — never raise exceptions from network errors, return empty.

### Key Reference Files

- `dataingestion.md` Section 1 (endpoint map) and Sections 0/7 (AMD specifics) — **read these carefully**
- `dataingestion/COLUMNS.md` Section I — **the column contract you must satisfy**
- `core_engine/THETA_API.md` — engine architecture (note: some endpoints there are v2/PRO; this plan uses v3/Standard)
- `core_engine/shared/theta_client.py` — read AsyncThetaClient.get() signature
- `core_engine/shared/parse.py` — parse_response_body, to_dataframe
- `core_engine/shared/config.py` — Config/CFG structure

### Verification Script

Run `dataingestion/__init__.py` as a module with `pytest` or `python -m pytest`:

```bash
cd c:\Users\Rudol\Desktop\ThetaData_greeks
python -m pytest dataingestion/test_fetchers.py -v
```

The verification script (`dataingestion/test_fetchers.py`) will:
1. Mock `AsyncThetaClient.get()` to return synthetic NDJSON responses.
2. Call each of your 6 functions and verify:
   - Correct endpoint paths and query parameters
   - Correct column names and dtypes in returned DataFrames
   - Empty DataFrame returned when status != 200
   - `_phase == "raw"` on all non-empty DataFrames
   - `timestamp` is timezone-aware UTC and floored to minutes
   - `underlying` is "AMD"
   - `option_type` is "C" or "P"
3. Verify the semaphore is never created inside your module.
4. Verify no disk I/O or DB connection attempts.

**Do not write the verification script yourself.** I will provide it in
`dataingestion/test_fetchers.py`. Your job is only `dataingestion/fetchers.py`.

### Common Mistakes to Avoid

- Using `interval="5m"` instead of `interval="1m"` — check the plan!
- Using `strike_range=N` instead of `strike="*"` — we want ALL strikes.
- Using `/v3/option/history/greeks/all` — that's PRO tier. Use `first_order`.
- Forgetting to floor timestamps to minute boundaries.
- Returning a DataFrame with different column names than the contract.
- Raising exceptions instead of returning empty DataFrames on failure.
- Using `date_range` as the param name — it's two separate `start_date` and `end_date` params.