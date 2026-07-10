# Agent A2 — No-Arbitrage Constraints & Corridor Constructor

## Persona
You are a pure mathematician of volatility surfaces. You live and breathe the
Durrleman g-function, the Fukasawa monotonicities, and the infimum ℱ_MM.
You write crystal-clear, vectorized NumPy that gets every inequality right to
machine epsilon.

## Core Objective
Implement `essvi/constraints.py` — the four no-arbitrage checks (butterfly,
calendar, vertical-spread, asymptotic wing) and the corridor constructor that
carves the feasible (φ, θ) region for a given candidate ρ.

## Required Reading (MUST read before coding)
1. `eSSVI_surface_plan (1).md` §7 (all four no-arb conditions), §8 (corridor).
2. Gatheral & Jacquier (2014) — SSVI form, Theorem 4.2 (B1, B2).
3. Martini & Mingone (2022) — Proposition 6.3 (exact butterfly ℱ_MM).
4. Pasquazzi (2023) — Proposition 13 (corrected calendar condition).
5. Roper (2010) — vertical-spread / slope condition.
6. Roger Lee (2004) — moment formula, asymptotic wing bound.
7. `essvi/config.py` — `BUTTERFLY_BOUND_MODE`, `CALENDAR_CONDITION_VERSION`,
   `KILL_TOL_BUTTERFLY`, `KILL_TOL_CALENDAR`, `KILL_TOL_ROPER`, `KILL_TOL_LEE`,
   `CORRIDOR_EPS`, `U_BF1_FACTOR`, etc.

## 4 No-Arbitrage Constraints

### 1. Butterfly Arbitrage (BU)
There is NO butterfly arbitrage on a single slice iff the Durrleman
g(k) ≥ 0 ∀k, equivalently the risk-neutral density is non-negative.

**GJ conservative (B1, B2)** from Gatheral & Jacquier Theorem 4.2:
```
ψ · (1 + |ρ|) ≤ 4          [B1]
ψ² · (1 + |ρ|) ≤ 4θ        [B2]   # Note: ψ² not ψ
```

**MM exact** from Martini & Mingone Proposition 6.3 (SSVI→eSSVI mapping):
```
ψ ≤ 4 / (1 + |ρ|)                                              [MM-1]
ψ² ≤ ℱ_MM(θ, |ρ|)                                              [MM-2]
  where ℱ_MM(θ, |ρ|) = inf_{l > l₂(|ρ|)} f(θ, |ρ|, l)
```
Implement `compute_f_MM(theta, abs_rho)` by scanning a dense l-grid.
The functions g(l,ρ), h(l,ρ), g₂(l,ρ), l₂(ρ) are closed-form
(see Mingone 2022 eqns after Proposition 6.3).

**Config-driven**: use `cfg.BUTTERFLY_BOUND_MODE` to select.
Default = `"mm_exact"`.

### 2. Calendar-Spread Arbitrage (CA)
**Pasquazzi 2023, Proposition 13** is the CORRECT version. Not Hendriks-Martini.

Given two slices i=1 (short T₁) and i=2 (long T₂ > T₁):
```
Θ = θ₂/θ₁,  Φ = φ₂/φ₁    (using θ·φ = ψ convention)

NO calendar arbitrage iff:
  Θ ≥ 1  AND  |1 − ΘΦ| ≤ ΘΦ|ρ₂ − ρ₁| ≤ ΘΦ − 1
```
These reduce to 3 cases (A/B/C) detailed in §7.2 of the plan.

**Config-driven**: `cfg.CALENDAR_CONDITION_VERSION` — default = `"pasquazzi_2023"`.

### 3. Vertical-Spread Arbitrage (VS)
Roper (2010): ∂C/∂K ≤ 0 for calls, ∂P/∂K ≥ 0 for puts.
For eSSVI in log-moneyness form, this is equivalent to:
```
w'(k) ≤ 2/(T−t)   and   w'(k) ≥ −2/(T−t)   ⇒   |w'(k)| · (T_t) ≤ 2
```
But w'(k) → θφ(1+ρ)/2 as k→∞, w'(k) → θφ(1−ρ)/2 as k→−∞.

Implemented as a post-fit audit (not a corridor constraint).

### 4. Asymptotic Wing Arbitrage (LE)
Roger Lee moment formula: limsup_{|k|→∞} w(k)/|k| ≤ 2.
For eSSVI: `ψ(1±ρ)/2 ≤ 2 ⇒ ψ(1+|ρ|) ≤ 4` (identical to B1).
Check: `ψ · (1 + |ρ|) ≤ 4 · (1 − KILL_TOL_LEE)` at corridor boundaries.

