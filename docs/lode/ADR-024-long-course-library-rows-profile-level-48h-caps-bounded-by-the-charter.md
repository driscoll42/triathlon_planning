---
id: ADR-024
type: adr
domain: workouts
title: "Long-Course Library Rows, Profile-Level 48h Caps Bounded By The Charter"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: 2026-11-16
supersedes: []        # e.g., ["ADR-001", "EXP-002"] -> Automatically marks those nodes SUPERSEDED in SQLite!
validated_by: []      # e.g., ["EXP-005"]
affects_services: ["library", "physiology", "control"]  # e.g., ["core", "db"]
one_liner: "Long Z1/Z2 runs (90-150) and rides (210-360) live in the library; 48h TSS/strain caps are profile fields validated <= the charter ceilings, never looser."
---

# `ADR-024`: Long-Course Library Rows, Profile-Level 48h Caps Bounded By The Charter

## 1. Context & Problem
`EXP-007`: the default library stopped at a 75 min run and a 180 min ride, so 70.3 (long run 90+, ride 150+) and Ironman (long run 105+, ride 180+) seasons had no race-specific long sessions, and the season diagnosis could only say "not in the workout library". Separately, the rolling 48h aerobic-TSS (295) and mechanical-strain (260) caps were `MesoCycleTargetsAndConstraints` dataclass defaults, invisible in `AthleteProfile` / YAML (ANS 700 was a profile field with no ceiling check).

## 2. Considered Alternatives
1. **Option A (Rejected): raise the 48h caps for the long-course reference profile.** `GEMINI.md` section 2 makes 295 / 260 / 700 global hard ceilings; ablation showed raising them is what it would take to reach Ironman build targets, which is exactly why it is reported, not done.
2. **Option B (Rejected): drop every optional session longer than the day caps.** Perturbs HiGHS tie-breaking for the existing rows (165 min ride etc.), changing `CURRENT_ATHLETE` schedules for no gain.
3. **Option C (ADOPTED):** add the long rows via the shared build formulas, expose the caps as validated profile fields, and gate only long-course sessions that no day cap can host.

## 3. Decision & Production Invariant
- **Target Component / Path:** `library/build.py` (`LONG_RUN_SPECS`, `LONG_RIDE_SPECS`, `long_endurance_rows`), `scripts/migrations/m2026_11_16_long_course_library.py` (versioned, idempotent, schema-validated, atomic), `physiology/athlete_profile.py` + `config/athlete_profile.yaml` (`max_48h_aerobic_tss`, `max_48h_mech_strain`; validated with `max_48h_ans_cost` against `CHARTER_MAX_48H_*`), `control/periodization.py::drop_unhostable_long_sessions`, `control/feasibility.py` (`peak_48h`, binding-cap message).
- **Locked Parameters / Contract:** 8 `agent_authored`, `lib_default`, `needs_review` rows: runs 90 / 105 / 120 / 150 min, outdoor rides 210 / 240 / 300 / 360 min, 100% Z1+Z2 (zone7 ZX/Z3/ZY/Z4/Z5 = 0), long runs flagged `is_long_run` + `is_heavy_run` (HEUR-004, 48h separation, ADR-003, ADR-022), TSS/IF/NP from `power_tss`, flags from `endurance_flags`, loads from `endurance_loads`. A profile 48h cap must be in (0, charter ceiling] (295 / 260 / 700): tighter is allowed, looser raises `ValueError`. `CURRENT_ATHLETE` keeps 295 / 260 (ANS 450, tighter than the 700 ceiling). Anchors (180 min ride, 75 min run) are unchanged.
- **Enforcement Mechanism:** `tests/test_long_course_library.py` (rows, flags, formulas, caps, round trip, validation, engine rows, diagnosis, gating).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` addendum (matrix before/after): `CURRENT_ATHLETE` schedules byte-identical for sprint / Ironman; 0 fallback, 0 violations in all 8 seasons; reference recovery weeks within 6 TSS of target. Ablation (reference Ironman): raising only the 48h TSS cap (420) or the CTL ramp (20) leaves build weeks at 572; raising 48h TSS 420 + strain 380 + ANS 1000 lifts them to 619 and picks 210 min rides.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** race-specific long sessions exist for any profile whose day caps host them; the 48h caps are configurable per athlete without ever exceeding the charter; the season report names the binding 48h cap.
- **Negative / Operational Cost:** under the charter caps the Ironman build-week targets stay unreachable (rolling 48h ANS 700 binds at 690); long Z1/Z2 sessions are ANS-expensive (ANS weight e^(0.65(z-1)) charges Z2 1.9x per minute).
- **When to Revisit:** with `TASK-021` (ANS / strain unification) or if the owner revisits the charter 48h ceilings.
