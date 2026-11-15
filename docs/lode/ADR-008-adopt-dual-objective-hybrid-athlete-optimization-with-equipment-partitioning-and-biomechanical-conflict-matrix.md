---
id: ADR-008
type: adr
domain: optimizer
title: "Adopt Dual-Objective Hybrid Athlete Optimization with Equipment Partitioning and Biomechanical Conflict Matrix"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
affects_services: ["optimizer", "mpc", "safety"]
one_liner: "Enforce exact linear hybrid MILP with equipment set partitioning, axial load caps, and biomechanical conflict exclusions."
---

# `ADR-008`: Adopt Dual-Objective Hybrid Athlete Optimization with Equipment Partitioning and Biomechanical Conflict Matrix

## 1. Context & Problem
Classical triathlon training schedules treat strength training as an auxiliary or unmodeled afterthought. When athletes concurrently pursue endurance goals (Swim, Bike, Run) and maximal barbell/dumbbell strength (Squat, Bench, Deadlift, Overhead Press), naive scheduling creates severe physiological and orthopedic failure modes:
1. **Lumbar Shear Catastrophe:** Heavy deadlifts produce up to $18.45\text{ kN}$ compressive and $3.28\text{ kN}$ shear load on L4/L5. Stacking or placing a heavy deadlift within 24 hours of high-impact threshold or long running causes lumbar erector failure and acute disc injury.
2. **Molecular Signaling Interference (AMPK vs. mTORC1):** Endurance training activates AMPK (inhibiting protein synthesis), whereas resistance training activates mTORC1. Uncoordinated same-day scheduling without a $\ge 6\text{h}$ intra-day separation blunts hypertrophy and strength gains.
3. **Equipment Asymmetry:** Athletes often possess an Olympic barbell at a commercial facility and a home gym capped at $75\text{ lb}$ dumbbells. Failing to partition movements forces inappropriate loading paradigms (e.g., trying to execute heavy bilateral leg training on light dumbbells).
4. **Split Incoherence:** Unconstrained optimization blends incompatible split archetypes (e.g. mixing Texas Method full-body workouts with Upper/Lower split workouts in the same week).

## 2. Considered Alternatives
1. **Option A (Rejected — Sequential Heuristic Post-Pass):** Solve the endurance schedule first, then greedily insert resistance workouts into rest days.
   - *Reason Rejected:* Violates the "Hard-Days-Hard" principle by turning easy endurance days into high-strain lifting days, causing chronic autonomic nervous system (ANS) non-functional overreaching. Furthermore, it cannot enforce joint weekly axial load ceilings ($X_{\text{wk}} \le 320$) across endurance and lifting.
2. **Option B (Rejected — Non-Linear / NLP Modeling):** Model continuous fatigue interaction decay curves via non-linear solvers (e.g. Ipopt / SciPy `minimize`).
   - *Reason Rejected:* Non-convex optimization suffers from local minima, slow convergence ($> 30\text{s}$), and inability to enforce discrete binary workout assignment invariants with mathematical guarantees.
3. **Option C (ADOPTED — HiGHS MILP with Exact Linearizations & Split Cuts):** Formulate the problem as a deterministic Mixed-Integer Linear Program in HiGHS (`HybridAthleteMILPOptimizer`), using exact linear ratio transformations, binary split coherence cuts, Canonical No-Good Integer Cuts, and hard biomechanical conflict matrices.

## 3. Decision & Production Invariant
The primary hybrid optimizer is standardized in `src/closed_loop_optimizer.py` as `HybridAthleteMILPOptimizer`:
- **Equipment Partitioning:** Enforce $\ge 60\%$ weekly strength set volume on Olympic barbell compound movements via exact linear ratio constraint:
  $$\sum_{d=1}^7 \sum_{i \in \mathcal{W}_{\text{STR}}} \left((1 - \beta_{\text{BB}}) V_i^{\text{BB}} - \beta_{\text{BB}} V_i^{\text{DB}}\right) x_{i,d} \ge 0 \quad (\beta_{\text{BB}} = 0.60)$$
  Dumbbells capped at $75\text{ lb}$ are strictly allocated to unilateral assistance and accessory hypertrophy.
- **Biomechanical Conflict Prevention Matrix:**
  1. *Deadlift vs. Heavy/Long Run:* Absolute same-day exclusion ($x_{i,d} + x_{j,d} \le 1$) and mandatory adjacent-day lockout ($x_{i,d} + x_{j,d+1} \le 1$ and $x_{i,d+1} + x_{j,d} \le 1$). Stacking with swimming is preferred for hydrostatic spinal decompression.
  2. *Squat vs. High-Torque Bike / Threshold Run:* Absolute same-day exclusion ($x_{i,d} + x_{j,d} \le 1$).
  3. *Heavy Press vs. CSS Threshold Swim:* Absolute same-day exclusion ($x_{i,d} + x_{j,d} \le 1$) to prevent rotator cuff and subacromial impingement.
  4. *Strength Frequency & Rest:* Maximum 2 workouts/day, non-consecutive strength days ($\sum_{i \in \mathcal{S}} x_{i,d} + \sum_{i \in \mathcal{S}} x_{i,d+1} \le 1$).
- **Axial Spinal & Neuromuscular Ceilings:**
  - Daily axial load $X_d \le 120.0$; weekly axial load $\sum_d X_d \le 320.0$.
  - Daily neuromuscular load $N_d \le 130.0$; weekly neuromuscular load $\sum_d N_d \le 360.0$.
- **Split Archetype Coherence & Canonical No-Good Cuts:** Binary variable $z_{\text{split}} \in \{0, 1\}$ strictly selects between Option A (Full-Body HLM / Texas Method) and Option B (Upper/Lower Split). The solver yields secondary distinct Pareto solutions via Canonical No-Good cuts:
  $$\sum_{(i,d) \in S_A} x_{i,d} - \sum_{(i,d) \notin S_A} x_{i,d} \le |S_A| - 1$$
- **Polarized Time-in-Zone Invariant:** Exact linear constraints enforce $\ge 78\%$ weekly aerobic duration in $Z_1/Z_2$ and $\le 20\%$ in $Z_4/Z_5$.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `src/stress_test_optimizer.py` Scenario 11 ("Dual-Objective Hybrid Athlete Optimization"). Option A (Full-Body HLM) solves in $0.182\text{s}$ with objective value $-40.70$. Option B (Upper/Lower) solves in $0.155\text{s}$. Both solutions achieve $100\%$ compliance across all 4 movement patterns ($\ge 10$ sets), $0$ axial or neuromuscular violations, and strict 24h deadlift-run spacing.
- **Supersedes:** Extends and formalizes single-discipline optimizer constraints previously specified in `ADR-002` and `ADR-003`.

## 5. Consequences & Trade-Offs
- **Positive:** Mathematically proves absence of overtraining and conflicting muscular recruitment; enables athletes to concurrently pursue Ironman/triathlon endurance and 1,000-lb barbell club strength safely.
- **Negative / Operational Cost:** Requires candidate workouts to carry axial, neuromuscular, and movement pattern metadata; un-tagged custom workouts must be enriched via `enrich_workouts_from_df()`.
- **When to Revisit:** Revisit if the athlete acquires heavier home dumbbells ($>100\text{ lb}$) allowing bilateral home compound loading, or if integrating continuous velocity-based training (VBT) sensor feedback into Tier 2 MPC.
