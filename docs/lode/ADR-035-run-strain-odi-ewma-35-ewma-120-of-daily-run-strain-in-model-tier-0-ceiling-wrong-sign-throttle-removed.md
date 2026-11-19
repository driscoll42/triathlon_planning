---
id: ADR-035
type: adr
domain: safety
title: "Run-strain ODI: EWMA_35 / EWMA_120 of daily run strain, in-model Tier-0 ceiling, wrong-sign throttle removed"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: 2026-11-19
supersedes: []        # e.g., ["ADR-001", "EXP-002"] -> Automatically marks those nodes SUPERSEDED in SQLite!
validated_by: []      # e.g., ["EXP-005"]
affects_services: ["physiology", "control", "solver"]
one_liner: "ODI^run = EWMA_35 / EWMA_120 of the SAME daily run strain, <= 1.35 (1.20 masters), an in-model per-day row; no post-hoc ODI throttle."
---

# `ADR-035`: Run-strain ODI: EWMA_35 / EWMA_120 of daily run strain, in-model Tier-0 ceiling, wrong-sign throttle removed

## 1. Context & Problem
`TASK-022`. `ODI = M(t) / C(t)` divided an EWMA (tau 35 d) of ALL-SPORT aerobic TSS by an EWMA (tau 120 d) of RUN mechanical strain: a dimensional mismatch (TSS vs strain units; bike / swim / strength inflate `M`) with no steady-state meaning (the ratio read 1.13 -> 2.44 on a flat plan; `EXP-007`: 4-6 for `CURRENT_ATHLETE`, 1.9-2.6 reference). The season loop then shrank `max_weekly_run_mech_strain` whenever `ODI > 1.35`, but cutting run strain lowers `C(t)` and leaves `M(t)` (mostly non-run) untouched, so the throttle raised ODI: a wrong-sign feedback loop that compounded each week. `ADR-032` / `TASK-062` patched the symptom (skip the throttle in run-keystone weeks).

## 2. Considered Alternatives
1. **Keep `M/C`, rescale `M` to strain units (Rejected):** needs an invented TSS -> strain conversion per sport; still not a ratio of one quantity, so "1.0 = steady state" never holds.
2. **Drop ODI, rely on in-model ACWR + CTL ramp (Rejected):** both already exist (`ADR-019`) but are 7 d / 28 d and weekly horizons; the charter ODI guardrail (35 d vs 120 d, `HEUR-002`) targets slow collagen lag that they do not see. Removing a charter guardrail needs owner approval.
3. **`ODI^run = EWMA_35(S_run) / EWMA_120(S_run)` as a state, a reported metric AND an in-model per-day linear row (ADOPTED).** Same daily run strain in both EWMAs, so the index is dimensionless, reads 1.0 at steady state, > 1 only while run load outpaces the collagen matrix, and falls when run strain is cut (right sign).

## 3. Decision & Production Invariant
- **Target Component / Path:** `physiology/state.py` (`run_strain_fast` / `run_strain_slow`, `ALPHA_ODI_FAST`, `alpha_odi_slow()`), `physiology/athlete_profile.py` + `config/athlete_profile.yaml` (`initial_run_strain_fast/slow`), `solver/engine/builders/load_dynamics.py` (`_odi_rows`), `solver/engine/carry_in.py` (`LoadState.odi_*`), `control/carry.py` (`odi_eps_floor`, `GLOBAL_ODI_MAX`, `MASTERS_ODI_MAX`), `control/season.py`, `control/mpc.py`, `control/feasibility.py`, `perf/season_validation.py`.
- **Locked Parameters / Contract:** `F`: tau 35 d, `S`: tau 120 d (210 d masters), both `1 - e^(-1/tau)` (exact, `ADR-034`), both fed `daily_run_mech`; `ODI^run = F / max(S, 1)`; limit **1.35** (**1.20** masters, charter unchanged). In-model Tier-0 row per day `d`: `F_d - rho S_d <= eps` (same unrolled-EWMA construction as the running ACWR row, rhs clamped at 0 so an already-overshooting start state is not charged to the week; `eps` = one smallest library run from a detrained state, so the 60 min/week floor stays admissible, `ADR-012`). A day's row is emitted only when it can bind: the hard weekly run-strain cap bounds the week's strain mass, so if `cap x max coefficient <= rhs` the row is provably slack and skipped (an always-emitted slack row perturbed HiGHS tie-breaking and moved a time-limited season test's week 3 from 149 to 178 TSS). No floor row (ODI < 1 is detraining, covered by the ACWR floor). The season loop has **no** ODI strain-cap throttle; the `process_weekly_execution_and_recalibrate` backstop (actuals, `TASK-025`) keeps its `ODI > 1.35 or ACWR > 1.30` trigger, now right-sign. Profile seeds `initial_run_strain_fast/slow` (`CURRENT_ATHLETE` 6 / 9, detrained ODI0 0.67) are priors, not measurements (`EXP-006`).
- **Enforcement Mechanism:** `tests/test_odi.py` (dimensional consistency, all-sport TSS cannot move ODI, exact tau, run-strain cut lowers ODI, MPC backstop sign, carry-in constants, in-model row blocks a breaching run / admits a small one / is emitted only when it can bind, independent checker `run_odi`), `check_season` re-derives the row for every solved week; 8-season matrix (`-m slow`).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** season probes with the throttle removed and BEFORE the row existed (`triplan.perf.season_validation.run_one`): max ODI^run `current/olympic` 1.00, `current/ironman` 1.13, `reference/olympic` 0.93, `reference/ironman` 0.93 (old definition: 4-6 / 1.9-2.6), 0 violations, 0 fallback weeks. The in-model ACWR rows already hold the ramp well inside 1.35, so the ODI row is a free Tier-0 backstop; see `docs/archive/TASKS_COMPLETED.md` (`TASK-022`) for the post-row matrix.
- **Supersedes:** None formally (`HEUR-002`'s 10% w/w guidance and `ADR-032`'s keystone guard stay); it retires the `M(t) = EWMA_35(all-sport TSS)` definition from `ADR-004` / `ADR-034`'s consequences and the `season.py` ODI throttle. Do not re-propose an all-sport-TSS numerator or an ODI throttle that shrinks the denominator's own input.

## 5. Consequences & Trade-Offs
- **Positive:** a dimensionally consistent, interpretable index; the wrong-sign runaway cannot recur; run-keystone weeks no longer need a special-case bypass; the ODI ceiling is a guaranteed per-day property of every solved week.
- **Negative / Operational Cost:** `AthletePhysiologicalState.metabolic_fitness_state` / `connective_tissue_state` and profile keys `initial_metabolic_state` / `initial_connective_tissue_state` are renamed (`run_strain_fast/slow`, `initial_run_strain_fast/slow`); `Recalibration` no longer reports `SAFETY_RECALIBRATION_APPLIED` from the season loop. A sustained run ramp of more than about 3.4 %/week reads ODI > 1.35 in the long run (`(1 + 120 r) / (1 + 35 r)`); the ACWR 1.25 and 25 % guard keep real plans below it.
- **When to Revisit:** once `TASK-025` ingests actual run strain (re-fit seeds and the limit against real data, `EXP-006`), or if a season ever shows the ODI row binding (it is meant to be slack).
