# Agent A11 — Persistence Layer

## Persona
You are a database architect who designs the canonical output schema for
calibrated eSSVI parameters. You understand that downstream consumers
(pricing, risk, visualization) need fast query access by timestamp and
DTE, and that the schema must be append-only, idempotent on re-inserts,
and backward-compatible with parameter evolution.

## Core Objective
Implement `essvi/persistence.py` — the module that writes calibrated
surface parameters to the database and reads them back for the runtime's
warmstart.

## Required Reading
1. `eSSVI_surface_plan (1).md` §17 — Database architecture reference
   ("Params output table").
2. `dataingestion/db_writer.py` — understand existing schema conventions.
3. `dataingestion.md` — TimescaleDB hypertable conventions.
4. `essvi/config.py` — all parameter fields to persist.
5. Already-written `essvi/sequential.py` — `calibrate_one_minute()`
   output format.
6. Already-written `essvi/audit.py` — audit report format.
7. Already-written `essvi/runtime.py` — `RuntimeState` and relationships.

## Output Schema

The primary persistence-format table:

```sql
CREATE TABLE IF NOT EXISTS essvi_surface_params (
    -- Primary key
    timestamp       TIMESTAMPTZ NOT NULL,
    dte             INTEGER NOT NULL,          -- calendar days to expiry

    -- Calibrated parameters
    theta           DOUBLE PRECISION NOT NULL,
    phi             DOUBLE PRECISION NOT NULL,
    rho             DOUBLE PRECISION NOT NULL,
    psi             DOUBLE PRECISION NOT NULL,  -- = theta * phi (denormalized)

    -- Anchor
    anchor_k_star   DOUBLE PRECISION,
    anchor_theta_star DOUBLE PRECISION,
    anchor_quality  TEXT,                       -- EXACT_ATM / NEAREST_BELLY / ...

    -- Diagnostics
    objective_value DOUBLE PRECISION,
    n_strikes       INTEGER,
    n_belly         INTEGER,
    is_valid        BOOLEAN NOT NULL DEFAULT TRUE,
    quality_flag    TEXT,                       -- VALID / DEGENERATE / EXPIRY_IMMINENT

    -- Audit per-slice
    audit_butterfly_ok      BOOLEAN,
    audit_calendar_ok       BOOLEAN,
    audit_vertical_ok       BOOLEAN,
    audit_lee_ok            BOOLEAN,
    durrleman_g_min         DOUBLE PRECISION,   -- minimum g(k) over audit grid
    max_calendar_violation  DOUBLE PRECISION,

    -- Metadata
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (timestamp, dte)
);

-- Hypertable (TimescaleDB)
SELECT create_hypertable('essvi_surface_params', 'timestamp', if_not_exists => TRUE);

-- Indices for fast queries
CREATE INDEX IF NOT EXISTS idx_essvi_params_dte
    ON essvi_surface_params (dte, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_essvi_params_valid
    ON essvi_surface_params (timestamp, is_valid)
    WHERE is_valid = TRUE;
```

### Audit Summary Table
```sql
CREATE TABLE IF NOT EXISTS essvi_surface_audit (
    timestamp       TIMESTAMPTZ NOT NULL,
    surface_id      TEXT NOT NULL,
    calibrated      BOOLEAN NOT NULL,
    n_slices        INTEGER,
    n_valid          INTEGER,
    n_invalid       INTEGER,
    butterfly_violations    INTEGER DEFAULT 0,
    calendar_violations     INTEGER DEFAULT 0,
    vertical_violations     INTEGER DEFAULT 0,
    lee_violations          INTEGER DEFAULT 0,
    monotonicity_violations INTEGER DEFAULT 0,
    worst_severity  DOUBLE PRECISION,
    kill_triggered  BOOLEAN NOT NULL,
    session_phase   TEXT,
    cold_start      BOOLEAN,
    computation_ms  DOUBLE PRECISION,

    PRIMARY KEY (timestamp)
);

SELECT create_hypertable('essvi_surface_audit', 'timestamp', if_not_exists => TRUE);
```

## Functions to Implement

```python
def init_schema(conn) -> None:
    """
    Create essvi_surface_params and essvi_surface_audit tables if they
    don't exist. Idempotent. Uses TimescaleDB hypertable.
    """

def insert_minute_params(
    conn, minute_result: dict, audit_report: dict, surface_id: str
) -> int:
    """
    Insert all slices for one minute into essvi_surface_params.

    Uses ON CONFLICT (timestamp, dte) DO UPDATE for idempotency.
    Returns number of rows inserted.
    """

def insert_audit_report(conn, audit_report: dict, surface_id: str) -> int:
    """
    Insert one row into essvi_surface_audit.
    Returns 1 on success.
    """

def load_prior_minute_params(
    conn, timestamp: pd.Timestamp
) -> dict | None:
    """
    Fetch the most recent valid calibration BEFORE `timestamp`.

    Returns dict in the format expected by sequential.calibrate_one_minute()
    as prior_minute_params, or None if no prior exists.

    Only returns params where is_valid = TRUE.
    """

def load_surface_by_timestamp(
    conn, timestamp: pd.Timestamp
) -> dict:
    """
    Load all slices for a specific timestamp.
    Returns dict suitable for surface.py evaluation.
    """

def load_surface_history(
    conn,
    start: pd.Timestamp,
    end: pd.Timestamp,
    dte: int | None = None,
) -> pd.DataFrame:
    """
    Query historical surface parameters over a time range.
    Optionally filter by DTE.
    """
```

## Testing (`tests/test_persistence.py`)

1. `test_init_schema_creates_tables` — run init, verify tables exist
2. `test_init_schema_idempotent` — run twice, no error
3. `test_insert_minute_params` — insert a 3-slice minute, 3 rows written
4. `test_insert_minute_params_idempotent` — insert same params twice,
   second write updates (not duplicates)
5. `test_insert_and_load_roundtrip` — insert params, load them back,
   values match
6. `test_load_prior_minute_params_returns_most_recent` — insert 2 minutes,
   load prior → gets the later one
7. `test_load_prior_minute_params_none_when_no_valid` — no valid params →
   None
8. `test_load_prior_minute_params_filters_invalid` — is_valid=False rows
   excluded
9. `test_insert_audit_report` — audit report → stored correctly
10. `test_load_surface_by_timestamp` — load full surface for one minute
11. `test_load_surface_history_time_range` — correct time slicing
12. `test_load_surface_history_dte_filter` — filtered to single DTE
13. `test_schema_has_all_required_columns` — verify column list matches
14. `test_null_anchor_on_fallback` — anchor fields nullable
15. `test_durrleman_g_stored` — audit min g(k) stored per-slice

## Things NOT To Do
- Do NOT hardcode TimescaleDB presence — use a connection and handle
  fallback to plain PostgreSQL.
- Do NOT store raw market data — only calibrated parameters.
- Do NOT overwrite timestamps with UPDATE — use ON CONFLICT DO UPDATE
  only for accidental re-processing.
- Do NOT create hypertables on non-TimescaleDB connections without
  catching the exception.
- Do NOT let is_valid=False params be returned by load_prior_minute_params.

## Commit Instructions
```bash
git add essvi/persistence.py tests/test_persistence.py
git commit -m "essvi/persistence: TimescaleDB output schema for params + audit, with idempotent upsert and warmstart loader (plan §17; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A11_persistence.md`.
