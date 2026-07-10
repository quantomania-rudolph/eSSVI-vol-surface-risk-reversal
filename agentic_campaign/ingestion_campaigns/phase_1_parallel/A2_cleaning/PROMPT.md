# A2 — Data Cleaning

**Role:** Quantitative data quality engineer, expert in options market microstructure.

## Your Mission

Build `dataingestion/cleaning.py` — a **pure pandas/numpy** module that takes raw option chain DataFrames from `fetchers.py` and produces (a) a clean DataFrame ready for math, and (b) a quarantine DataFrame documenting every rejected row.

**No HTTP. No Theta. No database. No file I/O.** Just DataFrames in, DataFrames out.

## What You Build

One file: `dataingestion/cleaning.py`

One public function:

```python
def clean_option_chain(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply all quality and arbitrage checks per dataingestion.md Sections 4-5.

    Args:
        df: Raw DataFrame from fetchers.py (must satisfy COLUMNS.md Section I).

    Returns:
        (clean_df, quarantine_df) tuple.
        clean_df has additional columns: mid_price, spread, rel_spread,
          quality_flags, dte_calendar, and _phase="clean".
        quarantine_df has all original columns plus reject_code, reject_detail,
          and _phase="quarantine".
    """
```

### The 8 Checks (exact order from dataingestion.md Section 5)

Implement as individual functions, then compose in `clean_option_chain`:

| # | Function name | Check | Rule | Reject code |
|---|---------------|-------|------|-------------|
| 1 | `check_no_quote` | No-quote | `bid > 0 AND ask > 0` (hard) | `NO_QUOTE` |
| 2 | `check_crossed` | Locked/Crossed | `ask > bid` (hard) | `CROSSED` |
| 3 | `check_subpenny` | Tick/Penny | `round(bid*100) == bid*100` and `round(ask*100) == ask*100` (hard) | `SUBPENNY` |
| 4 | `check_spread` | Spread two-tier | HARD reject `rel_spread > 0.25`. BELLY flag (quality_flags bit 0) for `rel_spread > 0.10`. | `SPREAD_HARD` |
| 5 | `check_zero_iv` | Zero-IV | `implied_vol > 0.005` (hard) | `ZERO_IV` |
| 6 | `check_intrinsic` | Intrinsic value | calls: `mid >= max(0, spot_close - strike)`. puts: `mid >= max(0, strike - spot_close)` (hard) | `INTRINSIC` |
| 7 | `check_monotonicity` | Strike monotonicity | Per `(expiration, timestamp, option_type)`, sort by strike: call mids non-increasing, put mids non-decreasing. Drop violating leg, not whole slice. (soft, can be relaxed with tick tolerance) | `MONOTONICITY` |
| 8 | `check_oi` | OI liquidity | `open_interest > 100` (or null OI → reject) (hard) | `LOW_OI` |

**Before checks, apply the pre-filter per Section 4:**

1. **Delta band:** `0.10 <= abs(delta) <= 0.90`
2. **DTE band:** `7 <= dte_calendar <= 90` where `dte_calendar = (expiration - bar_date).days`

The pre-filter runs BEFORE the 8 checks. Rows outside either band go to quarantine with codes `DELTA_BAND` and `DTE_BAND`.

### Column Contract

**Input:** Must match `dataingestion/COLUMNS.md` Section I exactly.

**Output (clean_df):**
- Same columns as input, **PLUS**: `mid_price`, `spread`, `rel_spread`, `quality_flags`, `dte_calendar`
- `_phase` set to `"clean"`
- All rows that passed all checks

**Output (quarantine_df):**
- Same columns as input
- **PLUS**: `reject_code` (str), `reject_detail` (str)
- `_phase` set to `"quarantine"`
- **One row per rejection** — if a row fails multiple checks, put it in quarantine ONCE with only the first failure's code (ordered by check priority above)

**Quality flags bitmask:**

| Bit | Flag | When set |
|-----|------|----------|
| 0 | `BELLY_SPREAD` | `rel_spread > 0.10` (row stays in clean_df, NOT quarantined) |

`quality_flags` is `int32`, default 0. Only bit 0 is used for now.

### Key Computations

```python
mid_price = (bid + ask) / 2.0
spread = ask - bid
rel_spread = spread / mid_price  # NaN-safe: 0 if mid <= 0
dte_calendar = (expiration - timestamp).dt.days  # integer
```

### Invariants — NEVER Violate

1. **Never drop rows silently.** Every rejected row goes to quarantine with a code.
2. **Never modify the original input DataFrame.** Work on a copy.
3. **Never import from dataingestion.fetchers, math, db_writer, or orchestrator.** Pure pandas/numpy only.
4. **Never use Theta Data API, HTTP, or database.** No network calls.
5. **Never read or write files.** No CSV, parquet, pickle.
6. **Checks must run in order** (1→8 as listed above). Earlier checks are cheaper and catch more.
7. **Pre-filter runs before checks.** Delta/DTE filtering first, then quality.
8. **One rejection code per row.** First failing check wins.
9. **Monotonicity check operates per (expiration, timestamp, type) group.**
10. **Every checked row must be accounted for:** either in clean_df or quarantine_df, never both.

### Key Reference Files

- `dataingestion.md` Sections 4 and 5 — **the authoritative spec for every check**
- `dataingestion/COLUMNS.md` Sections I and II — **exact column contracts**
- `dataingestion.md` Section 0 — AMD specifics (penny interval, no splits, no dividends)

### Verification Script

```bash
cd c:\Users\Rudol\Desktop\ThetaData_greeks
python -m pytest dataingestion/test_cleaning.py -v
```

The verification script (`dataingestion/test_cleaning.py`) will:
1. Create synthetic DataFrames with exactly the COLUMNS.md Section I columns.
2. Inject known violations (zero bid, crossed quote, sub-penny price, extreme spread, zero IV, intrinsic violation, monotonicity kink, low OI).
3. Call `clean_option_chain()` and verify:
   - Every injected violation is caught with the correct reject_code.
   - Clean DataFrame has no violated rows.
   - All input rows are accounted for (clean + quarantine row counts = input row count).
   - Additional columns exist and have correct dtypes.
   - Delta band filter works for both calls and puts.
   - DTE band filter works.
   - Belly spread flag is set (quality_flags bit 0) but row is NOT quarantined.
   - Pre-filter runs before quality checks.
4. Verify no imports from Theta, DB, or other pipeline modules.

**Do not write the verification script yourself.** It lives at `dataingestion/test_cleaning.py`.

### Common Mistakes to Avoid

- Quarantining belly-spread rows (rel_spread > 0.10). That's a FLAG, not a reject.
- Rejecting rows for MONOTONICITY when only one contract exists in the group.
- Using `abs_delta` instead of Theta's raw delta (puts are negative!).
- Computing `dte_calendar` before pre-filter (computed on ALL rows, but checked only during pre-filter).
- Modifying the input DataFrame in-place — always copy first.
- Using `>=` or `<=` in spread checks — the plan says `>` and `<` only.