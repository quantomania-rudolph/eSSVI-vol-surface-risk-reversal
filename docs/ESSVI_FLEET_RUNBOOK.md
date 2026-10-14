# eSSVI Fleet Runbook

Default universe: **liquidity** (~150 GICS-balanced names + sector anchors). Escalate with `--universe-mode index_union` only when needed.

**Bar cadence:** ingest writes **15m option bar closes** to `amd_surface_min` (1m spot OHLC joined as-of). Fleet symbol-day jobs calibrate ~26 bars/RTH, not 390 minutes.

## Success metric

Fleet coverage is **`surface_usable` symbol-minutes**: minutes where params were published (`calibrated` / `partial_surface`) **or** the consumer path reused a same-session published surface (`consume_published_surface`).

```text
surface_usable_pct = 100 * sum(n_usable) / sum(n_minutes)
```

## Day pipeline

```text
1) Ingest (default --set liquidity) → universe_run_id / ingest_run_id
2) Verify → degraded registry
3) essvi fleet enqueue (same symbols + optional --ingest-run-id)
4) Workers with mandatory --run-id
5) essvi fleet status --run-id N
6) Greeks fleet enqueue (same symbol list + linked run metadata)
```

### Prepare + enqueue

```bash
# Optional: update degraded registry from verify (Python)
python -c "import asyncio; from essvi.universe_operator import prepare_fleet_day; print(asyncio.run(prepare_fleet_day()))"

python -m essvi init-schema

python -m essvi fleet enqueue \
  --start 2024-01-02 --end 2024-01-03 \
  --universe-mode liquidity --liquidity-n 150 \
  --ingest-run-id 12345
# note printed run_id
```

### Run workers (silent, run-scoped)

Workers **must** pass `--run-id`. Without it they refuse to claim production jobs.

```bash
# Local in-process workers
python -m essvi fleet run \
  --start 2024-01-02 --end 2024-01-03 \
  --workers 8 --run-id N

# Or enqueue once, then spawn silent subprocesses
python -m essvi fleet run \
  --start 2024-01-02 --end 2024-01-03 \
  --workers 8 --run-id N --subprocess

# Standalone worker (another host / shell)
python -m essvi worker --run-id N --max-idle-rounds 5
```

When `workers >= cpu_count`, the coordinator forces `ESSVI_RHO_PARALLEL=0` to avoid nested ProcessPool oversubscription.

### Status / reclaim

```bash
python -m essvi fleet status --run-id N --watch
python -m essvi fleet reclaim
python -m essvi fleet requeue-dead --run-id N
```

## Job model (warmstart-safe)

- Job grain: **`(underlying, trade_date, run_id)`** — one claim runs the full RTH day chronologically with a single `RuntimeState`.
- Claim: `FOR UPDATE SKIP LOCKED` filtered by `run_id`.
- Lease heartbeat + reclaim expired claims; dead jobs after `ESSVI_JOB_MAX_ATTEMPTS`.
- **Ownership fence:** heartbeat loss aborts the minute loop; `complete_job` / `fail_job` only mutate rows still `claimed` by that `worker_id` (no poison of reclaimed pending jobs).
- Bulk panel load: one DB round-trip per symbol-day via `load_symbol_day_panels`, then `calibrate_minute(..., panel=...)`.
- Prefer `--ingest-run-id` (or `ESSVI_INGEST_RUN_ID`) so panels do not mix multi-ingest vintages.
- Claim DB (`PG*`) and calibrate DB (`ESSVI_DATABASE_URL`) must match; unset URL derives from `PG*`.

## Preflight

Before calibrating, workers probe **max distinct tradeable expiries across RTH** (not a single 14:00 bar). If max `< MIN_EXPIRIES_PER_MINUTE` (2), the job is marked `preflight_skip`.

## Env knobs

| Variable | Default | Meaning |
|----------|---------|---------|
| `ESSVI_FLEET_WORKERS` | `min(32, cpu)` | Suggested worker count |
| `ESSVI_JOB_LEASE_BASE_SEC` | 600 | Base lease |
| `ESSVI_JOB_LEASE_PER_MINUTE_SEC` | 2.0 | Lease add per est minute |
| `ESSVI_WORKER_HEARTBEAT_SEC` | 60 | Heartbeat interval |
| `ESSVI_INGEST_RUN_ID` | unset | Pin loader vintage |
| `ESSVI_RHO_PARALLEL` | auto | Forced `0` when workers saturate CPU |
| `PGHOST` / `PGPORT` / … | local | Fleet job queue (asyncpg) |
| `ESSVI_DATABASE_URL` | — | Sync calibrate connection (psycopg2) |

## Live RTH (thin follow-on)

```bash
python -m essvi live-minute --ts "2024-01-02 10:30:00" --universe-mode liquidity
```

Keep a long-lived `dict[str, RuntimeState]` across minutes in a daemon; shard symbols across processes for horizontal scale.

## Cross-pipeline run linkage

`essvi_run_registry` stores `run_id`, `universe_mode`, `liquidity_n`, optional `ingest_universe_run_id`, `ingest_run_id`, and later `greeks_run_id` so ingest → essvi → greeks share the same symbol list and vintage.
