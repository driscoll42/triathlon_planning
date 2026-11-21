# Strength Post-Workout Diagnostic Rubric

Grounded in `docs/INVARIANTS.md`, `ADR-008`, `ADR-012`, `HEUR-004`, and `src/triplan/control/mpc.py`.

## 1. Data Source & Re-Entry Context
- Structured strength metrics come from `data/lifting/parsed_lifting_history.json` (`PPL Routine.xlsx` pipeline), not Garmin `.fit` strength rep counts.
- **Re-Entry Phase Check (`CURRENT_ATHLETE.reentry_phase`):** While `reentry_phase == True`, athlete profile caps are tightened (`max_daily_axial_load = 65.0`, `max_weekly_axial_load = 160.0`, `max_daily_neuro_load = 75.0`, `reentry_heavy_strength_sessions = 0` when enforced). Post-re-entry charter ceilings are `X_day <= 120.0`, `X_wk <= 320.0`, `N_day <= 130.0`, `N_wk <= 360.0`.

## 2. Equipment & Movement Pattern Balance (`ADR-008`)
- **Olympic Barbell Share (`equipment_sets.barbell_share_vs_db`):** Target `>= 60%` (`0.60`) of weekly working sets on barbell compound movements; dumbbells (`<= 75 lb/hand`) are allocated to unilateral assistance and hypertrophy.
- **4-Pattern Coverage:** Track working sets across `Anterior_Chain`, `Posterior_Chain`, `Push`, and `Pull` (charter target `>= 10` sets/week post-re-entry; `CURRENT_ATHLETE.target_movement_sets` = `4 / 4 / 5 / 5` during re-entry).
- **Split Coherence (`z_split`):** Verify sessions follow either Full-Body HLM / Texas Method OR Upper/Lower without ad-hoc mixing.

## 3. Strength Progression (`best_e1rm_lbs` / Tier-2 Kalman Filter)
- Compare session `best_e1rm_lbs` on Squat, Deadlift, Bench, and OHP against baseline `barbell_1rm` (`210 / 205 / 155 / 115 lbs` current re-entry profile).
- Note sharp drops in top-set e1RM or high `estimated_axial_load` relative to recent endurance `TSS` / `ANS` and `wellness.alpha_d`.

## 4. Cross-Sport Interference & Guardrails
- **Deadlift-Run 24h Lockout (`HEUR-004`):** Heavy deadlifts must NEVER share a day with or precede a threshold/long run by 1 day (`deadlift_run_collisions`).
- **Same-Day Antagonisms:** No Squat + High-Torque Bike / Threshold Run; no Heavy Upper Press + CSS Swim.
- **Molecular Window (`ADR-012`):** Dual-session days require `>= 6h` separation, EXCEPT a Lift + Easy Cardio Combo (`<= 20 min` Z1/Z2 Bike after heavy lower lifting, or Bike/Run after upper/other lift, lift first).
