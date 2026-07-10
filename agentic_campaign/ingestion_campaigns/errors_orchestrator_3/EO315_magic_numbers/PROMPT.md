# EO315: Magic Numbers - Default Rate NaN Not 0.0

## Persona

You are a **quantitative analyst** who knows that defaulting missing risk-free rates to `0.0` is silent data corruption — it makes Black-Scholes produce wrong prices without any warning. Missing rates should be `NaN` so they propagate visibly.

## Core Objective

**Change the default missing rate from `0.0` to `NaN` in `_attach_rates`, and add validation that logs a warning when rates are missing.**

## Current Buggy Code (Line 431)

```python
def _attach_rates(df: pd.DataFrame, rates_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or rates_df.empty:
        df["r"] = 0.0  # BUG: Silent corruption!
        return df
    ...
    merged["r"] = merged["r"].fillna(0.0).astype(float)  # BUG: Fills NaN with 0.0
```

## Required Fix

```python
def _attach_rates(df: pd.DataFrame, rates_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or rates_df.empty:
        df["r"] = float("nan")  # Explicitly NaN
        log.warning("No rates data available — r set to NaN", extra={
            "rows_affected": len(df),
            "rates_df_empty": rates_df.empty,
        })
        return df
    
    df = df.copy()
    df["bar_date"] = pd.to_datetime(df["timestamp"]).dt.date
    merged = df.merge(rates_df[["date", "r"]], left_on="bar_date", right_on="date", how="left")
    merged = merged.drop(columns=["bar_date", "date"])
    
    # Log missing rates instead of silently filling
    missing = merged["r"].isna().sum()
    if missing > 0:
        log.warning("Missing rates for some dates", extra={
            "missing_count": int(missing),
            "total_rows": len(merged),
        })
    
    # Keep NaN — let downstream math handle it (or raise)
    merged["r"] = merged["r"].astype(float)  # NaN stays NaN
    return merged
```

## Invariants

- ✅ Missing rates = `NaN`, not `0.0`
- ✅ Warning logged when rates missing
- ✅ Downstream math (forward, vega) must handle `NaN` rates gracefully
- ✅ All tests pass (may need to update mock expectations)

## Success Criteria

### Functional
1. `df["r"]` is `NaN` when rates unavailable
2. Warning logged with context
3. No silent `fillna(0.0)` on rates

### Testing
```bash
python -m pytest dataingestion/test_math.py -v -k "forward or vega"
# Verify NaN handling in forward/vega
```