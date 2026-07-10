# EH207: Structured Logging Setup

## Persona

You are a **platform observability engineer** who knows that `logger.info("Backfill 50% complete")` is useless at 3 AM when you're debugging why chunk (exp=2026-07-17, end=2026-06-15) failed. Structured JSON logs with correlation IDs are the only way to trace a 7-year backfill.

## Mission

**Replace basic `logging` in `orchestrator.py` with structured JSON logging that includes: run_id, expiration, chunk, semaphore state, and timing.**

## Current State (BASIC)

```python
# Line 46
log = logging.getLogger("dataingestion.orchestrator")

# orchestrator.py

# Usage throughout:
log.info("Processing exp=%s chunk [%s, %s]", exp, chunk_start, chunk_end)
log.info("Backfill %.1f%% complete: %s/%s chunks", pct, done_chunks, total_chunks)
log.error("DB write error for exp=%s chunk [%s, %s]: %s", exp, chunk_start, chunk_end, e)
```

**Problems:**
1. No run_id correlation across log lines
2. No structured fields (can't query "all errors for run_id=42")
3. No timing info (how long did this chunk take?)
4. No semaphore wait time visibility
5. Human-readable only — can't feed to log aggregation

## Required Changes

### 1. Add Structured Logging Setup

```python
# At top of orchestrator.py
import logging
import json
import time
from contextvars import ContextVar

# Context variables for correlation
run_id_var: ContextVar[int | None] = ContextVar("run_id", default=None)
exp_var: ContextVar[str | None] = ContextVar("exp", default=None)
chunk_var: ContextVar[str | None] = ContextVar("chunk", default=None)

class StructuredFormatter(logging.Formatter):
    """JSON formatter with context variables."""
    
    def format(self, record):
        base = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add context vars
        if run_id_var.get() is not None:
            base["run_id"] = run_id_var.get()
        if exp_var.get() is not None:
            base["expiration"] = exp_var.get()
        if chunk_var.get() is not None:
            base["chunk"] = chunk_var.get()
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in {"name", "msg", "args", "created", "filename", "funcName",
                          "levelname", "levelno", "lineno", "module", "msecs",
                          "message", "name", "pathname", "process", "processName",
                          "relativeCreated", "thread", "threadName", "exc_info",
                          "exc_text", "stack_info", "getMessage"}:
                base[key] = value
        
        return json.dumps(base)


def setup_structured_logging(level: int = logging.INFO):
    """Configure root logger with JSON formatter."""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
```

### 2. Use Context Variables in Pipeline

```python
async def run_backfill(...):
    # ... get run_id ...
    run_id_var.set(run_id)
    
    log.info("backfill_started", extra={
        "start_date": str(start_date),
        "end_date": str(end_date),
        "total_expirations": len(valid_expirations),
    })
    
    for exp, exp_start, exp_end in valid_expirations:
        exp_var.set(exp.isoformat())
        
        for chunk_start, chunk_end in chunks:
            chunk_key = f"{chunk_start}_to_{chunk_end}"
            chunk_var.set(chunk_key)
            
            chunk_start_time = time.monotonic()
            
            log.info("chunk_started", extra={
                "chunk_start": str(chunk_start),
                "chunk_end": str(chunk_end),
            })
            
            # ... process ...
            
            chunk_duration = time.monotonic() - chunk_start_time
            log.info("chunk_completed", extra={
                "clean_rows": clean_rows,
                "quarantined_rows": quar_rows,
                "errors": errors,
                "duration_seconds": chunk_duration,
            })
            
            chunk_var.set(None)
        
        exp_var.set(None)
    
    log.info("backfill_completed", extra={
        "total_clean_rows": total_clean,
        "total_quarantined": total_quar,
        "total_errors": total_errors,
        "duration_seconds": elapsed,
    })
    
    run_id_var.set(None)
```

### 3. Log Semaphore Wait Times

```python
async def _fetch_with_semaphore(sem, fetch_func, *args, **kwargs):
    """Fetch with semaphore wait time logging."""
    wait_start = time.monotonic()
    async with sem:
        wait_time = time.monotonic() - wait_start
        if wait_time > 0.1:  # Log only significant waits
            log.debug("semaphore_acquired", extra={
                "semaphore": "OPT" if sem is OPT_SEM else "STK",
                "wait_seconds": wait_time,
            })
        return await fetch_func(*args, **kwargs)
```

### 4. Log Fetch Timing

```python
async def _fetch_opt():
    async with OPT_SEM:
        start = time.monotonic()
        result = await async_fetch_option_greeks_first_order(...)
        duration = time.monotonic() - start
        log.debug("fetch_completed", extra={
            "endpoint": "greeks_first_order",
            "duration_seconds": duration,
            "rows": len(result),
        })
        return result
```

## Invariants (Must Preserve)

- ✅ All existing log messages preserved (just structured)
- ✅ Run ID correlation across entire backfill
- ✅ Expiration + chunk context on every line
- ✅ Timing data for performance analysis
- ✅ Semaphore wait visibility
- ✅ JSON output parseable by log aggregators
- ✅ Log level configurable (DEBUG for semaphore timing)
- ✅ All tests pass

## Acceptance Criteria

### Functional
1. All log output is valid JSON
2. Every line includes `run_id`, `expiration` (when in chunk), `chunk` (when in chunk)
3. Chunk timing logged
4. Semaphore wait times logged at DEBUG
4. Error logs include full context

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py -v
```

### Manual Verification
```bash
# Run backfill and capture logs
python -c "from dataingestion.orchestrator import run_backfill; import asyncio; asyncio.run(run_backfill())" 2>&1 | head -20 | jq .
```

Should output valid JSON with structured fields.

## Dependencies

- **EH201, EH204 SHOULD BE COMPLETE** — async fetchers + client lifecycle

## Deliverables

1. **Modified** `dataingestion/orchestrator.py` — structured logging throughout
2. **Verification** all tests pass, logs are valid JSON