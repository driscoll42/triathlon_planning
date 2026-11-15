---
id: ADR-013
type: adr
domain: optimizer
title: "Enforce Three-Tier Lexicographic Constraint Hierarchy with Shared Tier-0 Safety Layer"
status: ACTIVE
created_at: 2026-11-15
supersedes: []
validated_by: []
affects_services: ["optimizer", "safety", "mpc"]
one_liner: "Tier-0 safety caps are hard in every engine (src/safety_constraints.py); targets relax only via named, reported Tier-1 slacks solved lexicographically."
---

# `ADR-013`: Enforce Three-Tier Lexicographic Constraint Hierarchy with Shared Tier-0 Safety Layer

## 1. Context & Problem
The TASK-008 tests showed safety rules being broken while the solver still reported success:
- **Soft safety caps.** `TriathlonMILPScheduler` gave the 32% single-run cap and the rolling 48h TSS/ANS/strain caps penalty slacks (`enable_soft_48h_slacks`). The solver paid the penalty and scheduled a 33.3% run.
- **Silent rewrites.** It also silently relaxed `rho` when there were fewer than 4 runs, rewrote frequency minimums it could not meet (and wrote the change into the caller's config), and de-anchored oversized anchors.
- **Missing rules in the hybrid engine.** `HybridAthleteMILPOptimizer` had no hard/easy adjacency, no 48h caps, no weekly neuromuscular cap, no frequency bounds, and no deadlift lockout against long runs. It applied the run cap only to `long_run` workouts. Its 610-TSS demo plans exceeded the 360 weekly neuro cap and put a deadlift the day before a long run.
- **Fallback.** When a week was infeasible, the season planner replaced it with a generic recovery config that ignored the athlete profile.

## 2. Considered Alternatives
1. **Option A (Rejected):** Patch each engine separately. The two engines would drift apart again, and they are due to merge (TASK-015).
2. **Option B (Rejected):** A single weighted objective mixing Tier-1 slacks (weight 1000) with Tier-2 goals. The 1% relative MIP gap scales with that weight, so Tier-2 quality degrades whenever a slack is active. Large weights also let large TSS deviations trade against session minimums.
3. **Option C (ADOPTED):** A shared Tier-0 builder, named integer Tier-1 slacks weighted lexicographically, and a two-phase solve.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/safety_constraints.py`, used by both engines in `src/closed_loop_optimizer.py`. The season planner no longer has a fallback.
- **Locked Parameters / Contract:**
  1. **Tier 0 (hard, never relaxed):** the following hold in every engine.
     - Day caps and facility/modality gating (pre-filtered); weekday single-session cap.
     - Rolling 48h TSS ≤ 295, ANS ≤ 700, mechanical strain ≤ 260.
     - Daily/weekly axial ≤ 120/320; daily/weekly neuro ≤ 130/360 (profile values override these).
     - Hard/easy: no two consecutive hard days. At most one heavy/long run per day, with ≥ 48h between them.
     - HEUR-004: no deadlift within ±24h of a threshold or long run. Unflagged heavy strength is treated as a deadlift.
     - Same-day antagonism: squat vs. high-torque bike or threshold/long run; heavy press vs. CSS swim.
     - Non-consecutive strength days.
     - Single-run share across all runs: ≤ 32% with ≥ 4 runs (ADR-003). A week with N < 4 runs gets an explicit pigeonhole cap (3 runs: 40%, 2 runs: 60%; `pigeonhole_run_share`). The applied cap is reported in each plan's `run_share`.
  2. **Tier 1 (integer slacks with names and plain-English messages, reported in `plan["relaxations"]`):** anchors, minimum sport frequency, minimum strides, heavy-strength count (floor 1), and minimum rest days (floor 1). Exceeding the maximum rest days is the cheapest relaxation. Maximum bounds stay hard.
     - Weight = `8^rank` in the athlete's `tier1_relaxation_order` (DECISION-006). No category can exceed 7 units of slack, so these weights are exactly lexicographic.
  3. **Lexicographic solve:** Phase A minimizes Tier-1 slack only (`mip_rel_gap 0`, `abs_gap 0.5`). That optimum is then locked with a row, and Phase B optimizes Tier 2 (`mip_rel_gap 1%`). Top-K cuts reuse the lock.
  4. **Explainer:** `explain_feasibility()` runs before every solve. It names oversized anchors, workouts excluded by axial/neuro caps, unmeetable sport minimums, too few distinct workouts, heavy-strength conflicts, and pigeonhole run counts. An infeasible Tier 0 returns `[]` together with `last_diagnostics`. The season planner records such a week as INFEASIBLE with its reasons and treats it as a rest week.
  5. **Immutability:** the scheduler deep-copies its config. `enable_soft_48h_slacks` is now a deprecated no-op.
- **Enforcement Mechanism:** `tests/test_safety_constraints.py` (isolated rule tests); `tests/test_stress_scenarios.py` (run-share, 48h/axial/neuro caps, HEUR-004, frequency/relaxation consistency, and config immutability across all scenarios).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** pytest (113 pass). Stress scenarios 1/2b/8 dropped from 33.3%/33.3%/32.4% single-run shares to ≤ 32%. Scenario 3 (oversized anchor) and 6a (pool closed, Swim (2,2)) now return plans with named relaxations instead of INFEASIBLE or silent rewrites. Hybrid scenario-11 TSS fell from 610 to 434 because the old plans broke the 360 weekly neuro cap and HEUR-004.
- **Supersedes:** None formally. This replaces the soft-slack "infeasibility recovery" described in `TriathlonMILPScheduler`'s former docstring. Do not reintroduce soft Tier-0 caps or profile-discarding fallbacks.

## 5. Consequences & Trade-Offs
- **Positive:** Safety rules cannot be traded for TSS. Every shortfall is named. Both engines share one rule set, which is a step toward TASK-015.
- **Negative / Operational Cost:** About 3× more run-share rows (one per count level). Phase A adds an extra solve (sub-second on current libraries). Weeks that cannot meet Tier 0 now surface as INFEASIBLE and need a human decision. Example: the athlete profile's axial cap of 65 excludes the library's only heavy lift, while the heavy-strength floor is 1.
- **When to Revisit:** When TASK-015 merges the engines, or if the pigeonhole caps (40%/60%) should be tuned by evidence.
