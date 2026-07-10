# eSSVI Surface Creation — Agentic Campaign

**Campaign:** Build the complete eSSVI calibration engine, tested end-to-end,
on top of the dataingestion panel (`amd_surface_min` hypertable).

**Context docs (read by all agents):**
- `dataingestion.md` — data contract, endpoint map, column schemas
- `eSSVI_surface_plan (1).md` — full calibration blueprint
- `essvi/config.py` — ALL engine configuration, single source of truth
- `dataingestion/COLUMNS.md` — column contract between ingestion & calibration
- `dataingestion/joins.py` — columns produced (session_phase, forward_price, business_t, vega, parity_skew, anchor_k_star, anchor_theta_star, log_moneyness, slice_strike_count)

**Research papers (agents read as needed):**
1. Hendriks & Martini (2019) — *The Extended SSVI Volatility Surface* — SSRN 2971502. Maturity-dependent ρ; original (now-corrected) calendar conditions.
2. Pasquazzi (2023) — *Correction to HM Proposition 3.1* — Proposition 13: necessary-and-sufficient calendar conditions (Case A/B/C). **Primary calendar reference.**
3. Corbetta, Cohort, Laachir & Martini (2019) — *Robust calibration and arbitrage-free interpolation of SSVI slices* — arXiv 1804.04924. Anchor reparametrization, sequential slice-by-slice, linear interpolation preserves no-arb. **Primary algorithm reference.**
4. Gatheral & Jacquier (2014) — *Arbitrage-free SVI volatility surfaces* — arXiv 1204.0646. SSVI form; Durrleman g(k)≥0; closed-form butterfly B1,B2.
5. Martini & Mingone (2022) — *No Arbitrage SVI* — SIAM J.Fin.Math 13(1):227-261. Proposition 6.3: exact necessary-and-sufficient butterfly boundary ℱ_MM(θ,|ρ|).
6. Mingone (2022) — *No arbitrage global parametrization for the eSSVI surface* — arXiv 2204.00312. Global (non-sequential) upgrade; reparametrization as product of intervals.
7. Roper (2010) — *Arbitrage-Free Implied Volatility Surfaces*. Slope / vertical-spread condition.
8. Roger Lee (2004) — *The Moment Formula for Implied Volatility at Extreme Strikes*. Wing bound limsup w(k)/|k| ≤ 2.

## eSSVI Slice Formula (locked convention)

```
w(k, T_t) = θ_t/2 · (1 + ρ_t φ_t k + sqrt((φ_t k + ρ_t)² + (1 − ρ_t²)))

ψ_t = θ_t · φ_t  — THE convention throughout. Do not use ψ = φ√θ.
```

Closed-form derivatives (never finite-difference):
```
u   = φ_t k + ρ_t
D   = u² + (1 − ρ_t²)
w   = θ_t/2 · (1 + ρ_t φ_t k + √D)
w'  = (θ_t φ_t / 2) · (ρ_t + u/√D)
w'' = (θ_t φ_t² (1 − ρ_t²)) / (2 · D^{3/2})      # always > 0
```

## Agent Run Order

Each agent produces one or more files + tests. They run **sequentially** in
this order because later agents import from earlier ones. Tests run after
each agent.

| # | Agent | File(s) produced | Depends on | Plan § |
|---|-------|-----------------|------------|--------|
| A1 | loader | `essvi/loader.py` + `tests/test_loader.py` | config, plan §3 | §3, §3.1-§3.3 |
| A2 | constraints | `essvi/constraints.py` + `tests/test_constraints.py` | config, plan §7-8 | §7, §8 |
| A3 | anchor | `essvi/anchor.py` + `tests/test_anchor.py` | config, plan §5 | §5 |
| A4 | objective | `essvi/objective.py` + `tests/test_objective.py` | config, plan §10, §13 | §10, §13 |
| A5 | regularize | `essvi/regularize.py` + `tests/test_regularize.py` | config, plan §11 | §11 |
| A6 | solver | `essvi/solver.py` + `tests/test_solver.py` | constraints, anchor, objective, regularize, plan §4, §9, §12 | §4, §9, §11-B, §12 |
| A7 | sequential | `essvi/sequential.py` + `tests/test_sequential.py` | solver, constraints, anchor, plan §4 | §4, §14 (degeneracy) |
| A8 | surface | `essvi/surface.py` + `tests/test_surface.py` | sequential, plan §15 | §15 |
| A9 | audit | `essvi/audit.py` + `tests/test_audit.py` | constraints, surface, plan §12 | §12 |
| A10 | runtime | `essvi/runtime.py` + `tests/test_runtime.py` | surface, audit, loader, plan §14, §16 | §14, §16 |
| A11 | persistence | `essvi/persistence.py` | runtime, config, plan §17 "Params output table" | §17 |
| A12 | tests | Run `pytest essvi/ -v` on ALL tests, fix any failures | all agents | all |

## Coverage Map

| Plan Section | Agent(s) |
|---|---|
| §0 Conventions | All (locked in this campaign.md) |
| §1 What the engine does | A7 (sequential), A10 (runtime) |
| §2 Execution-reality traps | A1 (same-minute join), A4 (spread/vega) |
| §3 Data contract | A1 (loader) |
| §3.1 OI condition | A1 |
| §3.2 Belly/wing partition | A1, A4 |
| §3.3 OTM selection + put-call parity | A1 |
| §4 Sequential master algorithm | A7 (sequential) |
| §4.1 Short-maturity slice handling | A7 (degeneracy), A3 (anchor fallback) |
| §5 Anchor extraction | A3 |
| §6 ψ convention | All (locked in campaign.md) |
| §7 No-arb constraints (4 types) | A2 (constraints), A9 (audit) |
| §7.1 Butterfly (GJ + MM) | A2 |
| §7.2 Calendar (Pasquazzi) | A2 |
| §7.3 Vertical-spread (Roper) | A2, A9 |
| §7.4 Asymptotic wing (Lee) | A2, A9 |
| §8 Corridor construction | A2 |
| §9 ρ grid & outer search | A6 (solver), A7 (sequential) |
| §10 Objective function | A4 |
| §11 Regularization (2 axes) | A5, A6 |
| §12 Clamp + audit + kill switch | A6 (clamp), A9 (audit) |
| §13 Belly-center emphasis | A4 |
| §14 Re-anchoring + overnight + no-trade | A10, A5 |
| §15 Interpolation/extrapolation | A8 |
| §16 Minute-level runtime loop | A10 |
| §17 Architectural map (not built — just file structure) | All |
| §18 Failure modes (per-module handling) | All |
| §19 Open items (resolved in config) | All |
| §20 References | All |

## Commit Cadence

Every agent **commits only after all its tests pass**:
```bash
git add <agent_module>.py tests/test_<agent>.py
git commit -m "<module>: <summary> (plan §X; tests pass)"
```

If a pre-existing test breaks due to an import change, fix the import,
re-run `pytest essvi/tests/test_<agent>.py -x -q`, and commit.

## Failure Protocol

If any agent's tests fail:
1. Read the failure output carefully.
2. Fix the code — do NOT delete the test or weaken the assertion.
3. Re-run the test until it passes.
4. If you cannot fix it after 3 attempts, write a `fails.md` file in
   `agentic_campaign/essvi_creation/fails/` recording:
   - Agent name, test name, failure output, what you tried, why you're stuck.
5. Then signal that A{N} needs human intervention and move on.

## Final Integration Check (after A12)

```bash
pytest essvi/ -v --tb=short -q
python -c "from essvi.config import validate; validate()"
python -c "from essvi.runtime import calibrate_minute; print('OK')"
```
