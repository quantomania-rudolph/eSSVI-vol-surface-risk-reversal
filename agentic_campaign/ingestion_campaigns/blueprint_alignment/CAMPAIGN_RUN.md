# Blueprint Alignment Campaign — CAMPAIGN_RUN

**Mission:** Eliminate all 47 blueprint-alignment findings from the thermo-nuclear audit by fixing every gap between the `dataingestion.md` specification and the implementation in `dataingestion/*.py`.

**Blueprint:** `dataingestion.md` (Sections 0–15)  
**Audit Report:** See the audit produced in this conversation — 8 CRITICAL, 15 HIGH, 14 MEDIUM, 10 LOW

---

## Execution Strategy

- **Max 3 subagents running concurrently** (respects the Standard tier API limit)
- Agents grouped by **thematic proximity** — errors that touch the same module/concept share an agent
- **Waves execute sequentially** (W0 → W1 → W2 → W3 → Verification); within each wave, up to 3 agents run in parallel
- **No dependency between agents within a wave** — all can run simultaneously
- **Verification agent (W_last)** runs last and validates everything end-to-end

---

## Wave Structure

| Wave | Priority | Agents | Findings Covered | Parallelism |
|------|----------|--------|-----------------|-------------|
| **W0** | CRITICAL | 3 agents | 8 critical errors | 3 parallel |
| **W1** | HIGH | 5 agents | 15 high errors | 3+2 parallel (2 batches) |
| **W2** | MEDIUM | 4 agents | 12 medium errors | 3+1 parallel (2 batches) |
| **W3** | LOW | 2 agents | 7 low errors | 2 parallel |
| **WV** | VERIFY | 1 agent | All 47 verified | 1 sequential |

---

## Agent Details

### W0 — CRITICAL ERRORS (3 agents, run in parallel)

| Agent | Folder | Fixes | Errors |
|-------|--------|-------|--------|
| **W0A** | `P0A_rates_tenor_matching` | Rate symbol tenor-matching, percent→decimal conversion in fetcher | #1, #2 |
| **W0B** | `P0B_business_time_T` | Business time half-day validation, double-exclude fix, pre-open minutes fix | #3, #4, #5 |
| **W0C** | `P0C_quarantine_semantics` | Reject_detail population, ingest_run_id in cleaning, STK_SEM default | #6, #7, #8 |

### W1 — HIGH ERRORS (5 agents, 2 batches of 3+2)

**Batch 1 (3 parallel):**
| Agent | Folder | Fixes | Errors |
|-------|--------|-------|--------|
| **W1A** | `P1A_fetcher_config_centralization` | Vega column validation, interval→config, annual_dividend/rate_type→config, retry error structure | #9, #10, #11, #29 |
| **W1B** | `P1B_survivorship_chunk_caching` | list/contracts per date, rates cached per chunk, schedule buffer size, expiration date filtering | #13, #14, #46, #55 |
| **W1C** | `P1C_cleaning_pipeline_fixes` | Pre-filter placement, cheap-first order, subpenny float precision, spread div/0, OI column defense, NaN spot_close | #15, #16, #17, #18, #19, #20 |

**Batch 2 (2 parallel, after Batch 1 completes):**
| Agent | Folder | Fixes | Errors |
|-------|--------|-------|--------|
| **W1D** | `P1D_forward_vega_assertions` | q=0 dividend assertion, vega parallel=True, vega units documentation | #21, #22, #23 |
| **W1E** | `P1E_db_schema_alignment` | _phase→hidden, underlying_timestamp added, explicit ON CONFLICT, watermark PK run_id, staging LIKE fix, ChunkResult semantics | #12, #24, #25, #26, #27, #28, #32 |

### W2 — MEDIUM ERRORS (4 agents, 2 batches of 3+1)

