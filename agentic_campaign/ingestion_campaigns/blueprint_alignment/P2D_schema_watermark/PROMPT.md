# W2D — Schema & Chunking Polish

## Persona
You are a **Data Pipeline Finisher** who obsesses over the small details that separate production-grade pipelines from prototypes. Trading-day chunking, missing schema columns, and timezone-naive dates are the kind of issues that silently erode data quality.

## Blueprint Vision
Read `dataingestion.md` Sections 3, 10, and 12.9 completely:
- **Chunking:** "chunked ≤1 month" — but what does "month" mean? The blueprint implies trading days, not calendar days (a month ≈ 21 trading days, not 31 calendar days)
- **Schema (Section 10):** All derived columns should be in the DB schema — `log_moneyness` is computed in math.py but never persisted
- **Leakage Rule 9:** UTC storage. `end_date` default should be UTC date.

## Core Objective
Polish the remaining schema and date-handling gaps:

1. **Trading-day chunk sizing** — `_month_chunks` should use trading day count (~21 days) instead of calendar days (31 days)
2. **Add `log_moneyness` to DB schema** — column computed in math.py but not in `COLUMN_MAP`
3. **Fix `run_backfill` default `end_date`** — use UTC date, not local date

## Errors to Fix

### Medium #36: `_month_chunks` Uses Calendar Days, Not Trading Days
**File:** `chunking.py:10-33`  
**Blueprint:** Section 3  
**Expected:** A "month" ≈ 21 trading days.  
**Actual:** `max_days=31` (calendar days). For chunks with lots of weekends/holidays, 31 calendar days might only be 19-20 trading days — not wrong, just imprecise.  
**Fix:** Add config constant `MAX_TRADING_DAYS_PER_CHUNK = 21` and use trading-day count (from the calendar) for chunk sizing instead of calendar days. Or keep calendar days but add a comment explaining the relationship.

### Medium #44: `log_moneyness` Computed But Not in DB Schema COLUMN_MAP
**File:** `math.py:82-84`, `db_writer.py:35-59`  
**Blueprint:** Section 10  
**Expected:** `log_moneyness` is in `COLUMN_MAP` and the hypertable DDL.  
**Actual:** Computed in math.py (`k = ln(K/F)`) but dropped before DB write because not in COLUMN_MAP. This is an important input for the eSSVI surface fit.  
**Fix:** Add `log_moneyness` to `COLUMN_MAP` and the `CREATE TABLE` DDL.

### Low #54: `run_backfill` Default `end_date=dt.date.today()` — Uses Local Date, Not UTC
**File:** `orchestrator.py:399-400`  
**Blueprint:** Section 12.9 (UTC storage)  
**Expected:** End date should be UTC date to avoid off-by-one when running late at night in a negative UTC offset.  
**Actual:** `dt.date.today()` returns local date (e.g., ET). If run at 11 PM ET (3 AM UTC next day), end_date = "today" but it's actually "yesterday" in UTC.  
**Fix:** Use `dt.datetime.now(dt.timezone.utc).date()` instead of `dt.date.today()`.

## Invariants (MUST HOLD)
1. **Backward compatible** — all tests pass
2. **No behavior change for explicit calls** — only affects the default `end_date` parameter
3. **Schema upgrade safe** — adding `log_moneyness` to DDL must use `IF NOT EXISTS` or similar

## Success Criteria
- `_month_chunks` has a configurable `MAX_TRADING_DAYS_PER_CHUNK` or similar improvement
- `log_moneyness` in `db_writer.COLUMN_MAP` and `CREATE TABLE` DDL
- `orchestrator.py` `run_backfill` default `end_date` uses UTC: `dt.datetime.now(dt.timezone.utc).date()`
- All tests pass

## Short Specialized Verification
```python
# 1) Check log_moneyness in COLUMN_MAP
from dataingestion.db_writer import COLUMN_MAP
assert "log_moneyness" in COLUMN_MAP

# 2) Check end_date uses UTC
src = open("dataingestion/orchestrator.py").read()
assert "timezone.utc" in src or "utc" in src.split("end_date")[1].split("\n")[0]

# 3) Full test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/chunking.py` (trading-day chunk sizing)
- `dataingestion/db_writer.py` (add log_moneyness to COLUMN_MAP and DDL)
- `dataingestion/config.py` (if adding MAX_TRADING_DAYS_PER_CHUNK)
- `dataingestion/orchestrator.py` (fix end_date default)