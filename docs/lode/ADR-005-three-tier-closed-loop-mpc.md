---
id: ADR-005
type: adr
domain: mpc
title: "Implement Three-Tier Closed-Loop Model Predictive Control with Daily Readiness Scaling"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
affects_services: ["mpc", "optimizer"]
one_liner: "Structure scheduling across Tier-1 daily morning readiness, Tier-2 weekly receding horizon, and Tier-3 mesocycle step cycles."
---

# `ADR-005`: Implement Three-Tier Closed-Loop Model Predictive Control with Daily Readiness Scaling

## 1. Context & Problem
Static multi-month training plans fail rapidly in practice due to accumulated autonomic fatigue, illness, poor sleep, or life schedule disruptions. Static plans either force an exhausted athlete into high-injury workouts or fall apart completely when missed sessions throw off downstream periodization.

## 2. Considered Alternatives
1. **Option A (Static 16-Week Pre-Computed Schedule):** Generate the entire season once. Rejected because static plans cannot accommodate biological readiness fluctuations or life disruptions.
2. **Option B (Reactive Heuristic Rulebook):** Hardcoded conditional checks (e.g., if missed workout, push to tomorrow). Rejected because shifting workouts blindly cascades into consecutive hard days or volume spikes.
3. **Option C (ADOPTED):** 3-Tier Closed-Loop Model Predictive Control (MPC) with daily morning readiness scaling ($R_t \in [0.70, 1.10]$) and weekly receding-horizon MILP re-solving.

## 3. Decision & Production Invariant
- **Target Component / Path:** `closed_loop_optimizer.py:ClosedLoopMPCController`.
- **Locked Parameters / Contract:**
  - Tier 1: Daily Morning Readiness Scalar $R_t \in [0.70, 1.10]$ computed from HRV RMSSD, sleep, and soreness.
  - Tier 2: Weekly Receding-Horizon re-optimization using actual executed load history.
  - Tier 3: Mesocycle 3:1 step cycles with 15-25% loading reductions on recovery weeks.
- **Enforcement Mechanism:** `ClosedLoopMPCController.step_day()` and `replan_weekly_schedule()`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Control theory benchmarks demonstrating zero acute overload violations over 16-week simulation runs in `stress_test_optimizer.py`.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** Dynamically protects athlete from overtraining syndrome while preserving season macrocycle progression.
- **Negative / Operational Cost:** Requires daily biometric inputs or sensible defaults when wearables are unavailable.
- **When to Revisit:** When automated bidirectional syncing with Garmin/Whoop/Oura cloud APIs is integrated.