**Batch 1 (3 parallel):**
| Agent | Folder | Fixes | Errors |
|-------|--------|-------|--------|
| **W2A** | `P2A_config_constants_alignment` | FETCH_NON_RETRYABLE_STATUS used, SUBPENNY_EPS used, numba eps constants used | #33, #34, #35 |
| **W2B** | `P2B_async_cache_safety` | Cache thread-safety (asyncio.Lock), utcnow→utcnow(timezone.utc) | #37, #38 |
| **W2C** | `P2C_joins_leakage` | Spot forward-fill removed, prior-session OI, T calendar re-query, schedule cache robustness | #39, #40, #42, #43 |

**Batch 2 (1 agent, after Batch 1 completes):**
| Agent | Folder | Fixes | Errors |
|-------|--------|-------|--------|
| **W2D** | `P2D_schema_watermark` | Trading-day chunking, log_moneyness in COLUMN_MAP, schedule buffer, UTC date default | #36, #44, #54 |

### W3 — LOW ERRORS (2 agents, run in parallel)

| Agent | Folder | Fixes | Errors |
|-------|--------|-------|--------|
| **W3A** | `P3A_fetcher_logging_cleanup` | Sync wrapper asyncio.run, _parse_date order, StructuredFormatter numpy types | #47, #48, #56 |
| **W3B** | `P3B_orchestrator_cleaning_polish` | quality_flags bits, COLUMN_MAP consistency | #49, #52 |

### WV — VERIFICATION (1 agent, runs last)

| Agent | Folder | Scope |
|-------|--------|-------|
| **WV** | `W_last_verification` | All 47 findings verified PASS, full test suite, mypy, flake8 |

---

## Execution Flow

```
W0 ─┬── P0A (rates tenor matching) ───┐
    ├── P0B (business time T) ─────────┤
    └── P0C (quarantine semantics) ────┤
                                       ├── ALL DONE ──→ W1 Batch 1
W1 ─┬── P1A (fetcher config) ─────────┤
B1  ├── P1B (survivorship caching) ───┤
    └── P1C (cleaning pipeline) ───────┤
                                       ├── ALL DONE ──→ W1 Batch 2
W1 ─┬── P1D (forward/vega) ───────────┤
B2  └── P1E (DB schema) ──────────────┤
                                       ├── ALL DONE ──→ W2 Batch 1
W2 ─┬── P2A (config constants) ───────┤
B1  ├── P2B (cache safety) ───────────┤
    └── P2C (joins leakage) ──────────┤
                                       ├── ALL DONE ──→ W2 Batch 2
W2 ─ P2D (schema/watermark) ──────────┤
B2                                    ├── ALL DONE ──→ W3 (parallel)
W3 ─┬── P3A (fetcher/logging) ────────┤
    └── P3B (orchestrator polish) ─────┤
                                       ├── ALL DONE ──→ WV
WV ─ Verification (all 47 checks) ────┤   → CAMPAIGN COMPLETE
```

---

## Prompt Template (used by every agent)

Each `PROMPT.md` must contain:
1. **Persona** — who the agent is
2. **Blueprint Vision** — reference `dataingestion.md` and what the overall system must look like
3. **Core Objective** — what this agent specifically achieves
4. **Invariants** — MUST hold throughout the fix (e.g., backward compatibility, test pass)
5. **Errors to Fix** — specific numbered findings from the audit, with file:line references
6. **Success Criteria** — measurable pass/fail conditions
7. **Short Specialized Verification** — what to check to confirm the fix is correct

---

## Prerequisites

```bash
# Tests must pass before and after changes
python verify_phase3.py  # baseline (22/22)
python -m pytest dataingestion/ -v --tb=short
```

## Verification Gate

After ALL agents complete, `W_last_verification` runs:
- `python verify_phase3.py` — 22/22 must pass
- `python -m pytest dataingestion/ -v --tb=short` — 0 failures
- `mypy dataingestion/` — 0 new errors
- `flake8 dataingestion/` — 0 new errors
- Specific checks for each of the 47 findings

## Rollback

If any agent's fix breaks tests, revert its changes and investigate before retrying. Each agent should make its changes in a focused, atomic manner.