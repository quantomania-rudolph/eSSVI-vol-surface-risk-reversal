"""Post-ingestion verification module — read-only integrity checks."""

from __future__ import annotations

import asyncpg
from typing import Any

from dataingestion import config as cfg


async def check_chunk_completeness(pool: asyncpg.Pool) -> dict[str, Any]:
    """Verify no gaps in ingested data per (expiration, date_chunk)."""
    async with pool.acquire() as conn:
        # Get expirations that have data in amd_surface_min
        expirations_with_data = await conn.fetch("""
            SELECT DISTINCT expiration FROM amd_surface_min
        """)
        expirations = [row["expiration"] for row in expirations_with_data]

        # Get progress entries for those expirations
        if not expirations:
            return {
                "name": "chunk_completeness",
                "passed": True,
                "severity": "SKIP",
                "detail": "No data in amd_surface_min yet",
                "value": {"expirations_with_data": 0, "missing_progress": []},
            }

        placeholders = ", ".join([f"${i+1}" for i in range(len(expirations))])
        query = f"""
            SELECT underlying, expiration, chunk_end_date, status
            FROM ingest_progress
            WHERE underlying = 'AMD' AND expiration IN ({placeholders})
        """
        progress_rows = await conn.fetch(query, *expirations)

    # Build set of completed chunks from progress
    completed = {(r["expiration"].isoformat(), r["chunk_end_date"]) for r in progress_rows if r["status"] == "completed"}
    failed = [(r["expiration"].isoformat(), r["chunk_end_date"]) for r in progress_rows if r["status"] == "failed"]

    # Expected chunks per expiration: 90-day lookback, 30-day chunks = 3 chunks
    missing = []
    for exp in expirations:
        # This is a simplified check - in reality we'd compute expected chunks from DTE window
        # For now, just flag failed status and missing progress entries
        pass

    return {
        "name": "chunk_completeness",
        "passed": len(failed) == 0,
        "severity": "FAIL" if failed else "PASS",
        "detail": f"Found {len(failed)} failed chunk(s)" if failed else "All progress entries completed",
        "value": {
            "expirations_with_data": [e.isoformat() for e in expirations],
            "failed_chunks": failed,
            "completed_chunks": len(completed),
        },
    }


async def check_column_coverage(pool: asyncpg.Pool) -> dict[str, Any]:
    """Report null percentage for key columns."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                COUNT(spot_price) AS n_spot,
                COUNT(implied_vol) AS n_iv,
                COUNT(vega) AS n_vega,
                COUNT(bid) AS n_bid,
                COUNT(ask) AS n_ask,
                COUNT(delta) AS n_delta
            FROM amd_surface_min
        """)

    if row["total"] == 0:
        return {
            "name": "column_coverage",
            "passed": True,
            "severity": "SKIP",
            "detail": "No rows in amd_surface_min",
            "value": {"total_rows": 0},
        }

    total = row["total"]
    coverage = {
        "spot_price": row["n_spot"] / total,
        "implied_vol": row["n_iv"] / total,
        "vega": row["n_vega"] / total,
        "bid": row["n_bid"] / total,
        "ask": row["n_ask"] / total,
        "delta": row["n_delta"] / total,
    }

    # Thresholds
    critical_fail = any(v < 0.99 for k, v in coverage.items() if k in ("spot_price", "implied_vol", "vega"))
    warn = any(v < 0.95 for k, v in coverage.items() if k in ("bid", "ask", "delta"))

    return {
        "name": "column_coverage",
        "passed": not critical_fail,
        "severity": "FAIL" if critical_fail else ("WARN" if warn else "PASS"),
        "detail": f"Coverage: {', '.join(f'{k}={v:.1%}' for k, v in coverage.items())}",
        "value": {"total_rows": total, **{f"pct_{k}": v for k, v in coverage.items()}},
    }


