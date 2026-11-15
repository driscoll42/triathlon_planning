---
id: PRD-002
type: prd
domain: optimizer
title: "Dual-Objective Hybrid Athlete MILP and MPC Optimization"
status: COMPLETED
created_at: 2026-11-08
updated_at: 2026-11-08
supersedes: ["PRD-001"]
depends_on: ["ADR-002", "ADR-003", "ADR-008"]
affects_services: ["optimizer", "mpc", "athlete_profile", "safety"]
one_liner: "Optimize concurrent triathlon and heavy barbell/dumbbell strength training via HiGHS MILP and 3-Tier closed-loop MPC."
---

# Engineering PRD: Dual-Objective Hybrid Athlete MILP and MPC Optimization (`PRD-002`)

## 1. Overview & Architectural Objective
The `HybridAthleteMILPOptimizer` and `ClosedLoopMPCController` subsystems in `src/closed_loop_optimizer.py` formulate and solve concurrent endurance (Swim, Bike, Run) and resistance (Olympic Barbell, Capped Dumbbells) training schedules. The architecture enforces equipment boundaries (home gym $\le 75\text{ lb}$ DBs, commercial barbell $\ge 60\%$ weekly sets), intracellular molecular signaling windows ($\ge 6\text{h}$ AMPK/mTOR separation), biomechanical/neuromuscular conflict avoidance (axial load caps, 24h deadlift-run buffer, squat-torque bike separation), Time-in-Zone limits ($\ge 78\% Z_1/Z_2, \le 20\% Z_4/Z_5$), and Pareto multi-objective balancing across training volume, durability, and target deviations, governed by 3-tier closed-loop MPC.

## 2. Problem Statement & Empirical Evidence
- **Current Bottleneck / Risk:** Classical triathlon planners either ignore resistance training entirely or treat strength as static auxiliary load without tracking muscle-pattern distribution (Push, Pull, Anterior Chain, Posterior Chain), equipment limits (dumbbells capped at 75 lb), or biomechanical conflicts. Concurrently executing heavy barbell deadlifts/squats and high-intensity running without conflict constraints leads to lumbar shear overload ($>18\text{ kN}$ at L4/L5) and blunted muscular adaptations via AMPK-mTORC1 interference.
- **Telemetry / Proof:** Benchmarked in `src/stress_test_optimizer.py` Scenario 11 and `references/Hybrid Athlete MILP_MPC Optimizer Blueprint.md`. Option A (Full-Body HLM) solves in $0.182\text{s}$ with objective $-40.70$, zero axial ceiling violations ($X_{\text{day}} \le 120, X_{\text{wk}} \le 320$), exactly $\ge 60\%$ barbell set share, and strict separation between deadlifts and running quality.

## 3. Target Goals (Measurable Outcomes)
- **[Performance / Efficiency]:** HiGHS solve latency $\le 0.50\text{s}$ for 1-week horizon (empirically $0.182\text{s}$); secondary solution generation via Canonical No-Good Integer Cuts within $\le 0.30\text{s}$.
- **[Security / Correctness]:** Exact linear ratio constraints for single run duration $\le 32\%$ weekly run volume, barbell set share $\ge 60\%$, and 80/20 polarized Time-in-Zone distribution ($\ge 78\% Z_1/Z_2, \le 20\% Z_4/Z_5$). Hard binary exclusion for same-day and $\pm 24\text{h}$ adjacent-day heavy deadlift and threshold/long runs.
- **[Reliability / Operability]:** Closed-Loop MPC auto-regulation reacting to daily Garmin morning readiness scalars ($\alpha_d \in [0.5, 1.2]$), dynamic Kalman-filtered e1RM adjustments with observation noise $\sigma_v = 3.5\text{ kg}$, and 3:1 mesocycle wave undulation.

## 4. Non-Goals (Strict Negative Scope — DO NOT TOUCH)
- **DO NOT** model continuous joint angle kinematics; use lumped axial and neuromuscular ratings.
- **DO NOT** replace low-level Garmin FIT file parsers or API synchronizers.
- **DO NOT** allow non-linear solver formulations (all constraints must remain purely linear in HiGHS MILP).

