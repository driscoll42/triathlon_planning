---
id: ADR-033
type: adr
domain: mpc
title: "Distance-aware long-ride keystone ladder in long-course builds"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: 2026-11-18
supersedes: []        # e.g., ["ADR-001", "EXP-002"] -> Automatically marks those nodes SUPERSEDED in SQLite!
validated_by: ["EXP-007"]      # e.g., ["EXP-005"]
affects_services: ["control"]  # e.g., ["core", "db"]
one_liner: "70.3/IM long-ride keystone weeks climb 180->210-300/360 min (BIKE_LC_OUT_*), one rung above the last ride, step-down on a dropped Bike anchor."
---

# `ADR-033`: Distance-aware long-ride keystone ladder in long-course builds

## 1. Context & Problem
`ADR-028` alternates a long-run and a long-ride keystone in 70.3 / Ironman race-block build / overload weeks, but the long-ride week kept the fixed 180 min `BIKE_DUR_OUT_DEF` anchor all season, so the longest ride never grew with the block even though `TASK-040` added the `BIKE_LC_OUT_210/240/300/360` rides.

## 2. Considered Alternatives
1. **Keep the 180 min anchor (Rejected):** no long-course bike progression; 70.3 / Ironman races need 3-6 h rides.
2. **Jump straight to the ladder rung (Rejected):** an unbounded jump above the last ride breaks the progression principle used for the run (`ADR-022`).
3. **Ladder + one-rung progression + step-down (ADOPTED):** mirrors the run keystone (`ADR-028`).

## 3. Decision & Production Invariant
- **Target Component / Path:** `control/periodization.py` (`KEYSTONE_RIDE_LADDER`, `KEYSTONE_RIDE_CODES`, `keystone_ride_rungs`, `keystone_ride_minutes`, `apply_keystone_week(..., ride_minutes)`), `control/season.py` (ride keystone setup, Bike step-down retry, `last_keystone_ride` / `prev_longest_ride`).
- **Ladder (index = ride-keystone weeks so far, clamped to the last rung):** 70.3 `180, 210, 210, 240, 240, 300`; Ironman `180, 210, 240, 300, 300, 360`. The rung is clamped to rungs the day caps can host and to one library rung above the longest ride actually scheduled the previous week (progression, never a jump). When the caps host no keystone ride (`CURRENT_ATHLETE`, 75 min), 180 min is kept so the `shorten_or_drop_anchor:Bike` diagnosis fires as before.
- **Anchor move:** in a ride week only the ride row of the chosen duration is `is_anchor`; other Bike rows and the Run anchor become optional. A library with no ride of that duration keeps its Bike anchors.
- **Step-down:** a ride keystone whose solve reports `shorten_or_drop_anchor:Bike` is retried one hostable rung lower; the first rung that keeps its anchor wins.
- **Never loosened:** every Tier-0 cap (48h TSS / ANS / strain, ACWR, ADR-003, ADR-022). `BIKE_LC_OUT_360` still needs a 360 min day cap, so `reference` (300 min Saturday cap) tops out at 300.
- **Enforcement:** `tests/test_alternating_keystones.py` (ladder, caps, anchor swap, slow reference 70.3 / Ironman progression).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` section 12 (reference seasons, 3.5 s / week). Ride keystone longest ride: 70.3 W3 180, W6 210, W9 210, W11 240, W14 240, W17 300; Ironman W3 180, W6 210, W9 240, W11-W19 300. 8 seasons / 240 weeks, 0 fallback, 0 violations; `CURRENT_ATHLETE` 100% violation-free with unchanged anchor behavior.
- **Supersedes:** None (extends `ADR-028`).

## 5. Consequences & Trade-Offs
- **Positive:** the long ride grows with the block; reference build-week solved/target 70.3 median 0.99 -> 1.02 (missed >10%: 3 -> 2 weeks), Ironman 0.95 -> 0.99 (5 -> 4 weeks).
- **Negative / Operational Cost:** Ironman worst under-target build week is worse (-139 -> -183 TSS, week 18), bound by the 48h ANS / TSS caps (`TASK-050` / `TASK-021`, reported, not fixed). Extra solves when a ride rung is dropped.
- **When to Revisit:** after `TASK-021` (ANS re-weighting), or if a profile gains a 360 min day cap.
