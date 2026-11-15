---
id: ADR-006
type: adr
domain: perf
title: "Adopt 15-Seed Parallel HiGHS Race with Per-Worker Timeout Limits"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: ["EXP-001"]
affects_services: ["perf", "optimizer"]
one_liner: "Execute a 15-worker x 2-thread HiGHS race with a 4.0-second worker timeout to exploit branch-and-bound seed variability."
---

# `ADR-006`: Adopt 15-Seed Parallel HiGHS Race with Per-Worker Timeout Limits

## 1. Context & Problem
Branch-and-cut MILP solvers exhibit extreme performance variability depending on the initial random seed chosen for simplex perturbations and branching tie-breaking. A single thread with an unlucky seed can take 15-30+ seconds to close the MIP gap on constrained combinatorial problems, whereas a favorable seed discovers an optimal basis in 0.3 seconds.

## 2. Considered Alternatives
1. **Option A (Single-Seed HiGHS Solver):** Standard execution with default seed 0. Rejected due to long tail latency when branching heuristics stall.
2. **Option B (Deterministic Multi-Threading):** Running 1 worker with 32 internal threads. Rejected because internal tree parallelism suffers from lock contention and cannot explore radically different root heuristics concurrently.
3. **Option C (ADOPTED):** Parallel portfolio race spawning 15 independent workers with randomized seeds and 2 threads each, taking the first solutions to prove optimality or lowest MIP gap within 4.0 seconds.

## 3. Decision & Production Invariant
- **Target Component / Path:** `highs_optimization.py:optimize_weekly_workouts_multiseed()`.
- **Locked Parameters / Contract:** Worker count = 15; threads per worker = 2 (30 total cores); per-worker time limit = 4.0s; candidate pruning active.
- **Enforcement Mechanism:** Subprocess pool isolation with explicit join timeouts.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-001` benchmarks demonstrating 95% of runs finding global optimum in < 1.2s total wall-clock time.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** Reduces p99 scheduling latency from 25s down to < 2.0s; leverages high-core CPUs (e.g. AMD Ryzen 5950X).
- **Negative / Operational Cost:** Higher instantaneous CPU utilization across multiple cores for 1-2 seconds.
- **When to Revisit:** When deploying on low-core serverless environments where worker pool size must be scaled down to 2-4 workers.
