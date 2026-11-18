# Domain Lode: `physiology`

## 1. Scope & Responsibility
Dual-load modeling (metabolic aerobic TSS vs peripheral mechanical strain, `ADR-004`), exponential ANS recovery costing, asynchronous multi-tissue remodeling dynamics ($M(t), C(t), ODI$), single-source-of-truth critical power & 80/20 7-zone physiology (`ADR-009`, `TASK-013`), CP retesting lockouts (`ADR-011`), quality-minute classification (`q_i`), and AMPK–mTORC1 concurrent training rules (`ADR-020`).

## 2. Key Modules & Entrypoints
- [`src/triplan/physiology/athlete_profile.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/athlete_profile.py): Frozen `AthleteProfile` loaded from [`config/athlete_profile.yaml`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/config/athlete_profile.yaml) (`CURRENT_ATHLETE`), canonical `bike_cp`/`run_cp`/`swim_css` (read-only `bike_ftp`/`run_ftp` aliases), validation, and `to_mesocycle_config()` / `to_hybrid_config()` (`TASK-035`).
- [`src/triplan/physiology/state.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/state.py): `AthletePhysiologicalState`, `ALPHA_ACUTE`/`ALPHA_CHRONIC`/`ALPHA_ATL`/`ALPHA_CTL`, convolutional tissue remodeling ($M(t), C(t), ODI$), Banister PMC accumulators, EWMA running ACWR, and `step_day()`.
- [`src/triplan/solver/engine/session_quality.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/engine/session_quality.py): Sport-specific quality minutes `q_i`, incidental grey-zone filtering, hard/key session masks, and canonical 80/20 Time-in-Zone helpers (`ADR-020`).
- [`src/triplan/library/compound.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/compound.py): `DECISION-021` lift + easy cardio compound candidate builder.
- [`src/triplan/model/classes.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/model/classes.py): `triVariables` zone ratios and sport target defaults (thresholds removed in `TASK-013`).

## 3. Core Architectural Rules & Physiological Laws

### A. Asynchronous Tissue Remodeling & Orthopedic Dissociation Index (`ADR-004`)
Human tissues adapt along distinct first-order exponential timelines:
1. **ANS & Glycogen Resynthesis ($\tau_1 = 3.0\text{ d}$):** Vagal reactivation (`HRV RMSSD`) and GLUT4 glycogen restoration.
2. **Cardiovascular / Mitochondrial ($\tau_2 = 35.0\text{ d}$):** Plasma volume and PGC-1$\alpha$ biogenesis (PMC CTL standardized to 42d).
3. **Muscular / Neuromuscular ($\tau_3 = 42.0\text{ d}$):** High-threshold motor unit recruitment and myofibrillar remodeling.
4. **Tendon Collagen & Cortical Bone ($\tau_4 = 120.0\text{ d}$, or $210.0\text{ d}$ for Masters $\ge 45$):** Slow avascular interstitial diffusion.

Parallel impulse-response accumulators track metabolic fitness $M(t)$ ($\tau_m = 35\text{ d}$) vs connective chassis adaptation $C(t)$ ($\tau_c = 120\text{ d}$):
$$M(t) = M(t-1)e^{-1/\tau_m} + \text{aerobic\_tss}_t(1 - e^{-1/\tau_m}), \quad C(t) = C(t-1)e^{-1/\tau_c} + \text{mechanical\_strain}_t(1 - e^{-1/\tau_c})$$
$$\text{ODI}(t) = \frac{M(t)}{C(t)} \le 1.35$$
When $\text{ODI} > 1.35$, the MPC tightens mechanical strain caps and shifts metabolic volume into non-impact swimming/cycling.

### B. Dual-Load Formulation & Sport Strain Exponents
$$\text{mechanical\_strain}_w = \text{duration}_w \cdot S_{sport(w)} \cdot (\text{IF}_w)^{\beta_{sport(w)}}$$
- **Swimming ($S=0.05, \beta=1.2$):** Weight-supported upper-body concentric propulsion.
- **Cycling ($S=0.10, \beta=2.0$):** Closed-chain non-impact pedaling.
- **Running ($S=1.00, \beta=2.5$):** Ballistic $2.5\text{–}3.0\times$ BW ground impact + eccentric SSC.
- **Heavy Resistance ($S=1.50, \beta=3.0$):** High axial compression and tendon shear.
- **Separate PMCs (Vance):** Bike and Run CTL/ATL/TSB are tracked independently; Bike→Run bricks split TSS proportionally between Bike and Run in `step_day()` (`ADR-030`).

### C. Single Source of Truth Thresholds, Two-Point CP & Retesting (`ADR-009`, `ADR-011`, `TASK-013`)
- **Single Source of Truth:** Frozen `AthleteProfile` (`config/athlete_profile.yaml`) is the sole owner of `bike_cp`, `run_cp`, and `swim_css` (manual integers; Auto-CP/Auto-FTP disabled in Stryd and Garmin). `apply_cp_retest_result()` returns a new `AthleteProfile` via `dataclasses.replace()`.
- **Two-Point CP Protocol:** Concurrent heavy barbell lifting inflates anaerobic work capacity ($W'$), making fixed-multiplier proxy tests ($0.75 \times \text{MAP}$ ramps or $0.95 \times P_{20}$ TTs) overestimate $LT_2$. Calibrate via Two-Point Linear CP ($P_3$ 180s + $P_{12}$ 720s: $CP = \frac{4 P_{12} - P_3}{3}$, $W' = 180(P_3 - CP)$) or Kolie Moore 35–45 min MLSS baseline.
- **Aero Positional Rule & CSS:** Test Bike CP in aero position (upright hoods read 3%–7% / 10–20W higher). Swim CSS uses 400m/200m TTs: $CSS = \frac{200}{T_{400} - T_{200}}$.
- **Retest Cadence & Lockouts (`ADR-011`):** Currently enforced in `control/periodization.py` via `filter_test_workouts()` (no tests in week 1, taper/race weeks, bike locked $\le 4\text{ wk}$ and run $\le 6\text{ wk}$ in re-entry, recovery weeks only, $\ge 8\text{ wk}$ gap). Automated telemetry-triggered retest prompts wait for `.fit` actuals ingestion (`TASK-024`).

### D. Session Quality `q_i`, Canonical 80/20 Engine Rules & AMPK–mTORC1 (`ADR-020`, `TASK-035`)
- **Quality Minutes `q_i`:** Replaces IF-based hard classification. $q_i$ sums Z4-equivalent minutes (weights: Z1 `0`, Z2 `0`, ZX `1/6`, Z3 `1/3`, ZY `2/3`, Z4 `1`, Z5 `1`, excluding Z5 alactic strides). After incidental allowance (`2 + 1/h`), $0 < q_i < 5$ is grey-zone (pre-filtered), $q_i \ge 5$ is `is_hard_endurance`, and `key = hard OR anchor OR long` (`Run >= 70 min`, `Bike >= 120 min`).
- **Canonical 80/20 Hard Bounds:** Across weekly endurance minutes (strength excluded), `Z1+Z2 >= 78%` (`intensity_low_min`) and `Z3+ZY+Z4+Z5 <= 20%` (`intensity_mod_high_max`) are hard constraints in both facades, with soft targets toward `80%` low (`intensity_low_target`) and `0` Zone X. `TASK-035` ensures `AthleteProfile` forwards these targets, `key_sessions_per_week`, and `am_pm_min_gap_h` directly into solver configs.
- **AMPK–mTORC1 & Hard-Days-Hard Stacking:** Dual-session days require $\ge 6\text{ h}$ separation (`am_pm_min_gap_h`), stacking heavy lower lifting on hard endurance days (AM endurance + PM lift, or AM lift + PM Z1/Z2 recovery).
- **`DECISION-021` Lift + Easy Cardio Combos:** Strength may be followed immediately in the same window by $\le 20\text{ min}$ strictly Z1/Z2 Bike (or Run only after non-heavy lifts), counting as one window and toward the cardio sport's volume/run limits.

### E. Durability & Axial Kinematics
- **Durability (Maunder et al., 2021):** Late-session threshold intervals after $>1,500\text{–}2,000\text{ kJ}$, low-cadence torque (55–65 rpm), and T2 bricks train fatigue resistance (target Vance cadence+power fade $\le 13\%$ 70.3, $\le 20\%$ IM).
- **Lumbar & Shoulder Protection:** Heavy deadlifts impose $18.45\text{ kN}$ compressive / $3.28\text{ kN}$ shear load (`HEUR-004` $\pm 24\text{h}$ run lockout; synergistic with swim); heavy upper press excludes same-day CSS threshold swim; barbell sets $\ge 60\%$ keep capped $\le 75\text{ lb}$ dumbbells for unilateral/hypertrophy work (`ADR-008`).

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs; edit narrative sections above, not this block. -->

## Registered Lineage (`physiology`)

### Active Architectural Decisions (`ADR`)
- **`ADR-004` — Decouple Aerobic TSS and Mechanical Strain with Asynchronous Tissue Remodeling:** Model metabolic load (tau=35d) separately from mechanical strain (tau=120d) with an Orthopedic Dissociation Index <= 1.35.
- **`ADR-009` — Adopt Canonical 80/20 7-Zone Power System and Two-Point Critical Power Protocol:** Enforce 80/20 7-zone power scale with Zone X/Y penalties and calibrate CP via 3m+12m protocol due to high W' from heavy lifting.
- **`ADR-011` — Integrate Closed-Loop Critical Power Retesting Engine with Dual Telemetry Triggers and Mesocycle Synchronization:** Retest CP every 8-12w in recovery deloads or via cardiac decoupling/EF triggers; enforce <=10% step jump caps and re-entry testing lockouts.
- **`ADR-020` — Sport-specific quality q_i classifies hard and key sessions; lift+easy combos are compound candidates; canonical 80/20 is a hard engine rule:** Hard/key = minutes-based q_i, not IF; lift+easy combos are compound candidates; Z1+Z2>=78% and Z3+ZY+Z4+Z5<=20% are hard rows in both facades.
<!-- LODE_AUTOGEN_END -->
