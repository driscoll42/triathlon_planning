# Telemetry, Threshold & Hardware Specification (Triathlon Optimizer Harness)

> **Canonical Specification Reference:** See root [`SPEC.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/SPEC.md) for full context and copy-pasteable configuration blocks.

## 1. Threshold Calibration & Critical Power (CP) Math
The athlete performs concurrent heavy barbell strength training (squats/deadlifts), resulting in a high Anaerobic Work Capacity ($W'$). Therefore, single-effort proxy tests with fixed multipliers (e.g., Cycling Ramp Test at $0.75 \times \text{MAP}$ or un-cleared 20-minute TT at $0.95 \times P_{20}$) overestimate sustainable aerobic threshold ($LT_2$) and MUST NOT be used.

### A. Running & Cycling: Two-Point Linear Work-Time CP Protocol (3-Min + 12-Min)
- **Test Inputs:** 
  - $P_3$: Mean maximal power (Watts) over a 180-second ($t_1 = 180\text{s}$) all-out effort.
  - $P_{12}$: Mean maximal power (Watts) over a 720-second ($t_2 = 720\text{s}$) all-out effort (after a full 30-minute active/passive recovery).
- **Critical Power ($CP$, Watts):**
  $$CP = \frac{t_2 P_{12} - t_1 P_3}{t_2 - t_1} = \frac{4 P_{12} - P_3}{3}$$
- **Anaerobic Work Capacity ($W'$, Joules):**
  $$W' = 180 \cdot (P_3 - CP)$$
- **Cycling Alternative (Kolie Moore Empirical Baseline):** 35–45 minute progressive effort at Maximum Lactate Steady State ($MLSS$), where $\text{FTP} = 1.00 \times P_{\text{avg}}$ and test duration equals baseline Time-to-Exhaustion ($TTE$).
- **Positional Rule (Cycling):** Indoor/Aero CP must be tested in the triathlon aero position if workouts are executed in aero; upright road-hoods power is typically 3%–7% (10–20W) higher and must not be mixed without a positional scaling factor.

### B. Swimming: Critical Swim Speed (CSS)
- **Test Inputs:** $T_{400}$ (400m/yd time trial in seconds) and $T_{200}$ (200m/yd time trial in seconds).
- **CSS Formula (m/s or yd/s):**
  $$CSS = \frac{400 - 200}{T_{400} - T_{200}} = \frac{200}{T_{400} - T_{200}}$$
- **Pace per 100m/yd (seconds):** $\text{Pace}_{100} = \frac{100}{CSS} = \frac{T_{400} - T_{200}}{2}$

---

## 2. Canonical Zone System: 80/20 Endurance (7-Zone Power)
Do NOT use Stryd 5-Zone or MyProCoach 5-Zone systems (their Zone 2 spans 80%–90% CP, which crosses above $LT_1$ into the moderate grey zone and causes overtraining). All workout prescriptions and `.fit` ingestion pipelines must map to the **80/20 7-Zone Scale** anchored to verified $CP$:

| Zone | Physiological Role | Run Power (% of Stryd CP) | Bike Power (% of Bike CP/FTP) | Optimizer Bucket | ANS Cost Weight |
|---|---|---|---|---|---|
| **Zone 1** | Active Recovery / Low Aerobic | 50% – 76% | 50% – 70% | Low (`z1_z2_dur`) | 0.0 |
| **Zone 2** | Aerobic Endurance (Ceiling = $LT_1$) | 76% – 88% | 70% – 83% | Low (`z1_z2_dur`) | 0.0 |
| **Zone X** | Moderate Drift Grey Zone *(Penalize)* | 88% – 94% | 83% – 91% | Grey Drift (`zx_dur`) | 0.7 (Unplanned Penalty) |
| **Zone 3** | Sub-Threshold / Tempo (Ceiling = $LT_2$) | 94% – 100% | 91% – 100% | Moderate/High (`z3_dur`) | 1.0 |
| **Zone Y** | Threshold Over-Reach Gap *(Penalize)* | 100% – 103% | 100% – 102% | High (`z4_dur`) | 1.4 |
| **Zone 4** | Supra-Threshold / $\text{VO}_2\text{max}$ | 103% – 120% | 102% – 110% | High (`z4_dur`) | 1.8 |
| **Zone 5** | Anaerobic / Repetition ($W'$ Depletion) | > 120% | > 110% | High (`z5_dur`) | 3.5 |

- **Physiological Sanity Checkpoints:**
  - **Run $LT_1$ (88% CP):** Must pass the Talk Test and keep HR <= 89% LTHR.
  - **Run/Bike $LT_2$ (96%–100% CP):** HR must plateau near LTHR during a 15–20 minute interval.
- **Single Source of Truth:** Disable `Auto-CP` in Stryd PowerCenter and `Auto-FTP` on Garmin Edge 530/Enduro 3 during training blocks. The exact same manual $CP$ integer must be locked across: (1) Stryd PowerCenter, (2) TrainingPeaks Run/Bike Power Zones, and (3) Optimizer `config.yaml` / `athlete_profile.py`.

---

## 3. Hardware Telemetry & `.fit` Ingestion Rules for Parser Harness

### 1. Stryd Foot Pod (Wind Model, 2019 Release, Model 17)
- **Developer Field Extraction:** In `.fit` files recorded on Garmin watches via the `Stryd Zones` Connect IQ field, NEVER read the standard `record.power` field (which contains Garmin Native Running Power, inflated by +20% to +30%). Extract the Connect IQ developer field where `field.is_dev == True` and `field.name == 'Power'` (case-sensitive).
- **Auxiliary Biomechanical Fields:** Extract developer fields `Form Power`, `Leg Spring Stiffness` (LSS, in kN/m — track for tendon fatigue/remodeling), and `Air Power`.
- **Treadmill vs. Outdoor Adjustment:** Model 17 uses a physical pitot wind port. On a treadmill (`sub_sport == 'treadmill'`), `Air Power` is 0W because air velocity is zero, making treadmill wattage ~2%–4% lower at identical belt speeds than calm outdoor running.
- **Weight Lock:** Stryd Model 17 computes W/kg internally and scales by user weight set in the Stryd app. Do not alter user weight in the Stryd app mid-block without re-baselining CP.
- **Bluetooth vs. ANT+:** Model 17 has single-channel Bluetooth. Pair strictly via ANT+ (`FP-XXXXX`, Pace & Distance = Always) to the Garmin Enduro 3 to prevent Bluetooth lockouts from mobile phone background sync.

### 2. Garmin Enduro 3 Watch
- **Run Profile Setup:** Garmin Native Running Power MUST be set to `Off` in Run Settings (`Activities & Apps -> Run -> Run Settings -> Running Power -> Off`). Stryd Model 17 must be paired via `ANT+` only (`FP-XXXXX`), never via Bluetooth or as an ANT+ Power Meter.
- **Primary Ecosystem Hub:** Enduro 3 supports Garmin's *Unified Training Status* (Physio TrueUp 2.0) and enhanced Pool Swim workouts. Prefer Enduro 3 over Swim 2 when executing structured CSS-targeted swim workouts if Unified Training Status/Readiness telemetry is desired.
- **Connect IQ Slot Limits:** Maximum 2 Connect IQ data fields per activity profile. Structured 80/20 runs synced from TrainingPeaks should be monitored via the `Stryd Zones` Connect IQ data field.

### 3. Garmin Swim 2 Watch
- **Pool Swim `.fit` Parsing:** Do NOT use `session.total_elapsed_time` to calculate swim duration or sTSS (it includes wall rest). Sum duration strictly across `length` messages where `length_type == 'active'`.
- **Drill Log Handling:** Lengths where `swim_stroke == 'drill'` or `event == 'drill'` have `total_strokes == None/0`. Exclude drill lengths from SWOLF and stroke-rate averages, but include their duration in low-intensity aerobic swim time.
- **Structured Workout Limitation:** Swim 2 does NOT support Garmin's newer *Pool Swim Workout Enhancements* (CSS-relative pace targets in structured workouts) and does NOT participate in Garmin *Unified Training Status* (Physio TrueUp 2.0).

### 4. Garmin Edge 530 Bike Computer
- **Recording Granularity:** Set `Data Recording = Every Second` (never `Smart` recording), `Power Averaging = Include Zeros`, and `Cadence Averaging = Include Zeros`.
- **Deduplication Rule:** If both Edge 530 and Enduro 3 are used during an indoor or outdoor ride, ingest only ONE `.fit` file per timestamp window (`abs(start_time_A - start_time_B) < 300s`) to prevent double-counting TSS and mechanical strain.

### 5. Tacx Neo 2T Smart Trainer
- **No Calibration Required:** Direct-drive optical/magnetic motor brake ($\pm 1\%$ accuracy); never prompt for spin-down calibration.
- **Testing & Interval Mode:** Execute all 3-min/12-min CP tests and Kolie Moore tests in **Slope/Incline Mode** (e.g., +1.5% to +2.0% fixed grade) with **Road Feel = Off** and **Downhill Drive = Off**. Never use ERG mode for maximal testing.
- **Ignore L/R Balance Stream:** The Neo 2T estimates `left_right_balance` via inductive sensors that spike erratically during wattage changes. Ignore `left_right_balance` from Neo 2T `.fit` files.
- **Wall Power Requirement:** Keep plugged into AC power during workouts/tests with long rest intervals so the trainer does not power down and drop ANT+ FE-C connections when coasting stops.
