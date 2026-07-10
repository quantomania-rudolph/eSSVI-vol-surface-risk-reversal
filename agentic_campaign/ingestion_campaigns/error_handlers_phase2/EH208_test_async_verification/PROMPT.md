# EH208: Async Test Verification

## Persona

You are a **test architect** who knows that tests mocking sync functions don't verify async behavior. The current `test_orchestrator.py` patches `fetch_option_greeks_first_order` (sync wrapper) — it never exercises the real async path or semaphore concurrency.

## Mission

**Rewrite `dataingestion/test_orchestrator.py` to mock and verify the ASYNC fetcher variants, ensuring semaphores actually limit concurrency and the async pipeline is exercised.**

## Current State (MOCKS SYNC)

```python
# test_orchestrator.py lines 136-145
patch("dataingestion.orchestrator.fetch_option_greeks_first_order",
      side_effect=_mock_fetch_greeks),  # SYNC WRAPPER!
patch("dataingestion.orchestrator.fetch_stock_ohlc",
      side_effect=_mock_fetch_ohlc),    # SYNC WRAPPER!
```

**Problems:**
1. Tests call sync wrappers → `asyncio.run()` creates new event loops
2. Semaphore acquire/release happens in test's event loop, but real work in isolated loops
3. Concurrency limiting never tested
4. Async/await chain never verified

## Required Changes

### 1. Mock Async Variants (Require EH-01 + EH201)

```python
# In patched_orchestrator fixture
with (
    patch("dataingestion.orchestrator.heartbeat", return_value={"ok": True}),
    patch("dataingestion.orchestrator.AsyncThetaClient") as mock_client_cls,
    patch("dataingestion.orchestrator.init_schema", new_callable=AsyncMock),
    # MOCK ASYNC VARIANTS
    patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
          new_callable=AsyncMock, side_effect=_mock_fetch_greeks_async),
    patch("dataingestion.orchestrator.async_fetch_stock_ohlc",
          new_callable=AsyncMock, side_effect=_mock_fetch_ohlc_async),
    patch("dataingestion.orchestrator.async_fetch_option_open_interest",
          new_callable=AsyncMock, side_effect=_mock_fetch_oi_async),
    patch("dataingestion.orchestrator.async_fetch_interest_rate_eod",
          new_callable=AsyncMock, side_effect=_mock_fetch_rate_async),
    patch("dataingestion.orchestrator.async_fetch_option_list_expirations",
          new_callable=AsyncMock, side_effect=_mock_list_expirations_async),
    # ... rest of patches
):
```

### 2. Create Async Mock Helpers

```python
async def _mock_fetch_greeks_async(client, symbol, expiration, start_date, end_date):
    """Async version of mock fetch."""
    return _mock_fetch_greeks(client, symbol, expiration, start_date, end_date)

async def _mock_fetch_ohlc_async(client, symbol, start_date, end_date):
    return _mock_fetch_ohlc(client, symbol, start_date, end_date)

async def _mock_fetch_oi_async(client, symbol, expiration, start_date, end_date):
    return _mock_fetch_oi(client, symbol, expiration, start_date, end_date)

async def _mock_fetch_rate_async(client, symbol, start_date, end_date):
    return _mock_fetch_rate(client, symbol, start_date, end_date)

async def _mock_list_expirations_async(client, symbol):
    return _mock_list_expirations(client, symbol)
```

### 3. Add Concurrency Verification Tests

