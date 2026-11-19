# Triathlon Workout & Season Planner — Empirical Experiments Master Index (`docs/experiments/INDEX.md`)

> *Auto-rendered from `docs/.context/lode.db` via `scripts/lodectx.py sync-docs`.*

| ID | Domain | Title | Effective Status | Tier | Summary |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `EXP-001` | `perf` | Multi-Seed HiGHS Race vs Single-Seed Branching Search Benchmark | **`NEEDS_RETEST`** | `TIER_1_ISOLATED` | 15-seed x 2-thread parallel HiGHS race reduces p99 solve time from 24.8s to 1.1s across 500 candidate workouts. |
| `EXP-002` | `safety` | Closed-Loop 16-Week Season Simulation Under Real Profile and Perturbations | **`ACTIVE`** | `QUEUED_SPEC` | Queued spec: verify 16-week closed-loop season simulation on CURRENT_ATHLETE under nominal, illness, pool-closure, travel, and stochastic execution noise. |
| `EXP-003` | `perf` | Solver Correctness, Sparse Build and Dominance Pruning Scaling Benchmark | **`ACTIVE`** | `QUEUED_SPEC` | Queued spec: benchmark sparse CSR WeekModel build and dominance pruning across 40-500 candidates, and retest EXP-001 15-seed race vs single solve. |
| `EXP-004` | `optimizer` | Goal-Weight Sensitivity and Session-Structure Constraints Sweep | **`ACTIVE`** | `QUEUED_SPEC` | Queued spec: 81-point soft-goal weight sensitivity sweep across 300/500/700 TSS with and without ADR-020 session-structure constraints. |
| `EXP-005` | `integrations` | Multi-Device FIT Ingestion Parser and Hardware Invariant Validation | **`ACTIVE`** | `QUEUED_SPEC` | Queued spec: validate multi-device FIT parser on synthetic and 2,020 real Garmin FIT files for Stryd CIQ power, active swim lengths, and 300s ride dedup. |
| `EXP-006` | `physiology` | Load-Model Calibration Against Historical Garmin and Lifting Telemetry | **`ACTIVE`** | `QUEUED_SPEC` | Queued spec: calibrate Banister PMC decay, 80/20 zone midpoints, running strain exponent beta, and 28-day HRV/RHR readiness alpha_d on 2,020 activities. |
| `EXP-007` | `perf` | Multi-Distance Season Validation on CURRENT_ATHLETE and an Unconstrained Reference Profile | **`ACTIVE`** | `TIER_1_ISOLATED` | 8 seasons (4 distances x 2 profiles): 0 fallback weeks, 0 independent Tier-0 violations; time-budget infeasibility is named, volume-target defects are queued. |
| `EXP-008` | `workouts` | Per-Sport Library Gap Audit Against Solver Demand | **`ACTIVE`** | `TIER_1_ISOLATED` | The season solver sees only 38 lib_default rows; 14 audited gap-fill rows cut CURRENT_ATHLETE relaxations 25-30% and single-run weeks 44-62%, at +50-90% wall time on CURRENT_ATHLETE. |
| `EXP-009` | `workouts` | Include 80/20 Endurance Library Options in Default and Hybrid MILP Pools | **`ACTIVE`** | `TIER_1_ISOLATED` | Including pruned feasible 8020_library options in default/hybrid pools keeps 0 fallback/0 violations across all 8 seasons and lets MILP pick 80/20 sessions. |
