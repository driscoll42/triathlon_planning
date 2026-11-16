# System Invariants & Non-Negotiable Guardrails (`docs/INVARIANTS.md`)

## 1. Orthopedic Safety & Biomechanical Guardrails
- **Single-Run Ceiling:** A single run must never exceed **32%** of total weekly running duration (`max_single_run_vol_ratio <= 0.32`, `ADR-003`) to prevent bone stress and patellofemoral injury.
- **Rolling 48-Hour Strain Caps:** Rolling 48-hour mechanical strain must not exceed **260.0** units; rolling 48-hour aerobic TSS must not exceed **295.0**; rolling ANS cost must not exceed **700.0**.
- **Axial Spinal & Neuromuscular Ceilings:** Daily axial load $X_{\text{day}} \le 120.0$; weekly axial load $X_{\text{wk}} \le 320.0$. Daily neuromuscular load $N_{\text{day}} \le 130.0$; weekly neuromuscular load $N_{\text{wk}} \le 360.0$ (`ADR-008`).
- **Deadlift-Run 24h Lockout:** Heavy deadlifts and threshold/long runs must NEVER share a calendar day ($x_{i,d} + x_{j,d} \le 1$) or adjacent days ($x_{i,d} + x_{j,d+1} \le 1$) to prevent lumbar shear failure (`HEUR-004`). Deadlifts stack synergistically with swimming.
- **Biomechanical Antagonism Exclusions:**
  - Squat vs. High-Torque Bike / Threshold Run: Absolute same-day exclusion.
  - Heavy Upper Press vs. CSS Threshold Swim: Absolute same-day exclusion to prevent subacromial impingement.
- **Orthopedic Dissociation Index (ODI):** Cardiovascular remodeling ($\tau=35d$) adapts faster than collagenous matrix remodeling ($\tau=120d$). The ratio of metabolic fitness to connective tissue adaptation ($ODI$) must never exceed **1.35**.
- **EWMA Mechanical ACWR:** Acute-to-Chronic Workload Ratio for mechanical running load ($\tau_a=7d, \tau_c=28d$) must remain within $[0.80, 1.30]$; tightened to $\le 1.20$ during running re-entry (run CTL < 15) and $\le 1.25$ thereafter, with an absolute-minutes guard (60 min/wk floor, +15 min/wk max) (`ADR-012`).
- **Adjacency Prohibitions:** Never schedule two breakthrough high-intensity sessions on consecutive days; maintain $\ge 48$ hours between heavy running workouts.

## 2. Optimization & Solver Invariants
- **Deterministic MILP Formulations:** All weekly scheduling must be formulated as Mixed-Integer Linear Programs (MILP) solved via HiGHS (`highspy`, `ADR-002`, `ADR-008`). Unconstrained random search is prohibited for production plan generation.
- **Equipment Set Partitioning:** Enforce $\ge 60\%$ weekly strength set volume on Olympic barbell compound movements via exact linear ratio constraint; home dumbbells capped at $\le 75\text{ lb}$ are strictly allocated to unilateral assistance and hypertrophy (`ADR-008`).
- **Movement Pattern Volume:** Minimum 10 working sets/week across all 4 movement patterns (Anterior Chain, Posterior Chain, Push, Pull).
- **Split Archetype Coherence:** Binary variable $z_{\text{split}} \in \{0, 1\}$ strictly partitions Option A (Full-Body HLM / Texas Method) and Option B (Upper/Lower Split).
- **Top-K Plan Diversity:** Alternative distinct schedules must be produced using canonical **No-Good Integer Cuts** ($\sum_{w \in S_k} x_{w,d} \le |S_k| - 1$), guaranteeing true structural uniqueness.
- **Pre-Filtering Invariant:** Workout candidate pools must be pruned of mathematically non-viable sessions prior to building the HiGHS problem matrix to guarantee sub-second solve latency.

