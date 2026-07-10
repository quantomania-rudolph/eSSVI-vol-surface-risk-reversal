# Agent A10 — Minute-Level Runtime Engine

## Persona
You are the control-room operator for a live trading desk. You know that the
calibration must run exactly once per minute, handle session boundaries
gracefully, detect and skip no-trade windows, feed the audit result to the
kill switch, and NEVER serve a stale or arbitrage-violating surface to
downstream consumers. You are also obsessed with logging — every decision
must be traceable.

## Core Objective
Implement `essvi/runtime.py` — the production runtime loop that orchestrates
`loader.load_minute()` → `sequential.calibrate_one_minute()` →
`audit.run_full_audit()` → persistence, with full session-phase awareness,
cold-start logic, stale-surface detection, and kill-switch enforcement.

## Required Reading
1. `eSSVI_surface_plan (1).md` §14 — Minute-level runtime loop, session
   awareness, re-anchoring.
2. `eSSVI_surface_plan (1).md` §16 — Complete minute-level runtime.
3. `essvi/config.py` — all `SESSION_*`, `NO_TRADE_*`, `HALF_DAY_SESSION_*`,
   `REGULAR_SESSION_*`, `COLD_START_AT_SESSION_OPEN`, `STALE_SLICE_MAX_MINUTES`.
4. Already-written `essvi/loader.py` — `load_minute()`.
5. Already-written `essvi/sequential.py` — `calibrate_one_minute()`.
6. Already-written `essvi/audit.py` — `run_full_audit()`, `is_surface_safe()`.
7. `dataingestion/joins.py` — `session_phase` values: `pre_open`, `rth`,
   `no_trade_window`, `post_close`.

## Runtime State Machine

The runtime maintains an internal state across successive calls:

```python
@dataclass
class RuntimeState:
    last_minute_params: dict | None = None
    last_calibration_time: pd.Timestamp | None = None
    last_surface_id: str | None = None
    last_audit_passed: bool = True
    stale_surface: bool = False      # True if no successful calibration
                                     # in last STALE_SLICE_MAX_MINUTES
    cold_start: bool = True          # True at session start
    minute_count: int = 0
    total_calibrations: int = 0
    total_failures: int = 0
```

## Main Runtime Loop (`calibrate_minute`)

The function is called once per minute with a timestamp:

```python
def calibrate_minute(timestamp: pd.Timestamp, conn=None) -> dict:
    """
    ONE-MINUTE CALIBRATION CYCLE.

    1. Determine session_phase for this timestamp:
       - pre_open: within 60 min (NO_TRADE_OPEN_MIN) of open → skip
       - no_trade_window: within 60 min (NO_TRADE_CLOSE_MIN) of close → skip
       - post_close: after close → skip
       - rth: proceed with calibration

    2. IF session_phase != 'rth':
       - Log reason.
       - Return {"calibrated": False, "reason": session_phase}.

    3. Load data: loader.load_minute(timestamp, conn)

    4. Check number of expiry slices:
       - IF < MIN_STRIKES_PER_SLICE valid slices → skip
         return {"calibrated": False, "reason": "too_few_slices"}

    5. Determine cold_start:
       - IF COLD_START_AT_SESSION_OPEN AND state.cold_start:
         prior = None, set state.cold_start = False after calibration.
       - ELSE: prior = state.last_minute_params

    6. Calibrate: sequential.calibrate_one_minute(df, prior, warmstart)

    7. Audit: audit.run_full_audit(calibration_result)

    8. IF audit_report['kill_triggered']:
       - Log violations.
       - IF state.last_minute_params is not None AND not too stale:
         → REUSE last valid surface.
         Update state.last_calibration_time but mark stale_surface.
       - ELSE:
         → Return {"calibrated": False, "reason": "kill_switch",
                    "violations": audit_report}
         state.total_failures += 1

    9. IF audit passes:
       - Update state.last_minute_params = calibration_result
       - Update state.last_calibration_time = timestamp
       - state.stale_surface = False
       - state.total_calibrations += 1

    10. Return result dict with:
        - calibrated: bool
        - timestamp
        - session_phase
        - n_slices, n_valid, any_invalid
        - surface_id (UUID or timestamp-based)
        - audit_passed: bool
        - params summary
    """
```

