---
id: HEUR-006
type: heuristic
domain: integrations
title: "Tacx Neo 2T Incline-Mode Testing and Invalidate Left-Right Balance"
status: ACTIVE
created_at: 2026-11-10
supersedes: []
validated_by: []
one_liner: "Test CP in Slope mode (+1.5-2.0%), keep AC power plugged in, and discard Neo 2T inductive left_right_balance stream."
---

# `HEUR-006`: Tacx Neo 2T Incline-Mode Testing and Invalidate Left-Right Balance

- **Target Subsystem / Path:** `src/clean_file.py`, Tacx Training/Utility app settings, smart trainer testing protocols.
- **Symptom / Failure Mode:**
  1. Erg-mode resistance clamps athlete cadences during maximal 3-minute or 12-minute CP tests, causing early failure ("spiral of death").
  2. Tacx Neo 2T unexpectedly shuts down and drops ANT+ FE-C / BLE connections during the 30-minute recovery period between test intervals.
  3. Telemetry analysis falsely flags a severe single-leg strength deficit due to erratic balance numbers.
- **Root Physics / Mechanism:**
  1. The Neo 2T motor brake has $\pm 1\%$ accuracy and auto-calibrates without spin-down. However, ERG mode prevents self-paced pacing required for Monod-Scherrer CP testing.
  2. When operated unplugged, the Neo 2T relies on pedaling to power its electronics. When pedaling ceases for $>1-2$ minutes during recovery, the unit powers down completely.
  3. The Neo 2T estimates `left_right_balance` via internal inductive stator coil sensors rather than direct pedal-spindle strain gauges. Dynamic cadence and torque transients create spurious sensor spikes despite total wattage accuracy.
- **Permanent Operational Rule:**
  1. Never perform spin-down calibrations on Tacx Neo 2T.
  2. Execute all 3-min/12-min CP tests and Kolie Moore tests in **Slope/Incline Mode** (+1.5% to +2.0% fixed grade) with **Road Feel = Off** and **Downhill Drive = Off**.
  3. Keep the external AC power brick plugged in at all times during workouts and tests with rest intervals.
  4. Explicitly ignore and discard the `left_right_balance` stream from Neo 2T `.fit` files.
- **Verification Check:**
  `python -c "from src.clean_file import clean_bike_fit; df = clean_bike_fit('sample_ride.fit'); assert 'left_right_balance' not in df.columns or df['left_right_balance'].isna().all()"`
