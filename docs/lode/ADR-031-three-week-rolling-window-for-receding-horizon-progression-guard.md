---
id: ADR-031
type: adr
domain: safety
title: "Three-week rolling window for receding-horizon progression guard"
status: ACTIVE
created_at: 2026-11-18
supersedes: []
validated_by: []
affects_services: ["control", "safety", "mpc"]
one_liner: "Tier-2 progression guard bounds target TSS and weekly run strain using the max of the last 3 solved weeks, preserving keystones after deload/ride weeks."
---

# `ADR-031`: Three-week rolling window for receding-horizon progression guard

## 1. Context & Problem
In `src/triplan/control/season.py`, the Tier-2 receding-horizon progression guard previously looked back at only the single most recent solved week (`solved_rows[-1]`):
- `target_tss` was clamped to `last_solved_tss * max_ramp` (`1.25`, or `1.15` in `masters_mode`).
- `cfg.max_weekly_run_mech_strain` was clamped to `max(last_mech * max_ramp, 150.0)`.

Under `ADR-028` alternating keystones in a 3:1 mesocycle (`build, build, overload, recovery`), consecutive long-run keystone weeks are separated by one long-ride keystone week and/or one recovery deload week (`lag = 2` or `3`). In `reference / 70.3` Week 4 (`recovery`), `Total_Mech_Strain` dropped to `144.6`, which clamped Week 5's `cfg.max_weekly_run_mech_strain` from `250.0` down to `180.8` (and clamped `target_tss` from `458` to `383`). That artificial clamp forced HiGHS to drop the Week 5 `75 min` long-run keystone anchor (`shorten_or_drop_anchor:Run`, `EXP-007` §11).

## 2. Considered Alternatives
1. **Option A (Rejected): Exempt long-run keystone weeks from the progression guard.** Removing the progression guard altogether on keystone weeks would allow unguarded TSS or strain jumps after an abnormal or multi-week low-volume stretch.
2. **Option B (Rejected): Keep the 1-week lookback (`solved_rows[-1]`).** Any 1-week deload or alternating ride-keystone week ratchets the run-strain cap below the profile's ceiling and drops the next build week's long-run keystone.
3. **Option C (ADOPTED):** Compute the progression guard over a rolling 3-week window (`PROGRESSION_GUARD_WINDOW = 3`) of solved weeks (`Solved_TSS > 0`) via `progression_guard_bounds()` in `src/triplan/control/periodization.py`, taking the max `Solved_TSS` and `Total_Mech_Strain` across the window, clamping `cfg.max_weekly_run_mech_strain` only when it exceeds `mech_cap` (never loosening above the profile ceiling), and recording `Run_Mech_Strain` in `season_summary_rows` for observability.

## 3. Decision & Production Invariant
- **Target Component / Path:**
  - `src/triplan/control/periodization.py`: `PROGRESSION_GUARD_WINDOW = 3` and `progression_guard_bounds(solved_rows, max_ramp, window=3, min_strain_floor=150.0)`.
  - `src/triplan/control/season.py`: Apply `progression_guard_bounds(season_summary_rows, max_ramp)` in `generate_closed_loop_season_schedule()` and record `"Run_Mech_Strain"` in `season_summary_rows`.
- **Locked Parameters / Contract:**
  - `max_ramp` remains `1.15` when `athlete_state.masters_mode` is `True`, else `1.25`.
  - `window = 3` spans up to 1 recovery week + 1 alternating ride-keystone week; weeks with `Solved_TSS <= 0` are excluded before slicing `[-window:]`.
  - `mech_cap = round(max(ref_mech * max_ramp, 150.0), 1)`, and `cfg.max_weekly_run_mech_strain` is only tightened (`if cfg.max_weekly_run_mech_strain > mech_cap`), never loosened above the profile ceiling (keeping `CURRENT_ATHLETE`'s `75.0` cap untouched).
  - No Tier-0 safety constraint (`ADR-003`, `ADR-019`, `ADR-022`, `ADR-024`, `HEUR-004`) is loosened.
- **Enforcement Mechanism:** `tests/test_alternating_keystones.py::test_progression_guard_window_preserves_post_recovery_and_post_ride_keystone` and `python -m triplan.perf.season_validation`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:**
  - `python -m pytest -q`: `362 passed, 8 deselected` (including unit and Week 1–5 integration coverage in `tests/test_alternating_keystones.py` verifying Week 5 of `reference / 70.3` hosts a `>= 75.0 min` keystone run with no `shorten_or_drop_anchor:Run` relaxation and `0` `check_season` violations).
  - `python -m triplan.perf.season_validation`: 8 seasons (240 total weeks across `current` and `reference` profiles for `sprint`, `olympic`, `70.3`, `ironman`), `0` fallback weeks, `0` Tier-0 violations; Week 5 of `reference / 70.3` and `reference / ironman` no longer drops `RUN_AE2_LONG`.
- **Supersedes:** None. Complements `ADR-028` (alternating keystones).

## 5. Consequences & Trade-Offs
- **Positive:** Preserves `ADR-028` long-run keystones immediately after 1-week deloads or alternating long-ride weeks without loosening any Tier-0 guardrail or profile ceiling.
- **Negative / Operational Cost:** A prolonged (>3-week) detraining or sickness gap still tightens the guard smoothly once the prior higher-load weeks age out of the 3-week window.
- **When to Revisit:** If `mesocycle_build_weeks + mesocycle_recovery_weeks` exceeds 4 weeks or multi-week deloads become standard, adjust `PROGRESSION_GUARD_WINDOW` accordingly.
