# Domain Lode: `perf`

## 1. Scope & Responsibility
Multiprocessing parallelism, CPU core scaling, timeout bounds, solver benchmark budgets, and candidate workout combinatorial complexity controls.

## 2. Key Modules & Entrypoints
- [`src/triplan/solver/highs_optimization.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/highs_optimization.py): `optimize_weekly_schedule()` (single deterministic solve; the worker pool and multi-seed race were deleted in `TASK-020`/`TASK-004`), candidate pruning (`filter_viable_workouts`); clone dominance pruning lives in [`pruning.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/pruning.py).
- [`src/triplan/solver/scheduler.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py): [`TriathlonMILPScheduler`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py).
- [`src/triplan/perf/stress.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/perf/stress.py): Stress test scenarios and benchmark harnesses across 5 baseline operational scenarios.
- [`src/triplan/perf/season_validation.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/perf/season_validation.py): Multi-distance season validation (`check_season` independent Tier-0 checker, parallel 4x2 matrix runner, `EXP-007`); `control/feasibility.py` holds the season-level infeasibility diagnosis.
- [`scripts/benchmark_scaling.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/benchmark_scaling.py): Combinatorial scaling analysis harness.
- [`ReadMe.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/ReadMe.md): CPU core scaling notes (historical: the multi-core race is retired).

## 3. Core Architectural Rules & Performance Invariants

### A. Global Guardrail Domain Invariant
Rules in this domain are automatically injected into agent briefings across all subsystems via [`scripts/lodectx.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/lodectx.py).

### B. Single Deterministic Solve (`ADR-018`, supersedes `ADR-006`)
The 15-seed x 2-thread race is retired. One week is one engine solve to a stated MIP gap (Tier-2 `mip_rel_gap` 1%), reported as `mip_gap` on every plan and in the CLI; the 16-week schedule is 16 sequential solves. Speed comes from the model (dominance pruning, week-reversal symmetry row, selection cuts), not from a seed portfolio. Reference timings (12 stress solves): 11 `OPTIMAL` at <= 0.97% gap in 0.3-4.1 s on the default 2 threads; the 200-workout scale scenario is `TIME_LIMIT_FEASIBLE` at 5.7%. `EXP-003` is the formal single-solve vs race benchmark.

### C. Candidate Workout Pruning Invariant ($W \le 120$)
- Candidate pruning (`filter_viable_workouts`) must execute before solver matrix assembly.
- Pre-filtering removes mathematically impossible sessions based on daily duration availability, facility gates, and phase restrictions.
- Keeping the active candidate pool $W \le 120$ bounds the decision variable space to $120 \times 7 = 840$ primary binaries, ensuring the MILP solves in under 0.50 seconds. Unpruned candidate sets ($W > 250$) lead to combinatorial branch-and-bound explosions.

### D. Sub-Second Exact Linearization Advantage
- By algebraically linearizing all physiological ratios (TiZ percentages, sport duration splits, 32% single-run caps) into exact linear inequalities, the solver avoids non-linear solver stalls, guaranteeing deterministic convergence without heuristic approximation errors.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
