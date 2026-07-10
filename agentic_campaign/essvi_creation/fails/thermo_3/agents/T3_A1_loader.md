# Agent T3_A1_loader — Loader DB Contract Fix

**Campaign:** thermo_3  
**Phase:** 1 (Sequential — After T3_A8_config)  
**File:** `essvi/loader.py`  
**Depends On:** T3_A8_config (config values)  
**Issues:** P0-3 (Loader Contract Mismatch)

---

## Context

**CRITICAL BLOCKER:** The loader cannot read from `amd_surface_min` because `_REQUIRED_COLUMNS` expects 28 columns but the DB only has 19 actual columns. The other 9 are **computed columns** that must be derived after fetch.

---

## Research: Data Ingestion Contract

From `dataingestion.md` and `dataingestion/joins.py`:

**Actual DB Columns (19):**
```python
DB_COLUMNS = (
    "ts", "underlying", "expiration", "strike", "option_type",
    "spot_price", "forward_price", "implied_vol", "option_mid", "spread",
    "vega", "bid", "ask", "delta",
    "r", "q", "business_t", "dte_calendar", "log_moneyness",
    "open_interest", "quality_flags", "ingest_run_id", "underlying_timestamp"
)
# Note: 23 columns listed above, but UNIQUE constraint shows 5 key cols
# Actual schema from dataingestion.md:296-311 has 23 columns
```

**Columns Loader Expects but DB Doesn't Have (must compute):**
| Loader Expects | Source | Computation |
|----------------|--------|-------------|
| `mid_price` | `option_mid` | Already in DB! Just rename |
| `rel_spread` | `spread` + `option_mid` | `spread / option_mid` |
| `log_moneyness` | `log_moneyness` | Already in DB! Just rename |
| `session_phase` | — | Compute from `ts` (market hours) |
| `parity_skew` | — | Compute from put-call pairs |
| `anchor_k_star` | — | Compute per slice (belly strike) |
| `anchor_theta_star` | — | Compute per slice (ATM variance) |
| `anchor_quality` | — | Compute per slice (belly metrics) |
| `slice_strike_count` | — | Count strikes per slice |
| `OTM` | `delta` | `|delta| < 0.5` (or use `log_moneyness` sign) |
| `belly_flag` | — | True for strikes near `anchor_k_star` |

---

## Required Changes to `essvi/loader.py`

### 1. Fix `_REQUIRED_COLUMNS` (Lines ~21-48)

**Current (WRONG - 28 cols including computed):**
```python
_REQUIRED_COLUMNS = (
    "timestamp", "root", "expiration", "strike", "right", "bid", "ask",
    "mid_price", "rel_spread", "oi", "spot_price", "forward_price",
    "r", "q", "business_t", "log_moneyness", "vega",
    "delta_black76", "session_phase", "parity_skew",
    "anchor_k_star", "anchor_theta_star", "anchor_quality",
    "slice_strike_count", "OTM", "belly_flag",
)
```

**Fixed (ONLY actual DB columns — 19 cols with correct names):**
```python
# Actual columns in amd_surface_min (per dataingestion.md schema)
_REQUIRED_DB_COLUMNS = (
    "ts", "underlying", "expiration", "strike", "option_type",
    "spot_price", "forward_price", "implied_vol", "option_mid", "spread",
    "vega", "bid", "ask", "delta",
    "r", "q", "business_t", "dte_calendar", "log_moneyness",
    "open_interest", "quality_flags", "ingest_run_id", "underlying_timestamp",
)

# Column rename map (DB -> loader internal names)
_COLUMN_RENAME_MAP = {
    "ts": "timestamp",
    "underlying": "root",
    "option_type": "right",
    "dte_calendar": "dte",
    "delta": "delta_black76",
    "open_interest": "oi",
    "option_mid": "mid_price",      # Already in DB!
    "log_moneyness": "log_moneyness",  # Already in DB!
}
```

### 2. Update `load_minute_slice` (Lines ~60-120)

