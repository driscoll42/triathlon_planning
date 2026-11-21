---
name: post-workout-review
description: >-
  Review a completed workout (.fit file or recent date) using deterministic
  telemetry extraction plus sport-specific coaching rubrics (Run, Bike, Swim,
  Strength) and cross-sport physiological/orthopedic guardrails. Use whenever
  the user asks to review, analyze, or grade a workout, .fit file, or today's
  training session.
---

# Post-Workout Review Skill

Perform an invariant-compliant, cross-sport coaching review of a completed workout. **Never** parse `.fit` files manually or perform ad-hoc NP/TSS/SWOLF/zone arithmetic—always run the Python extractor first.

## Workflow

1. **Extract Deterministic Telemetry Brief:**
   Run `triplan.io.workout_review` for the target `.fit` file (read-only by default; add `--record` only if the user asks to save into `ActualsStore`):
   ```bash
   python -m triplan.io.workout_review --fit "<path/to/file.fit>" --format json
   ```
   Optional flags:
   - `--planned-code <CODE>` (and `--plan-csv <path>`) to compare against a scheduled workout.
   - `--mass-kg <kg>` to compute Run ECOR.
   - `--record` to persist the parsed activity into `data/actuals.json`.
   - For a **Strength** session review (not a `.fit` endurance file), run a 3-day window brief around the session date so lifting history and adjacent runs are included:
     ```bash
     python -m triplan.io.workout_review --date <YYYY-MM-DD> --days 3 --format json
     ```

2. **Load ONLY the Matching Sport Rubric:**
   Read the single relevant reference rubric for the session's sport:
   - Run: [references/run.md](./references/run.md)
   - Bike: [references/bike.md](./references/bike.md)
   - Swim: [references/swim.md](./references/swim.md)
   - Strength: [references/strength.md](./references/strength.md)

3. **Check Cross-Sport & Orthopedic Guardrails:**
   Inspect `cross_sport_context` in the brief against `docs/INVARIANTS.md` and `SPEC.md`:
   - Rolling 48h peaks vs. caps (`TSS <= 295`, `Strain <= 260`, `ANS <= 55` athlete / `120` charter).
   - `deadlift_run_collisions` (`HEUR-004` same-day or +1d lockout).
   - `moderate_intensity_rut_flag` (`IF > 0.78` on a planned easy session -> reclassify `hard_d = 1`).
   - `wellness.alpha_d` readiness scalar and `missing_streams` (never treat `None` as `0`).

4. **Deliver Concise Coaching Report:**
   Structure the response with:
   - **Execution Summary & Grade** (duration, TSS, strain, ANS, 7-zone polarity, planned-vs-actual delta if available).
   - **Sport-Specific Mechanics & Pacing** (interpret the rubric metrics directly from JSON).
   - **Cross-Sport & Recovery Impact** (48h load budget, lifting/run interactions, readiness `alpha_d`).
   - **Actionable Next Step** (keep plan as-is, swap/shorten tomorrow's session, flag Moderate-Intensity Rut, or note CP/CSS retest evidence).
