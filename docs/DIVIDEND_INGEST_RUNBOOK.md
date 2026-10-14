# Dividend Ingest Runbook (Plan 1B + corporate-action hardening)

Yahoo chart `events=div` → TimescaleDB `dividends` + `reference_fetch_state` →
trailing TTM continuous `q` for dividend payers. Discrete PV schedules are
**strict point-in-time**: Yahoo announce dates are proxies (`announce_date_is_proxy`),
so future cash amounts never enter historical discrete forwards.

## Env

```bash
export DIVIDEND_PROVIDER=yahoo          # production default
export DIVIDEND_FORWARD_MODE=discrete   # PV-of-cash forwards (default)
export STRICT_DIVIDEND_CONFIG=1
export DIVIDEND_PREFETCH_TTL_HOURS=168
export DIVIDEND_HISTORY_BUFFER_DAYS=400
export SPLIT_PREFETCH_TTL_HOURS=168
export SPLIT_HISTORY_BUFFER_DAYS=14
# Optional: STRICT_DIVIDEND_IN_LIFE=1  # hard quarantine when ex in [bar, exp]
```

No API key required for Yahoo. FRED key still required for rates (`secrets.env`).

## Production acceptance (index ETF tier)

```bash
# Refresh durable dividends + full split history for INDEX_SURFACE_SYMBOLS
python scripts/check_index_reference_data.py --force-all --verify-source

# Audit only (fetch-state TTL + Yahoo reconcile)
python scripts/check_index_reference_data.py --check-only --verify-source
```

Expect `ok: true` with no `failures`. Empty Yahoo split history is proved via
`reference_fetch_state` (status=ok, event_count=0) — never via fake corporate_actions rows.

Scheduled feeds (`daily_ingest.feeds refresh-splits|refresh-dividends`) default to
`get_index_surface_symbols()` and return `status=failed` if any symbol errors.

## Universe

- Index surface tier: `get_index_surface_symbols()` (SPY, QQQ, IWM, GLD, SLV, TLT, EEM, USO, UNG)
- Zero-div profiles (GLD/SLV/USO/UNG): no dividend events; still require split fetch-state
- Payers: successful, fresh dividend fetch-state with event_count > 0

## Strict PIT limitation

Yahoo does not publish declaration dates. Events persist `announce_date_is_proxy=true`
with `announced_date = ex_date`. Historical discrete schedules therefore exclude all
Yahoo future cash; pricing falls back to parity-implied forward / trailing TTM `q`
until a genuine (non-proxy) announcement is available from another provider.

## Prefetch smoke

```python
import asyncio, datetime as dt
from dataingestion.dividends import DividendFetcher

async def main():
    f = DividendFetcher("yahoo")
    for sym in ("AAPL", "MSFT"):
        ev = await f.fetch_dividends(sym, start_date=dt.date(2020, 1, 1))
        print(sym, len(ev), ev[:2] if ev else None)

asyncio.run(main())
```

Expect `len(ev) > 0` for AAPL/MSFT since 2020.

## Re-backfill

After enabling Plan 1B / PIT hardening, **manually** re-backfill dividend-payer symbols —
historical `q`, `forward_price`, `log_moneyness`, `local_implied_vol`, and vega weights
may be stale. Zero-div names need no dividend-driven re-backfill.

## eSSVI gate

`essvi.runtime` fail-closes with `reason=reference_data_not_ready` when required
dividend/split fetch-state is missing, stale, or failed. Audit payloads include
`reference_readiness` with the exact deficiency. Corporate ex-date cold starts still
use persisted dividend + split events.

**Note:** end-to-end index eSSVI calibration cannot be certified until index panels
exist in `amd_surface_min`.

## Known limitations

- Yahoo has no declaration date → `announced_date = ex_date` + `announce_date_is_proxy`.
- Chunk-level Theta `annual_dividend` uses chunk-end as-of (vendor IV may skew on ex-dates); eSSVI uses `local_implied_vol`.
- Continuous `q` TTM remains for rows with no PIT-known ex in option life; discrete PV only when a non-proxy declaration is known.
- ``DIVIDEND_FORWARD_MODE=discrete`` (default) uses PV-of-cash forwards; ``continuous`` restores legacy TTM-only.
- ``GREEKS_DISCRETE_EX_DATE_POLICY=discrete`` (default) prices ex-in-life via bump solver; ``reject`` quarantines.
- Yahoo may historically adjust cash amounts after splits → document + re-backfill if needed.
