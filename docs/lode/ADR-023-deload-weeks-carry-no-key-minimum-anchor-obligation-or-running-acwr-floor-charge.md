---
id: ADR-023
type: adr
domain: optimizer
title: "Deload weeks carry no key minimum, anchor obligation or running-ACWR floor charge"
status: ACTIVE
created_at: 2026-11-16
supersedes: []
validated_by: []
affects_services: ["control", "solver"]
one_liner: "Recovery/taper/race weeks solve with key min 0, halved Bike/Run/Swim min, anchors optional and no ACWR-floor Tier-1 charge; Tier-0 caps unchanged."
---

# `ADR-023`: Deload weeks carry no key minimum, anchor obligation or running-ACWR floor charge

## 1. Context & Problem
After `TASK-034` (deload structure relief) `EXP-007` still showed recovery weeks up to +68 TSS (`CURRENT_ATHLETE`) / +244 (unconstrained reference) and taper/race weeks up to +44 / +275 over target. Single-week ablations on the real library (re-solving recorded `WeekRecord` configs and carry-ins) found three independent Tier-1 charges, each minimised BEFORE the soft Tier-2 TSS goal:
1. **Key-session minimum >= 1** (`CURRENT_ATHLETE`): the cheapest hard session forces ~160-170 TSS in a 72-141 target week (key min 0 alone: weeks 8-39 within +10 of target; stride min 0 alone did not help).
2. **Mandatory anchors** (reference profile, whose caps can host a 180 min ride and 75 min run): `shorten_or_drop_anchor` forces them every week (floor ~350-400 vs 186-220 target; dropping the obligation alone: reference recovery +0..+3).
3. **Running-ACWR 0.80 floor waiver** (`ADR-019`, binary Tier-1 slack of weight 1): whenever any plan can hold `A_d >= 0.8 C_d` the solver pays for extra running instead of waiving, although a deload week is *defined* by acute < chronic load. Reference recovery solved 275/269/269 vs 186/203/220 with the carry-in, 187/203/223 with the floor removed (`nocarry` and `nofloor` ablations coincide; CTL-ramp ablation changed nothing).
The reference swim minimum of 2 (not scaled by `TASK-034`) additionally held its race week +48.

## 2. Considered Alternatives
1. **Option A (Rejected): retarget deload weeks to the floor.** The floors (166-275 TSS) are larger than the build week they follow in several weeks; it would defeat the recovery week.
2. **Option B (Rejected): loosen/rank the ACWR floor in every week.** The floor stays in build weeks (`ADR-019`); only deload roles are exempt.
3. **Option C (ADOPTED): remove each charge in deload roles only.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `control/periodization.py` (`apply_deload_relief`: key min 0, Swim joins Bike/Run halving; `relax_deload_anchors`; `relax_deload_acwr_floor`), `control/season.py` (applied to the week library and the carry-in handed to the solver; the carry handed to the next week is the original).
- **Locked Parameters / Contract:** roles recovery/taper/race only. Anchors stay in the library as ordinary optional candidates. `LoadState.acwr_min` is set to 0 for the solve (no floor row, no waiver); the running-ACWR ceiling, boundary rows, CTL ramp, run-minutes guard, every cap and every maximum are untouched.
- **Enforcement Mechanism:** `tests/test_season_periodization.py` (relief only in deload roles; short 14-week `CURRENT_ATHLETE` season, deload weeks <= target + 25); slow `tests/test_season_validation.py::test_full_matrix_season` asserts recovery and taper/race max over-target <= 25 TSS and 0 violations for all 8 seasons.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` matrix re-run: recovery max over +68/+244 -> +8/+4, taper/race +44/+275 -> +11/+14 (see `TASK-038` archive entry). 0 independent Tier-0 violations in all 8 seasons.
- **Supersedes:** None; completes `TASK-034`.

## 5. Consequences & Trade-Offs
- **Positive:** deload weeks are deload weeks on both profiles; the ACWR floor no longer pushes extra running into recovery weeks.
- **Negative / Operational Cost:** a deload week may now contain no key session / no long keystone session (OWNER DECISION, reported in the PR); a deload week may fall below acute:chronic 0.80 without a reported relaxation.
- **When to Revisit:** if the structure floors become a modelled Tier-2 trade-off, or `TASK-023` walk-jog candidates change the floor economics.