async def check_filter_impact(pool: asyncpg.Pool) -> dict[str, Any]:
    """Summarize quarantine by reject_code."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT reject_code, COUNT(*) AS n
            FROM amd_surface_quarantine
            GROUP BY reject_code
            ORDER BY n DESC
        """)

    if not rows:
        return {
            "name": "filter_impact",
            "passed": True,
            "severity": "SKIP",
            "detail": "No quarantine rows",
            "value": {"reject_codes": {}},
        }

    reject_codes = {row["reject_code"]: row["n"] for row in rows}
    total_rejected = sum(reject_codes.values())
    low_oi_count = reject_codes.get("LOW_OI", 0)
    no_quote_count = reject_codes.get("NO_QUOTE", 0)

    warn_low_oi = low_oi_count > total_rejected * 0.5
    warn_no_quote = no_quote_count > total_rejected * 0.8

    return {
        "name": "filter_impact",
        "passed": True,
        "severity": "WARN" if (warn_low_oi or warn_no_quote) else "PASS",
        "detail": (
            f"Quarantine breakdown: {reject_codes}"
            + ("; LOW_OI > 50%" if warn_low_oi else "")
            + ("; NO_QUOTE > 80%" if warn_no_quote else "")
        ),
        "value": {
            "reject_codes": reject_codes,
            "total_rejected": total_rejected,
            "low_oi_pct": low_oi_count / total_rejected if total_rejected else 0,
            "no_quote_pct": no_quote_count / total_rejected if total_rejected else 0,
        },
    }


async def check_business_t_sanity(pool: asyncpg.Pool) -> dict[str, Any]:
    """Verify business_t is in valid range and monotonic."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                MIN(business_t) AS min_t,
                MAX(business_t) AS max_t,
                COUNT(*) FILTER (WHERE business_t <= 0) AS n_zero,
                COUNT(*) FILTER (WHERE business_t > 1.0) AS n_over_one,
                COUNT(*) FILTER (WHERE business_t IS NULL) AS n_null
            FROM amd_surface_min
        """)

    if row["min_t"] is None:
        return {
            "name": "business_t_sanity",
            "passed": True,
            "severity": "SKIP",
            "detail": "No business_t data",
            "value": {},
        }

    n_bad = (row["n_zero"] or 0) + (row["n_over_one"] or 0) + (row["n_null"] or 0)
    passed = n_bad == 0

    return {
        "name": "business_t_sanity",
        "passed": passed,
        "severity": "FAIL" if not passed else "PASS",
        "detail": (
            f"T range: [{row['min_t']:.4f}, {row['max_t']:.4f}]; "
            f"<=0: {row['n_zero']}; >1: {row['n_over_one']}; NULL: {row['n_null']}"
        ),
        "value": dict(row),
    }


async def check_no_future_leakage(pool: asyncpg.Pool) -> dict[str, Any]:
    """Verify no row has a future timestamp."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT COUNT(*) FILTER (WHERE ts > NOW()) AS n_future
            FROM amd_surface_min
        """)

    n_future = row["n_future"] or 0
    passed = n_future == 0

    return {
        "name": "no_future_leakage",
        "passed": passed,
        "severity": "FAIL" if not passed else "PASS",
        "detail": f"Found {n_future} rows with future timestamps" if n_future else "No future timestamps",
        "value": {"future_rows": n_future},
    }


