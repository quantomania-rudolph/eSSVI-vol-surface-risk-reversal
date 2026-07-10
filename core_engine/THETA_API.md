# Theta Data Connection Engine — Agent Reference

## Architecture

Theta Data is accessed through a **local Java bridge**, not a remote API. Your code sends HTTP requests to `ThetaTerminalv3.jar` running on `127.0.0.1:25510`. The JAR handles authentication (via `creds.txt`) and proxies requests to Theta Data's backend.

```
Your Python code  ──HTTP──>  ThetaTerminalv3.jar (Java, port 25510)  ──>  Theta Data servers
```

This engine wraps that HTTP layer with async I/O, concurrency controls, retries, and typed response parsing.

## Two-Layer API

**Layer 1: `AsyncThetaClient`** — raw HTTP client. Call `.get(path, params)` and get back `(status_code, parsed_body)` tuples. Use this for custom/one-off endpoints.

**Layer 2: `ThetaFetchers`** — high-level typed methods. Uses `AsyncThetaClient` under the hood. Returns `None`, `float`, `list[date]`, or `pd.DataFrame`. **This is what you should use.**

## Quick Start Pattern

```python
import asyncio
from core_engine.shared.theta_client import AsyncThetaClient, heartbeat
from core_engine.shared.fetchers import ThetaFetchers
from core_engine.shared.config import CFG

async def main():
    heartbeat()  # Raises ThetaTerminalDown if JAR not running
    
    async with AsyncThetaClient(CFG) as client:
        fetchers = ThetaFetchers(client)
        
        spot = await fetchers.spot_price("SPY", is_index=False)
        exps = await fetchers.list_expirations("SPY")
        chain = await fetchers.option_chain_greeks_snapshot(
            "SPY", exps[0], annual_div=0.0, spot=spot
        )

asyncio.run(main())
```

`AsyncThetaClient` is an **async context manager**. Use `async with` to create/destroy the underlying `aiohttp.ClientSession`.

## Concurrency Model

The client enforces two limits simultaneously:

| Mechanism | Config key | Default | Effect |
|-----------|-----------|---------|--------|
| `asyncio.Semaphore` | `MAX_CONCURRENT_REQUESTS` | 7 | Max in-flight HTTP calls |
| Token bucket | `REQUESTS_PER_SECOND` | 0 (disabled) | Max calls/sec |

Tier caps (enforced by `Config.validate()`):

| Tier | Max concurrent |
|------|---------------|
| FREE | 1 |
| VALUE | 2 |
| STANDARD | 4 |
| PRO | 8 |

The default of 7 leaves 1 slot for the heartbeat check on PRO.

**To share one client across many tickers**, use `asyncio.gather` with an additional semaphore to avoid deadlocking the pool:

```python
sem = asyncio.Semaphore(CFG.MAX_CONCURRENT_REQUESTS)
async def fetch_one(ticker):
    async with sem:
        return await fetchers.spot_price(ticker, is_index=False)

results = await asyncio.gather(*[fetch_one(t) for t in tickers])
```

## Error Handling & Retries

`AsyncThetaClient.get()` retries up to **3 times** with exponential backoff (0.5s, 1s, 2s) on:

- **Network errors**: `aiohttp.ClientError`, `asyncio.TimeoutError`
- **Retryable HTTP statuses** (transient server issues): `429, 471, 472, 474, 502, 503, 504, 570, 571`

Non-retryable errors (e.g., 400, 401, 404) return immediately as `(status, {"error": "...", "status": N})`.

After all retries exhausted: returns `(-1, {"error": str(last_exc)})`.

**Always check `status`** to distinguish success from failure:

```python
status, payload = await client.get("/v3/stock/snapshot/quote", {"symbol": "SPY"})
if status != 200:
    # handle error — payload will be {"error": "...", "status": N}
```

### Top-Level Fetchers

All `ThetaFetchers` methods check `status == 200` internally and return sentinel values (`None`, `0.0`, `[]`, empty `DataFrame`) on failure. No exceptions are raised from fetcher methods.

## All Available Endpoints (via `ThetaFetchers`)

### Stock / Index Spot Prices

```python
# Stock: GET /v3/stock/snapshot/quote?symbol=SPY
# Returns mid = (bid + ask) / 2, or None
spot = await fetchers.spot_price("SPY", is_index=False)

# Index: GET /v3/index/snapshot/price?symbol=SPX
# Tries columns: price, last, value, close
vix = await fetchers.index_snapshot_price("VIX")
```

### Option Expirations

```python
# GET /v3/option/list/expirations?symbol=SPY
# Returns sorted list of unique dates, or []
exps = await fetchers.list_expirations("SPY")

# Convenience: next N expirations from a given date
nearest = await fetchers.nearest_expirations("SPY", as_of=date.today(), n=3)
```

### Option Chain + Greeks (Snapshot)

```python
# GET /v3/option/snapshot/greeks/all
# Required params: symbol, expiration (YYYYMMDD), strike_range, annual_dividend, rate_type, version, stock_price
# Returns DataFrame with columns: strike, right/option_type, bid, ask, mid_price,
#   delta, gamma, theta, vega, rho, implied_vol/iv_api, volume, open_interest, etc.
# Empty DataFrame on failure.
chain = await fetchers.option_chain_greeks_snapshot(
    "SPY", 
    expiration=date(2026, 7, 21),
    annual_div=0.0,   # Annual cash dividend per share, NOT yield. 0.0 for indices.
    spot=spot          # Current underlying price (affects strike centering)
)
```

