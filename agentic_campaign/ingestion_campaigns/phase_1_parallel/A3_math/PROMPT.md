# A3 — Math Module

**Role:** Quantitative derivatives pricing engineer with deep knowledge of Black-Scholes, business-time conventions, and Numba JIT compilation.

## Your Mission

Build `dataingestion/math.py` — a **pure computation module** that takes cleaned option chain DataFrames and enriches them with business time `T`, forward prices, and locally-computed Black-Scholes vega.

**No HTTP. No Theta. No database. No cleaning logic.** Just math.

## What You Build

One file: `dataingestion/math.py`

Three public functions:

```python
def compute_business_T(
    df: pd.DataFrame,
    cal: mcal.MarketCalendar,
) -> pd.DataFrame:
    """Add column `business_t` (years) per dataingestion.md Section 6.

    Formula: (minutes_remaining_today + sum of session minutes between) / (390 * 252)

    Uses pandas_market_calendars (XNYS/Nasdaq calendar) for exact session lengths
    including half-days (Thanksgiving, Christmas Eve, July 3 — 210 min).

    Args:
        df: Clean DataFrame from cleaning.py (contains timestamp, expiration).
        cal: pandas_market_calendars schedule (XNYS).

    Returns:
        DataFrame with `business_t` column added (float64).
    """

def compute_forward(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Add columns `forward_price`, `r`, `q`.

    For AMD: q = 0.0, so F = spot_close * exp(r * business_t).

    `r` is the risk-free rate as a cc decimal (converted from SOFR percent).
    `r` and `q` are attached to every row for auditability.

    Args:
        df: DataFrame from compute_business_T (has spot_close, business_t, r attached).

    Returns:
        DataFrame with `forward_price`, `r`, `q` columns added.
    """

def compute_vega(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Add column `vega` (Black-76 forward convention, Numba JIT).

    Formula:
        d1 = (ln(F/K) + 0.5 * sigma^2 * T) / (sigma * sqrt(T))
        vega = exp(-r * T) * F * phi(d1) * sqrt(T)
        phi(x) = exp(-x^2 / 2) / sqrt(2 * pi)

    Numba guards:
        - sigma > 0 (enforced by cleaning)
        - T > 0 (enforced by DTE >= 7)
        - F, K > 0

    Uses `@njit(fastmath=False)`, float64 throughout.
    Vectorized over the DataFrame rows.

    Returns:
        DataFrame with `vega` column added.
    """
```

### Column Contract

**Input:** Must match `dataingestion/COLUMNS.md` Section II.A (clean_df from cleaning.py).

**Output:** Must match `dataingestion/COLUMNS.md` Section III.

Columns added by you (in order they should be computed):

| Column | Function | Formula |
|--------|----------|---------|
| `business_t` | `compute_business_T` | Section 6 |
| `forward_price` | `compute_forward` | `spot_close * exp(r * business_t)` |
| `r` | `compute_forward` | Attached rate (cc decimal) |
| `q` | `compute_forward` | 0.0 (hardcoded for AMD) |
| `vega` | `compute_vega` | Section 9, Black-76 form |
| `log_moneyness` | `compute_vega` (or compute_forward) | `ln(strike / forward_price)` |

`_phase` updated to `"math"` by whichever function writes the last column (typically `compute_vega`).

### Business Time T — Deep Specification

Per `dataingestion.md` Section 6:

```
T_years = (minutes_remaining_today + sum of session minutes between) / (390 * 252)
```

**Denominator:** `390 * 252 = 98,280` minutes per year (institutional convention).

**Minutes remaining today:**
- Only if `bar_timestamp` falls within a trading session.
- `max(0, session_close_today - bar_timestamp)` in minutes.
- Outside RTH → 0 (bar is at the close or after-hours).

**Minutes between (strictly between bar date and expiration):**
- Trading days `d` where `bar_ts.date() < d < expiration`.
- Each day has its own session length:
  - Regular: 390 min (09:30–16:00 ET)
  - Early close: 210 min (09:30–13:00 ET)
  - Holiday: 0 min (not in schedule)

**Calendar source:** `pandas_market_calendars.get_calendar("XNYS")` (NYSE/Nasdaq) via:
```python
import pandas_market_calendars as mcal
cal = mcal.get_calendar("XNYS")
schedule = cal.schedule(start_date="2018-01-01", end_date="2030-01-01")
```

**Edge cases:**
- Bar timestamp is on a non-trading day (weekend/holiday) → push T to next session open? No — bar won't exist. Non-trading minutes don't exist. `minutes_remaining_today = 0`.
- Bar timestamp after 16:00 ET → `minutes_remaining_today = 0`.
- Early-close day → session ends at 13:00 ET (210 min total).
- Expiration IS a trading day → excluded from "between" as spec says "strictly between."

