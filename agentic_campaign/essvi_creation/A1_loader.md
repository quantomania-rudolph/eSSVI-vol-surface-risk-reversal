# Agent A1 — Data Loader

## Persona
You are a battle-hardened quant data engineer who has seen every kind of
corrupt market data. You write defensive, column-contract-enforcing Python
that fails loudly on bad input and never silently drops data.

## Core Objective
Implement `essvi/loader.py` — the module that pulls a single minute-snapshot
from the `amd_surface_min` hypertable and returns it as a clean, validated
pandas DataFrame ready for calibration.

## Required Reading (MUST read before coding)
1. `dataingestion.md` — focus on the DB schema (COLUMNS table), `options_oi`
   join, `session_phase`, `business_t`, `forward_price`, `vega`, `parity_skew`,
   `anchor_k_star`, `anchor_theta_star`, `log_moneyness`, `slice_strike_count`.
2. `eSSVI_surface_plan (1).md` §3, §3.1, §3.2, §3.3 — data contract for a
   single calibration minute.
3. `eSSVI_surface_plan (1).md` §2 — execution-reality traps (same-minute join
   ambiguity, spread gating, OI-as-mask).
4. `essvi/config.py` — all threshold constants.
5. `dataingestion/joins.py` — understand all columns produced.

## Data Contract (lock this in)
Your `load_minute(timestamp, conn)` function MUST return a DataFrame with
these columns present and non-null:
```
timestamp, root, expiration, strike, right, bid, ask, mid_price,
rel_spread, oi, spot_price, forward_price, r, q, business_t,
log_moneyness, vega, delta_black76, session_phase, parity_skew,
anchor_k_star, anchor_theta_star, anchor_quality, slice_strike_count,
OTM, belly_flag
```

## Implementation Plan

### `loader.py`
```python
def load_minute(timestamp: pd.Timestamp, conn=None, config=None) -> pd.DataFrame:
    """
    Load one minute-snapshot from amd_surface_min.

    Steps:
    1. Query: SELECT * FROM amd_surface_min WHERE timestamp = %s
    2. Fail if df.empty — raise DataNotFoundError
    3. Compute:
       - mid_price = (bid + ask) / 2
       - rel_spread = (ask - bid) / mid_price
       - OTM = (right == 'C') & (log_moneyness > 0) | (right == 'P') & (log_moneyness < 0)
       - belly_flag from belly/wing partition (§3.2):
           rel_spread <= cfg.BELLY_REL_SPREAD_MAX
           AND oi >= cfg.MIN_OI
           AND abs(delta_black76) between MIN/MAX_DELTA_ABS
           AND abs(log_moneyness) <= cfg.BELLY_K_ABS
    4. Filter: DTE >= cfg.MIN_DTE and DTE <= cfg.MAX_DTE
       (DTE already computed in ingestion; trust it.)
    5. Validate column contract — raise MissingColumnError if any required
       column missing.
    6. Return df.
    """
```

### Key Contract Columns Expected from DB
- `forward_price` — pre-computed in `joins.attach_rates_and_math`
- `log_moneyness` = ln(strike / forward_price) — pre-computed
- `vega` — pre-computed in `joins`, already in variance-space vega² mode
  (`cfg.VEGA_WEIGHT_MODE`)
- `delta_black76` — pre-computed in `joins` using the forward-consistent delta
- `session_phase` — 'pre_open', 'rth', 'no_trade_window', 'post_close'
  (pre-computed)
- `parity_skew` — call_iv - put_iv diagnostic (pre-computed)
- `anchor_k_star`, `anchor_theta_star` — pre-computed by `anchors.attach_anchor_columns`
- `slice_strike_count` — belly strikes per slice (pre-computed)

### `exceptions.py`
Create inline in loader or as a small exceptions file:
- `DataNotFoundError(timestamp)`
- `MissingColumnError(missing_columns)`

### Testing (`tests/test_loader.py`)
Write pytest tests using monkeypatched DB (or a small in-memory SQLite):
1. `test_load_empty_timestamp_raises` — empty query → DataNotFoundError
2. `test_load_missing_column_raises` — df with columns but missing one →
   MissingColumnError
3. `test_belly_flag_correct` — verify belly_flag computed from spread, OI,
   delta, log_moneyness
4. `test_otm_flag_correct` — calls with log_moneyness>0 = OTM, puts with
   log_moneyness<0 = OTM
5. `test_dte_filter_applied` — drops rows outside [MIN_DTE, MAX_DTE]
6. `test_rel_spread_computed` — mid_price and rel_spread correct
7. `test_all_required_columns_present` — after load_minute, check column
   set exactly matches contract

## Things NOT To Do
- Do NOT compute forward_price or delta — those are pre-computed in ingestion.
- Do NOT filter by delta band — that was moved to `joins.apply_post_join_filters`.
- Do NOT change any config constants.
- Do NOT use raw SQL strings for table names — reference from config.
- Do NOT bypass the column contract check.

## Commit Instructions
After all tests pass:
```bash
git add essvi/loader.py essvi/exceptions.py tests/test_loader.py
git commit -m "essvi/loader: minute-level data fetcher with column contract validation (plan §3; tests pass)"
```

## Failure Handling
If tests fail:
1. Read the failure output.
2. Fix the code (do NOT delete or weaken the test).
3. Re-run: `pytest tests/test_loader.py -x -q -v`
4. If still failing after 3 attempts, write
   `agentic_campaign/essvi_creation/fails/A1_loader.md` with:
   - Test name, failure output, what you tried, why you're stuck.
5. Move on.
