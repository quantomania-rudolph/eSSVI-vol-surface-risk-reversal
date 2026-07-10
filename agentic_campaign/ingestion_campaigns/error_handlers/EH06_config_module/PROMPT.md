# EH-06: Config Module

## Persona

You are a **platform engineer** specializing in configuration management, type-safe settings, and eliminating magic numbers from production pipelines. You believe every threshold should be discoverable, overridable, and documented.

## Mission

**Create a new `dataingestion/config.py` module that centralizes all pipeline thresholds and constants, then update all four Phase 1 modules (+ orchestrator) to import from it.**

## Current State Analysis

**Hardcoded thresholds scattered across modules:**

| Module | Thresholds |
|--------|------------|
| `fetchers.py` | None (but API params like `interval=1m`, `annual_dividend=0`, `rate_type=sofr`) |
| `cleaning.py` | DTE: 7, 90; Delta: 0.10, 0.90; Spread hard: 0.25; Spread belly: 0.10; IV: 0.005; OI: 100; Subpenny eps |
| `math.py` | Business time denominator: 390 * 252; Numba guards: 1e-10 |
| `db_writer.py` | Pool sizes: min=1, max=10; Chunk interval: 7 days; Compression: 7 days |
| `orchestrator.py` | Semaphores: 4, 2; DTE window: 7, 90 |

## Required Changes

### 1. Create `dataingestion/config.py`

```python
"""Centralized configuration for AMD eSSVI data ingestion pipeline.

All thresholds, constants, and tunable parameters live here.
Import from this module — never hardcode values in pipeline modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

# ============================================================================
# THETA API PARAMETERS
# ============================================================================

THETA_INTERVAL: Final[str] = "1m"
THETA_FORMAT: Final[str] = "ndjson"
THETA_ANNUAL_DIVIDEND: Final[int] = 0
THETA_RATE_TYPE: Final[str] = "sofr"
THETA_VERSION: Final[str] = "latest"

# ============================================================================
# CLEANING THRESHOLDS (dataingestion.md Sections 4-5)
# ============================================================================

# DTE band (calendar days)
MIN_DTE: Final[int] = 7
MAX_DTE: Final[int] = 90

# Delta band (absolute)
MIN_DELTA_ABS: Final[float] = 0.10
MAX_DELTA_ABS: Final[float] = 0.90

# Quote quality
MAX_REL_SPREAD_HARD: Final[float] = 0.25
MAX_REL_SPREAD_BELLY: Final[float] = 0.10

# IV
MIN_IV: Final[float] = 0.005

# Open Interest
MIN_OI: Final[int] = 100

# Subpenny detection
SUBPENNY_EPS: Final[float] = 1e-8

# Quality flag bits
BELLY_SPREAD_BIT: Final[int] = 1

# ============================================================================
# BUSINESS TIME (dataingestion.md Section 6)
# ============================================================================

BUSINESS_MINUTES_PER_DAY: Final[int] = 390
TRADING_DAYS_PER_YEAR: Final[int] = 252
BUSINESS_MINUTES_PER_YEAR: Final[int] = BUSINESS_MINUTES_PER_DAY * TRADING_DAYS_PER_YEAR

# Numba guards
NUMBA_SIGMA_EPS: Final[float] = 1e-10
NUMBA_T_EPS: Final[float] = 1e-10

# ============================================================================
# DATABASE (TimescaleDB)
# ============================================================================

@dataclass(frozen=True)
class PGConfig:
    host: str = os.getenv("PGHOST", "127.0.0.1")
    port: int = int(os.getenv("PGPORT", "5432"))
    user: str = os.getenv("PGUSER", "postgres")
    password: str = os.getenv("PGPASSWORD", "postgres")
    database: str = os.getenv("PGDATABASE", "postgres")
    min_size: int = 1
    max_size: int = 10

PG_CONFIG = PGConfig()

# Hypertable
CHUNK_TIME_INTERVAL_DAYS: Final[int] = 7
COMPRESSION_INTERVAL_DAYS: Final[int] = 7

# ============================================================================
# CONCURRENCY (dataingestion.md Section V)
# ============================================================================

OPT_SEM_LIMIT: Final[int] = 4   # Standard tier
STK_SEM_LIMIT: Final[int] = 2   # Value tier

# ============================================================================
# ORCHESTRATOR
# ============================================================================

# DTE window for expiration eligibility (same as cleaning pre-filter)
DTE_WINDOW_MIN: Final[int] = MIN_DTE
DTE_WINDOW_MAX: Final[int] = MAX_DTE

# Chunk size
MAX_CHUNK_DAYS: Final[int] = 31  # ≤1 month
```