## 3. Physiological & Periodization Invariants
- **80/20 Intensity Partitioning:** Weekly endurance time-in-zone (strength excluded) must adhere to $\ge 78\%$ low intensity (Zone 1/2; 80% soft target) and $\le 20\%$ moderate + high intensity (Zone 3/Y/4/5 combined) via exact linear ratio bounds; Zone X penalized toward 0 (`ADR-012`).
- **Canonical 80/20 7-Zone Power Scale:** Prescriptions and `.fit` ingestion must use the 80/20 7-Zone Scale anchored to verified Critical Power ($CP$). Stryd 5-Zone and MyProCoach 5-Zone tables are prohibited (Zone 2 overreaches past $LT_1$). Zone X (88%–94% Run, 83%–91% Bike) and Zone Y (100%–103% Run, 100%–102% Bike) incurs non-zero ANS drift penalties (`ADR-009`).
- **Critical Power Testing Protocol:** Single-effort proxy tests (Cycling Ramp at $0.75 \times \text{MAP}$, un-cleared 20-min TT at $0.95 \times P_{20}$) are strictly prohibited due to high $W'$ from concurrent barbell lifting. Calibrate via Two-Point CP ($P_3$ 180s + $P_{12}$ 720s) or Kolie Moore 35–45m MLSS baseline (`ADR-009`).
- **Aero vs. Upright Hoods Positional Rule:** Indoor/Aero CP must be tested in aero position; upright road-hoods wattage is 3%–7% (10–20W) higher and must not be mixed without positional scaling.
- **Single Source of Truth Threshold Integer:** Auto-CP in Stryd and Auto-FTP on Garmin must be disabled; the identical manual integer must be locked across Stryd PowerCenter, TrainingPeaks, and optimizer profiles.
- **Molecular Signaling Window (AMPK vs mTORC1):** Dual-session days must enforce $\ge 6\text{h}$ intra-day separation between endurance and resistance training. When paired with easy Z1/Z2 recovery PM, lift fresh in the AM.
  - **Exception — Lift + Easy Cardio Combo (`ADR-012`):** A strength session may be followed immediately (same window) by $\le 20$ min of strictly Z1/Z2 Bike or Run. Lift first. Bike only after heavy lower-body lifting (no impact on a loaded spine, consistent with `HEUR-004`). The pair counts as one window and toward the day's time cap. Low-intensity, short-duration cardio produces negligible AMPK-mediated interference.
- **Hard-Days-Hard Stacking:** Strength sessions must be stacked onto hard endurance days (AM endurance + PM strength), preserving easy days for complete physiological restoration. Maximum 2 sessions/day; non-consecutive strength days.
- **3:1 Step-Cycle Rhythm:** Mesocycles must follow three progressive loading weeks followed by a mandatory recovery week (15–25% reduction in TSS, axial load, and mechanical strain).

## 4. Hardware Telemetry & FIT Ingestion Invariants
- **Stryd CIQ Developer Field Extraction:** FIT parser must extract Connect IQ developer field `Power` (`field.is_dev == True`, `field.name == 'Power'`). Never read native `record.power` (inflated by +20% to +30%). Stryd Model 17 paired to Enduro 3 via ANT+ only (`FP-XXXXX`) (`ADR-010`, `HEUR-005`).
- **Pool Swim Active Length Ingestion:** Swim duration and sTSS must be computed strictly from `length` records where `length_type == 'active'` (never `session.total_elapsed_time`). Exclude drills (`swim_stroke == 'drill'`) from SWOLF/stroke rate (`HEUR-007`).
- **Recording Granularity & Multi-Device Deduplication:** Bike computers (Edge 530) must record 1-second intervals with zeros included. Overlapping rides between Edge 530 and Enduro 3 within a 300-second window must ingest exactly one FIT file (`ADR-010`, `HEUR-007`).
- **Tacx Neo 2T Invariants:** Direct-drive optical brake requires zero spin-down calibration. All CP/FTP tests must use Slope/Incline mode (+1.5% to +2.0%); ignore `left_right_balance` stream; keep plugged into AC power during recovery periods (`HEUR-006`).

## 5. Environment & Security Invariants
- **Credential Hygiene:** TrainingPeaks credentials and session cookies must reside strictly in `config/.env` / `config/trainingpeak.env` and must never be committed to git or exposed in logs.
- **API Rate Limiting:** All scraping and TrainingPeaks REST sync calls must incorporate random jitter (1.0–3.0s) and handle HTTP 429 backoff gracefully.
