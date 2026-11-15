# Domain Lode: `perf`

## 1. Scope & Responsibility
Multiprocessing parallelism, CPU core scaling, timeout bounds, solver benchmark budgets, and candidate workout combinatorial complexity controls.

## 2. Key Modules & Entrypoints
- [`src/highs_optimization.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/highs_optimization.py): [`optimize_weekly_workouts_multiseed()`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/highs_optimization.py#L450-L550), candidate pruning (`filter_viable_workouts`), worker pool orchestration.
- [`src/closed_loop_optimizer.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py): [`TriathlonMILPScheduler`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py#L320-L650).
- [`src/stress_test_optimizer.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/stress_test_optimizer.py): Stress test scenarios and benchmark harnesses across 5 baseline operational scenarios.
- [`scripts/benchmark_scaling.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/benchmark_scaling.py): Combinatorial scaling analysis harness.
- [`ReadMe.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/ReadMe.md): Parallel CPU core scaling execution profiles (1 to 32 cores).

## 3. Core Architectural Rules & Performance Invariants

### A. Global Guardrail Domain Invariant
Rules in this domain are automatically injected into agent briefings across all subsystems via [`scripts/lodectx.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/lodectx.py).

### B. Multi-Seed Parallel HiGHS Race (`ADR-006`)
Branch-and-bound tree exploration in Mixed-Integer Linear Programming is highly sensitive to the initial pseudo-random seed. The platform exploits this via a parallel race:
- **Allocation:** 15 independent seeds distributed across worker processes with 2 threads per worker (30 cores total).
- **Early Termination:** The race terminates immediately when the first worker discovers the proven optimal solution or reaches the optimality gap tolerance.
- **Worker Timeouts:** Every worker process is strictly governed by a hard timeout: `time_limit_per_worker = 4.0s`.

### C. Candidate Workout Pruning Invariant ($W \le 120$)
- Candidate pruning (`filter_viable_workouts`) must execute before solver matrix assembly.
- Pre-filtering removes mathematically impossible sessions based on daily duration availability, facility gates, and phase restrictions.
- Keeping the active candidate pool $W \le 120$ bounds the decision variable space to $120 \times 7 = 840$ primary binaries, ensuring the MILP solves in under 0.50 seconds. Unpruned candidate sets ($W > 250$) lead to combinatorial branch-and-bound explosions.

### D. Sub-Second Exact Linearization Advantage
- By algebraically linearizing all physiological ratios (TiZ percentages, sport duration splits, 32% single-run caps) into exact linear inequalities, the solver avoids non-linear solver stalls, guaranteeing deterministic convergence without heuristic approximation errors.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
