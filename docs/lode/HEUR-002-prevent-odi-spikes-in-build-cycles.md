---
id: HEUR-002
type: heuristic
domain: safety
title: "Prevent Orthopedic Dissociation Index Spikes in Build Cycles"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
one_liner: "Never ramp weekly running mechanical strain by >10% week-over-week even when aerobic fitness allows higher volume."
---

# `HEUR-002`: Prevent Orthopedic Dissociation Index Spikes in Build Cycles

- **Target Subsystem / Path:** `closed_loop_optimizer.py:MesoCycleTargetsAndConstraints.max_weekly_run_mech_strain`.
- **Symptom / Failure Mode:** Athlete reports sudden Achilles tendinopathy or shin splints in week 6-8 of build phase despite low cardiovascular fatigue and positive HRV scores.
- **Root Physics / Mechanism:** Cardiovascular fitness ($\tau_m=35d$) adapts 3.4x faster than collagenous tendon/bone tissue ($\tau_c=120d$). As aerobic thresholds rise, athletes feel capable of absorbing high mileage, driving the Orthopedic Dissociation Index ($ODI$) above 1.35 and causing collagen micro-tearing before synthesis completes.
- **Permanent Operational Rule:** Never allow weekly running mechanical strain to increase by more than 10% between consecutive step-cycle loading weeks, regardless of how high aerobic CTL climbs. Maintain $ODI \le 1.35$.
- **Verification Check:** `python -c "from closed_loop_optimizer import MesoCycleTargetsAndConstraints; cfg=MesoCycleTargetsAndConstraints(); assert cfg.max_weekly_run_mech_strain <= 350.0"`.