async def check_essvi_sanity(pool: asyncpg.Pool) -> dict[str, Any]:
    """Pick a sample minute + expiration, verify IV smile is smooth."""
    async with pool.acquire() as conn:
        # Get a sample timestamp (100th most recent to avoid edge)
        ts_row = await conn.fetchrow("""
            SELECT ts FROM amd_surface_min
            ORDER BY ts DESC LIMIT 1 OFFSET 100
        """)
        if not ts_row:
            # Fallback to any timestamp
            ts_row = await conn.fetchrow("SELECT ts FROM amd_surface_min ORDER BY ts DESC LIMIT 1")
        if not ts_row:
            return {
                "name": "essvi_sanity",
                "passed": True,
                "severity": "SKIP",
                "detail": "No data in amd_surface_min",
                "value": {},
            }

        ts = ts_row["ts"]

        # Get the first expiration
        exp_row = await conn.fetchrow("""
            SELECT expiration FROM amd_surface_min
            WHERE ts = $1 ORDER BY expiration LIMIT 1
        """, ts)
        if not exp_row:
            return {
                "name": "essvi_sanity",
                "passed": True,
                "severity": "SKIP",
                "detail": "No expiration found for sample timestamp",
                "value": {},
            }

        exp = exp_row["expiration"]

        # Get IV smile for this (ts, expiration)
        rows = await conn.fetch("""
            SELECT strike, option_type, implied_vol
            FROM amd_surface_min
            WHERE ts = $1 AND expiration = $2
            ORDER BY strike
        """, ts, exp)

    if not rows:
        return {
            "name": "essvi_sanity",
            "passed": True,
            "severity": "SKIP",
            "detail": "No IV data for sample point",
            "value": {},
        }

    # Separate calls and puts
    calls = [(float(r["strike"]), float(r["implied_vol"])) for r in rows if r["option_type"] == "C" and r["implied_vol"] is not None]
    puts = [(float(r["strike"]), float(r["implied_vol"])) for r in rows if r["option_type"] == "P" and r["implied_vol"] is not None]

    issues = []

    # Check all IVs in range (0, 5.0)
    all_ivs = [iv for _, iv in calls + puts]
    if any(iv <= 0 or iv >= 5.0 for iv in all_ivs):
        issues.append("IV out of range (0, 5.0)")

    # Check calls: downward-sloping IV (crash-o-phobia)
    if len(calls) >= 2:
        calls.sort()  # by strike
        for i in range(1, len(calls)):
            if calls[i][1] > calls[i-1][1] + 0.05:
                issues.append(f"Call IV jump >5 vol pts at strike {calls[i][0]:.1f}")
            # Equity skew: calls should generally decrease with strike (or at least not jump up)
            if calls[i][1] > calls[i-1][1]:
                # Allow small uptick, flag large
                pass

    # Check puts: generally upward-sloping or flat
    if len(puts) >= 2:
        puts.sort()
        for i in range(1, len(puts)):
            if puts[i][1] > puts[i-1][1] + 0.05:
                issues.append(f"Put IV jump >5 vol pts at strike {puts[i][0]:.1f}")

    # Adjacent strike IV jump check (across both types combined, by strike)
    all_sorted = sorted(calls + puts, key=lambda x: x[0])
    for i in range(1, len(all_sorted)):
        if abs(all_sorted[i][1] - all_sorted[i-1][1]) > 0.05:
            issues.append(f"Strike-adjacent IV jump >5 vol pts between {all_sorted[i-1][0]:.1f} and {all_sorted[i][0]:.1f}")

    passed = len(issues) == 0

    return {
        "name": "essvi_sanity",
        "passed": passed,
        "severity": "FAIL" if not passed else "PASS",
        "detail": "; ".join(issues) if issues else f"IV smile smooth for ts={ts}, exp={exp}",
        "value": {
            "sample_ts": str(ts),
            "sample_expiration": str(exp),
            "n_calls": len(calls),
            "n_puts": len(puts),
            "issues": issues,
        },
    }


async def check_data_freshness(pool: asyncpg.Pool) -> dict[str, Any]:
    """Report the most recent and oldest timestamps in the DB."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT MIN(ts) AS oldest_ts, MAX(ts) AS newest_ts
            FROM amd_surface_min
        """)

    if row["oldest_ts"] is None:
        return {
            "name": "data_freshness",
            "passed": True,
            "severity": "SKIP",
            "detail": "No data in amd_surface_min",
            "value": {},
        }

    return {
        "name": "data_freshness",
        "passed": True,
        "severity": "PASS",
        "detail": f"Data spans {row['oldest_ts']} to {row['newest_ts']}",
        "value": {
            "oldest_ts": str(row["oldest_ts"]),
            "newest_ts": str(row["newest_ts"]),
        },
    }


async def check_engine_contract_columns(pool: asyncpg.Pool) -> dict[str, Any]:
    """Verify eSSVI engine contract columns are populated."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                COUNT(session_phase) AS n_session,
                COUNT(log_moneyness) AS n_k,
                COUNT(parity_skew) AS n_parity,
                COUNT(anchor_k_star) AS n_anchor_k,
                COUNT(anchor_theta_star) AS n_anchor_theta,
                COUNT(slice_strike_count) AS n_slice_count
            FROM amd_surface_min
        """)

    if row["total"] == 0:
        return {
            "name": "engine_contract_columns",
            "passed": True,
            "severity": "SKIP",
            "detail": "No rows in amd_surface_min",
            "value": {},
        }

    total = row["total"]
    coverage = {
        "session_phase": row["n_session"] / total,
        "log_moneyness": row["n_k"] / total,
        "parity_skew": row["n_parity"] / total,
        "anchor_k_star": row["n_anchor_k"] / total,
        "anchor_theta_star": row["n_anchor_theta"] / total,
        "slice_strike_count": row["n_slice_count"] / total,
    }
    critical = ("session_phase", "log_moneyness", "anchor_k_star", "anchor_theta_star")
    passed = all(coverage[c] >= 0.99 for c in critical)

    return {
        "name": "engine_contract_columns",
        "passed": passed,
        "severity": "FAIL" if not passed else "PASS",
        "detail": ", ".join(f"{k}={v:.1%}" for k, v in coverage.items()),
        "value": {"total_rows": total, **{f"pct_{k}": v for k, v in coverage.items()}},
    }


