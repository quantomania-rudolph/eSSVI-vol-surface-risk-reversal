# Companion: FILE OF DATA (PostgreSQL equity pipeline)

The **equity vault and SQL feature layer** (`data_foundation`) is maintained in a separate project tree called **FILE OF DATA**. It is **not fully vendored inside this Git repository**, but resume claims about equity ingest and leakage gates are **documented and reproducible** there.

This page tells reviewers exactly what to look for without pretending all SQL lives under `ThetaData_greeks/`.

---

## What was built

1. **Ingestion** — Async FMP `vault_equity` notebook → `market_5min` / `market_15min` with idempotent upserts and `pipeline_control.run_state` checkpoints.
2. **Cleaning / time semantics** — `bar_end_utc` views (bar **close** knowable time), calendar/session audits, splits/corp actions with PIT staging.
3. **Indexing** — Materialized `alpha_atoms`, PIT S&P membership (FMP index changes), walk-forward split tables, bar_end indexes.
4. **Quality gates** — SQL scripts return `n_violations`; production sign-off required **0** on blocking checks.

---

## Documented production metrics (journal)

Source: **JOURNAL.md** entry **2026-05-21** (gap ingest + audit 06). Snapshot: [`metrics/EQUITY_JOURNAL_SNAPSHOT.json`](metrics/EQUITY_JOURNAL_SNAPSHOT.json).

| Metric | Value |
|--------|------:|
| 5m equity bars (gap ingest) | ~214,000,000 |
| 15m equity bars (gap ingest) | ~75,000,000 |
| Approximate total | ~289,000,000 |
| Date span | 2010 → 2026-05-19 |
| Atoms refresh duration | ~95 minutes |
| Missing membership (before 08f) | 103 instruments |
| Missing membership (after 08f) | 4 → **0** after 5m∪15m fix |
| Leakage gates 01–04 | **0 violations** |
| Walk-forward splits per interval | 8 |

---

## Key paths (FILE OF DATA project)

| Purpose | Path |
|---------|------|
| Post-ingest checklist | `READ-ME-NOW.txt` |
| Operational journal | `JOURNAL.md` |
| Leakage scope | `DATA_LEAKAGE_ACTION_PLAN.txt` |
| Bar-end deploy + audits | `DONE/Bar end leakage/` |
| Leakage gate 06 (FAST) | `DONE/sql/audit/06_leakage_verification_suite/` |
| Vault ingest | `Vault/vault_equity (1).ipynb` |
| SQL run order | `DONE/sql/RUN_ORDER.txt` |

---

## SQL to verify on `data_foundation`

```sql
-- Scale check (expect millions+ on full universe)
SELECT COUNT(*) AS n5 FROM market_5min.equity_bars;
SELECT COUNT(*) AS n15 FROM market_15min.equity_bars;

-- Future raw bars (expect 0)
SELECT COUNT(*) FROM market_5min.equity_bars WHERE ts_utc > now();

-- Atoms refresh (long-running; run in maintenance window)
SELECT alpha_atoms.refresh_materialized_atoms();
```

Leakage gates: run staged scripts in `sql/audit/06_leakage_verification_suite/RUN_ORDER.txt` — each returns `n_violations`.

---

## Relationship to this repo

- **ThetaData_greeks** consumes optional equity features later; options ingest does **not** require vault completion.
- Shared concepts: **point-in-time**, **bar_end clocks**, **quarantine/audit instead of silent drops**.

For the master claim index, see [`RESUME_CLAIMS_EVIDENCE.md`](RESUME_CLAIMS_EVIDENCE.md).
