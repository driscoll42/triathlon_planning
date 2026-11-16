---
id: HEUR-005
type: heuristic
domain: integrations
title: "Disambiguate Stryd CIQ Developer Power Field and Enforce ANT+ Pairing"
status: ACTIVE
created_at: 2026-11-10
supersedes: []
validated_by: []
one_liner: "Never read record.power for running; extract CIQ dev field 'Power' and pair Stryd Model 17 via ANT+ to prevent BLE lockouts."
---

# `HEUR-005`: Disambiguate Stryd CIQ Developer Power Field and Enforce ANT+ Pairing

- **Target Subsystem / Path:** `src/clean_file.py`, `src/athlete_profile.py`, Garmin Enduro 3 sensor pairing.
- **Symptom / Failure Mode:** Running power in `.fit` files reads 20% to 30% higher than Stryd values (e.g., 340W native vs 270W Stryd), or Stryd foot pod fails to connect to the Garmin Enduro 3 watch prior to a run.
- **Root Physics / Mechanism:**
  1. Garmin does not use a native ANT+ Running Power profile for third-party foot pods. The `Stryd Zones` Connect IQ data field writes Stryd wattage into a Connect IQ developer field named `'Power'` (capital `P`), while Garmin native wrist running power writes to standard field `'power'` (lowercase `p`). Naive iteration over `record` fields overwrites or confuses the two streams.
  2. Stryd Model 17 (2019 "Wind" release) features a single-channel Bluetooth LE chip. If a mobile phone app holds the BLE connection, a watch attempting BLE pairing is locked out.
- **Permanent Operational Rule:**
  1. In `.fit` parsers, running power MUST be extracted where `field.is_dev == True` and `field.name == 'Power'`.
  2. Also extract developer fields `Form Power`, `Leg Spring Stiffness` (LSS, kN/m), and `Air Power`.
  3. Turn Garmin Native Running Power `Off` in Enduro 3 Run Settings (`Activities & Apps -> Run -> Run Settings -> Running Power -> Off`).
  4. Pair Stryd Model 17 to Enduro 3 strictly via **ANT+** (`FP-XXXXX`), setting Pace & Distance = Always.
- **Verification Check:**
  `python -c "from src.clean_file import parse_fit_power; assert parse_fit_power('sample_run.fit')['source'] == 'stryd_dev_field'"`