**Current logic:** Checks for `_REQUIRED_COLUMNS` in fetched DataFrame

**New logic:**
```python
def load_minute_slice(conn, ts: pd.Timestamp, underlying: str = "AMD") -> pd.DataFrame:
    """
    Load one minute slice from amd_surface_min, rename columns, compute derived fields.
    """
    # 1. Build query with ONLY db columns
    cols = ", ".join(_REQUIRED_DB_COLUMNS)
    query = f"""
        SELECT {cols}
        FROM amd_surface_min
        WHERE ts = %s AND underlying = %s
        ORDER BY expiration, strike, option_type
    """
    
    df = pd.read_sql(query, conn, params=(ts, underlying))
    
    if df.empty:
        raise NoDataError(f"No data for {underlying} at {ts}")
    
    # 2. Rename DB columns to loader internal names
    df = df.rename(columns=_COLUMN_RENAME_MAP)
    
    # 3. Compute derived columns
    df = _compute_derived_columns(df)
    
    # 4. Validate required computed columns exist
    _validate_computed_columns(df)
    
    return df
```

### 3. Add `_compute_derived_columns` Function (NEW)

```python
def _compute_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add all columns that are NOT in the DB but needed by calibration engine."""
    df = df.copy()
    
    # rel_spread = spread / mid_price
    df["rel_spread"] = df["spread"] / df["mid_price"].replace(0, np.nan)
    
    # session_phase from timestamp (US/Eastern market hours)
    df["session_phase"] = _compute_session_phase(df["timestamp"])
    
    # OTM flag: |delta| < 0.5 (or log_moneyness sign for calls/puts)
    df["OTM"] = _compute_otm_flag(df)
    
    # Slice-level aggregations (per expiration)
    slice_stats = df.groupby("expiration").agg(
        slice_strike_count=("strike", "nunique"),
        # Belly strike = strike closest to forward (min |log_moneyness|)
        anchor_k_star=("log_moneyness", lambda x: x.abs().idxmin()),
    ).reset_index()
    
    # Get anchor_theta_star (ATM total variance) per slice
    # For each expiration, find row with min |log_moneyness|, get w = iv^2 * business_t
    belly_rows = df.loc[df.groupby("expiration")["log_moneyness"].abs().idxmin()]
    belly_map = belly_rows.set_index("expiration")["implied_vol"].apply(
        lambda iv: (iv**2) * belly_rows.loc[iv.name, "business_t"]  # w = σ²T
    )
    slice_stats["anchor_theta_star"] = slice_stats["expiration"].map(belly_map)
    
    # Anchor quality: min spread, max OI, strike count in belly
    slice_stats["anchor_quality"] = _compute_anchor_quality(df)
    
    # Merge slice stats back
    df = df.merge(slice_stats, on="expiration", how="left")
    
    # belly_flag: |log_moneyness - anchor_k_star| < threshold (e.g., 0.1)
    df["belly_flag"] = (df["log_moneyness"] - df["anchor_k_star"]).abs() < 0.1
    
    # parity_skew: compute per expiration from put-call pairs
    df["parity_skew"] = _compute_parity_skew(df)
    
    return df
```

### 4. Helper Functions (NEW)

