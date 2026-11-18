---
id: ADR-029
type: adr
domain: workouts
title: "Include 80/20 Endurance Library Options in Default and Hybrid MILP Pools"
status: ACTIVE
created_at: 2026-11-17
supersedes: []
validated_by: ["EXP-009"]
affects_services: ["library", "solver", "control"]
one_liner: "Default and hybrid MILP candidate builders include 8020_library workouts by default, pruned per-week by feasibility and stratified duration/intensity buckets."
---

# `ADR-029`: Include 80/20 Endurance Library Options in Default and Hybrid MILP Pools

## 1. Context & Problem
The canonical library (`data/workout_library.csv`) contains 479 `8020_library` workouts compiled from `data/{Run,Bike,Swim}.xlsx`, yet `build_default_candidate_library()` (`lib_default`, 52 rows) and `build_hybrid_candidate_library()` (`lib_hybrid`, 22 rows) excluded all 479 rows. Meanwhile, `scripts/generate_level0_hybrid_20w.py` used hardcoded formulas rather than the MILP solver. Simply passing all 479 `8020_library` rows unfiltered caused `_add_run_share_cap` to grow to $O(R^2)$ nonzeros and timed out Phase B of `lexicographic_solve`, while duration collisions (`RF12`, `RAe3`, `RF16`) could trigger duplicate keystone anchors in `apply_keystone_week`.

## 2. Considered Alternatives
1. **Option A (Rejected): Flip `lib_default=True` on `8020_library` rows in CSV.** Would not survive `refresh_from_8020_scrape()`, breaks `HEUR-008` core accounting, and sends all 479 rows unfiltered into every MILP.
2. **Option B (Rejected): Opt-in `include_8020` flag only.** Leaves standard MILP season and hybrid entrypoints without 80/20 options unless callers remember an extra flag.
3. **Option C (ADOPTED):** Include `8020_library` options in `load_candidates(library="default"|"hybrid")` by default (with `include_8020=False` available for core-only checks), paired with lossless feasibility pre-filtering, stratified `8020_library` pruning inside `UnifiedWeekEngine`, sparse `r_tot` run-share rows, Phase-B MIP start, and canonical keystone selection.

## 3. Decision & Production Invariant
- **Target Component / Path:**
  - `src/triplan/model/candidates.py` (`CandidateWorkout.provenance`), `src/triplan/library/{loader,candidates}.py` (`include_8020=True` default).
  - `src/triplan/solver/pruning.py` (`filter_feasible_candidates`, `prune_8020_candidates` with `easy=6, tempo=4, hi=4` caps per sport).
  - `src/triplan/solver/engine/{config,engine}.py` (`prune_8020_pool=True` in `meso_options`/`hybrid_options`; pre-filter diagnostics retained for `explain_feasibility`).
  - `src/triplan/solver/safety_constraints.py` (sparse `r_tot` in `_add_run_share_cap`; `h.setSolution` MIP start in `lexicographic_solve`).
  - `src/triplan/control/periodization.py` (`KEYSTONE_RUN_CODES` canonical selection in `keystone_run_rungs` / `apply_keystone_week`).
  - `scripts/generate_level0_hybrid_20w.py` (MILP-driven 20-week hybrid plan generator).
- **Locked Parameters / Contract:**
  - Core `lib_default` (52) and `lib_hybrid` (22) CSV flags remain unchanged and `refresh_from_8020_scrape()` remains idempotent.
  - Curated core rows, anchors, tests, and stride-bearing sessions are never dropped by `prune_8020_candidates`.
  - No Tier-0 guardrail is loosened.
- **Enforcement Mechanism:** `tests/test_8020_milp_integration.py`, `tests/test_workout_library.py`, `tests/test_long_course_library.py`, `tests/test_library_gap_fill.py`, and `python -m triplan.perf.season_validation`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-009` — 8-season `season_validation` matrix (240 total weeks) passes with **0 fallback weeks and 0 Tier-0 violations**; `scripts/generate_level0_hybrid_20w.py` generates a 20-week MILP plan picking 27 `8020_library` sessions alongside 60 `agent_authored` and 32 `hand_coded_default` sessions.
- **Supersedes:** None. Refines `ADR-014` (library loader) and `ADR-028` (canonical keystone selection).

## 5. Consequences & Trade-Offs
- **Positive:** All MILP plans automatically have `8020_library` workouts as selectable options; `level0_hybrid_20w_daily.csv` is now genuinely MILP-optimized; `r_tot` and Phase-B MIP start improve solver robustness at larger candidate counts.
- **Negative / Operational Cost:** `CURRENT_ATHLETE` season-matrix wall time rises from ~56s to ~142s due to the larger feasible candidate pool (~30 extra 80/20 options after pruning).
- **When to Revisit:** With `TASK-060` (general dominance/symmetry pruning of near-identical rows and combo capping).