### 2. Update All Modules to Import from Config

**`cleaning.py`:**
```python
from dataingestion.config import (
    MIN_DTE, MAX_DTE,
    MIN_DELTA_ABS, MAX_DELTA_ABS,
    MAX_REL_SPREAD_HARD, MAX_REL_SPREAD_BELLY,
    MIN_IV, MIN_OI,
    SUBPENNY_EPS,
    BELLY_SPREAD_BIT,
)
```

**`math.py`:**
```python
from dataingestion.config import (
    BUSINESS_MINUTES_PER_YEAR,
    NUMBA_SIGMA_EPS,
    NUMBA_T_EPS,
)
```

**`db_writer.py`:**
```python
from dataingestion.config import PG_CONFIG, CHUNK_TIME_INTERVAL_DAYS, COMPRESSION_INTERVAL_DAYS
```

**`orchestrator.py`:**
```python
from dataingestion.config import (
    OPT_SEM_LIMIT, STK_SEM_LIMIT,
    DTE_WINDOW_MIN, DTE_WINDOW_MAX,
    MAX_CHUNK_DAYS,
)
```

**`fetchers.py`:**
```python
from dataingestion.config import (
    THETA_INTERVAL, THETA_FORMAT, THETA_ANNUAL_DIVIDEND,
    THETA_RATE_TYPE, THETA_VERSION,
)
```

### 3. Update Semaphore Definitions in Orchestrator

```python
# Before:
OPT_SEM = asyncio.Semaphore(4)
STK_SEM = asyncio.Semaphore(2)

# After:
from dataingestion.config import OPT_SEM_LIMIT, STK_SEM_LIMIT
OPT_SEM = asyncio.Semaphore(OPT_SEM_LIMIT)
STK_SEM = asyncio.Semaphore(STK_SEM_LIMIT)
```

## Invariants (Must Preserve)

- ✅ No hardcoded numeric thresholds remain in any pipeline module
- ✅ All thresholds have sensible defaults via environment variables where appropriate
- ✅ Type hints on all constants
- ✅ Docstrings explaining each constant's purpose and source (dataingestion.md section)
- ✅ Frozen dataclass for PG config (immutable)
- ✅ All existing tests pass with new config

## Acceptance Criteria

### Functional
1. New `dataingestion/config.py` exists with all constants
2. All 5 modules import from config
3. No magic numbers remain in pipeline logic
4. Environment variables work for PG config
5. All existing tests pass

### Testing
```bash
python -m pytest dataingestion/test_config.py -v         # new config tests
python -m pytest dataingestion/test_fetchers.py -v
python -m pytest dataingestion/test_cleaning.py -v
python -m pytest dataingestion/test_math.py -v
python -m pytest dataingestion/test_db_writer.py -v
python -m pytest dataingestion/test_orchestrator.py -v
```

### New Test File: `dataingestion/test_config.py`
- Verify all constants exist and have correct types
- Verify PG_CONFIG reads from environment
- Verify constants match dataingestion.md specifications
- Verify no circular imports

## Deliverables

1. **New** `dataingestion/config.py`
2. **Modified** `fetchers.py`, `cleaning.py`, `math.py`, `db_writer.py`, `orchestrator.py` to import from config
3. **New** `dataingestion/test_config.py`
4. **Verification** all tests pass