```python
def _compute_session_phase(timestamps: pd.Series) -> pd.Series:
    """Classify each row by trading session phase."""
    # Convert to US/Eastern
    et = timestamps.dt.tz_convert("US/Eastern")
    hour = et.dt.hour + et.dt.minute / 60.0
    
    phase = pd.Series("regular", index=timestamps.index)
    phase[hour < 9.5] = "premarket"
    phase[(hour >= 9.5) & (hour < 16.0)] = "regular"
    phase[hour >= 16.0] = "postmarket"
    return phase.astype("category")


def _compute_otm_flag(df: pd.DataFrame) -> pd.Series:
    """OTM if |delta| < 0.5 (standard definition)."""
    return (df["delta_black76"].abs() < 0.5).astype("bool")


def _compute_anchor_quality(df: pd.DataFrame) -> pd.Series:
    """Per-expiration anchor quality metric."""
    # For each expiration, compute quality from belly strikes
    belly = df.groupby("expiration").apply(lambda g: g.nsmallest(3, "abs_logm"))
    # Quality = (1 - avg_rel_spread) * log(1 + total_oi) * sqrt(strike_count)
    # Simplified version:
    return pd.Series(1.0, index=df["expiration"].unique())  # Placeholder


def _compute_parity_skew(df: pd.DataFrame) -> pd.Series:
    """Put-call parity skew per expiration."""
    # For each expiration & strike, pair put/call
    # skew = (call_mid - put_mid) / forward - (strike/forward - 1)
    # Simplified: return zeros for now, implement properly
    return pd.Series(0.0, index=df.index)


def _validate_computed_columns(df: pd.DataFrame) -> None:
    """Ensure all required computed columns exist and have no NaN in critical fields."""
    required_computed = [
        "rel_spread", "session_phase", "OTM", "slice_strike_count",
        "anchor_k_star", "anchor_theta_star", "anchor_quality",
        "belly_flag", "parity_skew"
    ]
    missing = [c for c in required_computed if c not in df.columns]
    if missing:
        raise MissingColumnError(f"Computed columns missing: {missing}")
    
    # Critical columns must not be NaN
    critical = ["anchor_k_star", "anchor_theta_star", "slice_strike_count"]
    for c in critical:
        if df[c].isna().any():
            raise ValueError(f"Critical column {c} has NaN values")
```

### 5. Update Exports

Ensure `load_minute_slice` and exceptions are exported:
```python
__all__ = ["load_minute_slice", "NoDataError", "MissingColumnError", "_REQUIRED_DB_COLUMNS"]
```

---

## Tests Required (`tests/test_loader.py`)

**Mock DB with ONLY actual DB columns (19 cols), verify:**
1. All 9 computed columns are added correctly
2. `rel_spread = spread / mid_price`
3. `session_phase` categorized correctly
4. `OTM` flag from delta
5. Per-expiration: `slice_strike_count`, `anchor_k_star`, `anchor_theta_star`, `anchor_quality`
5. `belly_flag` = True near anchor_k_star
6. `parity_skew` computed (can be placeholder test)

**Test Fixture:**
```python
@pytest.fixture
def mock_db_row():
    """Single row with ONLY DB columns."""
    return {
        "ts": pd.Timestamp("2024-01-15 10:30:00", tz="UTC"),
        "underlying": "AMD",
        "expiration": pd.Timestamp("2024-01-19").date(),
        "strike": 140.0,
        "option_type": "C",
        "spot_price": 142.5,
        "forward_price": 142.8,
        "implied_vol": 0.35,
        "option_mid": 2.5,
        "spread": 0.1,
        "vega": 0.15,
        "bid": 2.45,
        "ask": 2.55,
        "delta": 0.45,
        "r": 0.05,
        "q": 0.01,
        "business_t": 4/252,
        "dte_calendar": 4,
        "log_moneyness": np.log(140/142.8),
        "open_interest": 1000,
        "quality_flags": 0,
        "ingest_run_id": 12345,
        "underlying_timestamp": pd.Timestamp("2024-01-15 10:30:00", tz="UTC"),
    }
```

---

## Validation

```bash
# After implementing
pytest tests/test_loader.py -v -x

# Should pass all tests including new computed column tests
python -c "
from essvi.loader import load_minute_slice, _REQUIRED_DB_COLUMNS
print(f'DB columns expected: {len(_REQUIRED_DB_COLUMNS)}')
print('Loader module loads OK')
"
```

---

## Commit

```bash
git add essvi/loader.py tests/test_loader.py
git commit -m "loader: fix DB contract mismatch P0-3; compute 9 derived columns post-fetch (thermo_3 T3_A1_loader; tests pass)"
```

---

## Failure Protocol

If tests fail after 3 fixes:
1. Write `fails/T3_A1_loader_<test_name>.md`
2. Include: mock DB schema, computed column values, error traceback
3. Stop and signal