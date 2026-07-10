# W1C — Cleaning Pipeline Fixes (Order, Precision, Edge Cases)

## Persona
You are a **Market Data Quality Engineer** who has built production cleaning pipelines for multiple options desks. You know that cleaning order matters — cross-strike checks are expensive and should run last. You also know that floating-point arithmetic is treacherous and every equality comparison needs a tolerance.

## Blueprint Vision
Read `dataingestion.md` Sections 4, 5 completely. The blueprint defines:
- **Pre-filter (Section 4):** Delta band (0.10 ≤ |δ| ≤ 0.90) and DTE band (7 ≤ DTE ≤ 90) applied **immediately on pull, before cleaning** — not interleaved with cleaning checks
- **Cleaning order (Section 5):** "Cheap structural rejects first, cross-strike checks last" — monotonicity is the most expensive check and must run LAST
- **Subpenny check:** tolerance-based, not exact float equality
- **Spread check:** handle `mid_price <= 0` gracefully
- **OI check:** `open_interest` column must always exist
- **Intrinsic check:** `spot_close` must not be NaN

## Core Objective
Fix the cleaning pipeline to match blueprint order and robustness:

1. **Move pre-filter before cleaning** — DTE and delta bands applied immediately after fetch (not inside `clean_option_chain`)
2. **Reorder checks** — move monotonicity to LAST position (after OI and intrinsic)
3. **Subpenny precision fix** — use tolerance `np.abs(...) < SUBPENNY_EPS` instead of exact equality
4. **Spread mid_price <= 0 guard** — reject rows with non-positive mid price instead of silently passing
5. **OI column guard** — ensure `open_interest` exists before filtering
6. **NaN spot_close** — reject rows with NaN spot before intrinsic check

## Errors to Fix

### High #15: Pre-Filter Delta/DTE Applied in Cleaning, Not at Fetch Time
**File:** `cleaning.py:29-50`  
**Expected:** DTE/delta filters applied immediately after fetch (in orchestrator), before `clean_option_chain()`.  
**Actual:** Applied inside `clean_option_chain()` at the top — fine functionally, but blueprint wants them at selection time to reduce data in memory.  
**Fix:** Move DTE and delta pre-filters to `orchestrator.py` right after the `asyncio.gather()` fetch and before `clean_option_chain()`. Keep them in cleaning as a safety net or remove from cleaning.

### High #16: Cleaning Order Violates "Cheap First" Principle
**File:** `cleaning.py:29-186`  
**Expected order:** DTE → Delta → No-quote → Crossed → Subpenny → Spread → Zero-IV → OI → Intrinsic → Monotonicity (cheapest first, monotonicity last).  
**Actual order:** DTE → Delta → No-quote → Crossed → Subpenny → Spread → Belly flag → Zero-IV → **Monotonicity** → OI → Intrinsic (monotonicity runs before OI and intrinsic — expensive cross-strike check before cheap scalar checks).  
**Fix:** Move monotonicity block to after OI and intrinsic checks.

### High #17: Subpenny Check Uses Float Equality — Precision Bug
**File:** `cleaning.py:91-92`  
**Expected:** `np.abs(np.round(x * 100) - x * 100) < 1e-8`.  
**Actual:** `np.round(result["bid"].values * 100) == result["bid"].values * 100` — exact float equality.  
**Fix:** Use tolerance: `np.abs(np.round(bid * 100) - bid * 100) > cfg.SUBPENNY_EPS`.

### High #18: Spread Check Division by Zero When `mid_price <= 0`
**File:** `cleaning.py:57-61`  
**Expected:** `rel_spread > 0.25` check rejects wide spreads; `mid_price <= 0` is also rejected.  
**Actual:** When `mid_price <= 0`, `rel_spread` set to 0.0 via `np.where`, so spread hard check `rel_spread > 0.25` is False — bad rows pass silently.  
**Fix:** Add explicit `mid_price <= 0` → reject as `BAD_MID` before spread calculation.

### High #19: OI Check Uses `open_interest` Column That May Not Exist
**File:** `cleaning.py:158`  
**Expected:** `open_interest` column always present.  
**Actual:** Relies on fetcher initializing it as NA (fragile). If join order changes, column may be missing.  
**Fix:** Add defensive: `if "open_interest" not in result.columns: result["open_interest"] = 0` or similar fallback.

### High #20: Intrinsic Check Uses `spot_close` Which May Be NaN
**File:** `cleaning.py:174-178`  
**Expected:** NaN spot_close rows are caught before intrinsic calculation.  
**Actual:** `spot_close` NaN → intrinsic NaN → comparison `mid_price < intrinsic` is False → violations not caught.  
**Fix:** Add NaN spot check: `spot_close.notna()`; rows with NaN spot get rejected before intrinsic check.

## Invariants (MUST HOLD)
1. **Test backward compatibility** — all existing cleaning tests pass; update assertions for new ordering
2. **Same filter logic** — no change to what constitutes a violation, only when/where it's checked
3. **SUBPENNY_EPS** matches intent — use `config.py` constant

## Success Criteria
- Pre-filters (DTE, delta) applied in orchestrator before `clean_option_chain()` call
- Cleaning check order: DTE → Delta → No-quote → Crossed → Subpenny → Spread → Zero-IV → OI → Intrinsic → Monotonicity
- No float equality in subpenny — uses `np.abs(...) < ` threshold
- `mid_price <= 0` rejected before spread check
- `open_interest` column guaranteed present before OI check
- NaN `spot_close` caught before intrinsic check
- All existing cleaning tests pass

## Short Specialized Verification
```python
# 1) Check order in cleaning.py
src = open("dataingestion/cleaning.py").read()
order_markers = ["DTE_BAND", "DELTA_BAND", "NO_QUOTE", "CROSSED", "SUBPENNY", "SPREAD_HARD", "BAD_MID", "ZERO_IV", "LOW_OI", "INTRINSIC", "MONOTONICITY"]
positions = [src.find(m) for m in order_markers]
assert all(positions[i] < positions[i+1] for i in range(len(positions)-1)), "Order violation"

# 2) Subpenny uses EPS
assert "SUBPENNY_EPS" in src or "1e-8" in src

# 3) BAD_MID exists
assert "BAD_MID" in src

# 4) Full cleaning test suite
import subprocess
subprocess.run(["python", "-m", "pytest", "dataingestion/test_cleaning.py", "-v", "--tb=short"], check=True)
```

## Files to Modify
- `dataingestion/cleaning.py` (reorder checks, precision fix, edge case guards)
- `dataingestion/orchestrator.py` (move pre-filter application)
- `dataingestion/test_cleaning.py` (update test expectations if needed for reordering)