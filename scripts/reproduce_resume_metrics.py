#!/usr/bin/env python3
"""
Reproduce resume/GitHub-verifiable metrics for the options ingest pipeline.

Requires: dataingestion/secrets.env with PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE

  python scripts/reproduce_resume_metrics.py
  python scripts/reproduce_resume_metrics.py --json docs/metrics/INGEST_SNAPSHOT.json
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "dataingestion" / "secrets.env"


def _load_env() -> None:
    if not SECRETS.is_file():
        raise SystemExit(f"Missing {SECRETS} — copy from secrets.env.example")
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, help="Write snapshot JSON for docs/CI")
    args = parser.parse_args()
    _load_env()

    import psycopg2

    conn = psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.environ["PGPORT"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        dbname=os.environ["PGDATABASE"],
    )
    cur = conn.cursor()

    cur.execute(
        "SELECT COALESCE(SUM(rows_loaded),0), COALESCE(SUM(rows_quarantined),0) "
        "FROM ingest_progress WHERE status = 'completed'"
    )
    loaded_prog, quar_prog = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM amd_surface_min")
    loaded_hypertable = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM amd_surface_quarantine")
    quar_total = cur.fetchone()[0]

    cur.execute(
        """
        SELECT reject_code, COUNT(*) AS n
        FROM amd_surface_quarantine
        GROUP BY reject_code
        ORDER BY n DESC
        """
    )
    reject_rows = cur.fetchall()
    reject_breakdown = {code: int(n) for code, n in reject_rows}
    reject_pct = {
        code: round(100.0 * n / quar_total, 1) if quar_total else 0.0
        for code, n in reject_breakdown.items()
    }

    cur.execute("SELECT MIN(ts), MAX(ts), COUNT(DISTINCT underlying) FROM amd_surface_min")
    ts_min, ts_max, n_under = cur.fetchone()

    cur.execute(
        """
        SELECT hypertable_name, num_chunks
        FROM timescaledb_information.hypertables
        WHERE hypertable_name IN ('amd_surface_min', 'underlying_spot_1m', 'amd_surface_quarantine')
        ORDER BY 1
        """
    )
    hypertables = {name: chunks for name, chunks in cur.fetchall()}

    total_processed = int(loaded_prog) + int(quar_prog)
    pass_rate = round(100.0 * loaded_prog / total_processed, 1) if total_processed else 0.0

    snapshot = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": os.environ.get("PGDATABASE"),
        "scope_note": "Current DB snapshot; universe/date range may differ from full backfill plans.",
        "ingest_progress_completed": {
            "rows_loaded": int(loaded_prog),
            "rows_quarantined": int(quar_prog),
            "pass_rate_pct": pass_rate,
            "quarantine_rate_pct": round(100.0 - pass_rate, 1) if total_processed else 0.0,
        },
        "amd_surface_min": {
            "row_count": int(loaded_hypertable),
            "ts_min": ts_min.isoformat() if ts_min else None,
            "ts_max": ts_max.isoformat() if ts_max else None,
            "distinct_underlyings": int(n_under),
        },
        "amd_surface_quarantine": {
            "row_count": int(quar_total),
            "reject_code_counts": reject_breakdown,
            "reject_code_pct_of_quarantine": reject_pct,
        },
        "timescaledb_hypertables_chunks": hypertables,
        "reproduce_command": "python scripts/reproduce_resume_metrics.py",
        "verify_suite_command": "python -m dataingestion.verify",
    }

    print("=== Options pipeline metrics (reproducible) ===")
    print(f"generated_at_utc: {snapshot['generated_at_utc']}")
    print(f"ingest_progress: loaded={loaded_prog:,} quarantined={quar_prog:,} pass_rate={pass_rate}%")
    print(f"amd_surface_min: {loaded_hypertable:,} rows | underlyings={n_under} | {ts_min} .. {ts_max}")
    print(f"amd_surface_quarantine: {quar_total:,} rows")
    for code, n in reject_rows[:6]:
        print(f"  {code}: {n:,} ({reject_pct[code]}% of quarantine)")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
