# EH206: Fetch Resilience (Retry + Backoff)

## Persona

You are a **reliability engineer** who knows that network calls to a local terminal will fail — transient 5xx, momentary disconnects, GC pauses. A production backfill must survive these without manual intervention.

## Mission

**Add retry logic with exponential backoff to all Theta API calls in `orchestrator.py`, distinguishing retryable vs non-retryable errors.**

## Current State (NO RETRY)

```python
# _process_chunk - any fetch error = empty DF = skip chunk
opt_df, oi_df, stk_df = await asyncio.gather(_fetch_opt(), _fetch_oi(), _fetch_stk())

if opt_df.empty:
    log.info("Empty greeks fetch... skipping")
    return 0, 0, 0  # Chunk skipped forever!
```

**Problems:**
1. Transient error (503, timeout) → chunk permanently skipped
2. No backoff → hammering failing endpoint
3. No distinction: 400 (bad request) vs 503 (transient) treated same
4. No retry budget → infinite retries or zero retries

## Required Changes

### 1. Define Retry Policy (in config)

```python
# dataingestion/config.py (add to EH-06)
FETCH_MAX_RETRIES = 3
FETCH_BASE_DELAY = 1.0  # seconds
FETCH_MAX_DELAY = 30.0
FETCH_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
FETCH_NON_RETRYABLE_STATUS = {400, 401, 403, 404}
```

### 2. Create Retry Wrapper

```python
# In orchestrator.py (or shared util)
import asyncio
from dataingestion.config import (
    FETCH_MAX_RETRIES, FETCH_BASE_DELAY, FETCH_MAX_DELAY,
    FETCH_RETRYABLE_STATUS, FETCH_NON_RETRYABLE_STATUS
)

async def fetch_with_retry(fetch_func, *args, **kwargs):
    """Execute fetch_func with exponential backoff retry."""
    last_exception = None
    
    for attempt in range(FETCH_MAX_RETRIES + 1):
        try:
            result = await fetch_func(*args, **kwargs)
            
            # Check for HTTP error status in result (if fetchers return status)
            # Assuming fetchers raise on non-retryable, return empty on retryable
            # Better: fetchers should raise specific exceptions
            return result
            
        except Exception as e:
            last_exception = e
            
            # Check if retryable
            if not _is_retryable_error(e):
                log.warning("Non-retryable error in %s: %s", fetch_func.__name__, e)
                raise  # Re-raise immediately
            
            if attempt < FETCH_MAX_RETRIES:
                delay = min(FETCH_BASE_DELAY * (2 ** attempt), FETCH_MAX_DELAY)
                delay += asyncio.get_event_loop().time() * 0.1  # jitter
                log.warning(
                    "Fetch %s failed (attempt %d/%d), retrying in %.1fs: %s",
                    fetch_func.__name__, attempt + 1, FETCH_MAX_RETRIES, delay, e
                )
                await asyncio.sleep(delay)
            else:
                log.error(
                    "Fetch %s failed after %d retries: %s",
                    fetch_func.__name__, FETCH_MAX_RETRIES, e
                )
    
    raise last_exception


def _is_retryable_error(error: Exception) -> bool:
    """Determine if error is retryable based on type/status."""
    # Check for aiohttp.ClientResponseError with status
    if hasattr(error, 'status'):
        return error.status in FETCH_RETRYABLE_STATUS
    
    # Check for timeout/connection errors
    if isinstance(error, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    
    # Default: non-retryable
    return False
```

### 3. Apply to All Fetches in `_process_chunk`

```python
async def _process_chunk(...):
    # ...
    
    async def _fetch_opt():
        async with OPT_SEM:
            return await fetch_with_retry(
                async_fetch_option_greeks_first_order,
                client, "AMD", exp, chunk_start, chunk_end
            )
    
    async def _fetch_oi():
        async with OPT_SEM:
            return await fetch_with_retry(
                async_fetch_option_open_interest,
                client, "AMD", exp, chunk_start, chunk_end
            )
    
    async def _fetch_stk():
        async with STK_SEM:
            return await fetch_with_retry(
                _get_stock_ohlc_cached,  # Already has semaphore inside
                client, "AMD", chunk_start, chunk_end
            )
    
    opt_df, oi_df, stk_df = await asyncio.gather(
        _fetch_opt(), _fetch_oi(), _fetch_stk(),
        return_exceptions=True  # Handle individual failures
    )
    
    # Check for exceptions
    for i, (name, result) in enumerate([("greeks", opt_df), ("oi", oi_df), ("stock", stk_df)]):
        if isinstance(result, Exception):
            log.error("Fetch %s failed permanently: %s", name, result)
            # Decide: skip chunk or re-raise?
            return 0, 0, 1  # Mark as error
    
    # ... rest of processing
```

### 4. Apply to Rates and Expirations

```python
# In run_backfill
expirations = await fetch_with_retry(
    async_fetch_option_list_expirations, client, "AMD"
)

rates_df = await fetch_with_retry(
    _get_rates_async, client, start_date, end_date
)
```

## Invariants (Must Preserve)

- ✅ Retryable errors (5xx, 429, timeout) → retry with backoff
- ✅ Non-retryable errors (4xx except 429) → fail fast, quarantine chunk
- ✅ Max retries configurable
- ✅ Exponential backoff with jitter
- ✅ Semaphore still acquired per attempt (not held during sleep)
- ✅ Total retry time bounded
- ✅ All tests pass

## Acceptance Criteria

### Functional
1. Transient 5xx errors retry up to `FETCH_MAX_RETRIES` times
2. 429 (rate limit) retries with backoff
3. 400/401/404 fail immediately
4. Timeout errors retry
5. Chunk marked as error (not skipped) if all retries exhausted

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
```

### New Test in `test_orchestrator.py`
```python
class TestFetchResilience:
    def test_retryable_error_retries(self, patched_orchestrator):
        """503 error retries FETCH_MAX_RETRIES times then fails."""
        call_count = 0
        
        async def failing_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= FETCH_MAX_RETRIES:
                raise aiohttp.ClientResponseError(..., status=503)
            return mock_data
        
        with patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
                   side_effect=failing_fetch):
            result = await run_backfill(...)
            assert call_count == FETCH_MAX_RETRIES + 1
            assert result["errors"] > 0
    
    def test_non_retryable_error_fails_fast(self, patched_orchestrator):
        """400 error fails immediately, no retry."""
        call_count = 0
        
        async def bad_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise aiohttp.ClientResponseError(..., status=400)
        
        with patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
                   side_effect=bad_request):
            result = await run_backfill(...)
            assert call_count == 1  # No retry
            assert result["errors"] > 0
    
    def test_timeout_retries(self, patched_orchestrator):
        """asyncio.TimeoutError retries with backoff."""
        # Similar pattern
```

## Dependencies

- **EH-01 MUST BE COMPLETE** — async fetchers required
- **EH-06 MUST BE COMPLETE** — config for retry parameters
- **EH201 SHOULD BE COMPLETE** — async fetcher integration

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` — retry logic on all fetches
2. **Verification** all tests pass including new resilience tests