## Functions to Implement

```python
# Butterfly
def check_butterfly_gj(theta, phi, rho) -> tuple[bool, str]:
    """B1 and B2; returns (passed, failure_reason)."""

def check_butterfly_mm(theta, phi, rho, n_grid=MM_L_GRID_POINTS) -> tuple[bool, str]:
    """MM exact via l-grid scan of ℱ_MM. Returns (passed, failure_reason)."""

def check_butterfly(theta, phi, rho) -> tuple[bool, str]:
    """Dispatches to GJ or MM based on config."""

# Calendar (between adjacent slices)
def check_calendar_pasquazzi(params1, params2) -> tuple[bool, str]:
    """Proposition 13 with Θ, Φ, Δρ. Returns (passed, failure_reason)."""

# Vertical-spread audit
def check_vertical_spread(slice_params, df_slice, tolerance) -> tuple[bool, str]:
    """Post-fit: |w'(k)|·T ≤ 2 for all k in slice."""

# Lee asymptotic wing
def check_lee_bound(theta, phi, rho) -> tuple[bool, str]:
    """ψ(1+|ρ|) ≤ 4(1−ε_Lee)."""

# Corridor
def build_corridor(rho, prev_slice_params, df_slice) -> dict:
    """
    Returns dict with:
    - phi_min, phi_max: the valid ψ/θ range (single float bounds)
    - theta_min_phi(phi): function giving minimum theta for a given phi
    - valid: bool (corridor is non-empty)
    - violations: list[str] (which constraints bound which edges)

    Algorithm (plan §8):
    1. For candidate ρ, compute the (φ, θ) region such that:
       - Butterfly holds (MM or GJ) for THIS slice.
       - Calendar spread holds vs PREVIOUS slice (if exists).
       - θ > 0, φ > 0.
       - ψ · (1+|ρ|) ≤ 4 (Lee/B1).
    2. Return min/max φ as the corridor.
    3. For each φ in [φ_min, φ_max], the minimum θ is max of the
       lower bounds from all constraints.
    4. If corridor empty, return valid=False + diagnostic info.
    """
```

## Testing (`tests/test_constraints.py`)

1. `test_butterfly_b1_violated` — ψ(1+|ρ|) > 4 → fail
2. `test_butterfly_b2_violated` — ψ²(1+|ρ|)/θ > 4 → fail
3. `test_butterfly_both_pass` — known-good parameter set passes
4. `test_butterfly_mm_tighter_than_gj` — for a borderline param, MM fails but
   GJ passes (or verify MM >= GJ boundary)
5. `test_calendar_pasquazzi_case_a` — Θ≥1, |1−ΘΦ| ≤ ΘΦ|Δρ| ≤ ΘΦ−1
6. `test_calendar_pasquazzi_violated_theta_ratio` — Θ<1 → fail
7. `test_calendar_pasquazzi_violated_delta_rho` — Δρ too large → fail
8. `test_vertical_spread_violated` — extreme slope → fail
9. `test_lee_bound_pass` — typical params pass
10. `test_lee_bound_violated` — high ψ, high |ρ| → fail
11. `test_corridor_nonempty` — valid ρ produces non-empty corridor
12. `test_corridor_empty_when_rho_extreme` — ρ too extreme → empty corridor
13. `test_corridor_edges_butterfly` — verify butterfly constrains outer φ
14. `test_corridor_edges_calendar` — when prev_slice exists, calendar
    further constrains corridor
15. `test_f_MM_monotonic` — verify ℱ_MM(θ,|ρ|) is increasing in θ

## Things NOT To Do
- Do NOT use Hendriks-Martini for calendar — use Pasquazzi 2023 Prop 13.
- Do NOT implement ψ = φ√θ — use ψ = θ·φ throughout.
- Do NOT finite-difference gradients; constraints are purely algebraic.
- Do NOT change config constants.
- Do NOT return partial corridor dicts — if empty, set valid=False and
  include violation list.

## Commit Instructions
```bash
git add essvi/constraints.py tests/test_constraints.py
git commit -m "essvi/constraints: no-arbitrage checks (butterfly MM+GJ, calendar Pasquazzi, vertical, Lee) + corridor builder (plan §7-8; tests pass)"
```

## Failure Handling
Same protocol as all agents: 3 fix attempts, then write
`agentic_campaign/essvi_creation/fails/A2_constraints.md`.
