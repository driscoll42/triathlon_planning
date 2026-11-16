---
id: ADR-021
type: adr
domain: optimizer
title: "Re-entry weeks scale the structure minimums down when the full-week structure floor exceeds the TSS target"
status: ACTIVE
created_at: 2026-11-16
supersedes: []
validated_by: []
affects_services: ["control", "solver"]
one_liner: "Re-entry build weeks re-solve with halved, then zero, key/stride minimums only while solved TSS exceeds 1.15x target; Tier-0 caps never change."
---

# `ADR-021`: Re-entry weeks scale the structure minimums down when the full-week structure floor exceeds the TSS target

## 1. Context & Problem
`EXP-007` (`TASK-037`): `CURRENT_ATHLETE` re-entry build weeks (targets 96-160 TSS from `base_weekly_tss` 160 x 0.60..1.00) solved at a median 1.26-1.37x target (up to +82 TSS). Per-week ablation on the real library: the full-week structure minimums (key >= 2, strides >= 2, Bike/Run >= 2, <= 2 rest days) are Tier-1 slacks that the lexicographic solve minimises BEFORE the soft Tier-2 TSS goal, so together they set a TSS floor of 159-168 (early re-entry) to 214 (weeks 19-23) that a lower target can never pull down (solving with target 1 returns the same schedule). Stride sessions (-17 TSS), key sessions + rest days and Bike/Run frequency each contribute; halving all (and rest days <= 4) gives ~120-153, dropping key/stride gives 95-153.

## 2. Considered Alternatives
1. **Option A (Rejected): raise the re-entry targets to the structure floor.** Makes target == solved by relabelling: the owner's conservative 96 -> 160 ramp becomes 160-215 for 24 weeks, and the floor is not a number the profile knows (it varies 159-214 with carry-in state).
2. **Option B (Rejected): fixed per-week relief by `tss_factor`.** The floor is not monotone in the factor (week 9: 159, week 21: 214), so any fixed schedule is wrong for some weeks.
3. **Option C (ADOPTED): re-solve a re-entry base/build/overload week at relief level 1 then 2 while solved > 1.15 x target.** Level 1 is the `TASK-034` deload relief (each minimum halved rounded up, never below 1; max rest days 4); level 2 also drops key and stride minimums to 0 (profile comment: "0-1 key sessions during the first 4 re-entry weeks"). The relieved plan is kept only if it lowers TSS.

## 3. Decision & Production Invariant
- **Target Component / Path:** `control/periodization.py` (`apply_reentry_relief`, `REENTRY_RELIEF_ROLES`, `STRUCTURE_OVERSHOOT_TOL`), `control/season.py` (re-solve loop; the week summary's `Recalibration` carries `STRUCTURE_RELIEF_Ln (...)`).
- **Locked Parameters / Contract:** only structure MINIMUMS change (key, strides, Bike/Run frequency, max rest days); Strength frequency, heavy-strength count, every maximum and every Tier-0 cap are untouched. Applies only to the re-entry block, only in base/build/overload roles, only when the full structure overshoots by > 15%. Race-block weeks keep the full structure.
- **Enforcement Mechanism:** `tests/test_season_periodization.py::test_reentry_relief_levels_scale_minimums_only`, `::test_reentry_build_weeks_track_their_targets` (median solved/target in [0.9, 1.15]); `tests/test_season_validation.py` slow matrix stays 0 violations.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` seasons re-run: re-entry build-week median solved/target 1.4 -> 1.02 (see `TASK-037` archive entry).
- **Supersedes:** None. Extends the `TASK-034` deload relief to re-entry build weeks.

## 5. Consequences & Trade-Offs
- **Positive:** the re-entry ramp the owner approved is what the athlete is actually prescribed; no cap loosened.
- **Negative / Operational Cost:** a re-solve (up to two extra MILPs) in overshooting re-entry weeks; early re-entry weeks may carry 1 key session / 1 stride session instead of 2 (OWNER DECISION to confirm, see PR).
- **When to Revisit:** if the structure floor is made a modelled quantity (a Tier-2 trade-off instead of Tier-1 slacks), or `base_weekly_tss` is re-derived from the structure.
