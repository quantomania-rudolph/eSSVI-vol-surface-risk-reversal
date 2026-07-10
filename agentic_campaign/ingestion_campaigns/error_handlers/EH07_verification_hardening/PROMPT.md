# EH-07: Verification Hardening

## Persona

You are a **data quality assurance engineer** specializing in financial data validation, leakage detection, and automated verification pipelines. You build tests that catch silent data corruption before it reaches production models.

## Mission

**Harden `dataingestion/test_verify.py` to comprehensively verify the ingested data quality, detect leakage, and validate eSSVI surface inputs — without writing to the database during verification.**

## Current State Analysis

**File:** `dataingestion/test_verify.py` (12952 lines per directory listing — likely a typo, let me check actual size)

Actually, looking at the directory listing: `test_verify.py` is 12952 bytes, not lines. Let me understand what's there.

The current `test_verify.py` has tests but they're all **SKIPPED** (see test run output: 10 tests skipped). This suggests the verification tests exist but require a database connection.

## Required Changes

### 1. Make Verification Tests Runnable Without DB Writes

All verification tests should:
- Read from database (SELECT only)
- Assert data quality invariants
- **Never write** to database
- Work with a test database fixture

### 2. Add Missing Critical Verification Checks

Per the campaign run doc and dataingestion.md, verification must cover:

| Check | Description | Leakage Risk |
|-------|-------------|--------------|
| **Column Coverage** | No nulls in required columns (spot_close, business_t, forward_price, vega, r, q, log_moneyness) | High |
| **Filter Impact** | Quarantine breakdown by reject_code; measure % rows rejected per filter | Medium |
| **Business T Sanity** | T in reasonable range (0.01 to 1.0), monotonic per contract, no future timestamps | Critical |
| **Future Leakage** | No row has timestamp > ingestion time; no rate/OI from future dates | Critical |
| **eSSVI IV Smile** | IV surface has smile shape (ATM < OTM wings), no negative IV | High |
| **Data Freshness** | Latest timestamp per expiration; staleness detection | Medium |
| **Row Counts** | Clean + quarantine = raw input per chunk; no row loss | Critical |
| **Status Summary** | Per-chunk status: completed/failed/skipped with row counts | Medium |

### 3. Use Config for Thresholds

Import thresholds from `dataingestion.config` for assertions.

### 4. Add Synthetic Data Fixtures

Create test fixtures that don't require a real DB — use the same mocking pattern as other test files.

## Required Test Classes

### TestAllChecksRun
```python
def test_all_checks_return_results(self, db_conn):
    """Every verification check executes and returns a result dict."""
```

### TestColumnCoverage
```python
def test_no_nulls_in_required_columns(self, db_conn):
    """spot_close, business_t, forward_price, vega, r, q, log_moneyness all non-null."""
    
def test_null_rate_pct_below_threshold(self, db_conn):
    """Null rate % < 1% (rates should be forward-filled)."""
```

### TestFilterImpact
```python
def test_quarantine_breakdown_by_code(self, db_conn):
    """Count rows per reject_code; alert if any filter > 50%."""
    
def test_belly_spread_flag_rate(self, db_conn):
    """Belly flag rate reasonable (5-20%)."""
```

### TestBusinessTSanity
```python
def test_business_t_in_range(self, db_conn):
    """0.01 < business_t < 1.0 for all rows."""
    
def test_business_t_monotonic_per_contract(self, db_conn):
    """For each (underlying, expiration, strike, option_type), business_t decreases with timestamp."""
    
def test_no_future_business_t(self, db_conn):
    """business_t computed from timestamp + expiration only — no future info."""
```

### TestFutureLeakage
```python
def test_no_future_timestamps(self, db_conn):
    """All timestamps <= ingestion watermark."""
    
def test_rates_point_in_time(self, db_conn):
    """Rate date <= bar date (no forward-looking rates)."""
    
def test_oi_point_in_time(self, db_conn):
    """OI date <= bar date (prior session OI)."""
```

### TestESSVISanity
```python
def test_iv_smile_shape(self, db_conn):
    """For each (expiration, timestamp), IV has smile: ATM < OTM put/call wings."""
    
def test_no_negative_iv(self, db_conn):
    """implied_vol > 0 for all rows."""
    
def test_vega_positive(self, db_conn):
    """vega >= 0 for all rows."""
```

### TestDataFreshness
```python
def test_latest_timestamp_per_expiration(self, db_conn):
    """Report latest bar per expiration; flag if > 24h stale."""
```

### TestRowCounts
```python
def test_clean_plus_quarantine_equals_raw(self, db_conn):
    """Per chunk: clean_rows + quarantined_rows == raw_fetched_rows."""
    
def test_no_duplicate_pks(self, db_conn):
    """UNIQUE (underlying, expiration, strike, option_type, ts) holds."""
```

### TestStatusSummary
```python
def test_ingest_progress_complete(self, db_conn):
    """All expected chunks have status='completed' in ingest_progress."""
```

### TestInvariants
```python
def test_no_db_writes_during_verification(self):
    """Verify tests only SELECT, never INSERT/UPDATE/DELETE."""
    
def test_no_fetcher_imports(self):
    """Verification module doesn't import fetchers."""
    
def test_no_cleaning_imports(self):
    """Verification module doesn't import cleaning."""
    
def test_no_math_imports(self):
    """Verification module doesn't import math."""
    
def test_no_http_imports(self):
    """Verification module doesn't import aiohttp."""
```

## Invariants (Must Preserve)

- ✅ Verification only READS from database (SELECT only)
- ✅ No dataingestion module imports (fetchers, cleaning, math, db_writer)
- ✅ No HTTP client imports
- ✅ Uses config thresholds for assertions
- ✅ Works with test database fixture (mock or real)

## Acceptance Criteria

### Functional
1. All 10+ verification test classes implemented
2. Tests run against test database (can be mocked)
3. No skipped tests — all execute and assert meaningful invariants
4. Config thresholds used for all numeric assertions
5. Clear failure messages indicating which check failed and why

### Testing
```bash
python -m pytest dataingestion/test_verify.py -v    # all tests pass (not skipped)
```

## Deliverables

1. **Modified** `dataingestion/test_verify.py` with comprehensive verification tests
2. **Verification** all tests execute and pass (with test DB)