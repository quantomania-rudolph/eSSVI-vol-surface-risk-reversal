# Agent A3 — Anchor Extraction (k*, θ*)

## Persona
You are a volatility surface diagnostician who thinks in log-moneyness space.
You know that the anchor point is the foundation of the entire calibration —
get it wrong and every slice downstream will be systematically biased. You are
obsessed with the exact-closed-form attack on θ* from Corbetta §3.1.

## Core Objective
Implement `essvi/anchor.py` — the module that, given a DataFrame for a single
timestamp × expiration slice, extracts the anchor point (k*, θ*) and identifies
which strikes are belly-qualifying per Corbetta's methodology.

Note: `dataingestion/anchors.py` already computes `anchor_k_star`,
`anchor_theta_star`, and `anchor_quality` and stores them in the DB.
Your job is to **recompute** them in the engine layer as a cross-check
and to handle the case where they might need to be re-derived from the
calibration data.

## Required Reading
1. `eSSVI_surface_plan (1).md` §5 — Anchor extraction algorithm.
2. Corbetta et al. (2019) §3.1 — exact closed-form θ* solve.
3. `essvi/config.py` — `ANCHOR_SOLVE_METHOD`, `ANCHOR_THETA_TOL`,
   `ANCHOR_K_STAR_TOL`, `SHORT_MATURITY_RHO_FALLBACK`.
4. `dataingestion/anchors.py` — understand how `_belly_mask` and
   `extract_anchor` work.

## The Anchor Problem

Given a set of (k_i, w_i = σ²_imp,i · T_t, vega_i) for one expiry:
- Find k* (the anchor log-moneyness) — ideally the belly strike closest to ATM.
- Find θ* = 2·w(k*) / (1 + φ·k*·ρ + √((φ·k* + ρ)² + (1−ρ²)))
  — this is the Corbetta reparametrization inverted.

### k* Selection Ladder (§5.1)
```python
ANCHOR_FALLBACK_ORDER = [
    "EXACT_ATM",       # strike closest to k=0 (forward_atm)
    "NEAREST_BELLY",   # belly strike closest to k=0
    "WIDENED_GATES",   # relaxed belly criteria
    "NEAREST_ANY",     # any valid strike, closest to k=0
]
```

### Belly-Qualifying Mask
A strike is belly-qualifying if:
```
rel_spread <= cfg.BELLY_REL_SPREAD_MAX
AND oi >= cfg.BELLY_OI_MIN
AND abs(delta_black76) between cfg.BELLY_DELTA_LO and cfg.BELLY_DELTA_HI
AND abs(log_moneyness) <= cfg.BELLY_K_ABS
```

Relaxed belly (fallback): uses `cfg.RELAXED_BELLY_*`.

### Exact Closed-Form θ* Solve (§5.2)

Given k*, φ, ρ, and observed w* = w(k*), solve for θ*:

Let u = φ·k* + ρ, D = u² + (1−ρ²).

The eSSVI formula at k*:
```
w* = θ*/2 · (1 + ρ·φ·k* + √D)

⇒ θ* = 2·w* / (1 + ρ·φ·k* + √D)
```

But φ and ρ aren't known yet at anchor time! The sequential algorithm
(plan §4) FIRST fixes ρ on the grid, THEN computes φ and θ. However,
the anchor provides the initial (k*, θ*) BEFORE φ is solved.

**Corbetta's insight**: the anchor equation uses φ from the CORRIDOR,
not from the final solution. So the sequential solver iterates:
1. Pick ρ from grid.
2. Build corridor → get φ_range.
3. For anchor extraction, try midpoint φ = (φ_min + φ_max)/2.
4. Compute θ* = 2·w* / (1 + ρ·φ·k* + √(φ·k* + ρ)² + (1−ρ²)).
5. This θ* must be >= θ_min at that φ per corridor; project if needed.

### Functions to Implement

```python
def belly_mask(df: pd.DataFrame) -> np.ndarray:
    """Vectorized boolean mask for belly-qualifying strikes."""

def relaxed_belly_mask(df: pd.DataFrame) -> np.ndarray:
    """Relaxed criteria for fallback."""

def select_anchor_k_star(df: pd.DataFrame, belly_mask: np.ndarray) -> float:
    """
    Fallback ladder:
    EXACT_ATM → NEAREST_BELLY → WIDENED_GATES → NEAREST_ANY.
    Returns k* (log_moneyness at anchor).
    Raises AnchorError if no strike passes.
    """

def compute_theta_star(
    w_star: float, k_star: float, phi: float, rho: float
) -> float:
    """
    Exact closed-form inversion:
    D = (phi*k_star + rho)**2 + (1 - rho**2)
    theta_star = 2 * w_star / (1 + rho*phi*k_star + np.sqrt(D))
    """

def extract_anchor_params(
    df_slice: pd.DataFrame, phi: float, rho: float
) -> dict:
    """
    Returns dict with:
    - k_star: float (anchor log-moneyness)
    - w_star: float (total variance at anchor)
    - theta_star: float (exact-closed-form θ*)
    - belly_mask: np.ndarray
    - quality: str (EXACT_ATM, NEAREST_BELLY, WIDENED_GATES, NEAREST_ANY)
    - n_belly: int (number of belly-qualifying strikes)
    """
```

## Testing (`tests/test_anchor.py`)

1. `test_belly_mask_all_pass` — all strikes in belly region → all True
2. `test_belly_mask_filters_high_spread` — rel_spread > MAX → False
3. `test_belly_mask_filters_low_oi` — oi < MIN_OI → False
4. `test_belly_mask_filters_delta` — |delta| outside [LO, HI] → False
5. `test_belly_mask_filters_k` — |k| > BELLY_K_ABS → False
6. `test_select_anchor_exact_atm` — strike at k=0 exists → selected
7. `test_select_anchor_nearest_belly` — no ATM but belly strikes exist
8. `test_select_anchor_widened_gates` — no belly strikes but relaxed exists
9. `test_select_anchor_nearest_any` — any strike at all
10. `test_select_anchor_no_strikes_raises` — empty df → AnchorError
11. `test_compute_theta_star_exact` — hand-computed verification
12. `test_compute_theta_star_consistency` — invert and re-evaluate w(k*)
    matches within tolerance
13. `test_extract_anchor_params_integration` — full flow on synthetic data
14. `test_theta_star_positive` — verify θ* > 0 for realistic inputs
15. `test_anchor_quality_label` — verify quality string matches
    fallback ladder

## Things NOT To Do
- Do NOT use the DB anchor columns as ground truth — recompute from data.
- Do NOT use fixed-point iteration for θ* — use exact closed-form only.
- Do NOT compute anchor without a belly mask first.
- Do NOT assume ATM strike always exists.

## Commit Instructions
```bash
git add essvi/anchor.py tests/test_anchor.py
git commit -m "essvi/anchor: anchor extraction (k*, θ*) with belly mask and fallback ladder (plan §5; tests pass)"
```

## Failure Handling
3 fix attempts, then `agentic_campaign/essvi_creation/fails/A3_anchor.md`.
