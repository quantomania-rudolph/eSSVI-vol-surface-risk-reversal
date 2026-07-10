# A6 — Post-Ingestion Verification

**Role:** Quantitative data integrity engineer — the last line of defense before the eSSVI fitter touches the data.

## Your Mission

Build `dataingestion/verify.py` — a **read-only** verification module that queries the TimescaleDB hypertable and produces an integrity report. It validates that the ingested data is complete, clean, internally consistent, and free of look-ahead leakage.

**No writes to the DB. No Theta HTTP. No math computation.** Reads only.

## What You Build

One file: `dataingestion/verify.py`

One public entry point:

```python
async def run_verification(pool: asyncpg.Pool) -> dict:
    """Run all checks and return a verification report.

    Returns a dict with keys:
        status: "PASS" | "FAIL" | "WARN"
        checks: list of dicts, each with {name, passed, detail, value}
        summary: dict with aggregate stats
    """
```

### Verification Checks

Implement each as an async function, then compose in `run_verification`:

#### 1. Chunk Completeness

```python
async def check_chunk_completeness(pool) -> dict:
    """Verify no gaps in ingested data per (expiration, date_chunk).

    Cross-reference ingest_progress against expected chunks from
    expiration dates in amd_surface_min.
    """
```

Compare the expirations present in `amd_surface_min` against those tracked in `ingest_progress`. Any expiration with data but no progress entry = incomplete ingestion. Any progress entry with status "failed" = red flag.

#### 2. Column Non-Null Coverage

```python
async def check_column_coverage(pool) -> dict:
    """Report null percentage for key columns.

    spot_price, implied_vol, vega should be near 100% non-null.
    bid, ask, delta should be near 100% non-null.
    """
```

SQL pattern:
```sql
SELECT
    COUNT(*) AS total,
    COUNT(spot_price) AS n_spot,
    COUNT(implied_vol) AS n_iv,
    COUNT(vega) AS n_vega,
    COUNT(bid) AS n_bid,
    COUNT(ask) AS n_ask,
    COUNT(delta) AS n_delta
FROM amd_surface_min
```

Thresholds: spot/IV/vega < 99% → FAIL. bid/ask/delta < 95% → WARN.

#### 3. Filter Impact Report

```python
async def check_filter_impact(pool) -> dict:
    """Summarize quarantine by reject_code.

    Shows how many rows each filter caught. Helps tune thresholds.
    """
```

```sql
SELECT reject_code, COUNT(*) AS n
FROM amd_surface_quarantine
GROUP BY reject_code
ORDER BY n DESC
```

Report the top reject codes and their counts. If LOW_OI > 50% of all rejections → WARN (OI threshold too aggressive). If NO_QUOTE > 80% → likely a data source issue.

#### 4. Business Time Sanity

```python
async def check_business_t_sanity(pool) -> dict:
    """Verify business_t is monotonic and in valid range.

    - T should decrease within each (contract, day) as timestamps advance.
    - T should be between 0 and 1.0 (all AMD options < 1 year).
    """
```

```sql
SELECT MIN(business_t), MAX(business_t),
       COUNT(*) FILTER (WHERE business_t <= 0) AS n_zero,
       COUNT(*) FILTER (WHERE business_t > 1.0) AS n_over_one,
       COUNT(*) FILTER (WHERE business_t IS NULL) AS n_null
FROM amd_surface_min
```

#### 5. No Future Leakage

```python
async def check_no_future_leakage(pool) -> dict:
    """Verify no row has data from a future timestamp.

    - spot_price timestamp should match ts (no future spot).
    - r and q should be point-in-time.
    """
```

Simplest check: verify `ts` is never in the future relative to `NOW()`.

#### 6. eSSVI Surface Sanity

```python
async def check_essvi_sanity(pool) -> dict:
    """Pick a sample minute and verify the IV smile is smooth.

    - For one minute + one expiration, extract all strikes + IVs.
    - Verify IV is positive for all strikes.
    - Verify no extreme jumps between adjacent strikes.
    - Verify the smile has a reasonable shape (no V-shaped or inverted).
    """
```

```sql
SELECT strike, option_type, implied_vol
FROM amd_surface_min
WHERE ts = (SELECT ts FROM amd_surface_min ORDER BY ts DESC LIMIT 1 OFFSET 100)
  AND expiration = (SELECT expiration FROM amd_surface_min ORDER BY expiration LIMIT 1)
ORDER BY strike
```

