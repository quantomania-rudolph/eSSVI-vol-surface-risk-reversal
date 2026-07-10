# EO322: Verification Suite - Automated Validation of All Fixes

## Persona

You are a **release engineer** who knows that manual verification is error-prone. Every fix needs an automated verification that runs in CI and fails if regressions appear.

## Core Objective

**Create a comprehensive verification script that validates all 22 fixes and runs as part of the test suite.**

## Verification Checks

### P0 Bugs (7 checks)
```python
def test_no_broken_cache_expression():
    """No 'or pd.DataFrame()' in orchestrator."""
    source = Path("dataingestion/orchestrator.py").read_text()
    assert "or pd.DataFrame()" not in source

def test_oi_preserved_on_empty_daily_fetch():
    """_join_oi preserves raw OI when daily fetch empty."""
    # Mock empty oi_df, verify opt_df open_interest unchanged

def test_schedule_cache_covers_full_dte_range():
    """Schedule start = backfill_start - DTE_MAX - 5d."""
    # Verify _build_business_time_schedule called with correct range

def test_single_compute_business_T_definition():
    """Exactly one compute_business_T in math.py."""
    source = Path("dataingestion/math.py").read_text()
    assert source.count("def compute_business_T") == 1

def test_rates_cache_key_includes_symbol():
    """Rates cache key tuple includes rate_symbol."""
    source = Path("dataingestion/orchestrator.py").read_text()
    assert "cache_key = (rate_symbol" in source or "cache_key = (rate" in source

def test_watermark_race_logged():
    """UniqueViolationError caught and logged."""
    # Mock advance_watermark to raise UniqueViolationError
    # Verify warning log with exp/chunk/run_id

def test_context_vars_cleared_on_exception():
    """exp_var/chunk_var None after _process_chunk exception."""
    # Mock _process_chunk to raise, verify context cleared
```

### P1 Architecture (5 checks)
```python
def test_orchestrator_under_300_lines():
    """orchestrator.py < 300 lines after split."""
    assert count_lines("dataingestion/orchestrator.py") < 300

def test_six_modules_exist():
    """logging.py, cache.py, retry.py, joins.py, chunking.py exist."""
    for m in ["logging", "cache", "retry", "joins", "chunking"]:
        assert Path(f"dataingestion/{m}.py").exists()

def test_no_hardcoded_ticker():
    """No 'AMD' or 'SOFR' string literals in orchestrator logic."""
    source = Path("dataingestion/orchestrator.py").read_text()
    # Allow in default params only
    assert '"AMD"' not in source or "underlying: str = \"AMD\"" in source
    assert '"SOFR"' not in source or "rate_symbol: str = \"SOFR\"" in source

def test_single_get_pool():
    """Exactly one get_pool definition in codebase."""
    assert count_defs("dataingestion/db_writer.py", "get_pool") == 1

def test_config_imports_use_cfg():
    """All config accessed via cfg.CONSTANT."""
    source = Path("dataingestion/orchestrator.py").read_text()
    assert "from dataingestion import config as cfg" in source
    assert "from dataingestion.config import" not in source
```

### P2 Code Quality (6 checks)
```python
def test_all_private_functions_typed():
    """mypy passes on all modules."""
    # subprocess.run(["mypy", "dataingestion/"])

def test_no_inline_imports():
    """No import statements inside function bodies."""
    # Check each function body

def test_magic_numbers_replaced():
    """No fillna(0.0) on rates column."""
    source = Path("dataingestion/joins.py").read_text()
    assert 'fillna(0.0)' not in source or 'fillna(0)' not in source

def test_docstring_coverage():
    """All private functions have docstrings."""
    # AST check

def test_contextvar_types_optional():
    """ContextVar uses Optional[T]."""
    source = Path("dataingestion/orchestrator.py").read_text()
    assert "ContextVar[Optional[" in source

def test_no_unused_imports():
    """flake8 reports no F401."""
    # subprocess.run(["flake8", "dataingestion/orchestrator.py"])
```

### P3 Test/Docs (5 checks)
```python
def test_edge_case_coverage():
    """New edge case tests exist and pass."""
    # pytest test_chunking.py test_cache.py test_joins.py -v

def test_error_semantics_structured():
    """_process_chunk returns ChunkResult with fetch_error/db_error/skipped."""
    # Verify return type
```

## Integration

Create `verify_phase3.py` script that runs all checks and exits with code 1 if any fail.

## Success Criteria

```bash
python verify_phase3.py
# All 23 checks pass, exit code 0

python -m pytest dataingestion/ -v
# All 77+ tests pass
```