Column normalization is applied automatically:
- `right` → `option_type` (C/P)
- `implied_vol` → `iv_api`
- `delta/gamma/theta/vega/rho` → `delta_api/gamma_api/etc.`
- `mid_price` computed from `(bid + ask) / 2` if missing

### Option Chain + Greeks (Historical)

```python
# GET /v3/option/history/greeks/all
# Same as snapshot plus: interval, start_date (YYYYMMDD), end_date (YYYYMMDD)
# Returns 5-minute bars as a DataFrame with a "timestamp" column
hist = await fetchers.option_chain_greeks_history(
    "SPY",
    expiration=date(2026, 7, 21),
    start=date(2026, 6, 1),
    end=date(2026, 6, 28),
    annual_div=0.0,
    spot=0.0,  # 0 = let Theta use its own spot
)
```

### Option Trades (Snapshot)

```python
# GET /v3/option/snapshot/trade?symbol=SPY&expiration=YYYYMMDD&strike_range=20
# Returns DataFrame of most recent trade per contract
trades = await fetchers.option_trades_snapshot("SPY", expiration=date(2026, 7, 21))
```

### Risk-Free Rate

```python
# GET /v3/stock/history/rate?tenor=90 (fallback: /v2/hist/rate/value)
# Returns continuously compounded rate (decimal, e.g. 0.045 = 4.5%)
# Falls back to 0.045 if endpoint unavailable
rate = await fetchers.risk_free_rate_cc(tenor_days=90)
```

### Dividends

```python
# GET /v3/stock/history/dividend?symbol=SPY&start_date=20100101
# Returns trailing 12-month sum of cash dividends per share. 0.0 if no data.
annual_div = await fetchers.annual_dividend_amount("SPY")

# Computed: annual_div / spot. Returns 0.0 if spot <= 0.
div_yield = await fetchers.dividend_yield("SPY", spot=spot)
```

### Stock Splits

```python
# GET /v3/stock/history/split?symbol=SPY
# Returns DataFrame of split events, or empty DataFrame
splits = await fetchers.stock_splits("SPY")
```

## Response Parsing

`parse_response_body()` handles three response formats automatically:
1. **JSON object/array** → `dict` or `list[dict]`
2. **NDJSON** (one JSON object per line) → `list[dict]`
3. **CSV with header** → `pd.DataFrame`

`to_dataframe()` normalizes these into a DataFrame. It also handles Theta's v2 "format/response" envelope:
```json
{"format": ["col1", "col2"], "response": [[val1, val2], ...]}
```

## Configuration

All settings via environment variables. `Config` is a frozen `@dataclass` with defaults:

```
THETA_HOST=127.0.0.1      # Terminal address
THETA_PORT=25510           # Terminal port
THETA_TIMEOUT_S=30         # HTTP timeout per request
THETA_TIER=PRO             # FREE/VALUE/STANDARD/PRO
MAX_CONCURRENT_REQUESTS=7  # Max simultaneous requests
REQUESTS_PER_SECOND=0      # 0 = unlimited rate
HEARTBEAT_RETRIES=5        # Retries for heartbeat check
STRIKE_RANGE=20            # ±N strikes around ATM for option chains
THETA_RATE_TYPE=sofr       # Risk-free rate curve
THETA_GREEKS_VERSION=latest
MAX_EXPIRATIONS_PER_TICKER=3
```

The global singleton is `CFG = Config()`. Override by passing a custom `Config(...)` to constructors.

## Dependencies

```
aiohttp>=3.9.0
asyncpg>=0.29.0      # Only needed if using IngestionLogger
numpy>=1.26.0
pandas>=2.0.0
pytz>=2024.1
```

## Prerequisites (Outside This Engine)

1. **Java 21+** installed on the machine
2. **ThetaTerminalv3.jar** downloaded and running: `java -jar ThetaTerminalv3.jar`
3. **`creds.txt`** alongside the JAR: line 1 = email, line 2 = password
4. **Active Theta Data subscription** with access to the endpoints being called (US equity options, indices, Greeks, historical bulk, etc.)

## Common Gotchas

- **Indices use different endpoints**: `spot_price("SPX", is_index=True)` hits `/v3/index/snapshot/price`, not the stock quote endpoint.
- **Dividends are cash amounts, not yields**: `annual_dividend_amount()` returns dollars per share. Convert to yield yourself: `annual_div / spot`.
- **`stock_price` in Greeks calls centers strike selection**: Pass 0.0 to let Theta use its own spot, or pass the actual spot to control strike centering.
- **Historical Greeks require date range in `YYYYMMDD` format**, handled automatically by the fetcher.
- **The client is not thread-safe** — it's designed for `asyncio`, not `threading`.
- **`heartbeat()` is synchronous and blocks** — it uses `urllib` (not aiohttp) for a quick pre-flight check before async work begins.
- **No built-in universe management** — the engine doesn't know about S&P 500, tickers, or which symbols are valid. You provide symbol strings.