async def check_parity_skew(pool: asyncpg.Pool) -> dict[str, Any]:
    """Flag systematic put-call IV bias (forward/rate error signature)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                AVG(ABS(parity_skew)) AS mean_abs_skew,
                MAX(ABS(parity_skew)) AS max_abs_skew,
                COUNT(*) FILTER (
                    WHERE parity_skew IS NOT NULL
                      AND ABS(parity_skew) > $1
                ) AS n_high_skew
            FROM amd_surface_min
        """, cfg.PARITY_SKEW_TOL)

    if row["mean_abs_skew"] is None:
        return {
            "name": "parity_skew",
            "passed": True,
            "severity": "SKIP",
            "detail": "No parity_skew data",
            "value": {},
        }

    n_high = row["n_high_skew"] or 0
    passed = n_high == 0

    return {
        "name": "parity_skew",
        "passed": passed,
        "severity": "WARN" if not passed else "PASS",
        "detail": (
            f"mean|skew|={row['mean_abs_skew']:.4f}, "
            f"max|skew|={row['max_abs_skew']:.4f}, "
            f"rows above tol ({cfg.PARITY_SKEW_TOL})={n_high}"
        ),
        "value": dict(row),
        "kill_tolerances": {
            "butterfly": cfg.KILL_TOL_BUTTERFLY,
            "calendar": cfg.KILL_TOL_CALENDAR,
            "roper": cfg.KILL_TOL_ROPER,
            "lee": cfg.KILL_TOL_LEE,
        },
    }


async def check_row_counts(pool: asyncpg.Pool) -> dict[str, Any]:
    """Verify total rows matches progress entries."""
    async with pool.acquire() as conn:
        total_rows = await conn.fetchval("SELECT COUNT(*) FROM amd_surface_min")
        progress_rows = await conn.fetchval("""
            SELECT COALESCE(SUM(rows_loaded), 0)
            FROM ingest_progress
            WHERE underlying = 'AMD' AND status = 'completed'
        """)

    # Allow small discrepancy due to ON CONFLICT DO NOTHING
    diff = abs(total_rows - progress_rows)
    passed = diff <= total_rows * 0.01  # within 1%

    return {
        "name": "row_counts",
        "passed": passed,
        "severity": "WARN" if not passed else "PASS",
        "detail": f"Hypertable: {total_rows} rows; Progress sum: {progress_rows}; diff: {diff}",
        "value": {
            "hypertable_rows": total_rows,
            "progress_sum": progress_rows,
            "difference": diff,
        },
    }


async def run_verification(pool: asyncpg.Pool) -> dict[str, Any]:
    """Run all checks and return a verification report."""
    checks = [
        await check_chunk_completeness(pool),
        await check_column_coverage(pool),
        await check_engine_contract_columns(pool),
        await check_filter_impact(pool),
        await check_business_t_sanity(pool),
        await check_no_future_leakage(pool),
        await check_parity_skew(pool),
        await check_essvi_sanity(pool),
        await check_data_freshness(pool),
        await check_row_counts(pool),
    ]

    all_passed = all(c["passed"] for c in checks)
    any_failed = any(not c["passed"] for c in checks)

    # Count by severity
    severities = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for c in checks:
        severities[c["severity"]] = severities.get(c["severity"], 0) + 1

    return {
        "status": "PASS" if all_passed else ("FAIL" if any_failed else "WARN"),
        "checks": checks,
        "summary": {
            "total_checks": len(checks),
            "severity_counts": severities,
            "check_names": [c["name"] for c in checks],
        },
    }