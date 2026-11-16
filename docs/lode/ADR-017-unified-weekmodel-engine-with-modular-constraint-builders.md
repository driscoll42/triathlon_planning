---
id: ADR-017
type: adr
domain: optimizer
title: "Unified WeekModel engine with modular constraint builders"
status: ACTIVE
created_at: 2026-11-16
supersedes: []
validated_by: []
affects_services: ["solver"]
one_liner: "All week MILPs are built by solver/engine (WeekModel + builders, bulk HiGHS build); facades stay thin; new rules are new builder modules, not a 4th engine."
---

# `ADR-017`: Unified WeekModel engine with modular constraint builders

## 1. Context & Problem
Three separate HiGHS formulations lived side by side: `TriathlonMILPScheduler` (incremental `addVar`/`addRow`, partition + brick session model), `HybridAthleteMILPOptimizer` (bulk `addCols`, at most 2 workouts per day, split architecture, barbell share, TiZ band) and `highs_optimization.optimize_weekly_schedule` (DataFrame, highspy expression API, one workout per day). Each re-implemented column registries, row hygiene (duplicate columns, zero coefficients), soft-goal deviations with different scaling conventions, Top-K cuts and result extraction. Every new rule (`TASK-018` carry-in, `TASK-019` session structure, `TASK-020` cuts) would have had to be written three times.

## 2. Considered Alternatives
1. **Option A (Rejected): Keep three engines, share only `safety_constraints`.** This is the pre-`TASK-015` state; rule drift between engines had already produced the HEUR-004 miss in the hybrid engine (`TASK-017`).
2. **Option B (Rejected): Rewrite as one new formulation with new semantics.** Impossible to verify; it would change plans silently.
3. **Option C (ADOPTED): One engine, semantics selected by explicit options, built from modular builders over a buffered sparse model.** Each legacy engine is an option preset that reproduces its formulation exactly, so the refactor is verifiable by diffing solutions.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/triplan/solver/engine/` (`model.py`, `config.py`, `context.py`, `engine.py`, `builders/{calendar,orthopedic,intensity_structure,strength,goals}.py`). `scheduler.py`, `hybrid.py` and `highs_optimization.optimize_weekly_schedule` are thin facades.
- **Locked Parameters / Contract:**
  - `WeekModel` buffers columns (lb/ub/cost/integrality) and CSR rows in numpy, sums duplicate columns, drops zero coefficients, and performs ONE `addCols` and ONE `addRows`. Builders never talk to HiGHS. Only Top-K cuts are added to a built model.
  - Soft goals are dimensionless: `add_soft_goal(coeffs, target, scale, weight, name, pos_weight=None, neg_weight=None)` costs each deviation `weight / scale`. Hard rules are never expressed as soft goals.
  - `EngineOptions` (see `engine/config.py`) selects `session_model` (`partition_brick` | `per_sport_cap` | `single_per_day`), gating rule, cost/goal profile, `cut_mode`, rest-day default window, and the strength/TiZ/adjacency builders. `meso_options()`, `hybrid_options()`, `legacy_options()` reproduce the three legacy engines.
  - Workout and config attributes are read in one place (`WorkoutTable`, `WeekConfig`); builders never call `getattr`/`cfg[...]` directly. The orthopedic builder delegates to `safety_constraints` and must not duplicate it.
  - New rules (carry-in state/ACWR, session structure, selection cuts, pruning) are added as builder modules or replace `UnifiedWeekEngine._add_cut`, never as a fourth engine.
- **Enforcement Mechanism:** `tests/test_week_model.py` (buffering, duplicate summing, soft-goal math, bulk-build row counts, facade plan keys); existing `tests/test_safety_constraints.py`, `tests/test_current_athlete.py`, `tests/test_stress_scenarios.py` exercise both facades.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Pre/post solution diff over the 11 stress scenarios, three CURRENT_ATHLETE phases, the hybrid athlete config, 9 safety unit cases (26 engine solves, Top-3 each): status, rank-1..k objective, `tier1_penalty` and every selected `(workout, day)` set were identical (relative objective difference 0.0). The legacy day scheduler was compared at the model level (identical columns, costs, bounds, integrality; identical rows except that the two one-sided TSS guardrail rows are one ranged row) and by solved objective on 6 instances (relative difference <= 2e-10). See `docs/archive/TASKS_COMPLETED.md` (`TASK-015`).
- **Supersedes:** None. `ADR-013` (tiers) and `ADR-008` (axial/neuro caps) are unchanged and still enforced through `safety_constraints`.

## 5. Consequences & Trade-Offs
- **Positive:** One code path per rule; new rules are one builder; the Top-K cut and candidate pruning of `TASK-020` have a single seam; sparse numpy construction scales with candidate count.
- **Negative / Operational Cost:** Legacy quirks are preserved verbatim behind options: the hybrid `z_split` flip cut (defect owned by `TASK-020`), the hybrid TiZ slacks measured in minutes with unit scale (not dimensionless; recalibrate in `EXP-004`), the scheduler's partially redundant adjacency rows, and the scheduler's deviation cap of infinity versus the hybrid's 1e4.
- **When to Revisit:** After `TASK-018`/`TASK-019`/`TASK-020` land, drop the options that only exist to reproduce a retired formulation.
