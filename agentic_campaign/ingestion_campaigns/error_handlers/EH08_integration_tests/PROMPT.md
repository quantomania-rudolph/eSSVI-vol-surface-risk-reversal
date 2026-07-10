# EH-08: Integration Tests

## Persona

You are a **test architect** specializing in end-to-end pipeline testing, synthetic data generation, and mock-based integration verification. You build tests that exercise the full data flow from fetch → clean → math → load without external dependencies.

## Mission

**Create a new `dataingestion/test_integration.py` that tests the complete pipeline with synthetic data, mocking all external dependencies (Theta API, TimescaleDB), and verifying column contract adherence at every stage.**

## Current State Analysis

No integration test file exists. The pipeline has:
- Unit tests for each module (fetchers, cleaning, math, db_writer)
- Orchestrator tests that mock downstream modules
- But NO test that runs the full pipeline with synthetic data through all stages

## Required Test File: `dataingestion/test_integration.py`

### Test Strategy

1. **Synthetic Raw Data Generator** — creates DataFrames matching Theta v3 `greeks/first_order` response
2. **Mock All External Dependencies** — Theta client, asyncpg pool, calendar
3. **Run Full Pipeline** — fetchers (mocked) → cleaning → math → db_writer (staging mock)
4. **Assert Invariants at Every Stage**

### Test Classes

#### TestSyntheticDataFactory
```python
class TestSyntheticDataFactory:
    """Factory for generating realistic synthetic data at each pipeline stage."""
    
    def test_raw_greeks_factory(self):
        """Generate raw greeks DataFrame matching COLUMNS.md §I."""
        
    def test_clean_factory(self):
        """Generate clean DataFrame matching COLUMNS.md §II.A."""
        
    def test_math_factory(self):
        """Generate math-enriched DataFrame matching COLUMNS.md §III."""
```

#### TestFullPipeline
```python
class TestFullPipeline:
    """End-to-end pipeline test with mocked dependencies."""
    
    @pytest.fixture
    def mock_theta_client(self):
        """Mock AsyncThetaClient returning synthetic data."""
        
    @pytest.fixture
    def mock_pg_pool(self):
        """Mock asyncpg.Pool with staging table capture."""
        
    @pytest.fixture
    def mock_calendar(self):
        """Mock pandas_market_calendars XNYS calendar."""
    
    def test_pipeline_raw_to_loaded(self, mock_theta_client, mock_pg_pool, mock_calendar):
        """
        Full pipeline: fetch → join → clean → math → load.
        
        1. Fetch synthetic greeks + OHLC + OI + rates
        2. Join spot + OI + rates
        3. Clean → verify clean + quarantine = input
        4. Math → verify all §III columns present
        5. Load → verify staging capture matches expected columns
        6. Assert column contract at every stage
        """
        
    def test_pipeline_empty_fetch_handling(self, mock_theta_client, mock_calendar):
        """Empty greeks fetch → skip chunk gracefully."""
        
    def test_pipeline_all_quarantined(self, mock_theta_client, mock_calendar):
        """All rows fail cleaning → quarantine gets all, clean empty."""
        
    def test_pipeline_partial_clean(self, mock_theta_client, mock_calendar):
        """Mix of clean and quarantined rows → both paths work."""
```

#### TestColumnContractAdherence
```python
class TestColumnContractAdherence:
    """Verify column contract (COLUMNS.md) at every pipeline stage."""
    
    def test_fetchers_output_matches_section_I(self):
        """Fetchers output has exactly §I columns + _phase='raw'."""
        
    def test_cleaning_output_matches_section_II_A(self):
        """Clean output has exactly §II.A columns + _phase='clean'."""
        
    def test_cleaning_quarantine_matches_section_II_B(self):
        """Quarantine has raw cols + reject_code + reject_detail + _phase='quarantine'."""
        
    def test_math_output_matches_section_III(self):
        """Math output has §III columns + _phase='math'."""
        
    def test_db_writer_mapping_matches_section_IV(self):
        """COLUMN_MAP maps §III → §IV correctly."""
```

