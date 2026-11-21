# Bike Post-Workout Diagnostic Rubric

Grounded in `SPEC.md` §2–§3, `ADR-009`, `ADR-010`, `HEUR-006`, and `HEUR-007`.

## 1. Telemetry & Positional Context
- **Zero-Averaging & L/R Balance (`HEUR-006`, `HEUR-007`):** Edge 530 records 1s with zeros included (`zero_power_pct` reported); Neo 2T inductive `left_right_balance` is always ignored.
- **Aero vs. Upright Hoods (`positional_context`):** Indoor/Aero CP (`135W` current provisional baseline) is calibrated in the aero position. Upright road-hoods riding produces ~3%–7% (`10–20W`) higher sustainable power; adjust interpretation before comparing outdoor hood rides to indoor aero thresholds.

## 2. Pacing, Smoothness & Intensity
- **Variability Index (`variability_index = NP / AvgP`):**
  - Steady aerobic / tempo / triathlon simulation: target `VI <= 1.05` (ideally `1.01–1.03` indoors on Neo 2T).
  - `VI > 1.08` on a steady Z2 ride indicates surging/poor pacing unless the prescription was structured intervals (`laps` will show work/rest bouts).
- **80/20 Polarity & Zone X:** Easy rides should stay in `Z1` (<70% CP) and `Z2` (70%–83% CP). Flag Zone X (83%–91% CP) creep on endurance rides and `moderate_intensity_rut_flag` (`IF > 0.78` on planned `EASY`).

## 3. Durability & Muscular Fade
- **Pw:HR Decoupling (`decoupling_pct`):**
  - `< 5.0%` on steady Z2: Aerobic target met; `< 3.0%` signals readiness for progression / CP retest evaluation (`ADR-011`).
  - `> 5.0%`: Cardiovascular drift / fueling / thermal or systemic fatigue.
- **Second-Half Power & Cadence Fade (`power_change_pct`, `cadence_change_pct`):**
  - Watch for simultaneous cadence drop + power fade on steady rides or late intervals (muscular glycogen/torque fatigue; compare against `recent_lifting` squat sessions).
  - Reference durability targets: cadence-power fade `<= 13%` (70.3) / `<= 20%` (140.6).

## 4. Cross-Sport Impact
- High-torque / threshold bike sessions conflict with same-day heavy squats (`docs/INVARIANTS.md`) and draw down the 48h `TSS <= 295` and `ANS <= 55` (athlete) budgets.