Then in Python:
- Calls should be downward-sloping IV (crash-o-phobia skew for equities)
- No strike-adjacent IV jump > 0.05 (5 vol points)
- All IVs > 0 and < 5.0

#### 7. Data Freshness

```python
async def check_data_freshness(pool) -> dict:
    """Report the most recent and oldest timestamps in the DB."""
```

#### 8. Row Count Consistency

```python
async def check_row_counts(pool) -> dict:
    """Verify: total rows = clean rows in amd_surface_min.

    Also cross-check: sum of rows per progress entry ≈ total rows.
    """
```

### Aggregator

```python
async def run_verification(pool) -> dict:
    checks = [
        await check_chunk_completeness(pool),
        await check_column_coverage(pool),
        await check_filter_impact(pool),
        await check_business_t_sanity(pool),
        await check_no_future_leakage(pool),
        await check_essvi_sanity(pool),
        await check_data_freshness(pool),
        await check_row_counts(pool),
    ]
    
    all_passed = all(c["passed"] for c in checks)
    any_failed = any(not c["passed"] for c in checks)
    
    return {
        "status": "PASS" if all_passed else ("FAIL" if any_failed else "WARN"),
        "checks": checks,
        "summary": _build_summary(checks),
    }
```

### Report Format

Each check returns:
```python
{
    "name": "chunk_completeness",
    "passed": True,         # True if check passes, False if fails
    "severity": "FAIL",     # "PASS", "WARN", or "FAIL"
    "detail": "...",        # Human-readable explanation
    "value": {...},         # Relevant metrics/numbers
}
```

### Invariants — NEVER Violate

1. **Read-only.** SELECT queries only. No INSERT, UPDATE, DELETE, TRUNCATE, DROP.
2. **No Theta, no HTTP.** Pure DB queries.
3. **No math computation.** Don't recompute vega, T, or forward. Read what was stored.
4. **All checks run, even if one fails.** Don't short-circuit — produce the full report.
5. **Report both PASS and FAIL.** Don't suppress passing checks.
6. **Never modify the DB schema.** No ALTER TABLE, no CREATE INDEX.
7. **Handle empty tables gracefully.** If no data yet, report it as a SKIP, not a FAIL.
8. **Always return a dict with `status`.** Orchestrator/caller just checks `result["status"]`.
9. **Never import from dataingestion modules you're verifying.** No importing fetchers, cleaning, math, db_writer, or orchestrator.

### Key Reference Files

- `dataingestion.md` Section 10 (schema), Section 11 (TimescaleDB layout), Section 12 (data leakage) — **what to check**
- `dataingestion/COLUMNS.md` Section IV — **the column mapping to verify**
- `dataingestion/db_writer.py` — the schema it created (for understanding what tables/columns exist)

### Verification Script

```bash
cd c:\Users\Rudol\Desktop\ThetaData_greeks
python -m pytest dataingestion/test_verify.py -v
```

The verification script (`dataingestion/test_verify.py`) will:
1. Create a test DB with synthetic data matching the schema.
2. Insert data with known issues (null columns, out-of-range T, quarantine rows, etc.).
3. Call `run_verification()` and verify:
   - All 8 checks execute and return results.
   - Chunk completeness catches missing progress entries.
   - Column null coverage fails when columns are too sparse.
   - Filter impact reports quarantine breakdowns.
   - Business T sanity catches out-of-range values.
   - Future leakage check catches future timestamps.
   - eSSVI sanity catches IV jumps.
   - Row counts are consistent.
   - Summary status correctly aggregates pass/fail/warn.
4. Verify no writes to the DB (all queries are SELECT).
5. Verify no imports from other dataingestion modules.

**Do not write the verification script.** It lives at `dataingestion/test_verify.py`.

### Common Mistakes to Avoid

- Writing to the DB (INSERT/UPDATE/DELETE). Verification is read-only.
- Skipping checks when one fails (short-circuit). Run ALL checks.
- Returning "PASS" when data is empty (should return "SKIP" / "WARN").
- Hardcoding thresholds that might not apply to the user's subscription tier or data window.
- Not handling NULL values in SQL queries (NULL business_t = NaN → count it).
- Comparing IVs across expirations (the smile is per-expiration-per-minute).