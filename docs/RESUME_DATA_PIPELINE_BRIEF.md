# Resume Brief — Data Ingestion, Cleaning & Indexing (Quant Pipelines)

**Canonical reviewer entry:** [`REVIEWER_GUIDE.md`](REVIEWER_GUIDE.md) · **Claim map:** [`RESUME_CLAIMS_EVIDENCE.md`](RESUME_CLAIMS_EVIDENCE.md)

**One-line blurb (resume summary line)**

Built production-grade equity and options data pipelines on **PostgreSQL** and **TimescaleDB**: ingested **~289M** intraday equity bars, deployed **bar-end–correct** feature layers with **zero-violation** leakage gates on **200M+** rows, and engineered a **quarantine-first** Theta → Timescale options stack with **8** automated post-ingest quality checks.

---

## Measured outcomes (documented production runs)

These numbers come from **FILE OF DATA** operational journal and pipeline specs, plus a **live DB snapshot** for options (regenerate: `python scripts/reproduce_resume_metrics.py`).

### Equity vault (`data_foundation` / PostgreSQL)

| Metric | Value | Context |
|--------|--------|---------|
| **Equity bars ingested (gap backfill)** | **~214M** (5m) + **~75M** (15m) ≈ **289M rows** | S&P historical gap ingest, **2010 → 2026-05-19** |
| **Future-dated raw bar gate** | **0** violations (target) | `ts_utc > now()` checks before trusting vault |
| **PIT membership coverage gap** | **103 → 4** missing instruments | After strict S&P rebuild + gap ingest; **~96% reduction** in membership gaps via `08f` fallback |
| **Final membership gate** | **4 → 0** (after 5m∪15m fix) | Audit `04_instruments_missing_membership` signed off at **n_violations = 0** |
| **Core leakage suite (post-ingest)** | **0** on checks **01–04** | Raw future ts, future bar_end, split assignment, membership |
| **Atom layer rebuild time** | **~95 minutes** | `refresh_materialized_atoms()` at full vault scale |
| **Audit runtime improvement** | **3+ hour hung monolith → staged gate** | Replaced full-table monolith on **200M+** rows with FAST staged scripts **01–04** |
| **Walk-forward index splits** | **8 splits / interval** | SQL split rebuild on bar_end-anchored atoms (feature indexing layer) |
| **Bar-end look-ahead removed** | **5–15 minutes** per intraday join | Documented risk when joining on vendor `ts_utc` (bar open) vs `bar_end_utc` (bar close) |
| **Backtest-safe views deployed** | **8** `*_bars_bt` views | Equity/commodity/forex 5m+15m bar-end surfaces |
| **Exchange calendar coverage** | **2000–2040** | Session/holiday/half-day audit chain (7 steps, violation grids) |
| **Ingest write batching** | **50,000 rows** flush | Vault async pipeline control + sub-symbol checkpoints |

Journal snapshot: [`metrics/EQUITY_JOURNAL_SNAPSHOT.json`](metrics/EQUITY_JOURNAL_SNAPSHOT.json) · companion paths: [`EQUITY_DATA_FOUNDATION.md`](EQUITY_DATA_FOUNDATION.md)

### Options / greeks (Theta → **TimescaleDB**) — **live DB snapshot**

| Metric | Value | Context |
|--------|--------|---------|
| **Chunk-level pass rate** | **~31.7% loaded** / **~68.3% quarantined** | `ingest_progress` sums: **8.76M** loaded vs **18.86M** quarantined (completed chunks) |
| **Clean surface rows (hypertable)** | **8,754,632** | `amd_surface_min` (SPY + EEM, **2021-01-04 → 2024-08-23**) |
| **Quarantine rows (auditable)** | **19,541,729** | `amd_surface_quarantine` — not silently dropped |
| **Top reject: PARITY_SKEW** | **55.3%** of quarantine | Put/call parity spot consistency filter |
| **DELTA_BAND** | **23.2%** | Outside tradeable delta band post-join |
| **LOW_OI** | **9.8%** | Below open-interest floor |
| **NO_QUOTE + SPREAD_HARD** | **5.7% + 5.6%** | Missing/crossed quotes vs hard relative spread |
| **LOCAL_IV_FAIL + tail codes** | **<1%** | IV solver / monotonicity / business-T edge cases |
| **Default option bar cadence** | **5-minute** bar closes | Timestamp floor to option bar close (not tick noise) |
| **DTE safety band (pre-filter)** | **5–180 days** | Shared MIN/MAX DTE band before deeper math/joins |
| **Quarantine reject taxonomy** | **10+ codes** | e.g. `NO_QUOTE`, `CROSSED`, `SPREAD_HARD`, `LOW_OI`, `MONOTONICITY`, `LOCAL_IV_FAIL`, … |
| **Post-ingest verification suite** | **8 automated checks** | `dataingestion/verify.py` (completeness, null coverage, filter impact, no future rows, …) |
| **Critical column null SLA** | **≥ 99%** non-null | `spot_price`, `implied_vol`, `vega` coverage gate in verify |
| **Load vs progress reconciliation** | **within 1%** | Hypertable row counts vs summed `rows_loaded` in `ingest_progress` |
| **Filter impact alerts** | **>50% LOW_OI** or **>80% NO_QUOTE** | Flags bad vendor days / config drift (quarantine analytics, not silent drops) |
| **Timescale chunk interval** | **7 days** | Hypertable tuning + compression policy cadence |
| **Hypertables (examples)** | `amd_surface_min`, `underlying_spot_1m`, health tables | Staging → upsert two-phase load; compression toggled for bulk backfill |
| **Spot vol adjunct DB** | Dedicated **`spot_vol`** Timescale (port **5433** in docs) | 1m Garman–Klass on `bar_end_utc`; parallel to main ingest silo |

Live snapshot: [`metrics/INGEST_SNAPSHOT.json`](metrics/INGEST_SNAPSHOT.json)

---

## Resume bullets (copy-paste, numbers-forward)

- Ingested and validated **~289M** US equity intraday bars (**214M @ 5m**, **75M @ 15m**, **2010–2026**) into PostgreSQL with checkpointed async FMP vault ingest and idempotent upserts.
- Reduced **PIT membership gaps by ~96%** (**103 → 4** instruments) after historical gap load; closed remaining **15m-only** orphan gap to hit **0** missing-membership violations on the production audit gate.
- Achieved **0 violations** on **4** core leakage gates over **200M+** atom rows (future timestamps, future bar_end, split assignment, membership coverage).
- Replaced a **3+ hour** monolithic leakage audit with a **staged FAST gate suite** suitable for repeated post-ETL sign-off.
- Eliminated **5–15 minute** intraday look-ahead by deploying **8** bar-end backtest views and auditing join semantics across equity, commodity, and forex vaults.
- Built **Theta → TimescaleDB** options pipeline with **10+** quarantine reject codes, **8** post-ingest verification checks, and **≥99%** non-null SLAs on critical greeks/IV columns.
- Implemented **two-phase hypertable loads** (staging COPY → upsert), **7-day** chunking/compression policies, and **±1%** row-count reconciliation against ingest progress metadata.

---

## Honest scope note (for interviews)

- **Delivered:** Data ingestion, cleaning, SQL feature/index layers, leakage gates, Timescale options ingest, verification tooling, operational runbooks.
- **Out of scope for this brief:** Genetic alpha mining / NEAT training (not treated as a shipped outcome).

*Refresh options totals with `python scripts/reproduce_resume_metrics.py --json docs/metrics/INGEST_SNAPSHOT.json`.*
