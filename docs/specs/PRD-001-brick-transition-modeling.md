---
id: PRD-001
type: prd
domain: optimizer
title: "Brick Transition Modeling and Same-Day Strength Stacking"
status: COMPLETED
created_at: 2026-11-08
updated_at: 2026-11-08
supersedes: []
superseded_by: "PRD-002"
depends_on: ["ADR-002", "ADR-003"]
affects_services: ["optimizer", "mpc", "workouts"]
one_liner: "Model same-day multi-discipline transitions and strength stacking within the HiGHS MILP constraint matrix."
---

# Engineering PRD: Brick Transition Modeling and Same-Day Strength Stacking (`PRD-001`)

## 1. Overview & Architectural Objective
Enable the HiGHS scheduling solver to explicitly place and constrain multi-session training days, specifically Saturday/Sunday Bike-to-Run (Brick) transitions and weekday evening resistance training stacked onto morning endurance quality sessions.

## 2. Problem Statement & Empirical Evidence
- **Current Bottleneck / Risk:** The current weekday scheduler limits daily assignments to a single workout ($\sum_w x_{w,d} \le 1$ on weekdays). This prevents modeling standard triathlon double-days (e.g. AM Tempo Swim + PM Lower-Body Strength).
- **Telemetry / Proof:** Currently, strength and brick sessions are either manually scheduled outside the solver or combined into synthetic composite workouts in `brick_workouts.csv`.

## 3. Target Goals (Measurable Outcomes)
- **[Performance / Efficiency]:** Solve time remains $\le 2.0\text{s}$ with multi-slot daily binary decision variables $x_{w,d,s}$ where $s \in \{\text{AM}, \text{PM}\}$.
- **[Security / Correctness]:** Hard constraint enforcing $\ge 6$ hours between morning threshold session and afternoon strength work.
- **[Reliability / Operability]:** Guaranteed zero strength sessions placed on designated easy/recovery days.

## 4. Non-Goals (Strict Negative Scope — DO NOT TOUCH)
- **DO NOT** modify the underlying 80/20 zone calculation formulas in `util.py` or `clean_file.py`.
- **DO NOT** introduce continuous non-linear rest curves; use discrete time slot indexing.

## 5. System Invariants & Guardrails (`docs/INVARIANTS.md`)
- Single run volume remains $\le 32\%$ of weekly running duration (`ADR-003`).
- Heavy strength sessions must never precede a heavy run session within 24 hours.
- Rolling 48-hour mechanical strain remains $\le 260.0$.

## 6. Deterministic Technical Requirements & Tasks
- [ ] **[Task 1 - MUST] Slot Dimension Expansion:**
  - *System Benefit:* Allows modeling discrete morning and evening workout slots per day.
  - *Implementation Spec:* Update `highs_optimization.py` decision variables to $x_{w,d,slot}$.
- [ ] **[Task 2 - MUST] Hard-Days-Hard Stacking Constraint:**
  - *System Benefit:* Guarantees recovery days remain 100% rest or light aerobic active recovery.
  - *Implementation Spec:* If $x_{\text{strength}, d, \text{PM}} = 1$, enforce $\text{HighDur}_{d, \text{AM}} > 0$.

## 7. Linked Empirical Experiments (If Applicable)
- Validated or gated by: `EXP-001`.

## 8. Acceptance Gates (Definition of Done)
- [ ] **Gate 1 (Rollback Ready):** Clean git commit before schema changes.
- [ ] **Gate 2 (Tests & Verification):** `stress_test_optimizer.py` passes all 5 baseline scenarios.
- [ ] **Gate 3 (Target Metric Verified):** HiGHS multiseed solver completes schedule generation with stacked strength in $< 2.0\text{s}$.
- [ ] **Gate 4 (Lineage & Docs Synced):** `python scripts/lodectx.py ingest docs/specs/PRD-001-brick-transition-modeling.md` and `lodectx.py stats` pass.

## 9. Open Questions (Halt Gate)
- [x] All architectural ambiguities resolved.
