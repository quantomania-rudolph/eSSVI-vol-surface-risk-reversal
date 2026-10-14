# Documentation index

**Hiring / resume verification** — read in this order:

1. [`REVIEWER_GUIDE.md`](REVIEWER_GUIDE.md) — 5-minute orientation  
2. [`RESUME_CLAIMS_EVIDENCE.md`](RESUME_CLAIMS_EVIDENCE.md) — every claim → code or SQL  
3. [`RESUME_DATA_PIPELINE_BRIEF.md`](RESUME_DATA_PIPELINE_BRIEF.md) — numbers-forward resume copy (aligned with snapshots)  
4. [`metrics/INGEST_SNAPSHOT.json`](metrics/INGEST_SNAPSHOT.json) — options / TimescaleDB (live)  
5. [`metrics/EQUITY_JOURNAL_SNAPSHOT.json`](metrics/EQUITY_JOURNAL_SNAPSHOT.json) — equity / PostgreSQL (journal)  
6. [`EQUITY_DATA_FOUNDATION.md`](EQUITY_DATA_FOUNDATION.md) — companion equity project paths  

**Reproduce:** `python scripts/reproduce_resume_metrics.py` · **Verify ingest:** `python -m dataingestion.verify`

**Pipeline deep dives:** [`../dataingestion.md`](../dataingestion.md) · [`../dataingestion/README.md`](../dataingestion/README.md)
