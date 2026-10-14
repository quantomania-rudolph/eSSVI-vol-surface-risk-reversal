# Zero-Dividend Equity Ingestion Runbook

Theta options + stock OHLC ingestion for **equities that have never paid cash dividends**.
Rates from **FRED**; splits from **Yahoo Finance**.
Universe = (S&P 500 ∪ Russell 2000) ∩ `zero_dividend_registry.csv`.

## Scope

| Included | Excluded |
|----------|----------|
| US equity options + minute OHLC via Theta | Dividend payers (AAPL, MSFT, JPM, …) |
| SOFR / T-bill rates via **FRED** | Theta interest_rate (deprecated) |
| Stock splits via **Yahoo** + `corporate_actions` cache | Theta stock/history/split (deprecated) |
| History from **2021-01-01** onward | Pre-2021 (Stocks Value minute floor) |
| ~1,071 registry symbols | Commodities / CME futures |

**Alpaca:** used only by `HMM-GMM/spot_bars/` — **not** the eSSVI `spot_close` source (Theta OHLC).

## Subscriptions

| Asset class | Tier | Role |
|-------------|------|------|
| Options | **Standard** | Greeks, OI, contract lists |
| Equities | **Value** | 1m OHLC from 2021-01-01 |
| Interest rates | **FRED** (external) | SOFR, DTB4WK, DTB3 |
| Splits | **Yahoo** (external) | Corporate-action quarantine |
| **Theta account cap** | highest tier | **4** concurrent |

## Environment

```bash
export DIVIDEND_PROVIDER=none          # required — no external dividend API
export STRICT_DIVIDEND_CONFIG=1
export FRED_API_KEY=...                # required — see dataingestion/secrets.env.example
export THETA_HOST=127.0.0.1
export THETA_PORT=25510                # from terminal banner
export GLOBAL_THETA_SEM_LIMIT=4
export STRICT_LOADER_METADATA=1
export STRICT_INGEST_START=1           # default — reject start_date < 2021-01-01
```

No `POLYGON_API_KEY` or `ALPHAVANTAGE_API_KEY` needed.
Do not commit `FRED_API_KEY` — use `dataingestion/secrets.env` (gitignored).

## Registry maintenance

Refresh when index constituents change or quarterly:

```bash
python scripts/build_zero_dividend_registry.py
```

This script:
1. Downloads Russell 2000 constituents → `config/universe/russell2000.csv`
2. Scans S&P 500 ∪ R2K via Yahoo Finance dividend history
3. Writes symbols with **zero** dividend events → `config/universe/zero_dividend_registry.csv`

Symbols that initiate dividends (e.g. GOOGL 2024) are automatically dropped on next refresh.

## Daily ingestion

```python
import asyncio
import datetime as dt
from dataingestion.orchestrator import run_daily_universe
from core_engine.shared.universe import get_ingest_symbols, get_pilot_symbols

trade_date = dt.date(2026, 7, 13)

# Pilot (~5 liquid zero-div names)
asyncio.run(run_daily_universe(trade_date, symbols=get_pilot_symbols()))

# Full ingest universe (~1,071 symbols — expect multi-hour overnight)
asyncio.run(run_daily_universe(trade_date))  # default: get_ingest_symbols()
```

## Verification

```python
from dataingestion.db_writer import get_pool
from dataingestion.verify import run_universe_checks
from core_engine.shared.universe import get_pilot_symbols

async def main():
    pool = await get_pool()
    report = await run_universe_checks(
        pool, trade_date=trade_date, symbols=get_pilot_symbols()
    )
    print(report["status"])

asyncio.run(main())
```

All ingested rows should have `mean_q ≈ 0`.

## eSSVI calibration

```python
from essvi.runtime import calibrate_universe
from core_engine.shared.universe import get_pilot_symbols
import pandas as pd

ts = pd.Timestamp("2026-07-13 15:59", tz="America/New_York")
result = calibrate_universe(ts, get_pilot_symbols(), conn=db_conn)
```

Per-symbol `RuntimeState` and DB warmstart are isolated by `underlying` / `surface_id` prefix.

## Math contract (zero-div)

- `q = 0` for all bars
- `annual_dividend = 0` on Theta greeks fetch
- `F = S · exp(r · T)`

## Config files

| File | Role |
|------|------|
| `config/universe/sp500.csv` | S&P 500 constituents |
| `config/universe/russell2000.csv` | Russell 2000 constituents |
| `config/universe/zero_dividend_registry.csv` | Ingest allowlist (never paid dividends) |
| `config/symbol_profiles.yaml` | Defaults `dividend_policy: zero` |

## Explicit symbol guard

`run_backfill` calls `assert_ingest_eligible(symbol)` — dividend payers raise before any Theta fetch.
