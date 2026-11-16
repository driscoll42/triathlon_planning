---
id: ADR-018
type: adr
domain: optimizer
title: "Single deterministic solve with selection cuts, dominance pruning and symmetry breaking replaces the multi-seed race"
status: ACTIVE
created_at: 2026-11-16
supersedes: ["ADR-006"]
validated_by: []
affects_services: ["solver", "perf"]
one_liner: "One deterministic engine solve per week; Top-K via workout-selection cuts; exact-clone pruning and week-reversal symmetry rows; no seed race, no random generator."
---

# `ADR-018`: Single deterministic solve with selection cuts, dominance pruning and symmetry breaking replaces the multi-seed race

## 1. Context & Problem
`ADR-006` bought speed with a 15-seed x 2-thread race (30 cores) over the selection-only `optimize_weekly_workouts`. After `ADR-017` every weekly MILP is built by one engine, so the model itself is the lever: the race could not share state between weeks, its "best" plan was ranked by `util.calculate_plan_score` (a heuristic, not the objective), and the Top-K cuts were weak. `cut_mode='active_x'` / `'pair_pm1_split_flip'` excluded only one exact `(workout, day)` layout, so Top-K returned day permutations of the same workouts, and the hybrid `z_split` flip equality capped hybrid Top-K at two plans.

## 2. Considered Alternatives
1. **Option A (Rejected): Keep the seed race.** 30 cores to hide model weakness; non-deterministic; benchmarked best-of-5 plans all stopped at `TIME_LIMIT_FEASIBLE` with 62-87% gaps on the 337-candidate library, so it did not even prove quality.
2. **Option B (Rejected): Prune by "smaller is better" dominance** (shorter, lower TSS/ANS/strain). Unsound: TSS, zone and sport-split goals are two-sided and caps interact, so a lighter workout can make a plan worse.
3. **Option C (Rejected): Symmetry-break arbitrary day permutations.** Not symmetries: rolling 48h windows and hard/easy adjacency couple consecutive days. Only the week reversal `d -> 6 - d` is a candidate automorphism.
4. **Option D (ADOPTED): one deterministic solve through the unified engine, selection cuts, exact-clone dominance pruning, conditional reversal symmetry row.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/triplan/solver/engine/engine.py` (`_add_cut`), `engine/builders/symmetry.py`, `src/triplan/solver/pruning.py` (`prune_dominated_candidates`), `engine/config.py` (`EngineOptions`).
- **Locked Parameters / Contract:**
  - Top-K: `cut_mode='selection'` is the default for both facades. With `s_i = sum_d x[i,d]` and `S` the workouts of the plan just found, the cut is `sum_{i in S} s_i <= |S| - m` (`cut_min_diff = m`, default 1). `cut_mode='layout'` keeps the exact `(i,d)` no-good (day permutations count as distinct). `none` = single solve. The hybrid `z_split` flip equality is removed; `split_architecture` is still reported from `z_split`'s value.
  - Dominance pruning (`prune_dominated`, default on): workout j is dropped only if it is identical to at least `cap` kept workouts in every attribute except `preference_penalty` (lower is better) and `durability_score` (higher is better), the kept ones are no worse on both, and `cap` = that sport's maximum weekly frequency (or 7). Anchors, `test`-flagged workouts and non-standard sports are never pruned. Never prune on duration/TSS/ANS/strain order.
  - Symmetry breaking (`symmetry_breaking`, default on): one row `sum_d (d-3) sum_i dur_i x[i,d] <= 0` only when the week is provably invariant under reversal (palindromic caps, readiness alpha, `allowed(i,d) == allowed(i,6-d)`; no weekday-only cap, no weekend brick, no legacy day rules). Off for `layout` cuts.
  - No seed race: `optimize_weekly_workouts`, `optimize_weekly_workouts_multiseed`, `_solve_seed_worker`, `_solve_olympic_week_worker`, `generate_16_week_olympic_schedule` (solver module) and `util.generate_random_workout_plan` are deleted. `util.calculate_plan_score` is a reporting metric only. The CLI runs sequential single solves and prints status and `mip_gap`.
- **Enforcement Mechanism:** `tests/test_selection_cuts.py` (hybrid k=4 distinct selections, `m` difference, layout mode, pruning soundness and rank-1 objective, symmetry row conditions and optimum preservation with `tier2_gap=0`).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Pre/post harness over 26 engine solves: rank-1 objective and `tier1_penalty` identical in 25; the one difference (`hybrid_dl_long`, +0.77%) was inside the Tier-2 1% gap and vanished (515.95 vs 515.95) with `tier2_gap=0`. 11/12 stress solves stay `OPTIMAL` at <= 1% gap in 0.3-3.6 s; the 200-workout scale scenario is `TIME_LIMIT_FEASIBLE` at 5.7% in both. See `docs/archive/TASKS_COMPLETED.md` (`TASK-020`, `TASK-004`).
- **Supersedes:** `ADR-006`. Do not reintroduce a seed portfolio to mask a weak model; improve the formulation (cuts, pruning, symmetry) or the gap/time policy instead. `EXP-003` (single solve vs race at scale) stays queued as the formal benchmark.

## 5. Consequences & Trade-Offs
- **Positive:** Deterministic, reproducible plans; 1 core instead of 30; Top-K are genuinely different weeks; hybrid Top-K no longer capped at 2.
- **Negative / Operational Cost:** Selection cuts exclude supersets of earlier selections and cost 1.1-1.5x solve time at k=3 (`benchmark_scaling` 50/100 workouts: 5.8 -> 7.1 s, 9.6 -> 11.5 s); clone pruning hides equivalent clone alternatives; the legacy DataFrame day scheduler (`optimize_weekly_schedule`) still ends `TIME_LIMIT_FEASIBLE` (gap ~100%) on a 337-candidate library, before and after.
- **When to Revisit:** If `EXP-003` shows a regression at >200 candidates, or `TASK-018`/`TASK-019` add day-asymmetric rows (re-audit `is_week_reversal_symmetric`).
