# thermo_3 Campaign — Thermo-Nuclear Fix Execution

**Location:** `agentic_campaign/essvi_creation/fails/thermo_3/`  
**Source:** `thermal_error_3.md` (27 findings → 10 P0/P1 fixes)  
**Status:** Ready for agent execution

---

## Campaign Overview

This campaign fixes **5 P0 (blocking)** and **5 P1 (correctness)** issues in the eSSVI engine that invalidate calibration accuracy. All 158 existing tests pass but validate **wrong math** — they were written against the buggy implementation.

---

## Agent Execution Plan

### Phase 1: Foundation (Sequential — Run in Order)
| Order | Agent | File | Issues | Est. Time |
|-------|-------|------|--------|-----------|
| 1 | **T3_A8_config** | `essvi/config.py` | P0-5, P2-1, P2-2, P2-4, P2-5 | 15 min |
| 2 | **T3_A1_loader** | `essvi/loader.py` | P0-3 | 30 min |
| 3 | **T3_A2_anchor** | `essvi/anchor.py` | P0-1 | 30 min |

### Phase 2: Core Engine (Parallel Groups)

**Group A (after Phase 1 done):**
| Agent | File | Issues | Est. Time |
|-------|------|--------|-----------|
| **T3_A3_objective** | `essvi/objective.py` | P0-2 | 20 min |
| **T3_A4_constraints** | `essvi/constraints.py` | P0-4, P1-1, P1-5 | 45 min |

**Group B (after Group A done):**
| Agent | File | Issues | Est. Time |
|-------|------|--------|-----------|
| **T3_A5_solver** | `essvi/solver.py` | P0-1 (call site), P0-5 | 20 min |
| **T3_A6_sequential** | `essvi/sequential.py` | P1-2 | 20 min |

### Phase 3: Surface & Tests (Parallel, after Phase 2)

**Group C:**
| Agent | File | Issues | Est. Time |
|-------|------|--------|-----------|
| **T3_A7_surface** | `essvi/surface.py` | P1-3, P1-4 | 30 min |
| **T3_A9_tests** | `tests/test_*.py` | Rewrite all tests | 45 min |

---

## Quick Start Commands

```bash
cd c:\Users\Rudol\Desktop\ThetaData_greeks\agentic_campaign\essvi_creation\fails\thermo_3

# Phase 1 - Sequential
python -m agent T3_A8_config    # Must complete first
python -m agent T3_A1_loader    # Depends on config
python -m agent T3_A2_anchor    # Depends on config

# Phase 2 - Group A (Parallel - two terminals)
# Terminal 1:
python -m agent T3_A3_objective
# Terminal 2:
python -m agent T3_A4_constraints

# Phase 2 - Group B (Parallel - after Group A)
# Terminal 1:
python -m agent T3_A5_solver
# Terminal 2:
python -m agent T3_A6_sequential

# Phase 3 - Group C (Parallel - after Phase 2)
# Terminal 1:
python -m agent T3_A7_surface
# Terminal 2:
python -m agent T3_A9_tests

# Final Validation
pytest essvi/ -v --tb=short -q
python -c "from essvi.config import validate; validate()"
python -c "from essvi.runtime import calibrate_minute; print('OK')"
```

---

## Issue Mapping

| Issue | Description | Fixed By |
|-------|-------------|----------|
| **P0-1** | Anchor inversion: `compute_theta_star` computes θ* from θ | T3_A2_anchor + T3_A5_solver |
| **P0-2** | Objective weights inverted: `1/vega²` downweights ATM | T3_A3_objective |
| **P0-3** | Loader expects 28 cols, DB has 19 (9 computed) | T3_A1_loader |
| **P0-4** | Pasquazzi Case A missing (Θ≈1, ρ₁≠ρ₂ → infeasible) | T3_A4_constraints |
| **P0-5** | Rho grid asymmetric: [-0.99, 0.90] | T3_A8_config |
| **P1-1** | Corridor returns single interval, not all | T3_A4_constraints |
| **P1-2** | No pre-loop C1 check for θ monotonicity | T3_A6_sequential |
| **P1-3** | Tail extrapolation missing slope cap | T3_A7_surface |
| **P1-4** | Long θ extrapolation uses wrong slope | T3_A7_surface |
| **P1-5** | MM butterfly table not precomputed (500× slow) | T3_A4_constraints |

---

## Key Mathematical Fixes

### Anchor (P0-1)
```python
# OLD (WRONG): θ* from θ
theta_star = 2*w_star / (1 + ρφk* + sqrt(...))

# NEW (CORRECT): θ from (ψ, ρ, θ*, k*)
θ = θ* - ρψk* + ψ²k*²(1-ρ²)/(4θ*)  # Exact closed form, Corbetta 2019
```

### Objective Weights (P0-2)
```python
# OLD (WRONG): inverse weights
weights = 1.0 / vega**2

# NEW (CORRECT): variance-space vega²
σ = sqrt(w/T)
ν_var = ν_vol / (2 * σ * sqrt(T)) = ν_vol / (2 * sqrt(w*T))
weights = ν_var**2
```

### Pasquazzi Case A (P0-4)
```python
# Θ = θ₂/θ₁ ≈ 1
if abs(theta_ratio - 1.0) <= 1e-4:
    # Feasible ONLY if:
    # (i) ρ₁ = ρ₂ = 0 AND Φ ≥ 1
    # (ii) ρ₁ = ρ₂ AND Φ = 1
    # ELSE INFEASIBLE
```

---

## Agent Files

```
thermo_3/
├── campaign.md           # This file
├── agents/
│   ├── T3_A8_config.md       # Config fixes (RUN FIRST)
│   ├── T3_A1_loader.md       # Loader DB contract
│   ├── T3_A2_anchor.md       # Anchor inversion
│   ├── T3_A3_objective.md    # Objective weights
│   ├── T3_A4_constraints.md  # Pasquazzi + corridor + MM table
│   ├── T3_A5_solver.md       # Solver wire-up
│   ├── T3_A6_sequential.md   # Pre-loop C1 check
│   ├── T3_A7_surface.md      # Tail cap + long extrapolation
│   └── T3_A9_tests.md        # Test rewrite
└── fails/                  # Failure logs (if any)
```

---

## Success Criteria

- [ ] All 10 P0/P1 issues fixed
- [ ] `pytest essvi/ -v` → **ALL GREEN** (no xfail, no skip)
- [ ] `python -c "from essvi.config import validate; validate()"` → no error
- [ ] `python -c "from essvi.runtime import calibrate_minute; print('OK')"` → OK
- [ ] No test validates buggy math (all rewritten by T3_A9_tests)

---

## Failure Protocol

If any agent fails after 3 fix attempts:
1. Write `fails/T3_A{N}_<test>.md` with error details
2. Stop that agent
3. Continue independent agents if possible
4. Signal for human review

---

## Research References (For Agents)

| Paper | Role |
|-------|------|
| Pasquazzi (2023) Prop 13 | Calendar Case A |
| Martini & Mingone (2022) Prop 6.3 | ℱ_MM(θ,|ρ|) table |
| Corbetta et al. (2019) | Anchor formula, sequential algo |
| Gatheral & Jacquier (2014) | SSVI form, Lee bound |
| Lee (2004) | Wing bound w(k)/|k| ≤ 2 |

---

**Campaign Owner:** Thermo-Nuclear Review Follow-up  
**Created:** 2026-07-09  
**Status:** Ready for Execution