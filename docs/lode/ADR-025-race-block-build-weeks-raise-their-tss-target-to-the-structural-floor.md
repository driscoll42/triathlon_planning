---
id: ADR-025
type: adr
domain: optimizer
title: "Race-block build weeks raise their TSS target to the structural floor"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: 2026-11-16
supersedes: []        # e.g., ["ADR-001", "EXP-002"] -> Automatically marks those nodes SUPERSEDED in SQLite!
validated_by: []      # e.g., ["EXP-005"]
affects_services: ["control.season", "control.periodization"]  # e.g., ["core", "db"]
one_liner: "Race-block base/build/overload weeks solve at target = max(matrix target, structural floor); no structure relief there; deload/re-entry relief unchanged."
---

# `ADR-025`: Race-block build weeks raise their TSS target to the structural floor

## 1. Context & Problem
After `ADR-021` (re-entry) and `ADR-023` (deload) the remaining over-target weeks of `CURRENT_ATHLETE` were the race-block base / build / overload weeks: sprint weeks 25-31 solved 222-241 TSS against matrix targets 140-211 (ratio 1.03-1.57). The owner's committed weekly structure (2-3 key sessions, 2 strides, Bike/Run >= 2, strength incl. lift+cardio combos, one session per day) costs ~222-241 TSS; the structure minimums are Tier-1 slacks minimised before the soft Tier-2 TSS goal, so the target can never pull the week below that floor.

## 2. Considered Alternatives
1. **Scale the structure down like re-entry (`ADR-021` ladder) (Rejected):** owner decision: in race-block build weeks the structure is the plan; no sessions dropped.
2. **Raise `base_weekly_tss` / start factor globally (Rejected):** shifts every role and distance, also the deload and re-entry weeks tuned by `ADR-021/023`, and lifts reference profiles that have no floor problem.
3. **Raise the per-week target to the structural floor (ADOPTED).**

## 3. Decision & Production Invariant
- **Target Component / Path:** `control/season.py` (solve loop), `control/periodization.py` (`FLOOR_RAISE_ROLES`, `FLOOR_PROBE_TARGET`, `raised_target`), `control/feasibility.py` (`TARGET_RAISED_TO_STRUCTURE` roll-up).
- **Contract:** in a race-block base / build / overload week, with no perturbation, when the first solve overshoots its target by more than `STRUCTURE_OVERSHOOT_TOL` (1.15), the floor is probed (same week model, carry-in and library, TSS goal ~0, so the lexicographic solve returns the cheapest plan meeting Tier-0 + all Tier-1 structure; the floor is the smaller of that and the first solve), the target becomes `max(matrix target, floor)` and the week is re-solved at it. Recovery / taper / race weeks and the re-entry block are untouched. The probe runs only on overshooting weeks (+2 solves each; CURRENT_ATHLETE olympic season 32 s before and 30 s after). Tier-0 caps, the in-model CTL ramp, ACWR and 48h rows stay hard and may still cap the week.
- **Reporting:** summary columns `Matrix_Target_TSS` (before the raise) and `StructFloor` (blank = not probed), `Target_TSS` is the raised target; `Recalibration` carries `TARGET_RAISED_TO_STRUCTURE (old->new, structural floor F)`; `season_infeasibility_report` rolls the weeks up.
- **Enforcement:** `tests/test_season_periodization.py` (raised_target; 8-week sprint CURRENT_ATHLETE season: base/build/overload solved/target in [0.9, 1.15], deload targets unchanged).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `CURRENT_ATHLETE` olympic race block: build/overload ratios 1.57 / 1.43 / 1.37 / 1.49 / 1.16 / 1.11 / 1.27 / 1.26 -> 1.00 (see TASK-041 archive entry); 8-season matrix 0 fallback / 0 violations.
- **Supersedes:** None (extends `ADR-021`'s scope decision: re-entry scales the structure, the race block does not).

## 5. Consequences & Trade-Offs
- **Positive:** targets are honest, CTL progression is driven by the real structure, the "solved > target by 25%" message disappears for build weeks.
- **Negative:** race-block build targets are 222-241 TSS instead of 140-211, i.e. a steeper early ramp than the matrix implies (the ramp rows remain hard). The probe repeats a MILP solve on overshooting weeks.
- **When to Revisit:** if the profile's structure minimums or `base_weekly_tss` change (then the floor falls below the matrix target and nothing is raised), or after `TASK-021` re-weights strength TSS.
