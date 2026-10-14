# Resume claims → evidence map

Use this table to verify resume bullets in **under 10 minutes**. Numbers marked **LIVE** can be regenerated; **JOURNAL** come from documented equity production runs.

---

## Options / TimescaleDB (this repository)

| Resume claim | Metric | Evidence | Reproduce |
|--------------|--------|----------|-----------|
| Theta → TimescaleDB ingest pipeline | Architecture | [`dataingestion/orchestrator.py`](../dataingestion/orchestrator.py), [`dataingestion.md`](../dataingestion.md) | Read module docstrings |
| Hypertable + staging two-phase load | `create_hypertable`, COPY → upsert | [`dataingestion/db_writer.py`](../dataingestion/db_writer.py) | Search `create_hypertable`, `staging` |
| **~8.76M** clean surface rows | **8,754,632** in `amd_surface_min` | [`metrics/INGEST_SNAPSHOT.json`](metrics/INGEST_SNAPSHOT.json) **LIVE** | `scripts/reproduce_resume_metrics.py` |
| **~68%** quarantined at chunk level (strict QA) | **18.86M** quarantined / **31.7%** pass | Same snapshot **LIVE** | `SELECT SUM(rows_loaded), SUM(rows_quarantined) FROM ingest_progress WHERE status='completed'` |
| **~19.5M** auditable reject rows | `amd_surface_quarantine` count | Snapshot **LIVE** | Same script |
| **55%** PARITY_SKEW / **23%** DELTA_BAND | reject_code breakdown | Snapshot **LIVE** | `GROUP BY reject_code` on quarantine table |
| 10+ reject codes, not silent drops | Quarantine schema + codes | [`dataingestion/COLUMNS.md`](../dataingestion/COLUMNS.md), [`cleaning.py`](../dataingestion/cleaning.py) | Unit tests in `test_cleaning.py` |
| 8 core post-ingest checks (18 in `run_verification`) | verify suite | [`dataingestion/verify.py`](../dataingestion/verify.py) `run_verification`; core table in [`dataingestion.md`](../dataingestion.md) §22 | Import and call `run_verification(pool)` or run tests in `test_verify.py` |
| ≥99% non-null on spot/IV/vega | SLA in verify | `check_column_coverage` in verify.py | Run verify on populated DB |
| ±1% row count vs ingest_progress | SLA in verify | `check_row_counts` in verify.py | Run verify |
| 7-day Timescale chunks | Config | [`dataingestion/config.py`](../dataingestion/config.py) `CHUNK_TIME_INTERVAL_DAYS` | Read constant |
| 5m option bar cadence | Timestamp floor | [`dataingestion/fetchers.py`](../dataingestion/fetchers.py), COLUMNS.md | — |

---

## Equity / PostgreSQL (`data_foundation` — companion project)

Documented in [`EQUITY_DATA_FOUNDATION.md`](EQUITY_DATA_FOUNDATION.md). Evidence lives in **FILE OF DATA** folder on author machine; journal excerpt in [`metrics/EQUITY_JOURNAL_SNAPSHOT.json`](metrics/EQUITY_JOURNAL_SNAPSHOT.json).

| Resume claim | Metric | Evidence | Reproduce |
|--------------|--------|----------|-----------|
| **~289M** equity intraday bars ingested | 214M @ 5m + 75M @ 15m | `JOURNAL.md` 2026-05-21 **JOURNAL** | `SELECT COUNT(*) FROM market_5min.equity_bars` (on `data_foundation`) |
| PIT membership gap **103 → 4** (~96% reduction) | Audit 04 before/after 08f | `JOURNAL.md` **JOURNAL** | Run `04_violation_instruments_missing_membership.sql` |
| **0** core leakage violations (gates 01–04) | Sign-off | `JOURNAL.md` **JOURNAL** | `DONE/sql/audit/06_leakage_verification_suite/apply_all_06_violations_FAST.sql` |
| **8** bar-end backtest views | Deploy list | `DONE/Bar end leakage/READ.ME` | `01_deploy_bar_end_views.sql` |
| **5–15 min** look-ahead removed | bar_end vs ts_utc | Bar end README | Audit `03_audit_bar_end_semantics.sql` |
| Atoms refresh **~95 min** at full vault | Ops timing | `JOURNAL.md` **JOURNAL** | `SELECT alpha_atoms.refresh_materialized_atoms()` |
| Staged audit vs **3h+** monolith hang | Ops | `JOURNAL.md` **JOURNAL** | Compare staged `06/` vs legacy monolith script |
| Async FMP vault + checkpoints | Ingest design | `Vault/vault_equity (1).ipynb`, `pipeline_control.run_state` | Notebook + SQL schema |

---

## Explicitly out of scope (do not expect in this repo)

- NEAT Alpha Miner / genetic programming training outcomes
- Live trading PnL or deployed production strategies

---

## One-line resume blurb (aligned with this map)

Built production-grade equity and options data pipelines on PostgreSQL and TimescaleDB: ingested ~289M intraday equity bars with zero-violation leakage gates; engineered a quarantine-first Theta ingest with ~8.8M clean surface rows and ~19M auditable rejects (~68% strict filter rate on completed chunks).
