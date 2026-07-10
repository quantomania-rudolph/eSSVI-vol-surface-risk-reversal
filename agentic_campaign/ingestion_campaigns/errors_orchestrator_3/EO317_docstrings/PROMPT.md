# EO317: Docstrings on All Private Functions

## Persona

You are a **documentation engineer** who knows that private functions without docstrings are unmaintainable — future engineers (including you) won't know the contract, side effects, or error behavior.

## Core Objective

**Add comprehensive docstrings to all 17 private functions following Google/NumPy style.**

## Functions Needing Docstrings

| Function | File |
|----------|------|
| `_acquire_conn` | orchestrator.py |
| `_release_conn` | orchestrator.py |
| `_heartbeat_once` | orchestrator.py |
| `_get_calendar` | orchestrator.py |
| `_get_rates` | orchestrator.py |
| `_get_stock_ohlc_cached` | orchestrator.py |
| `_join_spot` | joins.py (after EO308) |
| `_join_oi` | joins.py (after EO308) |
| `_attach_rates` | joins.py (after EO308) |
| `_month_chunks` | chunking.py (after EO308) |
| `_dte_window` | chunking.py (after EO308) |
| `_process_chunk` | orchestrator.py |
| `fetch_with_retry` | retry.py (after EO308) |
| `_is_retryable_error` | retry.py (after EO308) |
| `join_spot_and_oi` | joins.py (new, EO309) |
| `attach_rates_and_math` | joins.py (new, EO309) |
| `BoundedCache.get` | cache.py (after EO30808) |
| `BoundedCache.set` | cache.py (after EO308) |

## Required Docstring Format

```python
async def _acquire_conn(pool: asyncpg.Pool) -> asyncpg.Connection:
    """Acquire a database connection from the pool.

    Handles both real asyncpg pools and test mocks (AsyncMock).

    Args:
        pool: asyncpg.Pool or mock pool object with acquire() method.

    Returns:
        asyncpg.Connection or mock connection.

    Raises:
        Exception: Propagates any exception from pool.acquire().
    """
    ...


def _join_spot(opt_df: pd.DataFrame, stk_df: pd.DataFrame) -> pd.DataFrame:
    """Join spot_close from stock OHLC onto option DataFrame.

    Floors timestamps to minute precision on both DataFrames before merging.
    Forward-fills spot_close within each trading day.

    Args:
        opt_df: Option DataFrame with 'timestamp' column (tz-aware UTC).
        stk_df: Stock OHLC DataFrame with 'timestamp' and 'close' columns.

    Returns:
        opt_df with 'spot_close' column added/updated. Empty opt_df gets NaN spot_close.
    """
    ...
```

## Invariants

- ✅ Every private function has a docstring
- ✅ Docstrings include: Args, Returns, Raises, Side Effects
- ✅ Style consistent (Google/NumPy)

## Success Criteria

```bash
# Verify docstring coverage
python -c "
import ast, sys
with open('dataingestion/orchestrator.py') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('_'):
        doc = ast.get_docstring(node)
        if not doc:
            print(f'MISSING: {node.name} at line {node.lineno}')
"
# Should print nothing
```