## 5. System Invariants & Guardrails (`docs/INVARIANTS.md`)
- Single run volume cap: $(1 - 0.32) T_i x_{i,d} - 0.32 \sum_{j \ne i} T_j x_{j,d'} \le 0$.
- Daily axial load ceiling $X_d \le 120.0$, weekly axial load ceiling $\sum_d X_d \le 320.0$.
- Daily neuromuscular load ceiling $N_d \le 130.0$, weekly neuromuscular ceiling $\sum_d N_d \le 360.0$.
- Barbell set share: $\sum_{d,i} ((1 - 0.60) V_i^{\text{BB}} - 0.60 V_i^{\text{DB}}) x_{i,d} \ge 0$.
- Movement pattern volume: $\ge 10$ sets/week for Anterior Chain, Posterior Chain, Push, Pull.
- Split coherence: $z_{\text{split}} \in \{0, 1\}$ strictly partitions Full-Body HLM vs Upper/Lower split workouts.

## 6. Deterministic Technical Requirements & Tasks
- [x] **[Task 1 - MUST] Hybrid Data Modeling (`src/classes.py`):**
  - *System Benefit:* Introduces 4 movement patterns, equipment tracking (BB vs DB), durability scores, and neuromuscular/axial ratings.
  - *Implementation Spec:* Defined `HybridVariables` with default TSS (Swim: 100, Bike: 260, Run: 198, Strength: 52), caps, and equipment partition ratios.
- [x] **[Task 2 - MUST] Blueprint Workout Library & Dynamic Enrichment (`src/closed_loop_optimizer.py`):**
  - *System Benefit:* Populates standardized 15-workout blueprint library and provides automatic tagging for external CSV workouts.
  - *Implementation Spec:* `build_hybrid_candidate_library()` and `enrich_workouts_from_df()`.
- [x] **[Task 3 - MUST] HiGHS MILP Formulation with Exact Ratio Linearization (`src/closed_loop_optimizer.py`):**
  - *System Benefit:* Guaranteed global optimality without non-linear solver overhead or approximation heuristics.
  - *Implementation Spec:* `HybridAthleteMILPOptimizer` implementing Pareto multi-objective, conflict matrix, $z_{\text{split}}$ indicator cuts, and Canonical No-Good Integer Cuts.
- [x] **[Task 4 - MUST] 3-Tier Closed-Loop MPC Controller (`src/closed_loop_optimizer.py`):**
  - *System Benefit:* Dynamic auto-regulation responding to biometric readiness, strength adaptations, and mesocycle waves.
  - *Implementation Spec:* `ClosedLoopMPCController` with Tier 1 Garmin scalar, Tier 2 e1RM Kalman filtering, and Tier 3 3:1 mesocycle wave programming.
- [x] **[Task 5 - MUST] Athlete Profile Integration & Verification (`src/athlete_profile.py`, `src/stress_test_optimizer.py`):**
  - *System Benefit:* Configurable athlete constraints and regression prevention via test harness.
  - *Implementation Spec:* Added hybrid parameters to `AthleteProfile`, wired `run_hybrid_athlete_demo()` into `main.py`, and added Scenario 11 in `stress_test_optimizer.py`.

## 7. Linked Empirical Experiments (If Applicable)
- Validated by: `src/stress_test_optimizer.py` (Scenario 11: Dual-Objective Hybrid Athlete Optimization).

## 8. Acceptance Gates (Definition of Done)
- [x] **Gate 1 (Solve Performance):** Highs solver converges in $< 0.50\text{s}$ ($0.182\text{s}$ verified).
- [x] **Gate 2 (Constraint Integrity):** Barbell share $\ge 60\%$, single-run volume $\le 32\%$, axial caps $\le 120/\text{day}$ and $\le 320/\text{wk}$, zero deadlift/run violations.
- [x] **Gate 3 (Split Diversity):** Canonical No-Good cuts successfully generate both Option A (Full-Body HLM) and Option B (Upper/Lower Split).