### Forward Price

For AMD: `q = 0`.

```python
forward_price = spot_close * np.exp(r * business_t)
```

Note: `r` needs to be in continuous compounding. The rate from `fetchers.py`'s `interest_rate/history/eod` comes as a **percent** (e.g., 4.50 = 4.5%). You convert:

```python
r_simple = rate_percent / 100.0
r_cc = r_simple  # Acceptable simplification for short tenors per Section 7
# OR the exact form:
# r_cc = np.log(1 + r_simple * tenor_years) / tenor_years
```

The plan says the flat (`r = rate/100` treated as cc) is the acceptable simplification — make it a module-level constant `USE_EXACT_RATE_CONVERSION = False` for now, switchable.

### Black-Scholes Vega — Numba Implementation

```python
from numba import njit
import numpy as np

@njit(fastmath=False)
def _vega_kernel(F_vec, K_vec, sigma_vec, T_vec, r_vec):
    """Vectorized vega computation. All inputs float64 arrays."""
    n = len(F_vec)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        F = F_vec[i]
        K = K_vec[i]
        sigma = sigma_vec[i]
        T = T_vec[i]
        r = r_vec[i]

        # Guards (already enforced by cleaning, but be safe)
        if sigma <= 1e-10 or T <= 1e-10 or K <= 0 or F <= 0:
            out[i] = np.nan
            continue

        sqrt_T = np.sqrt(T)
        d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_T)
        # phi(d1) = standard normal PDF at d1
        phi_d1 = np.exp(-0.5 * d1 * d1) / np.sqrt(2.0 * np.pi)
        out[i] = np.exp(-r * T) * F * phi_d1 * sqrt_T

    return out
```

The public `compute_vega(df)` extracts arrays from the DataFrame, calls `_vega_kernel`, and assigns the result back.

**Vega units:** The computed vega is `∂Price/∂σ` for a 1.00 (100%) volatility move. If you want per-vol-point, divide by 100 downstream. Document which you store — the plan says store the raw value.

### Invariants — NEVER Violate

1. **No Theta, no HTTP.** This module computes, it doesn't fetch.
2. **No database.** No asyncpg, psycopg, SQLite.
3. **No file I/O.** No CSV, parquet, pickle.
4. **No cleaning logic.** Assume input is already clean.
5. **No concurrency.** Functions are synchronous and single-threaded.
6. **Never modify input in-place.** Always return a copy with new columns.
7. **Never hardcode the calendar year range.** Accept the calendar as a parameter.
8. **Never use `fastmath=True` in Numba.** It can silently produce incorrect results for edge cases.
9. **float64 throughout.** No float32, no Python float → numpy for numba.
10. **T must be in years**, not days or minutes. The formula produces years.

### Key Reference Files

- `dataingestion.md` Sections 6, 7, 8, 9 — **the exact specifications for T, r/q, forward, vega**
- `dataingestion/COLUMNS.md` Sections II and III — **input/output column contracts**
- `dataingestion.md` Section 0 — AMD q=0, no split adjustments

### Verification Script

```bash
cd c:\Users\Rudol\Desktop\ThetaData_greeks
python -m pytest dataingestion/test_math.py -v
```

The verification script (`dataingestion/test_math.py`) will:
1. Create a synthetic clean DataFrame with known timestamps, strikes, spot, IV.
2. Provide a mock calendar (hardcoded schedule for 2026-06-15 to 2026-07-21).
3. Call each of your three functions sequentially.
4. Verify:
   - `business_t` is in years (roughly 0.01 to 0.25 for 7-90 DTE).
   - `business_t` is positive and monotonic with timestamp.
   - `forward_price > spot_close` (positive r) and close to spot for small T.
   - `vega` is non-negative.
   - `vega` is larger for ATM than far OTM.
   - `vega` scales with sqrt(T) (longer DTE → higher vega, ceteris paribus).
   - Numba kernel produces same result as scipy's BS vega.
   - Guards work: zero sigma → NaN, zero T → NaN, negative F → NaN.
5. Verify no imports from Theta, DB, cleaning, or HTTP libraries.

**Do not write the verification script.** It lives at `dataingestion/test_math.py`.

### Common Mistakes to Avoid

- Using calendar days instead of business days for T.
- Forgetting half-days (210 min sessions).
- Including the expiration day or bar-date day in "between" days.
- Using Black-Scholes spot form instead of Black-76 forward form.
- Forgetting to convert SOFR percent to decimal.
- Using fastmath=True in Numba.
- Computing vega per-vol-point (dividing by 100) — store raw.
- Not handling the early-close / holiday edge case of minutes_remaining_today = 0.