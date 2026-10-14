# Data Ingestion (`dataingestion/`)

**Reviewers:** verified metrics → [`../docs/metrics/INGEST_SNAPSHOT.json`](../docs/metrics/INGEST_SNAPSHOT.json) · reproduce → `python scripts/reproduce_resume_metrics.py` · claims map → [`../docs/RESUME_CLAIMS_EVIDENCE.md`](../docs/RESUME_CLAIMS_EVIDENCE.md)

Two **separate Theta worker pools**:

| Pipeline | CLI | Theta tier | Writes |
|----------|-----|------------|--------|
| **options** (default) | `--pipeline options` | STANDARD greeks + OI | `amd_surface_min` |
| **stock** | `--pipeline stock` | VALUE stock OHLC | `underlying_spot_1m` |

Pools are independent adaptive gates (`OPT_SEM` / `STK_SEM` in `theta_limiter.py`). Keep `OPT_SEM_LIMIT + STK_SEM_LIMIT ≤` terminal max when both run; stock-only or options-only may use the full per-pool budget up to `GLOBAL_THETA_SEM_LIMIT`.

- **Universe:** `SPY, QQQ, IWM, GLD, SLV, TLT, EEM, USO, UNG` (`--set indexes` / default)
- **Options DTE band:** calendar `MIN_DTE=5` … `MAX_DTE=180`
- **Options chunks:** **1 trading day** (`MAX_TRADING_DAYS_PER_CHUNK=1`)
- **Stock chunks:** `STOCK_MAX_TRADING_DAYS_PER_CHUNK` (default 21)
- **Spot on options path:** put-call parity (`SPOT_SOURCE=parity`); Theta stock OHLC only if `ohlc` / `hybrid`
- **ALFRED / Yahoo:** rates + dividends/splits (options path)

```bash
# Recommended index options backfill (one symbol at a time; Theta sem=2 in secrets.env)
python -m dataingestion.universe_cli run --mode backfill \
  --start 2024-01-01 --end 2026-07-24 --set indexes --pipeline options \
  --symbol-concurrency 1

# Stock-only VALUE-tier OHLC for the index ETF pool
python -m dataingestion.universe_cli run --mode backfill \
  --start 2024-01-01 --end 2026-07-24 --set indexes --pipeline stock

# Indexes stock backfill, then options surface (one process; options starts only if stock has zero failures)
python -m dataingestion.universe_cli run --mode backfill \
  --start 2024-01-01 --end 2026-07-24 --set indexes \
  --pipeline stock --then-pipeline options --verify

# Options surface only (STANDARD tier) with post-run verify
python -m dataingestion.universe_cli run --mode backfill \
  --start 2024-01-01 --end 2026-07-24 --set indexes --pipeline options --verify \
  --symbol-concurrency 1
```

Theta pacing (in `dataingestion/secrets.env`): `GLOBAL_THETA_SEM_LIMIT=2`, `OPT_SEM_LIMIT=2`,
`THETA_SLOTS_PER_CHUNK=1` (Phase A greeks-only), `REQUESTS_PER_SECOND=0.4`, greeks read timeout 300s.
See **`dataingestion/THETA_PACING.md`** for HTTP -1 mitigation, terminal `request_queue_length`, and preflight:
`python -m dataingestion.theta_preflight`.
Prefer **one** `universe_cli run` (file lock in `artifacts/.universe_cli.lock`).
**Windows:** use `scripts/run_index_options_ingest.ps1` (stops duplicates, verifies lock PID).
Stop workers: `scripts/stop_universe_cli.ps1`. Restart terminal: `scripts/restart_theta.ps1`.
Do not raise sem to tier “4” until `theta_preflight` and history greeks probes pass.
Phase A: `INGEST_OPTIONS_FETCH_OI=0` (OI in Phase B `oi_patch`).
`greeks/` and `essvi/` read the same `MIN_DTE` / `MAX_DTE` via `core_engine.shared.calibration_config` (no separate ingest window).

See `COLUMNS.md`, `run_order.md`, and `secrets.env.example`.
