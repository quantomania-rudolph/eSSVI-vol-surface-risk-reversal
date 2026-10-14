# Zero-Dividend Equity Ingestion (see docs/ZERO_DIV_INGEST_RUNBOOK.md)

Forward-looking daily ingestion for **never-paid-dividend** equities only.
Universe = (S&P 500 ∪ Russell 2000) ∩ `config/universe/zero_dividend_registry.csv`.

> **Note:** The SP500 pilot stages below are superseded. Use `get_pilot_symbols()` and
> `get_ingest_symbols()` from `core_engine.shared.universe`.

## Subscriptions (this deployment)

| Asset class | Tier | Concurrent |
|-------------|------|------------|
| Options | **Standard** | — |
| Equities / index | **Value** | — |
| Interest rates | **Free** | — |
| **Account-wide cap** | highest tier | **4** |

`GLOBAL_THETA_SEM_LIMIT=4` — options, stock OHLC, rates, and list endpoints share one funnel.

## Environment checklist

```bash
export DIVIDEND_PROVIDER=polygon   # or alphavantage
export STRICT_DIVIDEND_CONFIG=1
export POLYGON_API_KEY=...         # or ALPHAVANTAGE_API_KEY
export THETA_HOST=127.0.0.1
export THETA_PORT=25510            # confirm from ThetaTerminalv3 banner (often 25503)
export GLOBAL_THETA_SEM_LIMIT=4
export STRICT_LOADER_METADATA=1
```

Theta Terminal: place `creds.txt` next to `ThetaTerminalv3.jar` under `theta_terminal/`.

## Rollout stages

### Stage 1 — Pilot (5 symbols)

```python
from core_engine.shared.universe import get_pilot_symbols
from dataingestion.orchestrator import run_daily_universe
import datetime as dt, asyncio

trade_date = dt.date(2026, 7, 13)  # last completed RTH session
asyncio.run(run_daily_universe(trade_date, symbols=get_pilot_symbols()))
```

Pilot set: `AMD, AMZN, TSLA, PLTR, CRWD` (zero-dividend registry only).

### Stage 2 — Liquidity bucket (~50)

```python
from core_engine.shared.universe import get_liquidity_bucket_symbols
asyncio.run(run_daily_universe(trade_date, symbols=get_liquidity_bucket_symbols(50)))
```

Tune `strike_range` in `config/symbol_profiles.yaml` if payloads are too large.

### Stage 3 — Full S&P 500

```python
asyncio.run(run_daily_universe(trade_date))  # loads config/universe/sp500.csv
```

Expect **hours** overnight at 4 concurrent requests.

## Daily procedure

1. Confirm Theta Terminal is up (`heartbeat` / port validation).
2. On index rebalance days, refresh `config/universe/sp500.csv`.
3. Run `run_daily_universe(trade_date=yesterday)`.
4. Run verification:

```python
from dataingestion.db_writer import get_pool
from dataingestion.verify import run_universe_checks
import asyncio

async def main():
    pool = await get_pool()
    report = await run_universe_checks(pool, trade_date=trade_date, symbols=get_pilot_symbols())
    print(report["status"])

asyncio.run(main())
```

5. Calibrate:

```python
from essvi.runtime import calibrate_universe
from core_engine.shared.universe import get_pilot_symbols
import pandas as pd

ts = pd.Timestamp("2026-07-13 15:59", tz="America/New_York")
result = calibrate_universe(ts, get_pilot_symbols(), conn=db_conn)
```

For liquidity-universe EOD fleet (job queue, `--run-id` scoped workers, bulk panels), see
[`docs/ESSVI_FLEET_RUNBOOK.md`](ESSVI_FLEET_RUNBOOK.md):

```bash
python -m essvi fleet run --start 2026-07-13 --end 2026-07-14 --workers 8
```

6. Alert on: quarantine rate spikes, `ConnectivityContractError`, kill-switch triggers.

## Dividend dual-path (critical)

For each non-zero-div symbol:

1. Provider yield `q` → forward `F = S·exp((r−q)·T)` via `dividends_map`.
2. Theta `annual_dividend` (trailing cash dollars) passed into `greeks/first_order`.
3. AMD uses `dividend_policy: zero` / `q_override: 0.0` — both layers stay at 0.

## Config files

| File | Role |
|------|------|
| `config/universe/sp500.csv` | Current constituents |
| `config/symbol_profiles.yaml` | tick_scheme, dividend_policy, strike_range, split_check |

## Out of scope (v1)

- Historical SP500 survivorship backfill
- Multi-terminal sharding
- Earnings event windows
