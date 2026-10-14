# Reviewer guide (5 minutes)

If you are verifying **resume claims** about data ingestion, cleaning, and indexing, start here.

This repository (**[eSSVI-vol-surface-risk-reversal](https://github.com/quantomania-rudolph/eSSVI-vol-surface-risk-reversal)**) contains the **options / TimescaleDB** pipeline. The **equity PostgreSQL** pipeline is documented as a companion project in [`EQUITY_DATA_FOUNDATION.md`](EQUITY_DATA_FOUNDATION.md) (same author; not all equity SQL lives in this repo).

---

## 60-second navigation

| I want to verify… | Go here |
|-------------------|---------|
| **Claim → evidence map (master table)** | [`RESUME_CLAIMS_EVIDENCE.md`](RESUME_CLAIMS_EVIDENCE.md) |
| **Live options ingest numbers** | [`metrics/INGEST_SNAPSHOT.json`](metrics/INGEST_SNAPSHOT.json) + run [`../scripts/reproduce_resume_metrics.py`](../scripts/reproduce_resume_metrics.py) |
| **Equity ~289M bars, leakage gates** | [`metrics/EQUITY_JOURNAL_SNAPSHOT.json`](metrics/EQUITY_JOURNAL_SNAPSHOT.json) + [`EQUITY_DATA_FOUNDATION.md`](EQUITY_DATA_FOUNDATION.md) |
| **Quarantine / reject codes** | [`../dataingestion/COLUMNS.md`](../dataingestion/COLUMNS.md) § quarantine + snapshot JSON |
| **Timescale hypertables & load pattern** | [`../dataingestion/db_writer.py`](../dataingestion/db_writer.py) + [`../dataingestion.md`](../dataingestion.md) |
| **Post-ingest QA (8 checks)** | [`../dataingestion/verify.py`](../dataingestion/verify.py) — run `python -m dataingestion.verify` |
| **Pipeline blueprint** | [`../dataingestion.md`](../dataingestion.md) |

---

## Reproduce options metrics (requires local DB)

1. Copy `dataingestion/secrets.env.example` → `dataingestion/secrets.env` (gitignored).
2. Start Postgres + TimescaleDB with the schema from `python -m dataingestion.db_writer` / orchestrator init.
3. Run:

```bash
python scripts/reproduce_resume_metrics.py
python scripts/reproduce_resume_metrics.py --json docs/metrics/INGEST_SNAPSHOT.json
python -m dataingestion.verify
```

Expected headline numbers (snapshot **2026-10-14** on author machine):

- **8.76M** rows loaded (`ingest_progress`, completed)
- **18.86M** rows quarantined with `reject_code`
- **31.7%** chunk pass rate to `amd_surface_min`
- Top quarantine: **PARITY_SKEW ~55%**, **DELTA_BAND ~23%**

---

## What this repo is / is not

| In scope (documented + code) | Out of scope for this repo |
|------------------------------|----------------------------|
| Theta → clean panel → TimescaleDB | NEAT / genetic alpha mining training |
| Quarantine-first cleaning | Production trading execution |
| eSSVI calibration (`essvi/`) | Full FMP equity vault source tree |

---

## Reviewer checklist

- [ ] Read [`RESUME_CLAIMS_EVIDENCE.md`](RESUME_CLAIMS_EVIDENCE.md) — each claim has a file or SQL path.
- [ ] Confirm [`INGEST_SNAPSHOT.json`](metrics/INGEST_SNAPSHOT.json) matches `reproduce_resume_metrics.py` on a DB with data.
- [ ] Skim [`dataingestion/verify.py`](../dataingestion/verify.py) for automated SLAs (≥99% critical columns, ±1% row reconciliation).
- [ ] For equity claims, read journal-backed [`EQUITY_JOURNAL_SNAPSHOT.json`](metrics/EQUITY_JOURNAL_SNAPSHOT.json) and companion doc — do not expect all SQL under this repo root.

---

## Simulated reviewer feedback (and what we changed)

**Problem:** Root README led with eSSVI vision; resume claims about ingest metrics were hard to find.

**Fix:** This guide + claims map at top of README + committed JSON snapshots + reproduce script.

**Problem:** Quarantine percentages looked like marketing without raw SQL.

**Fix:** Snapshot JSON includes counts and percents; script prints the same queries reviewers can run.

**Problem:** Equity and options pipelines split across folders/repos.

**Fix:** Explicit companion doc — equity evidence paths listed; no implied single-repo fiction.

**Problem:** Token-in-remote URL (security).

**Fix:** Remote URL must be credential-free; use `gh auth login` or env `GH_TOKEN` locally (never commit tokens).
