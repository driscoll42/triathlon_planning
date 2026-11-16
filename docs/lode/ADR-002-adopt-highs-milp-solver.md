---
id: ADR-002
type: adr
domain: optimizer
title: "Adopt HiGHS Mixed-Integer Linear Programming Solver via highspy"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: ["EXP-001"]
affects_services: ["optimizer", "mpc"]
one_liner: "Use HiGHS C++ MILP solver via highspy with binary assignment variables instead of unconstrained greedy search."
---

# `ADR-002`: Adopt HiGHS Mixed-Integer Linear Programming Solver via highspy

## 1. Context & Problem
Generating weekly triathlon schedules across 71 swim, 218 bike, and 190 run workouts yields $1.2 \times 10^{16}$ possible combinations. The original greedy random replacement search required 60 seconds on 30 CPU threads to evaluate 10,000 combinations, often failing to satisfy tight multi-zone and TSS targets simultaneously.

## 2. Considered Alternatives
1. **Option A (Greedy Random Perturbation):** Random sampling with 25 replacement iterations. Rejected because it takes 60 seconds, provides no mathematical optimality guarantee, and struggles with hard daily time caps.
2. **Option B (Genetic Algorithms):** Stochastic search across chromosomes. Rejected due to convergence inconsistency and slow constraint handling for rolling strain caps.
3. **Option C (ADOPTED):** Formulate weekly scheduling as a Mixed-Integer Linear Program (MILP) solved via HiGHS (`highspy`).

## 3. Decision & Production Invariant
- **Target Component / Path:** `highs_optimization.py`, `closed_loop_optimizer.py`.
- **Locked Parameters / Contract:** Model workout selection as binary decision variables $x_{w,d} \in \{0, 1\}$. Candidate pool must be pre-pruned using `filter_viable_workouts`.
- **Enforcement Mechanism:** Unit test suite in `stress_test_optimizer.py` verifying optimal solve status in < 3.0 seconds.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-001` benchmarks demonstrating optimal solutions in 0.25 to 1.5 seconds vs. 60+ seconds for greedy search.
- **Supersedes:** None (replaces legacy greedy search in `util.py:generate_random_workout_plan`).

## 5. Consequences & Trade-Offs
- **Positive:** Guaranteed global optimality within defined tolerances; sub-second solution time; deterministic constraint satisfaction.
- **Negative / Operational Cost:** Non-linear physiological relationships must be linearized or pre-computed into static coefficients.
- **When to Revisit:** If non-linear dynamic constraints (e.g. continuous hydration curves) cannot be accurately linearized.
