# Agent A10 Prompt — Integration Lead: Cross-Agent Consistency Review & Final Plan Update

## Role: Integration Lead (Runs LAST, after A1–A9 complete)

## Mission
Merge all agent changes into a single consistent `eSSVI_surface_plan (1).md`, verify cross-references, update section numbers, and produce the final deliverable.

## Required Reading
- All agent prompts A1–A9
- Original `eSSVI_surface_plan (1).md`
- Updated `config.py` from Agent A9

## What to Do

### 1. Wait for A1–A9 Completion
Do not start until all 9 agents report completion. Each agent modifies the plan document in place with `<<A#_CHANGE>>` markers.

### 2. Merge Process

#### Step 1: Collect All Changes
Read the plan document and extract all `<<A#_CHANGE>>` sections. Create a merge map:

| Section | Modified By | Description |
|---------|-------------|-------------|
| §4.1 (new) | A7 | Short-maturity handling |
| §5 | A3, A7 | Exact anchor solve, fallback |
| §7.1 | A2 | MM butterfly bounds |
| §7.1.1 (new) | A2 | MM conditions detail |
| §7.2 | A1 | Pasquazzi calendar |
| §8 | A4 | Corridor with exact θ(ψ) |
| §10 | A5 | Objective weighting modes |
| §11 | A8 | Warm-start, regularization |
| §12 | A1, A8 | Audit with Pasquazzi, kill switch |
| §14 | A1, A4, A7 | Degeneracy handling |
| §15 | A6 | Interpolation/extrapolation |
| §16 | A8 | In-calibration calendar check |
| §17 | A9 | Config.py reference |
| §19 | A1, A2, A5, A6, A7 | Resolved items |

#### Step 2: Resolve Conflicts
Key conflict zones:
- **§8 Corridor**: A1 (Pasquazzi L_cal), A2 (MM U_bf), A3 (exact θ), A4 (ψ-dependent bounds) — ALL modify this. Must combine into single coherent algorithm.
- **§14 Degeneracy**: A1 (Θ=1 strictness), A4 (order of fallbacks), A7 (projection vs constrained) — unify.
- **§11/§16**: A8 (warm-start, in-cal check) vs original loop.

#### Step 3: Rewrite Sections Coherently
For each conflict zone, write a **single unified section** incorporating all fixes. Do not keep multiple versions.

#### Step 4: Update Cross-References
- Section numbers may shift (new §4.1, §7.1.1, etc.)
- Update all internal references (e.g., "as defined in §8" → correct section)
- Update config parameter names to match A9's `config.py` exactly

#### Step 5: Update §19 Open Items
Mark each item as:
- **RESOLVED** — with config parameter reference
- **DEFERRED** — with reason and future ticket
- **KNOWN LIMITATION** — documented

### 3. Final Validation Checklist

```
[ ] All 19 original sections present (some expanded)
[ ] New sections: 4.1, 7.1.1, 14.1, 15.1–15.5
[ ] §7.2 uses Pasquazzi (A1), not HM
[ ] §7.1.1 documents MM exact bounds (A2)
[ ] §5 uses exact θ closed-form (A3)
[ ] §8 corridor uses ψ-dependent θ(ψ) bounds (A3, A4)
[ ] §8 L_cal uses Pasquazzi Case B with θ-monotonicity (A1, A4)
[ ] §10 objective has VEGA_WEIGHT_MODE with var_vega2 default (A5)
[ ] §11 warm-start clips to corridor (A8)
[ ] §11 term-structure vs temporal regularization distinct (A8)
[ ] §12 kill switch uses KILL_TOL = 1e-10 (A8)
[ ] §12 audit uses Pasquazzi check (A1)
[ ] §14 degeneracy: calendar level checked FIRST (A4)
[ ] §14 anchor relocation + projection documented (A7)
[ ] §15 extrapolation: ψ FLAT long-term (A6)
[ ] §15 tail cap algorithm specified (A6)
[ ] §16 in-calibration calendar check added (A8)
[ ] §17 references config.py with all params (A9)
[ ] §19 all items resolved/deferred
[ ] Config parameter names match config.py exactly
[ ] No `<<A#_CHANGE>>` markers remain (replace with clean text)
```

### 4. Produce Deliverables

1. **Final `eSSVI_surface_plan (1).md`** — clean, merged, validated
2. **Final `config.py`** — from A9, verified against plan
3. **Campaign Summary** — this file updated with completion status

### 5. Integration Testing (Mental/Documented)

Verify the complete flow works:
```
MINUTE τ:
  For each slice t:
    1. Get warm-start (ρ_center, ψ_seed) clipped to current corridor (A8)
    2. Coarse ρ-grid search with term-structure penalty (A8, §11)
    3. For each ρ_t:
       a. Compute exact θ_t(ψ) (A3)
       b. Compute L_ψ(ψ) with Pasquazzi + θ-mono (A1, A4)
       c. Compute U_ψ(ψ) with MM/GJ + θ(ψ) (A2, A4)
       d. Find feasible ψ interval via root-find (A4)
       e. Brent on inner objective (var_vega2 default) (A5)
    4. Refine ρ around best (A8, §4)
    5. Lock best (ρ, ψ, θ) (A8)
    6. IMMEDIATE calendar check vs t-1 (Pasquazzi) (A1, A8)
    7. If fail: fallback in-calibration (A8, §16)
  8. All slices locked → full audit (A1, A2, A8)
  9. Interpolate/extrapolate surface (A6)
  10. Temporal regularization for next minute (A8)
```

## Output Format

Update `eSSVI_surface_plan (1).md` **in place** — final clean version. No change markers.

Create `INTEGRATION_LOG.md` documenting:
- Each conflict resolved
- Section number changes
- Any remaining open questions

## Validation

- [ ] Plan document passes all checklist items
- [ ] Config.py imports and validates
- [ ] Cross-references consistent
- [ ] No duplicate/conflicting definitions
- [ ] All paper citations accurate (Pasquazzi, Corbetta, MM, Mingone, GJ, Lee, Roper)