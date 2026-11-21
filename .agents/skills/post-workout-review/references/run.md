# Run Post-Workout Diagnostic Rubric

Grounded in `SPEC.md` §2–§3, `ADR-009`, `ADR-010`, and `HEUR-005`.

## 1. Telemetry Hygiene Checks
- **Stryd CIQ Power (`HEUR-005`):** Verify `sport_diagnostics.has_stryd_power == true`. Native `record.power` is inflated (+20% to +30%) and is automatically ignored; if `stryd_ciq_power` is in `missing_streams`, note that power/zone metrics are unavailable rather than fabricating them.
- **Treadmill (`is_treadmill == true`):** `Air Power` is `0 W` on a treadmill, making wattage ~2%–4% lower at the same pace than calm outdoor running. Account for this before flagging low power/EF.

## 2. Intensity & 80/20 Polarity
- **80/20 Target:** Easy/aerobic runs should accumulate nearly all minutes in `Z1` (<76% CP) and `Z2` (76%–88% CP, $LT_1$ ceiling).
- **Zone X / Zone Y Leakage:** Check `polarity.zone_x_y_min`. Unplanned Zone X (88%–94% CP) and Zone Y (100%–103% CP) drive ANS drift without threshold/$\text{VO}_2$ stimulus.
- **Moderate-Intensity Rut (`moderate_intensity_rut_flag`):** If a planned `EASY` run has `IF > 0.78` (or HR exceeds $LT_1$ ~89% LTHR), explicitly recommend reclassifying the day as `hard_d = 1` so the next 48h schedule protects recovery.

## 3. Aerobic & Neuromuscular Fatigue Markers
- **Pw:HR Decoupling (`decoupling_pct`):**
  - `< 3.0%` on steady Z2 ($\ge 30$ min): Strong aerobic durability (supports `ADR-011` upward adaptation if sustained).
  - `3.0% – 5.0%`: Acceptable steady aerobic drift.
  - `> 5.0%` on steady Z2: Elevated cardiovascular drift (dehydration, heat, or accumulated fatigue).
- **Leg Spring Stiffness (`lss_change_pct`):**
  - Steady or `< 2%` drop: Good elastic/tendon resilience.
  - Drop `>= 4%–5%` in 2nd half (`lss_second_half < lss_first_half`): Neuromuscular/tendon fatigue marker—cross-check `recent_lifting` (heavy squats/deadlifts) and `rolling_48h_peaks`.
- **Form Power Ratio (`form_power_ratio = avg_form_power / avg_power`):** Rising ratio across blocks or high values relative to baseline indicate vertical oscillation inefficiency or fatigue.
- **Cadence Drift (`cadence_change_pct`):** Watch for cadence dropping alongside falling LSS in the second half.

## 4. Orthopedic Cross-Checks
- Check `deadlift_run_collisions` (`HEUR-004` same-day or +1d after deadlifts).
- During re-entry (`run CTL < 15`), ensure single runs stay within the `ADR-022` duration cap (`<= 45 min` or `prev + 10 min`).