#### TestPipelineInvariants
```python
class TestPipelineInvariants:
    """System-level invariants that must hold across the full pipeline."""
    
    def test_row_accounting_holds(self):
        """clean_rows + quarantine_rows == raw_input_rows for every chunk."""
        
    def test_no_data_leakage(self):
        """No future information in any computed column."""
        
    def test_business_t_monotonic(self):
        """business_t decreases with later timestamp per contract."""
        
    def test_vega_nonnegative(self):
        """vega >= 0 for all rows."""
        
    def test_forward_gt_spot_for_positive_r(self):
        """forward_price > spot_close when r > 0."""
        
    def test_log_moneyness_correct(self):
        """log_moneyness = ln(strike / forward_price)."""
        
    def test_quality_flags_bitmask(self):
        """quality_flags only uses defined bits (BELLY_SPREAD=bit 0)."""
```

#### TestConcurrencyAndResumability
```python
class TestConcurrencyAndResumability:
    """Verify orchestrator concurrency control and resume logic."""
    
    def test_semaphores_limit_concurrent_requests(self, mock_theta_client):
        """OPT_SEM limits to 4, STK_SEM limits to 2 concurrent fetches."""
        
    def test_watermark_resume_skips_completed(self, mock_pg_pool):
        """Completed chunks skipped on re-run."""
        
    def test_chunk_size_limit(self):
        """Chunks never exceed MAX_CHUNK_DAYS (31)."""
```

#### TestErrorBoundaries
```python
class TestErrorBoundaries:
    """Verify pipeline handles errors gracefully without data loss."""
    
    def test_db_write_error_does_not_crash(self, mock_pg_pool):
        """DB error → chunk retried next run, watermark not advanced."""
        
    def test_empty_clean_df_skips_math_and_load(self):
        """All rows quarantined → math and load skipped, quarantine loaded."""
        
    def test_network_error_returns_empty(self, mock_theta_client):
        """Theta 5xx → empty DataFrame, chunk skipped, no crash."""
```

## Mock Infrastructure

### Mock Theta Client
```python
class MockThetaClient:
    """Mock AsyncThetaClient returning configurable synthetic responses."""
    
    def __init__(self):
        self.responses: dict[str, tuple[int, list[dict]]] = {}
        
    async def get(self, endpoint, params, ticker):
        return self.responses.get(endpoint, (200, []))
```

### Mock PG Pool
```python
class MockPGPool:
    """Mock asyncpg.Pool capturing COPY data for verification."""
    
    def __init__(self):
        self.staging_data: list[dict] = []
        self.quarantine_data: list[dict] = []
        
    async def acquire(self):
        return MockConnection(self)
```

### Mock Calendar
```python
class MockCalendar:
    """Mock pandas_market_calendars with known schedule."""
    
    def schedule(self, start_date, end_date):
        # Return fixed schedule for testing
        pass
    
    @property
    def tz(self):
        import pytz
        return pytz.timezone("US/Eastern")
```

## Invariants (Must Preserve)

- ✅ No real HTTP calls (all Theta endpoints mocked)
- ✅ No real database (asyncpg mocked)
- ✅ No real calendar (pandas_market_calendars mocked)
- ✅ Tests run in < 10 seconds
- ✅ Synthetic data realistic but deterministic
- ✅ Column contract verified at every stage
- ✅ All pipeline invariants asserted

## Acceptance Criteria

### Functional
1. New `dataingestion/test_integration.py` exists
2. All test classes implemented with meaningful assertions
3. Tests run without external dependencies (fully mocked)
4. Tests complete in < 10 seconds
5. Catches regressions in column contract, row accounting, data leakage

### Testing
```bash
python -m pytest dataingestion/test_integration.py -v    # all tests pass
```

### Coverage Targets
- Full pipeline: 100% of orchestration paths
- Column contract: 100% of columns verified
- Error paths: all error boundaries tested

## Deliverables

1. **New** `dataingestion/test_integration.py` with all test classes
2. **Verification** all tests pass