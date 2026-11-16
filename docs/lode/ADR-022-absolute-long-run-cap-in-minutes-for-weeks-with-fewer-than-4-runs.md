---
id: ADR-022
type: adr
domain: safety
title: "Absolute long-run cap in minutes for weeks with fewer than 4 runs"
status: ACTIVE
created_at: 2026-11-16
supersedes: []
validated_by: ["EXP-007"]
affects_services: ["safety", "optimizer", "mpc"]
one_liner: "Weeks with <4 runs cap the longest run at max(30, prev longest+10) min (and 45 min in re-entry); >=4 runs keep the ADR-003 32% ratio."
---

# `ADR-022`: Absolute long-run cap in minutes for weeks with fewer than 4 runs

## 1. Context & Problem
`ADR-003` caps a single run at 32% of weekly run time, which is only satisfiable with >= 4 runs. `ADR-013` fell back to pigeonhole caps (40% for 3 runs, 60% for 2, 100% for 1), so for `CURRENT_ATHLETE` (1-3 runs per week, one session per day) the injury-prevention intent was vacuous: `EXP-007` found every week of every season had < 4 runs and 13-25 had exactly one, with no limit on that run's length. Owner decision (2026-11-16): below 4 runs, enforce an ABSOLUTE cap in minutes instead of the ratio.

## 2. Considered Alternatives
1. **Option A (Rejected):** Keep the pigeonhole shares. 100% for one run is no cap at all.
2. **Option B (Rejected):** A fixed global ceiling (e.g. 90 min), already rejected in `ADR-003` Option B; it also ignores progression (a 75 min run after 30 min runs).
3. **Option C (ADOPTED):** Progression cap + re-entry ceiling in minutes, applied only when the realized run count is < 4.

## 3. Decision & Production Invariant
- **Rule (Tier 0, hard):** if a week has fewer than 4 runs, every run `r` satisfies `run_minutes(r) <= L`, with `L = min(max(floor, prev_longest + step), reentry_ceiling if run CTL < reentry exit)`. Weeks with >= 4 runs are governed only by the 32% ratio (unchanged); the pigeonhole caps remain as before.
- **Defaults:** `long_run_step_min` 10, `long_run_floor_min` 30 (shortest library run, so week 1 is schedulable; week 1 has previous longest 0), `reentry_long_run_max_min` 45 for `CURRENT_ATHLETE` (None elsewhere). `L` is lifted to the shortest schedulable run so a run always fits. A rule with `long_run_step_min=None` and no ceiling is off (library/test default); the profile enables it.
- **Previous longest** is carried in `WeekCarryIn.prev_longest_run_min` (`control/carry.py`), held through run-free weeks so a rest week does not reset the ratchet. An isolated week (no carry-in) gets only the ceiling.
- **Linearisation:** the run-count indicator `z4` (1 iff >= 4 runs) already exists for the ratio rows; for each run candidate with `run_minutes > L` the row `x_{i,d} - z4 <= 0` forbids it unless the week has >= 4 runs. No big-M and no new binaries. Lift+run combos count toward the run count with their run leg (<= 20 min <= floor, so they are never capped).
- **Targets:** `safety_constraints._add_run_share_cap` / `resolve_long_run_cap`; both facades via `UnifiedWeekEngine`; `run_share_report` returns `regime` (`absolute` / `pigeonhole` / `ratio`), `abs_cap_min`, `longest_run_min`; independent check `perf.season_validation.check_week` rule `long_run_abs` (re-derives `L` from the previous week's actual schedule).
- **Enforcement:** `tests/test_long_run_cap.py`; the 8-season matrix must stay at 0 fallback and 0 violations.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` (problem), `tests/test_long_run_cap.py`, 8-season matrix (see TASK-039 archive entry).
- **Supersedes:** None. Refines `ADR-003` / `ADR-013` for < 4 runs; the 45 min re-entry ceiling is a conservative judgement (about 3 x 15 min continuous run/walk blocks; weekly re-entry minutes are separately capped by `ADR-012`) and is flagged for owner review.

## 5. Consequences & Trade-Offs
- **Positive:** A 1-3 run week can no longer hold an arbitrarily long run; long runs ramp at most 10 min/week.
- **Negative / Operational Cost:** Long-course (70.3/IM) long runs of 90+ min are reachable only through the 10 min/week ramp or with >= 4 runs; `TASK-040` library gaps interact. Week 1 of a season with a real running history is capped at the floor unless a starting longest run is supplied (queued).
- **When to Revisit:** After re-entry (run CTL >= 15) if the ramp proves too slow for race-specific long runs, or when the athlete's run frequency rises to >= 4.
