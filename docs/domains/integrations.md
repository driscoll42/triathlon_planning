# Domain Lode: `integrations`

## 1. Scope & Responsibility
TrainingPeaks private REST API authentication, structured workout step serialization, FIT file processing, multi-sport telemetry ingestion algorithms (cycling power, running biomechanics, swimming cubic drag scaling), and execution compliance quantification.

## 2. Key Modules & Entrypoints
- [`src/triplan/io/trainingpeaks.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/io/trainingpeaks.py): [`TrainingPeaksClient`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/io/trainingpeaks.py), credential management, step serialization, and folder hierarchy syncing.
- [`src/triplan/library/clean_file.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/clean_file.py): HTML table scraping, FIT download extraction, and TSS/sTSS algorithms.
- [`data/fit_files/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/data/fit_files/), [`data/workout_library.html`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/data/workout_library.html): Local workout cache and downloaded binary streams.
- [`config/trainingpeak.env`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/config/trainingpeak.env): Secure environment credentials.

## 3. Core Architectural Rules & Telemetry Ingestion Algorithms

### A. API Hygiene & Credential Security
- Credentials (`TP_USERNAME`, `TP_PASSWORD`, `TP_COOKIE`) must reside strictly in `config/.env` or `config/trainingpeak.env` and must never be committed to git or printed in logs.
- Outbound API requests must incorporate random jitter delays (1.0–3.0s) and exponential backoff upon encountering HTTP 429 status codes.
- Structured workout steps must serialize into standard TrainingPeaks step schemas (`Warmup`, `Active`, `Recovery`, `Cooldown`).

### B. Cycling Power Telemetry (Coggan / Vance Framework)
From the 1 Hz power ($P$, watts) and heart rate ($HR$, bpm) time series:
1. **30-Second Rolling Average Power:** $P_{30s,t} = \frac{1}{30}\sum_{i=0}^{29} P_{t-i}$.
2. **Normalized Power (NP):** Fourth-power physiological weighting:
   $$\text{NP} = \left(\frac{1}{N}\sum_{t=1}^N (P_{30s,t})^4\right)^{1/4}$$
3. **Intensity Factor & Variability Index:** $\text{IF} = \frac{\text{NP}}{\text{bFTP}}$ and $\text{VI} = \frac{\text{NP}}{\text{Average Power}}$ (steady endurance VI $\le 1.05$; race aero VI $\le 1.03$).
4. **Efficiency Factor & Aerobic Decoupling:**
   $$\text{EF} = \frac{\text{NP}}{\text{Average HR}}, \quad Pw:HR = \left(\frac{\text{EF}_{\text{half1}} - \text{EF}_{\text{half2}}}{\text{EF}_{\text{half1}}}\right) \times 100\%$$
   $Pw:HR < 5.0\%$ during a steady Zone 2 ride demonstrates adequate aerobic base conditioning.
5. **Vance Quadrant Analysis & Fade:** Segments power into Quadrants I–IV (Average Effective Pedal Force vs Circumferential Pedal Velocity) and computes **Cadence Fade %** and **Power Fade %** between initial and final segments ($\le 13\%$ for 70.3; $\le 20\%$ for 140.6).

### C. Running Power & Stryd Biomechanical Telemetry
1. **Running Intensity Factor (rIF):** Computed from Stryd Critical Power (CP) or Normalized Graded Pace (NGP):
   $$\text{rIF} = \frac{\text{CP}}{\text{rFTPa}} \quad \text{or} \quad \frac{\text{NGP}}{\text{rFTP}}$$
2. **Energy Cost of Running (ECOR):**
   $$\text{ECOR} = \frac{\text{Power (W)}}{\text{Speed (m/s)} \cdot \text{Mass (kg)}} \quad [\text{kJ/kg/km}]$$
3. **Biomechanical Fatigue Markers:** Ingests Form Power Ratio ($\text{FPR} = \text{Form Power} / \text{Total Power}$), Ground Contact Time (GCT, in ms), and Leg Spring Stiffness (LSS, in kN/m). Longitudinal decay in LSS and escalation of ECOR during long runs flags acute elastic recoil failure.
4. **Aerobic Decoupling:** $Pa:HR = \left(\frac{\text{EF}_{run,\text{half1}} - \text{EF}_{run,\text{half2}}}{\text{EF}_{run,\text{half1}}}\right) \times 100\%$.

### D. Swimming Telemetry (Critical Swim Speed & Cubic Drag Scaling)
1. **Critical Swim Speed (CSS):** Derived from 400m and 200m time trials:
   $$\text{CSS} = \frac{400 - 200}{T_{400} - T_{200}} \quad [\text{m/s}]$$
2. **Normalized Swimming Speed (NSS):** Active swim distance divided by active swim duration (excluding rest intervals at the wall).
3. **Cubic Drag Intensity Factor ($sIF^3$):** Because hydrodynamic drag escalates quadratically with velocity and power escalates cubically ($P \propto v^3$), swimming intensity factor is cubed in sTSS calculations:
   $$sIF = \frac{\text{NSS}}{\text{CSS}}, \quad sTSS = \frac{T \cdot (sIF)^3 \cdot 100}{3600}$$

### E. Workout Compliance & Intensity Creep Engine
Evaluates realized execution against prescription across three metrics:
$$\epsilon_{dur} = \frac{|D_{real} - D_{plan}|}{D_{plan}}, \quad \epsilon_{tss} = \frac{|\text{TSS}_{real} - \text{TSS}_{plan}|}{\text{TSS}_{plan}}, \quad \epsilon_{int} = \frac{|\text{IF}_{real} - \text{IF}_{plan}|}{\text{IF}_{plan}}$$
- **Moderate-Intensity Rut Reclassification:** If an easy Zone 1/2 session is executed with intensity creep ($IF_{real} > 0.78$ or $HR > LT1$), the session is dynamically reclassified as a Hard Day ($hard_d = 1$), recomputing the exponential ANS cost and feeding it into the rolling 48-hour MPC safety bound.

### F. Multi-Device Hardware Telemetry & FIT Parser Harness (`SPEC.md`, `ADR-010`)

1. **Stryd Foot Pod (Wind Model, 2019 Release, Model 17):**
   - **Developer Field Extraction:** In `.fit` files recorded on Garmin watches via `Stryd Zones`, NEVER read standard `record.power` (native wrist power, inflated +20% to +30%). Extract Connect IQ developer field where `field.is_dev == True` and `field.name == 'Power'` (case-sensitive) (`HEUR-005`).
   - **Auxiliary Biomechanical Streams:** Extract developer fields `Form Power`, `Leg Spring Stiffness` (LSS, in kN/m — track longitudinal decay as a marker of Achilles/patellar tendon fatigue), and `Air Power`.
   - **Treadmill vs. Outdoor Adjustment:** Model 17 features a physical pitot wind port. On treadmills (`sub_sport == 'treadmill'`), `Air Power = 0W` because air velocity is zero, making treadmill wattage ~2%–4% lower at identical belt speeds than calm outdoor running.
   - **Weight Lock:** Model 17 calculates W/kg internally and scales by the athlete weight entered in the Stryd app. Never alter user weight in the app mid-block without re-baselining CP.
   - **ANT+ vs. Single-Channel BLE:** Model 17 supports only one concurrent Bluetooth connection. Pair strictly via **ANT+** (`FP-XXXXX`, Pace & Distance = Always) to the Enduro 3 watch to leave Bluetooth open for the smartphone Stryd app.

2. **Garmin Enduro 3 Watch:**
   - **Run Profile Configuration:** Garmin Native Running Power MUST be toggled `Off` (`Activities & Apps -> Run -> Run Settings -> Running Power -> Off`). Stryd must be paired as an ANT+ foot pod, never via Bluetooth or as an ANT+ cycling power meter.
   - **Primary Ecosystem Hub:** Enduro 3 supports Garmin Unified Training Status (Physio TrueUp 2.0) and enhanced Pool Swim workouts. Use Enduro 3 for structured CSS-targeted swims if Training Readiness/HRV telemetry is required.
   - **Connect IQ Slot Limits:** Maximum 2 Connect IQ data fields per profile. Structured 80/20 runs synced from TrainingPeaks should be monitored via the `Stryd Zones` Connect IQ data field.

3. **Garmin Swim 2 Watch:**
   - **Active Length Parsing:** Do NOT use `session.total_elapsed_time` (includes lane-end rest). Sum active swim duration strictly across `length` messages where `length_type == 'active'` (`HEUR-007`).
   - **Drill Log Handling:** Lengths where `swim_stroke == 'drill'` or `event == 'drill'` have `total_strokes == None/0`. Exclude drill lengths from SWOLF and stroke-rate averages, but retain their duration in low-intensity aerobic swim time.
   - **Firmware Ecosystem Limitations:** Swim 2 does NOT support CSS-relative structured workout pace targets and does NOT participate in Garmin Unified Training Status (Physio TrueUp 2.0).

4. **Garmin Edge 530 Bike Computer:**
   - **Recording Granularity:** Set `Data Recording = Every Second` (never `Smart`), `Power Averaging = Include Zeros`, and `Cadence Averaging = Include Zeros`.
   - **Cross-Device Deduplication Window:** If both Edge 530 and Enduro 3 record during an indoor or outdoor ride, ingest only ONE `.fit` file per timestamp window (`abs(start_time_A - start_time_B) < 300s`) to prevent double-counting weekly TSS and mechanical strain (`HEUR-007`).

5. **Tacx Neo 2T Smart Trainer:**
   - **Zero Calibration:** Direct-drive optical and magnetic motor brake ($\pm 1\%$ accuracy) with automatic temperature compensation; never run spin-down calibration (`HEUR-006`).
   - **Testing Mode:** Execute all 3-min/12-min CP tests and Kolie Moore tests in **Slope/Incline Mode** (e.g., +1.5% to +2.0% fixed grade) with **Road Feel = Off** and **Downhill Drive = Off**. Never use ERG mode for maximal threshold testing.
   - **Discard Left/Right Balance:** The Neo 2T estimates `left_right_balance` via inductive sensors that spike erratically during torque changes. Discard `left_right_balance` from Neo 2T `.fit` files (`HEUR-006`).
   - **External AC Power:** Keep plugged into AC wall power during tests and interval workouts so the trainer does not power down during long coasting rest intervals.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
