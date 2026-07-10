# EO302: OI Join Nullifies Valid Data Fix

## Persona

You are a **financial data engineer** who understands that open interest is a critical field for options analytics. When the daily OI fetch returns empty (e.g., no trades that day), you MUST NOT nullify the open interest that came from the raw greeks fetch — the raw fetch already has valid OI per bar.

## Core Objective

**Fix `_join_oi` to preserve raw open_interest when the daily OI fetch returns empty, instead of nullifying the entire column.**

## Current Buggy Code (Lines 395-399)

```python
def _join_oi(opt_df: pd.DataFrame, oi_df: pd.DataFrame) -> pd.DataFrame:
    if opt_df.empty or oi_df.empty:
        opt_df["open_interest"] = pd.NA  # BUG: Drops valid raw OI!
        return opt_df
    # ... join logic
```

**Problem**: The raw greeks fetch (`async_fetch_option_greeks_first_order`) already includes `open_interest` per bar. The daily OI fetch (`async_fetch_option_open_interest`) provides a daily aggregate. When the daily fetch is empty (no trades that day), the code currently **overwrites valid per-bar OI with NA**.

## Required Fix

```python
def _join_oi(opt_df: pd.DataFrame, oi_df: pd.DataFrame) -> pd.DataFrame:
    if opt_df.empty:
        opt_df["open_interest"] = pd.NA
        return opt_df

    opt_df = opt_df.copy()
    # Drop existing open_interest column if present (from raw fetch)
    if "open_interest" in opt_df.columns:
        opt_df = opt_df.drop(columns=["open_interest"])

    if oi_df.empty:
        # Daily OI unavailable — PRESERVE raw per-bar OI if it exists
        # But we dropped it above, so we need to not drop it in this case
        # Better: only drop if we're about to replace with daily data
        pass
    else:
        opt_df["bar_date"] = pd.to_datetime(opt_df["timestamp"]).dt.date
        oi_df = oi_df.copy()
        oi_df["date"] = pd.to_datetime(oi_df["date"]).dt.date
        merged = opt_df.merge(
            oi_df[["date", "open_interest"]],
            left_on="bar_date",
            right_on="date",
            how="left",
        ).drop(columns=["bar_date", "date"])
        merged["open_interest"] = merged["open_interest"].astype("Int64")
        return merged

    return opt_df  # Returns opt_df with raw open_interest intact
```

**Better approach**: Don't drop the raw column unless we have daily data to replace it.

```python
def _join_oi(opt_df: pd.DataFrame, oi_df: pd.DataFrame) -> pd.DataFrame:
    if opt_df.empty:
        opt_df["open_interest"] = pd.NA
        return opt_df

    if oi_df.empty:
        # Daily OI unavailable — keep raw per-bar OI if present
        if "open_interest" not in opt_df.columns:
            opt_df["open_interest"] = pd.NA
        return opt_df

    # Daily OI available — replace raw with daily aggregate
    opt_df = opt_df.copy()
    if "open_interest" in opt_df.columns:
        opt_df = opt_df.drop(columns=["open_interest"])

    opt_df["bar_date"] = pd.to_datetime(opt_df["timestamp"]).dt.date
    oi_df = oi_df.copy()
    oi_df["date"] = pd.to_datetime(oi_df["date"]).dt.date

    merged = opt_df.merge(
        oi_df[["date", "open_interest"]],
        left_on="bar_date",
        right_on="date",
        how="left",
    ).drop(columns=["bar_date", "date"])
    merged["open_interest"] = merged["open_interest"].astype("Int64")
    return merged
```

## Invariants

- ✅ Raw per-bar OI preserved when daily OI fetch is empty
- ✅ Daily OI replaces raw OI when daily fetch succeeds
- ✅ Empty OI column added if neither source has data
- ✅ `open_interest` dtype is `Int64` (nullable integer)
- ✅ Column contract: `_phase="raw"` has OI from greeks fetch; `_phase="clean"` has OI from daily fetch (or preserved raw)

## Success Criteria

### Functional
1. When `oi_df.empty=True`, output retains `open_interest` from input `opt_df`
2. When `oi_df` has data, output uses daily OI (left join on date)
3. Output always has `open_interest` column as `Int64`

### Testing
```bash
python -m pytest dataingestion/test_orchestrator.py::TestAsyncMockVerification::test_mock_oi_has_required_columns -v
python -m pytest dataingestion/test_orchestrator.py::TestPipelineColumnPropagation::test_columns_flow_from_fetch_through_math -v
# Add new test for OI preservation
```

## Verification Agent

Add test in `test_orchestrator.py`:
```python
def test_oi_preserved_when_daily_fetch_empty(self, patched_orchestrator):
    """Raw per-bar OI preserved when daily OI fetch returns empty."""
    # Mock oi_df empty, verify opt_df open_interest unchanged
```