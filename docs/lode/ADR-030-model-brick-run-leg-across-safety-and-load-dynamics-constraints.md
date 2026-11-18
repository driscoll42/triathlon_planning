---
id: ADR-030
type: adr
domain: safety
title: "Model Brick Run Leg Across Safety and Load-Dynamics Constraints"
status: ACTIVE
created_at: 2026-11-18
supersedes: []
validated_by: []
affects_services: ["library", "model", "solver", "control", "perf"]
one_liner: "Brick workouts carry explicit schema columns (brick_run_min, brick_run_strain, is_brick_run_hard) enforced across safety, ACWR/CTL, frequency, and meso goals."
---

# `ADR-030`: Model Brick Run Leg Across Safety and Load-Dynamics Constraints

## 1. Context & Problem
Previously (`EXP-008` Finding 5), `safety_constraints.run_minutes(w)`, `run_strain(w)`, `classify_workouts`, and `WorkoutTable` only counted standalone `"Run"` workouts and lift + easy-run combos (`cardio_sport == "Run"`). Any workout with `sport == "Brick"` contributed **0** run minutes and **0** run mechanical strain to single-run volume share (`ADR-003`), absolute long-run caps (`ADR-022`), weekly running strain caps, in-model EWMA running ACWR (`ADR-019`), re-entry run-minutes guards, 48h heavy-run separation, and `HEUR-004` deadlift $\pm 24\text{h}$ lockouts. In addition, `Brick` sessions were omitted from `load_dynamics._ctl_ramp_row` and `goals._meso_rows` sport splits, and no short (45–60 min) default bricks existed in `lib_default`.

## 2. Considered Alternatives
1. **Option A (Rejected): Parse `description` strings at solve time to infer Brick run legs.** Violates `ADR-014` (never infer flags or loads from text descriptions at solve time) and breaks deterministic schema validation.
2. **Option B (Rejected): Treat an entire Brick's duration and mechanical strain as running load.** Overstates orthopedic running impact on Bike->Run bricks (e.g. 45m Bike + 15m Run) and misclassifies Swim->Bike bricks (`SW_T1_BRICK`, 0m run) as runs.
3. **Option C (ADOPTED):** Add explicit canonical schema columns `brick_run_min`, `brick_run_strain`, and `is_brick_run_hard` (validated by Pandera + Pydantic, derived via `build.brick_run_loads`), map them to `CandidateWorkout` / `WorkoutTable`, wire them into `safety_constraints`, `load_dynamics`, `goals`, `session_structure`, `control/season.py`, and `season_validation.py`, and add `HEUR-008`-gated short default bricks (`BRICK_AE_45_30B_15R`, `BRICK_AE_60_45B_15R`, `BRICK_TH_60_40B_20R`).

## 3. Decision & Production Invariant
- **Target Component / Path:**
  - `src/triplan/library/schema.py` (`brick_run_min`, `brick_run_strain`, `is_brick_run_hard`), `src/triplan/library/build.py` (`brick_run_loads`, `EXISTING_BRICK_RUN_ZONES`, `SHORT_BRICK_SPECS`, `short_brick_rows`), `src/triplan/library/loader.py`, `src/triplan/model/candidates.py`.
  - `src/triplan/solver/safety_constraints.py` (`run_minutes`, `run_strain`, `classify_workouts`), `src/triplan/solver/engine/config.py` (`WorkoutTable.brick_ids`, `extra_members`, `members`, `run_minutes`, `run_strain`), `src/triplan/solver/engine/builders/{orthopedic,session_structure,load_dynamics,goals,calendar}.py`.
  - `src/triplan/control/season.py` (split Brick TSS across Bike/Run in `step_day`), `src/triplan/perf/season_validation.py` (`ctl_ramp` includes `"Brick"`).
- **Locked Parameters / Contract:**
  - Non-Brick workouts must have `brick_run_min == 0`, `brick_run_strain == 0`, and `not is_brick_run_hard`.
  - Brick workouts must satisfy `brick_run_min <= duration_min`, `brick_run_strain <= mechanical_strain`, and zero `brick_run_min` implies zero `brick_run_strain` and `not is_brick_run_hard`.
  - Bike->Run bricks (`brick_run_minutes > 0`) count toward both `Bike` and `Run` session frequency bounds and cannot share a calendar day with another standalone `Bike`, `Run`, `Brick`, or cardio combo.
  - Swim->Bike bricks (`brick_run_minutes == 0`, e.g., `SW_T1_BRICK`) are excluded from `S.runs`, `S.heavy_or_long_runs`, and `brick_ids`.
- **Enforcement Mechanism:** `tests/test_brick_run_leg.py`, `tests/test_workout_library.py`, `tests/test_stress_scenarios.py`, and `python -m triplan.perf.season_validation`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `tests/test_brick_run_leg.py` and the 8-season `season_validation` matrix (240 weeks, 0 fallback weeks, 0 Tier-0 violations; all 3 short bricks picked across the matrix under `HEUR-008`: `BRICK_AE_45_30B_15R` 110 picks, `BRICK_AE_60_45B_15R` 49 picks, `BRICK_TH_60_40B_20R` 5 picks).
- **Supersedes:** None. Extends `ADR-014` (canonical schema) and `ADR-019` (in-model load dynamics) to `Brick` sessions.

## 5. Consequences & Trade-Offs
- **Positive:** Closed-loop safety, ACWR, ADR-003/ADR-022 run caps, and `HEUR-004` deadlift lockouts hold faithfully when Bricks are scheduled, and short default bricks reduce season relaxations across `current` and `reference` profiles.
- **Negative / Operational Cost:** Any future `Brick` row authored in `data/workout_library.csv` must populate `brick_run_min`, `brick_run_strain`, and `is_brick_run_hard` explicitly via `brick_run_loads`.
- **When to Revisit:** If multi-sport bricks beyond Bike->Run (e.g., Swim->Run or Swim->Bike->Run) require separate swim-leg accounting in `WorkoutTable`.
