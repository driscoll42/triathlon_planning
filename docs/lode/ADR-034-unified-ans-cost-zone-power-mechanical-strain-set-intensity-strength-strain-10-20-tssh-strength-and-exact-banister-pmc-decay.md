---
id: ADR-034
type: adr
domain: physiology
title: "Unified ANS cost, zone-power mechanical strain, set-intensity strength strain, 10-20 TSS/h strength and exact Banister PMC decay"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: 2026-11-19
supersedes: []        # e.g., ["ADR-001", "EXP-002"] -> Automatically marks those nodes SUPERSEDED in SQLite!
validated_by: ["EXP-007"]
affects_services: ["physiology", "library", "solver"]
one_liner: "ANS, strain, strength TSS are computed once in physiology/load_model.py; Banister PMC uses alpha=1-exp(-1/tau); 48h ANS cap 120 (charter) / 55."
---

# `ADR-034`: Unified ANS cost, zone-power mechanical strain, set-intensity strength strain, 10-20 TSS/h strength and exact Banister PMC decay

## 1. Context & Problem
`TASK-021`. ANS cost had three incompatible definitions (the `SPEC.md` 7-zone table, `e^(0.65 (z-1))` in `library.build`, hand-entered hybrid values about 5x lower), so a Z2 minute cost 1.9 Z1 minutes and the 700 cap acted as a volume cap (`TASK-050`: a 240 min Z1/Z2 ride = 372 ANS). Mechanical strain stacked constants (`1.5 x max(TSS,15) x dur/45 x 2.5` for strength) and the libraries disagreed (a heavy squat session was 65 in one, 28 in another). Strength was given 42-58 endurance TSS/h. `step_day` used `alpha = 2/(N+1)`, halving the PMC time constants (42 days at 100 TSS/day read CTL 86.5, exact 63.2), and `total_ctl` excluded strength while `metabolic_fitness_state` included it.

## 2. Considered Alternatives
1. **Keep the exponential ANS formula, re-tune the cap (Rejected):** contradicts `SPEC.md` / charter weights and keeps easy minutes costing.
2. **Run strain beta = 2 (Rejected):** current code is already about IF^3 for runs; beta = 2 loosens every high-intensity run in a Tier-0 cap. beta = 3 is kept, flagged a sensitivity-bounded prior (`EXP-006` rule 3).
3. **Strength strain `p^2` (Rejected):** volume-heavy light DB rows exceeded the 260 cap (816); `p^4` with per-limb reps keeps them 80-220.
4. **Unified model computed from zone minutes / set specs (ADOPTED).**

## 3. Decision & Production Invariant
- **Target Component / Path:** `physiology/load_model.py` (single source), `library/build.py` (`ans_cost`, `endurance_loads`, `brick_run_loads`, `recompute_load_columns`), `scripts/migrations/m2026_11_19_unify_load_model.py`, `physiology/state.py`, `solver/engine/builders/load_dynamics.py`, `perf/season_validation.py`.
- **ANS:** `sum_z w_z t_z + 0.35 max(0, D - 90)`, `w = (0,0,0.7,1.0,1.4,1.8,3.5)` over Z1,Z2,ZX,Z3,ZY,Z4,Z5; alactic strides weight 0. No typed-in ANS values.
- **Strain:** `kappa sum_z t_z p_z^beta`: run (100/60, 3), bike (10/60, 2.2), swim (5/60, 3) reproduce the old closed forms for a uniform session; brick = run leg (run model) + rest (bike model); strength `kappa_s sum_e c_e sets reps p^4` from `STRENGTH_SET_SPECS`, `kappa_s` anchoring the four `TASK-033` heavy variants at mean 145.8. Strength TSS 10-20/h.
- **Banister:** `ALPHA_CTL = 1 - e^(-1/42)`, `ALPHA_ATL = 1 - e^(-1/7)`, `M(t)`/`C(t)` use `1 - e^(-1/tau)`. `ALPHA_ACUTE/CHRONIC` stay `2/(N+1)` (published ACWR convention). `total_ctl`/`total_atl` and the in-model CTL-ramp row integrate all-sport TSS incl. strength.
- **Recalibrated caps (matched exclusion, `scripts/calibrate_ans_caps.py`):** 48h ANS charter 700 -> **120**, `CURRENT_ATHLETE` 450 -> **55** (old caps excluded 0.9-4.7% / 27-34% of 48h windows already within the 295 TSS / 260 strain caps; the new caps exclude the same share). 48h TSS 295 and strain 260 unchanged. `max_weekly_ctl_ramp` 7.0 -> **3.75** and masters 5.0 -> **2.75** (exact CTL moves ~1.9x slower per TSS, so the same weekly TSS ramp is permitted).
- **Enforcement:** `tests/test_load_model.py` (weights, strides, surcharge, closed-form equivalence, 63.2 CTL, strength in `total_ctl`, every library row recomputes to its stored loads, strength 10-20 TSS/h), existing cap / carry / season tests.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` season matrix (see `docs/archive/TASKS_COMPLETED.md`, `TASK-021`); full `pytest`. `EXP-006` (calibration against telemetry) is still `QUEUED`: only the exact-identity Banister change is empirical-free; `beta`, `gamma`, `D0`, class weights are priors.
- **Supersedes:** None (refines `ADR-004` / `ADR-009` load math; replaces the `e^(0.65 (z-1))` costing and `2/(N+1)` PMC decay, which must not be re-proposed).

## 5. Consequences & Trade-Offs
- **Positive:** one definition per load quantity; the ANS cap binds intensity, not easy volume (`TASK-050`: 240 min Z1/Z2 ride 372 -> 52.5); strength no longer inflates CTL; CTL / ATL are the textbook Banister values.
- **Negative / Operational Cost:** CTL, TSB and run CTL values shift (reported CTL ~ half-speed; run re-entry exit at run CTL 15 is reached later); heavy hybrid / default strength rows are far heavier in strain than their old hand-set values (`STR_MS1_HEAVY_DEF` 245 of 260) and bricks' whole-session strain is lower (run-leg ACWR strain unchanged). `library` CSV (gitignored) must be re-migrated.
- **When to Revisit:** after `EXP-006` fits `beta`, `gamma`, `D0`, zone midpoints; with `TASK-022` (ODI uses `M`/`C` constants) and `TASK-058`.