## Session Phase Detection

```python
def get_session_phase(timestamp: pd.Timestamp) -> str:
    """
    Determine session phase from timestamp.

    Uses pandas_market_calendars or simple hour/minute checks:
    - pre_open: 9:00-9:30 ET
    - rth: 9:30-16:00 ET
    - no_trade_window: 15:00-16:00 ET (last 60 min of rth)
    - post_close: after 16:00 ET

    Also checks for half-days (210-minute sessions).
    """
```

## Cold Start Logic

At session open (or first minute after a gap):
```
IF cfg.COLD_START_AT_SESSION_OPEN:
  - Reset temporal regularization (λ_temp effectively disabled
    since prior=None)
  - Allow a fresh-slate calibration
  - Set state.cold_start = True → consumed after first successful
    calibration
```

## Stale Surface Detection

```python
def is_surface_stale(state: RuntimeState, current_time) -> bool:
    """
    True if time since last successful calibration > STALE_SLICE_MAX_MINUTES.
    """
```

## Functions to Implement

```python
class RuntimeState:
    """Mutable state tracking across calibration cycles."""

def get_session_phase(timestamp, calendar=None) -> str:
    """Determine pre_open, rth, no_trade_window, post_close."""

def should_calibrate(timestamp, session_phase) -> tuple[bool, str]:
    """Return (proceed, reason). Only 'rth' proceeds."""

def calibrate_minute(timestamp, conn=None, state=None) -> dict:
    """Main runtime loop described above."""

def calibrate_batch(
    start_time, end_time, freq="1min", conn=None
) -> list[dict]:
    """Bulk calibration over a time range — for backtesting."""

def get_runtime_summary(state: RuntimeState) -> str:
    """Human-readable summary of runtime state."""
```

## Testing (`tests/test_runtime.py`)

1. `test_get_session_phase_rth` — 10:30 ET → 'rth'
2. `test_get_session_phase_pre_open` — 9:15 ET → 'pre_open'
3. `test_get_session_phase_no_trade` — 15:30 ET → 'no_trade_window'
4. `test_get_session_phase_post_close` — 16:30 ET → 'post_close'
5. `test_should_calibrate_rth_only` — only 'rth' returns proceed=True
6. `test_calibrate_minute_pre_open_skips` — returns calibrated=False
7. `test_calibrate_minute_no_trade_skips` — returns calibrated=False
8. `test_calibrate_minute_success` — mock all subsystems, full flow
    produces calibrated=True
9. `test_calibrate_minute_kill_switch_reuses_prior` — audit fails →
    falls back to prior params
10. `test_calibrate_minute_kill_switch_no_prior` — audit fails AND no
    prior → calibrated=False
11. `test_cold_start_resets_prior` — first RTH minute uses prior=None
12. `test_cold_start_consumed` — second RTH minute uses prior from
    first calibration
13. `test_stale_surface_detection` — no calibration for > MAX_MINUTES →
    stale=True
14. `test_state_transitions` — state updated correctly after each cycle
15. `test_batch_calibration_iterates_all_minutes`
16. `test_runtime_summary_format`

## Things NOT To Do
- Do NOT calibrate outside RTH — skip with a logged reason.
- Do NOT serve a surface that failed the kill switch unless a valid prior
  exists.
- Do NOT skip the audit step — every successful calibration must be audited.
- Do NOT log PII or raw data — only metrics and flags.
- Do NOT mix up calendar days and trading days for stale detection.

## Commit Instructions
```bash
git add essvi/runtime.py tests/test_runtime.py
git commit -m "essvi/runtime: minute-level calibration loop with session awareness, cold start, stale detection, kill-switch enforcement (plan §14, §16; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A10_runtime.md`.
