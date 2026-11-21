# Domain Lode: `perf`

## 1. Scope & Responsibility
Single deterministic HiGHS solve governance (`ADR-018`), candidate-pool complexity and `MAX_CANDIDATES_BUDGET <= 120` gating (`TASK-060`, `ADR-029`), process-parallel stress and season-validation suites (`TASK-032`, `TASK-027`, `EXP-007`), and solver scaling benchmarks.

## 2. Key Modules & Entrypoints
- [`src/triplan/solver/highs_runtime.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/highs_runtime.py): `apply_threads()` and `run_and_check()` enforcing consistent thread counts and explicit HiGHS status/`mip_gap` reporting.
- [`src/triplan/solver/pruning.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/pruning.py): `filter_feasible_candidates()`, `prune_8020_candidates()`, and `prune_dominated_candidates()` keeping the active MILP candidate count bounded before matrix assembly.
- [`src/triplan/perf/stress.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/perf/stress.py): 11-scenario parallel stress suite (`REGISTRY` of picklable `_scenario_1..11` functions, `ProcessPoolExecutor` with `TRIPLAN_STRESS_WORKERS` / `TRI_HIGHS_THREADS`, `TASK-032`, `HEUR-001`).
- [`src/triplan/perf/season_validation.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/perf/season_validation.py): Parallel 4-distance × 2-profile season validation matrix (`EXP-007`), independent Tier-0 checker (`check_season`), and `MAX_CANDIDATES_BUDGET = 120` gate (`TASK-060`).
- [`scripts/benchmark_scaling.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/benchmark_scaling.py): Combinatorial scaling analysis harness.

## 3. Core Architectural Rules & Performance Workflow

### A. Global Guardrail Domain & Single Deterministic Solve (`ADR-018`, supersedes `ADR-006`)
Rules in `perf` are injected into briefings across all domains (`scripts/lodectx.py`).
- **One Deterministic Solve per Week:** The 15-seed × 2-thread multi-seed race (`ADR-006`, `EXP-001`) is retired and formally superseded (`EXP-003`). Every week executes a single deterministic `UnifiedWeekEngine` solve to a stated `mip_rel_gap` (`1%` default for single plan; `0%` recommended for strict monotone Top-K ranking), always reporting `status` and `mip_gap`.
- **Structural Speedups Inside & Before the Model (`EXP-003`):** Convergence and build throughput come from cached library materialization/candidates (`library/loader.py`), precomputed `(N, 7)` `allowed` matrices (`builders/calendar.py`), $O(k)$ sorted fast paths + direct `set_bounds` in `WeekModel`, `s[i] = sum_d x[i, d]` weekly indicator decomposition (`7x` sparser weekly/cut/split rows), Balas-lifted `_add_run_share_cap` (`ADR-029`), goal-free Phase A + per-weight Tier-1 bound locking (eliminating $10^{11}$ row scaling), clone dominance pruning, and week-reversal symmetry breaking.
- **Verified Scaling (`EXP-003`, 2026-11-21):** Unpruned single deterministic `threads=4` achieves 100% optimal convergence across $N \in \{40..500\}$ (`1.63–1.97s` p95 for $N \le 160$, `2.42s` med / `4.49s` p95 at $N=500$, `11.1–25.2ms` build, `119.4–148.2 MB` Peak RSS) and beats the `15x2` race by `2.35–3.70x` wall time and `7.0–11.7x` CPU thread-seconds (`scripts/run_exp003_benchmark.py`).

### B. Candidate Complexity Control & `MAX_CANDIDATES_BUDGET <= 120` (`TASK-060`, `ADR-029`)
Keeping the active post-combo candidate pool $W \le 120$ bounds binary assignment variables ($W \times 7 \le 840$) and prevents branch-and-bound blowups:
1. **Lossless Feasibility Pre-Filter:** `filter_feasible_candidates()` removes non-anchor, non-required-test workouts with zero valid days under time/facility/axial/neuro caps and drops heavy lifts when `required_heavy_strength_sessions == 0`.
2. **Stratified 80/20 Pruning (`ADR-029`, `TASK-060`):** `prune_8020_candidates()` drops `8020_library` near-duplicates within `(<= 2.0 min, <= 3.0 TSS)` of a core workout in the same `(sport, intensity_bucket, is_indoor_bike)`, then caps remaining `8020_library` rows at `easy=6, tempo=4, hi=4` per sport.
3. **Combo Caps & Post-Combo Filtering (`TASK-060`):** `make_lift_easy_combos()` limits `max_non_heavy_bases=2` and `max_combos_per_lift=2`, and runs generated combos back through `filter_feasible_candidates()` before `WorkoutTable` assembly.
4. **Season Gate:** `control/season.py` records `WeekRecord.n_candidates` and `Candidates` in season summaries; `perf/season_validation.py` enforces `max_candidates <= 120` across the 8-season validation matrix (`CURRENT_ATHLETE` max `64`, `reference` max `95` in `TASK-060`).

### C. Windows-Safe Multiprocessing for Validation Suites (`HEUR-001`, `TASK-032`)
- **Picklable Worker Boundary:** On Windows (`spawn`), worker functions in `perf/stress.py` and `perf/season_validation.py` receive pure Python configs/arguments and construct `Highs` instances strictly inside each child process (never sharing `Highs` C++ objects across processes).
- **Thread Budgeting:** `perf/stress.py` caps per-worker HiGHS threads to `min(DEFAULT_THREADS, cpu_count // workers)` via `TRI_HIGHS_THREADS` (serial debugging supported via `workers=1` or `TRIPLAN_STRESS_WORKERS=1`).

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs; edit narrative sections above, not this block. -->

## Registered Lineage (`perf`)

### Active Operational Heuristics (`HEUR`)
- **`HEUR-001` — Guard Against Python Multiprocessing Worker Deadlocks on Windows in Parallel HiGHS Workers:** Never pass unpicklable Highs objects to child workers; pass pure dicts/DataFrames and initialize Highs inside worker.

### Empirical Experiments (`EXP`)
- **`EXP-001` [`SUPERSEDED` | `TIER_1_ISOLATED`] — Multi-Seed HiGHS Race vs Single-Seed Branching Search Benchmark:** SUPERSEDED by EXP-003 (2026-11-21): single deterministic solve outperforms 15-seed x 2-thread race by 2.35-3.70x wall time and 7.0-11.7x CPU thread-seconds at equal objective.
- **`EXP-003` [`ACTIVE` | `TIER_1_ISOLATED`] — Solver Correctness, Sparse Build and Dominance Pruning Scaling Benchmark:** Single deterministic s_i/Balas-lifted WeekModel achieves 100% optimality across N=40..500 (p95 <= 1.97s for N<=160), beats 15x2 race by 2.35-3.70x wall and 7.0-11.7x CPU-s (superseding EXP-001), and delivers monotone Top-5 plans at gap=0.
- **`EXP-007` [`ACTIVE` | `TIER_1_ISOLATED`] — Multi-Distance Season Validation on CURRENT_ATHLETE and an Unconstrained Reference Profile:** 8 seasons (4 distances x 2 profiles): 0 fallback weeks, 0 independent Tier-0 violations; time-budget infeasibility is named, volume-target defects are queued.

> **Superseded (`perf`):** `ADR-006`, `EXP-001` (`python scripts/lodectx.py trace <ID>`).
<!-- LODE_AUTOGEN_END -->
