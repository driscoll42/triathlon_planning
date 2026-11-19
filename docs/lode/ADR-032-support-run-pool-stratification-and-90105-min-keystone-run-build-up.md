---
id: ADR-032
type: adr
domain: safety
title: "Support-run pool stratification and 90/105-min keystone run build-up"
status: ACTIVE
created_at: 2026-11-18
supersedes: []
validated_by: []
affects_services: ["safety", "optimizer", "mpc", "control"]
one_liner: "Stratify 8020 easy runs <=70m (keeping RRe9), align 48h heavy-run spacing to >=70m/anchors, allow 6 runs for 90+m keystones, and guard run keystones from ODI throttle."
---

# `ADR-032`: Support-run pool stratification and 90/105-min keystone run build-up

## 1. Context & Problem
Even after `ADR-031` (`TASK-061`) preserved `max_weekly_run_mech_strain = 250.0` across recovery and ride-keystone weeks, `90 min` (`RUN_LC_Z2_90`, strain `68.8`) and `105 min` (`RUN_LC_Z2_105`, strain `80.3`) keystone runs were still dropped or stepped down in `reference / 70.3` and `reference / ironman`. A 4-part binding stack prevented hosting `281.25–328.13` weekly run minutes under the `ADR-003` `32%` single-run ceiling and `250.0` weekly run-strain cap:
1. `prune_8020_candidates` (`solver/pruning.py`) spent `easy` Run slots on `70–150 min` `8020_library` runs already covered by the canonical keystone ladder, crowding out low-strain `<= 60 min` Z1/Z2 support runs (`RRe9`, `60 min`, strain `25.0`).
2. `classify_workouts` (`solver/safety_constraints.py`) put any `Run` with `long_run == 1` (`duration >= 60.0`)—and any `Brick` with `run_minutes >= 60.0`—into `heavy_or_long_runs`, imposing 48h separation and $\pm 24\text{h}$ heavy-strength lockout on non-anchor `60 min` easy support runs (`RRe9`), contradicting `schema.py` (`is_heavy_run >= 70 min`).
3. `sport_freq_bounds["Run"] = (4, 5)` capped weeks at 5 runs (including lift+easy-run combos), making `>= 328.1 min` (`105 min / 0.32`) infeasible without exceeding `250.0` strain.
4. `control/season.py` applied the uncalibrated `pre_odi > odi_limit` run-strain throttle (`TASK-022`, driven by Bike/Swim TSS inflating $M(t)$) even when running ACWR was normal (`pre_acwr <= acwr_cap`), slashing `max_weekly_run_mech_strain` to `146–190` in later build weeks.

## 2. Considered Alternatives
1. **Option A (Rejected): Loosen the `ADR-003` `32%` single-run share ceiling or `250.0` weekly run-strain / `260.0` 48h strain caps.** Violates `GEMINI.md` §2 hard orthopedic guardrails.
2. **Option B (Rejected): Remove `RUN_AE2_LONG` / `RUN_LC_Z2_*` keystone step-up in `70.3` / `ironman`.** Leaves long-course build blocks capped at `75 min` runs.
3. **Option C (ADOPTED):** Fix the 4-part support-run and attribution stack without loosening any Tier-0 safety limit:
   - Add `attribute_keystone_run_week()` (`control/feasibility.py`) for one-row-at-a-time attribution of `90+ min` keystone blockers.
   - Stratify `8020_library` `easy` Run pruning (`solver/pruning.py`) across `<= 70.0 min` when canonical long runs exist, retaining the lowest-strain `~60 min` Z1 support run (`RRe9`).
   - Align `classify_workouts()` (`solver/safety_constraints.py`) so `heavy_or_long_runs` covers `is_heavy_run`, `heavy_run`, or `long_run` that is `>= 70.0 min` or `is_anchor` (and `Brick` run legs `brick_run_hard` or `>= 70.0 min`).
   - Add `apply_keystone_run_support()` (`control/periodization.py`) raising `Run` max frequency from `5` to `6` only in `keystone == "run"` weeks with `ks_run >= 90.0` and `>= 5` two-session windows; skip the `pre_odi > odi_limit` throttle in `control/season.py` on `keystone == "run"` weeks while `pre_acwr <= acwr_cap`.

## 3. Decision & Production Invariant
- **Target Component / Path:**
  - `src/triplan/control/feasibility.py`: `KEYSTONE_RUN_ROWS`, `attribute_keystone_run_week()`.
  - `src/triplan/solver/pruning.py`: `prune_8020_candidates()` (`<= 70.0 min` easy-run stratification + `RRe9` retention).
  - `src/triplan/solver/safety_constraints.py`: `classify_workouts()` (`heavy_or_long_runs` `>= 70.0 min` / `is_anchor` alignment).
  - `src/triplan/control/periodization.py` & `src/triplan/control/season.py`: `apply_keystone_run_support()` and `pre_acwr <= acwr_cap` guard around the ODI throttle in `keystone == "run"` weeks.
- **Locked Parameters / Contract:**
  - All Tier-0 limits (`ADR-003` 32% share, `ADR-022`, `ADR-019` ACWR, `ADR-024` 48h caps, `HEUR-004` deadlift lockout, `250.0` profile run strain ceiling) remain hard and untouched.
  - `CURRENT_ATHLETE` re-entry weeks (`block == "reentry"`) and `control/mpc.py` Scenario 10 are untouched.
- **Enforcement Mechanism:**
  - `tests/test_single_run_diagnosis.py::test_keystone_run_attribution_names_binding_row_without_mutating`
  - `tests/test_8020_milp_integration.py::test_prune_8020_retains_rre9_support_run_and_drops_long_8020_easy_runs`
  - `tests/test_safety_constraints.py::test_classification_excludes_non_anchor_60m_easy_run_from_heavy_or_long_runs`
  - `tests/test_alternating_keystones.py::test_apply_keystone_run_support_only_raises_freq_on_90plus_run_keystone`
  - `tests/test_alternating_keystones.py::test_reference_peak_keystone_reaches_90_and_105_min`
  - `python -m triplan.perf.season_validation`

## 4. Empirical Evidence & Validation Lineage
- **Validated By:**
  - `python -m triplan.perf.season_validation`: all 8 seasons (240 weeks across `current` and `reference` profiles for `sprint`, `olympic`, `70.3`, `ironman`) solved with `0` fallback weeks and `0` Tier-0 violations; `reference / 70.3` hosts `90.0 min` keystones across peak build/overload weeks, and `reference / ironman` hosts `105.0 min` keystones with `0` dropped run anchors in build/overload weeks.
  - `tests/test_alternating_keystones.py`: `reference / 70.3` reaches `>= 90.0 min` by W5 and `reference / ironman` reaches `>= 105.0 min` by W7 with `0` `check_season` violations.
- **Supersedes:** None. Builds on `ADR-022`, `ADR-028`, and `ADR-031`.

## 5. Consequences & Trade-Offs
- **Positive:** Unlocks `90 min` (`70.3`) and `105 min` (`ironman`) long-run keystones using low-strain Z1/Z2 support runs while strictly preserving every Tier-0 orthopedic guardrail.
- **Negative / Operational Cost:** Relies on `pre_acwr <= acwr_cap` to guard against the uncalibrated ODI throttle (`TASK-022`) in `keystone == "run"` weeks until `TASK-022` replaces ODI with a dimensionally consistent metric.
- **When to Revisit:** When `TASK-021` (load-model unification) and `TASK-022` (ODI fix/replacement) land.