```python
class TestConcurrency:
    """Verify semaphores actually limit concurrent async fetches."""
    
    def test_opt_sem_limits_to_4(self, patched_orchestrator):
        """OPT_SEM allows max 4 concurrent greeks/OI fetches."""
        opt_calls = []
        opt_active = 0
        opt_max_active = 0
        
        async def tracking_greeks(*args, **kwargs):
            nonlocal opt_active, opt_max_active
            opt_active += 1
            opt_max_active = max(opt_max_active, opt_active)
            opt_calls.append(("greeks", args, kwargs))
            await asyncio.sleep(0.01)  # Simulate network delay
            opt_active -= 1
            return _mock_fetch_greeks(*args, **kwargs)
        
        with patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
                   side_effect=tracking_greeks):
            await run_backfill(start_date=..., end_date=...)
            assert opt_max_active <= 4, f"OPT_SEM exceeded: {opt_max_active} concurrent"
    
    def test_stk_sem_limits_to_2(self, patched_orchestrator):
        """STK_SEM allows max 2 concurrent stock/rate fetches."""
        stk_calls = []
        stk_active = 0
        stk_max_active = 0
        
        async def tracking_ohlc(*args, **kwargs):
            nonlocal stk_active, stk_max_active
            stk_active += 1
            stk_max_active = max(stk_max_active, stk_active)
            stk_calls.append(("ohlc", args, kwargs))
            await asyncio.sleep(0.01)
            stk_active -= 1
            return _mock_fetch_ohlc(*args, **kwargs)
        
        with patch("dataingestion.orchestrator.async_fetch_stock_ohlc",
                   side_effect=tracking_ohlc):
            await run_backfill(...)
            assert stk_max_active <= 2, f"STK_SEM exceeded: {stk_max_active} concurrent"
    
    def test_semaphores_independent(self, patched_orchestrator):
        """OPT_SEM and STK_SEM operate independently."""
        # Can have 4 opt + 2 stk = 6 concurrent total
        pass
    
    def test_no_asyncio_run_in_orchestrator(self):
        """Static check: no asyncio.run() in orchestrator source."""
        source = Path("dataingestion/orchestrator.py").read_text()
        assert "asyncio.run" not in source
        assert "asyncio.run(" not in source
```

### 4. Update Pipeline Order Test for Async

```python
def test_pipeline_order_async(self, patched_orchestrator):
    """Verify async pipeline order: fetch → join → clean → math → load → watermark."""
    call_order = []
    
    async def track_fetch(*a, **kw):
        call_order.append("fetch")
        return await _mock_fetch_greeks_async(*a, **kw)
    
    # ... similar for clean, business_t, forward, vega, load, watermark
    
    with (
        patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
              side_effect=track_fetch),
        # ... other async patches
    ):
        await run_backfill(...)
        
        # Verify order
        assert call_order.index("fetch") < call_order.index("clean")
        assert call_order.index("clean") < call_order.index("businesst")
        # ...
```

### 5. Verify Async/Await Chain

```python
def test_all_fetchers_awaited(self, patched_orchestrator):
    """Verify all async fetcher mocks are awaited (not just called)."""
    awaited = set()
    
    async def mark_awaited_greeks(*a, **kw):
        awaited.add("greeks")
        return await _mock_fetch_greeks_async(*a, **kw)
    
    # ... similar for others
    
    with (
        patch("dataingestion.orchestrator.async_fetch_option_greeks_first_order",
              side_effect=mark_awaited_greeks),
        # ...
    ):
        await run_backfill(...)
        assert "greeks" in awaited
        assert "ohlc" in awaited
        assert "oi" in awaited
        assert "rate" in awaited
```

## Invariants (Must Preserve)

- ✅ All existing tests still pass (same assertions)
- ✅ Tests exercise REAL async code path
- ✅ Semaphore concurrency verified with timing
- ✅ No `asyncio.run()` in orchestrator
- ✅ Mocks return same data as before (column contract)

## Acceptance Criteria

### Functional
1. All 19 existing tests pass
2. New concurrency tests pass (semaphore limits verified)
3. No `asyncio.run()` in orchestrator source
4. Async fetcher mocks are awaited

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v    # All pass
```

## Dependencies

- **EH-01, EH201 MUST BE COMPLETE** — async fetcher variants exist and used
- **EH202, EH203, EH204 SHOULD BE COMPLETE** — integrated features

## Deliverables

1. **Modified** `dataingestion/test_orchestrator.py` — async mocks + concurrency tests
2. **Verification** all tests pass