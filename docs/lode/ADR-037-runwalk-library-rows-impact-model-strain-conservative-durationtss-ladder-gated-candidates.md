---
id: ADR-037
type: adr
domain: workouts
title: "Run/Walk Library Rows: Impact-Model Strain, Conservative Duration/TSS, Ladder-Gated Candidates"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: 2026-11-20
supersedes: []
validated_by: ["EXP-010"]
affects_services: ["library", "control"]
one_liner: "RUN_RW_* rows take strain from run_walk_mechanical_strain, count walking as Z1 running in duration/TSS, and are offered only at the WALKJOG_LADDER rung that allows them."
---

# `ADR-037`: Run/Walk Library Rows: Impact-Model Strain, Conservative Duration/TSS, Ladder-Gated Candidates

## 1. Context & Problem
`TASK-058`. `CURRENT_ATHLETE` is in re-entry (run CTL < 15) and bound by the running-ACWR and weekly run-strain rows; the shortest candidate runs were continuous 15-30 min (`EXP-008`). Run/walk sessions are the standard return-to-run tool, but the canonical schema has zone minutes, one `mechanical_strain`, one `neuromuscular_load`, one `axial_load` and no walk component. A hand-typed lower load for a 1:1 session would be an unmeasured number that loosens the in-model ACWR / strain rows (`GEMINI.md` §2). `TASK-023` (`ADR-036`) supplied the impact model and the ladder; the rows were left to this task.

## 2. Considered Alternatives
1. **Add walk columns to the schema (Rejected).** Every consumer (`candidates`, `safety_constraints`, engine builders, pruning, CSV) would change to carry a number the model already derives from two minute counts; no solver row needs the split.
2. **Type a reduced strain per row (Rejected).** An invented number; exactly what `EXP-008` refused.
3. **Rows with model-derived strain, otherwise conservative encoding, ladder-gated offering (ADOPTED).**

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/triplan/library/build.py` (`RUN_WALK_SPECS`, `run_walk_split`, `run_walk_strain`, `run_walk_rows`, `recompute_load_columns`), `src/triplan/control/periodization.py::filter_walkjog_workouts`, its single call in `control/season.py`, `scripts/migrations/m2026_11_19_run_walk_rows.py`.
- **Locked Parameters / Contract:**
  - A `RUN_RW_*` row is a `Run` row; walked minutes are Zone 1 minutes. `mechanical_strain = load_model.run_walk_mechanical_strain([run_min,0,...], walk_min)`; neuro / axial follow it by the ordinary non-heavy-run rule (0.40 / 0.30 x strain).
  - `duration_min` is the whole session and `tss` is the aerobic TSS of all minutes at Zone 1 run intensity: the run-minute guard, `ADR-022` longest-run cap, run share and run-minute ramp see walking as running. Only strain (hence neuro / axial) is relieved; the strain stays strictly above the run minutes alone and strictly below the continuous run of the same length.
  - Rows are `agent_authored`, `lib_default`, `needs_review` until `EXP-006` calibrates the walk dose. They are not `lib_hybrid`, anchors or tests.
  - `filter_walkjog_workouts(candidates, rung)` only removes candidates: none at rung 0; at rung `s` only rows with run share <= the rung's and length <= the rung's `session_min`.
- **Enforcement Mechanism:** `tests/test_run_walk_rows.py` (strain == impact model, conservative duration / TSS, 100% low intensity, parse validity, gate semantics), `tests/test_load_model.py` (library coherence knows the run/walk exception), `recompute_load_columns` idempotence; the 8-season matrix (`check_season`).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-010`: every row picked in all four `CURRENT_ATHLETE` seasons, single-run weeks 5-6 -> 3, raw running ACWR 1.45-1.46 -> 1.41, 0 fallback / 0 violations in 8 seasons.
- **Supersedes:** None. Resolves `EXP-008` Finding 6 and the "no library rows" note in `ADR-036`.

## 5. Consequences & Trade-Offs
- **Positive:** a detrained athlete gets 20-30 min sessions at 60-75% of the strain of continuous running; no solver row, bound or schema column changed; the ladder is enforced, not only reported; reference (trained) seasons are unaffected (rung 0).
- **Negative / Operational Cost:** the walk-aware strain exists only in `build.py` (`RUN_WALK_SPECS` is keyed by code), so a new run/walk row must be added there, not by editing the CSV; `data/workout_library.csv` is gitignored, so the migration must be re-run on a fresh checkout. The walk dose is a prior (x2 safety factor) and the 3:1 / 4:1 rungs (35 min) have no dedicated row.
- **When to Revisit:** after `EXP-006` (fit the walk dose against actuals and drop the safety factor), if a ladder rung needs a row at 3:1 / 4:1, or if `acwr_eps_floor` is re-derived (`ADR-036`).
