# Swim Post-Workout Diagnostic Rubric

Grounded in `SPEC.md` §2–§3, `ADR-010`, and `HEUR-007`.

## 1. Telemetry Hygiene Checks (`HEUR-007`)
- **Active-Only Duration & sTSS:** Duration, distance, `sIF`, and `sTSS` are computed strictly from `length` messages with `length_type == 'active'`, never `session.total_elapsed_time`.
- **Drill Exclusion:** Lengths tagged `swim_stroke == 'drill'` or `event == 'drill'` retain their active aerobic duration for `sTSS` but are excluded from `avg_swolf` and `avg_stroke_rate`.
- **Active Ratio (`active_ratio = active_duration / elapsed`):** Reflects rest discipline. Continuous endurance sets typically show higher `active_ratio` than high-rest CSS or sprint sets.

## 2. Pacing & Hydrodynamic Efficiency
- **sIF vs. CSS (`pace_per_100m_sec` vs. `css_pace_per_100m_sec`):**
  - `sIF = NSS / CSS` scales cubically into `sTSS = (T_hr * sIF^3 * 100)`. Small pace surges (`+5%` speed) produce `+15.8%` `sTSS` cost.
- **First-Half vs. Second-Half Mechanics (`pace_change_pct`, `swolf_change_pct`, `stroke_rate_change_pct`):**
  - **Ideal / Negative Split:** `pace_second_half_sec <= pace_first_half_sec` with stable `swolf` and `stroke_rate`.
  - **Form Breakdown:** Increasing `swolf_second_half` paired with higher `stroke_rate_second_half` and slower `pace_second_half_sec` indicates stroke-length collapse (fatigue / shoulder drop).
  - **Dead-Arm / Heavy Upper-Body Fatigue:** Falling `stroke_rate` + rising `pace_per_100m_sec`—cross-check `recent_lifting` for heavy upper-body pressing (`docs/INVARIANTS.md`: Heavy Upper Press vs. CSS Swim same-day impingement prohibition).

## 3. Cross-Sport Context
- Swimming adds minimal orthopedic mechanical strain (`0.05 * sTSS`) and stacks synergistically with lower-body lifting (`HEUR-004`), making it safe around deadlift days unless upper-body pressing fatigue compromises shoulder mechanics.
