---
id: ADR-012
type: adr
domain: mpc
title: "Lock Athlete Decision Record for Nov 2026 Re-Entry and Olympic Season"
status: ACTIVE
created_at: 2026-11-15
supersedes: []
validated_by: []
affects_services: ["mpc", "optimizer", "physiology", "safety", "workouts", "integrations"]
one_liner: "Athlete inputs DECISION-001..020 are settled; src/athlete_profile.py is the source of truth. Re-verify on 2027-05-15 (time budget, pool access)."
---

# `ADR-012`: Lock Athlete Decision Record for Nov 2026 Re-Entry and Olympic Season

## 1. Context & Problem
`docs/TASKS.md` held 20 blocking or shaping athlete decisions (`DECISION-001..020`). Without them, the optimizer ran on conflicting or placeholder inputs: three different swim CSS values, a 16-week Olympic placeholder, pool access on conflicting days, strength zeroed in recovery weeks, and CP values of unknown age. On 2026-11-15 the athlete answered all 20. Where they asked for a recommendation (001, 006, 012–014, 016, 017, 019), this ADR records the recommendation that was adopted.

## 2. Considered Alternatives
1. **Option A (Rejected):** Leave decisions inline in `TASKS.md`. Rejected because they are durable athlete facts, not engineering tasks, and they bloat the startup core.
2. **Option B (Rejected):** Hard-code the answers in the solver. Rejected because it breaks the single source of truth (`ADR-009`) and the answers will change in about 6 months.
3. **Option C (ADOPTED):** Record the answers here and encode them as typed fields in `src/athlete_profile.py`. The fields that the unified engine (`TASK-015..019`) must still read are listed in §3.

## 3. Decision & Production Invariant
**Target Component / Path:** `src/athlete_profile.py` (`AthleteProfile` / `CURRENT_ATHLETE`).

