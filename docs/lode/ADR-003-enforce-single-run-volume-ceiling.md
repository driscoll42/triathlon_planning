---
id: ADR-003
type: adr
domain: safety
title: "Enforce Single-Run Volume Ceiling at 32 Percent of Weekly Running Duration"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
affects_services: ["safety", "optimizer", "mpc"]
one_liner: "Cap single run duration at <=32% of total weekly running volume in solver constraints to prevent bone stress injuries."
---

# `ADR-003`: Enforce Single-Run Volume Ceiling at 32 Percent of Weekly Running Duration

## 1. Context & Problem
In endurance triathlon training, disproportionately long runs account for the majority of bone stress injuries, tibial stress syndrome, and plantar fasciitis. When the optimizer attempts to satisfy total weekly running TSS on a weekend, it can inadvertently allocate 45-55% of weekly volume to a single long run.

## 2. Considered Alternatives
1. **Option A (Uncapped Duration):** Rely only on absolute daily caps (e.g. 140 min). Rejected because 140 min inside a 200 min weekly run budget represents 70% of weekly load, an extreme orthopedic hazard.
2. **Option B (Fixed Duration Ceiling e.g. 90m):** Rigid duration limit. Rejected because as total volume scales from 4 to 8 hours of running, a 90m ceiling prevents adequate long-run stimuli for half/full Ironman athletes.
3. **Option C (ADOPTED):** Dynamic proportional ceiling: $\forall d, \text{Run Duration}_d \le 0.32 \cdot \sum_t \text{Run Duration}_t$.

## 3. Decision & Production Invariant
- **Target Component / Path:** `closed_loop_optimizer.py:MesoCycleTargetsAndConstraints.max_single_run_vol_ratio`.
- **Locked Parameters / Contract:** Single run volume ratio is hard-locked to $\le 0.32$ in standard profiles and $\le 0.25$ in `RESTRICTED_RUN` mode.
- **Enforcement Mechanism:** Exact linear inequality constraint inside `TriathlonMILPScheduler._build_milp_model()`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Empirical orthopedic injury data and stress test scenario `Scenario 1: Baseline Nominal` in `stress_test_optimizer.py`.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** Mathematically eliminates excessive long-run orthopedic spikes while scaling naturally with weekly volume.
- **Negative / Operational Cost:** Forces the optimizer to distribute running volume across 3-4 days rather than 1-2 days.
- **When to Revisit:** When preparing ultra-distance trail athletes where single runs intentionally exceed 35% of weekly volume.
