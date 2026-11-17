# Triathlon Workout & Season Planner — Empirical Experiments Master Index (`docs/experiments/INDEX.md`)

> *Auto-rendered from `docs/.context/lode.db` via `scripts/lodectx.py sync-docs`.*

| ID | Domain | Title | Effective Status | Tier | Summary |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `EXP-001` | `perf` | Multi-Seed HiGHS Race vs Single-Seed Branching Search Benchmark | **`NEEDS_RETEST`** | `TIER_1_ISOLATED` | 15-seed x 2-thread parallel HiGHS race reduces p99 solve time from 24.8s to 1.1s across 500 candidate workouts. |
| `EXP-007` | `perf` | Multi-Distance Season Validation on CURRENT_ATHLETE and an Unconstrained Reference Profile | **`ACTIVE`** | `TIER_1_ISOLATED` | 8 seasons (4 distances x 2 profiles): 0 fallback weeks, 0 independent Tier-0 violations; time-budget infeasibility is named, volume-target defects are queued. |
| `EXP-008` | `workouts` | Per-Sport Library Gap Audit Against Solver Demand | **`ACTIVE`** | `TIER_1_ISOLATED` | The season solver sees only 38 lib_default rows; 14 audited gap-fill rows cut CURRENT_ATHLETE relaxations 24-28% and single-run weeks 44-62%, at +30-50% wall time. |