| ID | Decision (2026-11-15) | Profile encoding |
|---|---|---|
| 001 | No swimming since 2024-08. 2024 pool moving pace was ~2:18–2:27/100m, which puts CSS at ~2:12 then. Adding ~10 s/100m for more than 2 years off gives a **provisional CSS of 2:30/100m (40.0 m/min)**. This replaces 24.0 and 35.29 m/min. Do a 400/200 CSS test on the 2nd weekend after pool access returns. Default pool: 25 yd. | `swim_css=40.0`, `swim_pool_length="25y"` |
| 002 | **Olympic** is the next race. No date yet. Recommended: late Aug 2027, so the 16-week race block starts when pool access returns (~May 2027). The optimizer must also be validated on sprint, 70.3 and IM (`TASK-027`). | `target_race_date=None` |
| 003 | Confirmed no runs or rides for more than 90 days (last run 2026-07-13, last ride 2026-06-20). **No injury history**, so no orthopedic hard caps. | `orthopedic_status="HEALTHY"` |
| 004 | The 45–75 min/day budget is a **real ceiling for at least 6 months** (childcare), reviewed 2027-05-15. No AM/PM split days assumed, so the limit is 1 session/day. | `two_session_windows=[False]*7`, `weekday_single_workout_cap=60` |
| 005 | CP values are months old. Bike CP was tested **in aero** on the Neo 2T. **Provisional bike 135 W, run 200 W** (~10% / ~7% detraining discount; 2025 best 20-min power was 156 W). First tests follow `ADR-011` lockouts: bike Two-Point CP at the start of week 5, run CP at the week-8 deload. Until then, run easy runs by HR and the talk test. | `bike_cp=135`, `run_cp=200`, `thresholds_verified=False` |
| 006 | Safety first: Tier-0 caps are never relaxed. Sport priority: **Strength > Bike > Run > Swim**. Tier-1 relaxation order, first to last: weekly TSS → strides → Swim anchor/frequency → Run anchor/frequency → Bike anchor/frequency → rest days (never below 1) → heavy strength count (never below 1). | `sport_priority`, `tier1_relaxation_order` |
| 007 | **No pool for ~6 months.** After that, weekends only (Sat/Sun). | `facility_access["Swim"]` all False, `pool_open_days=[]` |
| 008 | Split chosen by the solver (`AUTO`). Rack available every day. Plates 2.5–45 lb (bar load up to ~400 lb, 5 lb jumps). Dumbbells 5–75 lb in 5 lb steps. **Squat and deadlift may share a session.** No trap bar. | `facility_access["Strength"]` all True, `barbell_*`, `dumbbell_increment_lbs`, `has_trap_bar=False` |
| 009 | Strength is **co-equal** with triathlon. Recovery, taper and race weeks keep **1 maintenance (non-heavy) session** instead of 0. | `strength_priority="COEQUAL"`, `recovery_week_strength_sessions=1` |
| 010 | **3:1** mesocycles (3 build weeks + 1 recovery week). | `mesocycle_build_weeks=3`, `mesocycle_recovery_weeks=1` |
| 011 | No fixed calendar commitments. | – |
| 012 | All rides on the trainer (Neo 2T). All runs outdoors by default. Treadmill is recommended on ice, in extreme cold or dark, and optionally for walk-run re-entry or intervals. On a treadmill, Stryd Model 17 `Air Power`=0, so power reads ~2–4% lower at the same speed. Steer by power, not belt speed, and tag `sub_sport=treadmill`. | `outdoor_bike_days=[]`, `default_run_surface="outdoor"` |
| 013 | **Canonical 80/20** (Fitzgerald, *80/20 Triathlon*; Seiler): measured as time-in-zone over endurance minutes only (strength excluded). Z1+Z2 ≥ 78% hard floor, 80% soft target (85–90% in re-entry/base). **Z3+ZY+Z4+Z5 ≤ 20% combined** (this corrects the current Z4+Z5-only rule). Zone X ≤ 5%, penalized toward 0. 2–3 key sessions/week (0–1 in the first 4 re-entry weeks). No "medium" grey-zone sessions. | `intensity_*`, `key_sessions_per_week` |
| 014 | Running ACWR ceiling **1.20 during re-entry** (run CTL < 15), then **1.25**. Re-entry also uses absolute minutes, because the ratio is unstable at near-zero chronic load: 60 min/week of run/walk is always allowed, and weekly run minutes rise by at most 15. | `max_running_acwr*`, `reentry_*`, `max_weekly_run_minutes_increase` |
| 015 | Convert mile-based long runs at **easy pace 14:30/mi** (2026 median was 15:08/mi at HR 143). Update this as fitness returns. | `easy_run_pace_min_per_mile=14.5` |
| 016 | The 14 custom library rows and `recommended_workout_legos.csv` were written by agents, not taken from the 80/20 library. Treat them as **provisional**. Replace Trap Bar DL with conventional DL. Pull-up bar, bands and a Nordic anchor still need confirming. | `TASK-028` |
| 017 | Raw completed activity FIT files already exist: `data/garmin/activities/fit_files/` (2,020 files, 2013–2026). `data/fit_files/` holds 80/20 workout *definitions*. No athlete action needed. | `TASK-024` input |
| 018 | One device per sport, no dual-recording: Edge 530 for bike; Enduro 3 for run, swim and strength. Swim 2 is retired. The 300 s dedup rule stays for historical data. | `recording_device` |
| 019 | **Garmin Connect** is authoritative for completed activities (raw FIT with Stryd developer fields). TrainingPeaks holds the *plan* only. | `completed_activity_source`, `planned_workout_target` |
| 020 | TrainingPeaks push mode: **dry run**. | `trainingpeaks_push_mode="dry_run"` |
| 021 | **Lift + easy cardio combo:** a strength session may be followed right away, in the same window, by ≤20 min of strictly Z1/Z2 Bike or Run. Lift comes first. After heavy lower-body lifting, Bike only (no impact on a loaded spine, per `HEUR-004`). The pair counts as one window toward the day's cap. This is an exception to the 6h AMPK/mTOR rule (`INVARIANTS.md`). | `allow_lift_cardio_combo`, `combo_cardio_*` |
| 022 | Equipment: pull-up bar ✔, treadmill ✔, Nordic anchor ✔, resistance bands ✘. Banded rows become DB rear-delt fly or prone Y-T-W. | `has_pullup_bar`, `has_treadmill`, `has_nordic_anchor`, `has_resistance_bands=False` |

**Enforcement Mechanism:** These profile fields are **not yet read by the solver** and must be wired in by the unified engine work: the 80/20 definition (013), ACWR caps and minute floors (014), relaxation order (006), the 3:1 season matrix (010) and two-window gating (004). Tracked in `TASK-016`, `TASK-018`, `TASK-019` and `TASK-026`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Garmin history in `data/garmin/activities/activities_summary.json` (2,020 activities): swim paces 2023–24, max 20-min bike power by year, run pace and HR by year. Athlete answers dated 2026-11-15.
- **Supersedes:** None. Operationalizes `ADR-005` (3:1), `ADR-009`/`ADR-011` (CP protocol, retest lockouts) and `ADR-010` (devices).

## 5. Consequences & Trade-Offs
- **Positive:** One coherent athlete model. Provisional thresholds are deliberately conservative, keeping Z2 below LT1.
- **Negative / Operational Cost:** With 1 window/day and ≥1 rest day there are 6 windows/week. Strength 3× + Bike 2× + Run 2× fits only through the `DECISION-021` lift + easy cardio combo, which the engine must model as a compound candidate (`TASK-019`).
- **When to Revisit:** 2027-05-15 (pool access, time budget), after the first CP or CSS tests, or when a race date is set.
