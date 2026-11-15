---
id: ADR-011
type: adr
domain: physiology
title: "Integrate Closed-Loop Critical Power Retesting Engine with Dual Telemetry Triggers and Mesocycle Synchronization"
status: ACTIVE
created_at: 2026-11-10
supersedes: []
validated_by: []
affects_services: ["physiology", "mpc", "optimizer", "workouts"]
one_liner: "Retest CP every 8-12w in recovery deloads or via cardiac decoupling/EF triggers; enforce <=10% step jump caps and re-entry testing lockouts."
---

# `ADR-011`: Integrate Closed-Loop Critical Power Retesting Engine with Dual Telemetry Triggers and Mesocycle Synchronization

## 1. Context & Problem
Athletes often intuitively push to retest threshold power too frequently or artificially force their Critical Power (CP) upward under the misconception that elevating CP is required to "force" physiological development. In polarized/pyramidal 80/20 training, inflating CP shifts prescribed Zone 2 workouts into the moderate-intensity grey zone (Zone X / Zone 3, above $LT_1$), inducing chronic autonomic suppression, glycogen depletion, and elevated orthopedic strain.

Conversely, static CP assignments lead to undertraining over multi-month seasons as cardiovascular stroke volume and peripheral capillarization improve. A deterministic closed-loop mechanism is required to govern retesting cadence: balancing periodic mesocycle check-ins against data-driven physiological triggers (cardiovascular decoupling suppression and Efficiency Factor gains) while strictly forbidding high-intensity testing during deconditioned re-entry phases.

## 2. Considered Alternatives
1. **Option A (Fixed Arbitrary Testing Schedule, e.g. Every 4 Weeks):** Imposing maximal 3m/12m testing every mesocycle. Rejected because frequent maximal tests exact high autonomic/mechanical toll, disrupt training continuity, and risk tissue injury during build cycles.
2. **Option B (Unrestricted Telemetry Auto-FTP Jumps):** Automatically incrementing CP on every ride/run breakthrough (e.g. Garmin Auto-FTP). Rejected because single high-motivation bursts or un-cleared spikes contaminate baseline zones with anaerobic work ($W'$), violating `ADR-009`.
3. **Option C (ADOPTED):** Dual-Trigger Retesting Engine:
   - **Re-Entry Protection Guard:** Absolute lockout of all-out testing during initial re-entry ($\le 4\text{ weeks}$ bike, $\le 6\text{ weeks}$ run).
   - **Calendar Mesocycle Cadence:** Synchronized retesting every 8 to 12 weeks during recovery deload weeks.
   - **Data-Driven Telemetry Trigger:** Trigger testing or $+3\text{–}5\%$ calibration prompts when rolling aerobic decoupling drops below $3.0\%$ and 28-day Efficiency Factor ($EF$) trends $\ge +5.0\%$.
   - **Runaway Ceiling Guard:** Hard clamp capping single-test CP step increases at $\le 10\%$ to prevent injury and overreaching.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/athlete_profile.py`, `src/closed_loop_optimizer.py`, `docs/domains/physiology.md`.
- **Locked Parameters / Contract:**
  1. **Candidate Test Library:** Expose canonical benchmark test workouts (`BK_CP_TEST`, `RN_CP_TEST`, `SW_CSS_TEST`) adhering to the Monod-Scherrer Two-Point Work-Time protocol ($P_3 + P_{12}$).
  2. **Re-Entry Lockout:** `ClosedLoopMPCController.evaluate_cp_retest_triggers` locks testing during early re-entry.
  3. **Telemetry Adaptation Criteria:** Trigger flag requires at least two concordant signals:
     - Rolling aerobic decoupling ($Pw:HR$ or $Pa:HR$) $< 3.0\%$ across steady Zone 2 sessions $\ge 60\text{ min}$.
     - Rolling 28-day Efficiency Factor trend $\ge +5.0\%$.
     - $\ge 3$ consecutive breakthrough interval sessions completed with low RPE / suppressed HR.
  4. **Max Jump Cap:** Single retest step update capped at $\le 10.0\%$ via `apply_cp_retest_result`.
- **Enforcement Mechanism:** Unit tests in `test_athlete_profile.py` / `stress_test_optimizer.py` and MPC Tier 2 recalibration checks.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Monod-Scherrer work-time linearity; Seiler Polarized Training and aerobic decoupling ($Pa:HR < 5\%$ ceiling); Coggan Efficiency Factor formulations.
- **Supersedes:** None. Complements and operationalizes `ADR-009`.

## 5. Consequences & Trade-Offs
- **Positive:** Eliminates premature testing injuries; dynamically accommodates fitness gains without manual guesswork; prevents grey-zone overtraining caused by vanity threshold spikes.
- **Negative / Operational Cost:** Requires consistent heart rate and power telemetry streams to compute decoupling and Efficiency Factor metrics.
- **When to Revisit:** If athlete transitions to continuous blood-lactate biosensors or direct VO2 metabolic cart testing.
