---
id: ADR-020
type: adr
domain: physiology
title: "Sport-specific quality q_i classifies hard and key sessions; lift+easy combos are compound candidates; canonical 80/20 is a hard engine rule"
status: ACTIVE
created_at: 2026-11-16
supersedes: []
validated_by: []
affects_services: ["solver", "library"]
one_liner: "Hard/key = minutes-based q_i, not IF; lift+easy combos are compound candidates; Z1+Z2>=78% and Z3+ZY+Z4+Z5<=20% are hard rows in both facades."
---

# `ADR-020`: Sport-specific quality q_i classifies hard and key sessions; lift+easy combos are compound candidates; canonical 80/20 is a hard engine rule

## 1. Context & Problem
`ADR-012` (`DECISION-013`, `DECISION-021`, `DECISION-004`) fixed the athlete's session structure, but the engine did not model it. (a) "Hard" came from a library flag whose precursor was a raw `IF >= 0.85` test; swim IF starts at ~0.795, so easy swims looked hard, and the flag was never re-derived from the zone minutes the model schedules. (b) The hybrid time-in-zone band capped only `Z4+Z5` (Z3 and ZY escaped) with minute-scale soft slacks, and the meso facade had no TiZ rule beyond a 5-zone Tier-2 goal. (c) A lift followed by short easy cardio (the only way 3 lifts + 2 rides + 2 runs fit 6 windows) could not be expressed, because every constraint keys on `workout.sport`. (d) Key-session counts, Hard-Days-Hard stacking and AM/PM windows (`two_session_windows`) were not read at all.

## 2. Considered Alternatives
1. **Option A (Rejected): Keep classifying by IF with per-sport IF thresholds.** IF scales are not comparable across sports and mislabel mixed sessions.
2. **Option B (Rejected): Model a combo as two workouts tied by a same-window indicator.** Doubles columns, needs a coupling row per pair/day, and lets the solver count the pair as two sessions.
3. **Option C (Rejected): Strict Tier-0 Hard-Days-Hard `sum heavy <= sum hard`.** Infeasible whenever no hard session can legally share the lifting day (squat vs high-torque bike, time caps, no hard sessions), which makes the heavy-strength floor of `ADR-013` unreachable.
4. **Option D (ADOPTED):** minutes-based sport-specific quality `q_i`; combos as compound candidates (`sport` stays `Strength`, cardio component in `cardio_*` fields); each structure rule is its own builder function with an `EngineOptions` switch; canonical 80/20 as hard rows.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/triplan/solver/engine/session_quality.py`, `engine/builders/session_structure.py`, `engine/builders/intensity_structure.py` (`add_canonical_8020`), `src/triplan/library/compound.py`, `engine/engine.py` (pre-filter pipeline, AM/PM output), `safety_constraints.py` (`run_minutes`, `run_strain`, combo-aware `runs`).
- **Locked Parameters / Contract:**
  - `q_i = sum_z w_z * minutes_z` over (Z1, Z2, ZX, Z3, ZY, Z4, Z5), weights `(0, 0, 1/6, 1/3, 2/3, 1, 1)` (Z4-equivalent minutes; the library rule "15 min Z3+ or 5 min Z4+" is exactly `q = 5`). Strides' alactic Z5 is excluded. Incidental allowance: `q <= q_noise + q_noise_per_hour * hours` (defaults 2 and 1) counts as easy. `is_hard = q >= q_hard` (default 5). Grey zone `0 < q < q_hard` is removed before the model is built (anchors and tests never). Key = hard, anchor, or long (Run >= 70, Bike >= 120 min). Overridable per sport through config key `session_quality`. `is_hard_endurance` is re-derived from `q_i` at engine construction, never from IF or a stale flag.
  - Pipeline order in `UnifiedWeekEngine.__init__`: grey-zone drop -> `q_i` hard classification -> combo generation -> dominance pruning -> `WorkoutTable`.
  - One hard endurance session per day in both facades. Key sessions `key_sessions_per_week` (default `[2, 3]`): upper bound hard, lower bound the Tier-1 slack `reduce_key_sessions`. Strides: `min_stride_sessions` default 2 as the single Tier-1 `drop_stride_count` slack.
  - Hard-Days-Hard: on days with `two_session_windows[d]`, `sum_{heavy lower} x <= k_d = sum_{hard endurance} x`, only on days where stacking is physically possible, with a per-day named slack `unstack_heavy_strength`. `double[d] <= two_session_windows[d]` (default all True). Plan output carries `am_pm` slots and day lists are ordered AM -> PM (hard endurance AM + lift PM; lift AM with an easy PM session; gap `am_pm_min_gap_h`, default 6).
  - Combos (`DECISION-021`): `sport = "Strength"`, `cardio_sport` Bike or Run, `cardio_minutes <= 20`, strictly Z1/Z2, only Bike after heavy lower-body lifts. One session per window, full duration against the day cap, counts toward the cardio sport's Tier-1 frequency min and max; the run leg (`run_minutes`, `run_strain`) feeds the single-run share cap, weekly run strain cap, carry-in ACWR and run-minutes rows (`TASK-018`); at most one Bike (Run) carrier per day.
  - Canonical 80/20 over endurance minutes (strength excluded, combo cardio included): `Z1+Z2 >= 0.78 T` and `Z3+ZY+Z4+Z5 <= 0.20 T` HARD in both facades; `Z1+Z2 >= 0.80 T` soft (`w_8020_low`), Zone X soft toward 0 (`w_zone_x`). The meso 5-zone `target_zone_pcts` stays a Tier-2 goal; hybrid `tiz_band` is off.
  - Day-asymmetric rule audit (`ADR-018`): a non-palindromic `two_session_windows` disables the week-reversal symmetry row.
- **Enforcement Mechanism:** `tests/test_session_structure.py`, `tests/test_stress_scenarios.py`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** full pytest green (see `docs/archive/TASKS_COMPLETED.md`, `TASK-019`). Stress suite: every MILP scenario still solves; canonical 80/20 and 2-3 keys hold in every stress plan; hybrid scenario 11 rank-1 objective unchanged (60.68).
- **Supersedes:** None. Refines `ADR-009` (7-zone scale) and `ADR-012`/`ADR-013` (structure rules are Tier-1 named slacks; the 80/20 floor and ceiling are hard rows).

## 5. Consequences & Trade-Offs
- **Positive:** Hard/key classification is sport-comparable and auditable; 80/20 is enforced identically by both facades (hybrid Z3/ZY gap closed); the no-double-day, lift + easy cardio week is representable.
- **Negative / Operational Cost:** Combos add 2-3 columns per lift; the library flag `is_hard_endurance` is no longer the engine's source of truth (heavy-run, long-run, high-torque flags still are). Grey-zone pre-filtering removes real library workouts (31 of 556 with defaults).
- **When to Revisit:** If `q_hard` needs athlete calibration after a CP/CSS retest (`ADR-011`); if the unified durability scale (`TASK-023`) changes combo durability.
