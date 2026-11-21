# Domain Lode: `physiology`

## 1. Scope & Responsibility
Dual-load modeling (metabolic aerobic TSS vs peripheral mechanical strain, `ADR-004`), exponential ANS recovery costing, asynchronous multi-tissue remodeling dynamics (run-strain $F(t), S(t)$, $\text{ODI}^{\text{run}}$), single-source-of-truth critical power & 80/20 7-zone physiology (`ADR-009`, `TASK-013`), CP retesting lockouts (`ADR-011`), quality-minute classification (`q_i`), and AMPK–mTORC1 concurrent training rules (`ADR-020`).

## 2. Key Modules & Entrypoints
- [`src/triplan/physiology/athlete_profile.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/athlete_profile.py): Frozen `AthleteProfile` loaded from [`config/athlete_profile.yaml`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/config/athlete_profile.yaml) (`CURRENT_ATHLETE`), canonical `bike_cp`/`run_cp`/`swim_css` (read-only `bike_ftp`/`run_ftp` aliases), validation, and `to_mesocycle_config()` / `to_hybrid_config()` (`TASK-035`).
- [`src/triplan/physiology/load_model.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/load_model.py): Unified load model (`TASK-021`, `ADR-034`): `ANS_WEIGHTS` / `ans_cost`, zone-power `zone_strain` / `brick_strain`, set-intensity `strength_strain` (`STRENGTH_SET_SPECS`), `strength_tss`, and exact `banister_alpha`. Single source for `library.build`, `model.classes` and `athlete_profile`.
- [`src/triplan/physiology/state.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/state.py): `AthletePhysiologicalState`, `ALPHA_ACUTE`/`ALPHA_CHRONIC`/`ALPHA_ATL`/`ALPHA_CTL`, run-strain EWMAs $F(t), S(t)$ and $\text{ODI}^{\text{run}}$, Banister PMC accumulators, EWMA running ACWR, and `step_day()`.
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

Two parallel impulse-response accumulators of the **same** daily run strain $s_t$ (`run_mech`) track fast (cardio-horizon) and slow (collagen) adaptation, $\tau_f = 35\text{ d}$ and $\tau_s = 120\text{ d}$ ($210\text{ d}$ Masters):
$$F(t) = F(t-1)e^{-1/\tau_f} + s_t(1 - e^{-1/\tau_f}), \quad S(t) = S(t-1)e^{-1/\tau_s} + s_t(1 - e^{-1/\tau_s})$$
The Banister PMC uses the same exact discretisation, $\alpha = 1 - e^{-1/\tau}$ with $\tau_{CTL}=42$ d ($\alpha=0.0235$) and $\tau_{ATL}=7$ d ($\alpha=0.1331$) (`ALPHA_CTL`, `ALPHA_ATL`; `TASK-021`). The old $2/(N+1)$ form made the effective constants about 21 d / 3.5 d (42 days of 100 TSS/day read CTL 86.5, truth 63.2). Running ACWR keeps the published $2/(N+1)$ EWMA convention (`ALPHA_ACUTE`, `ALPHA_CHRONIC`). Because the same weekly TSS now moves CTL ~1.9x slower, `max_weekly_ctl_ramp` is 3.75 (was 7.0) and the masters ramp 2.75 (was 5.0): the same TSS ramp is permitted.
$$\text{ODI}^{\text{run}}(t) = \frac{F(t)}{S(t)} \le 1.35 \quad (\le 1.20\text{ Masters})$$
Dimensionless (run strain over run strain): $1.0$ at steady state, $>1$ only while run load outpaces collagen remodeling, and it falls when run strain is cut (`TASK-022`, `ADR-035`; the previous $M(t)$ was all-sport TSS over a run-strain $C(t)$, so its throttle drove the index up). It is an in-model per-day Tier-0 row (`safety.md`), not a post-hoc throttle; the all-sport TSS EWMA now lives only in the Banister `total_ctl` / `total_atl`. Profile seeds: `initial_run_strain_fast` / `initial_run_strain_slow`.

### B. Unified Load Model: ANS Cost, Mechanical Strain, Strength TSS (`ADR-034`, `TASK-021`)
One definition per quantity in [`src/triplan/physiology/load_model.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/load_model.py), computed from zone minutes (never typed in). `library.build.recompute_load_columns` applies it to every library row (`scripts/migrations/m2026_11_19_unify_load_model.py`); `derive_8020_rows`, `materialize_distance_row`, `endurance_loads`, `compound.cardio_piece` and the gap-fill / long-course / brick builders call the same functions.

**ANS cost** (replaces three conflicting definitions: the `SPEC.md` table, $e^{0.65(z-1)}$ in `library.build`, and hand-entered hybrid values):
$$\text{ANS}_i = \sum_{z \in \{Z1,Z2,ZX,Z3,ZY,Z4,Z5\}} w_z\, t_{i,z} + \gamma \max(0,\, D_i - D_0), \quad w=(0,0,0.7,1.0,1.4,1.8,3.5),\ D_0=90\text{ min},\ \gamma=0.35$$
Alactic strides ($\le 20$ s, Zone 5) carry weight 0. Easy Z1/Z2 minutes are free until $D_0$, so the 48h ANS cap now binds intensity, not volume (a 240 min Z1/Z2 ride is 52.5, was 372; `TASK-050`). Rolling 48h cap **120** (charter) / **55** (`CURRENT_ATHLETE`), recalibrated by matched exclusion from 700 / 450 (`scripts/calibrate_ans_caps.py`, `ADR-034`).

**Mechanical strain** is zone-additive, $S = \kappa \sum_z t_z\, p_z^{\beta}$ with $p_z$ the zone midpoint as a fraction of CP (CSS for swim); $(\kappa, \beta)$ reproduce the pre-`TASK-021` closed forms for a uniform-intensity session so every strain cap keeps its meaning:
- **Running** $(\kappa=100/60,\ \beta=3)$: equals $\text{TSS} \times \text{IF}$ for a constant session. $\beta=3$ is a sensitivity-bounded prior (`EXP-006` confirmed ACWR is invariant across $\beta \in \{1,1.5,2,3\}$ and no injury-labelled data exists); $\beta=2$ would loosen every high-intensity run (Tier-0), so it is not adopted. `EXP-006` also verified `BIKE_MID7[1] = 0.765` against 667 power rides (realized easy median `0.753`, +1.65%) and `TASK-025` readiness $\alpha_d$ weights `(0.08, 0.05, 0.04)` on 263/270 HRV/RHR nights (`14.45% < 0.85`).
- **Cycling** $(10/60,\ 2.2)$ = $0.10\,\text{TSS}\,\text{IF}^{0.2}$. **Swimming** $(5/60,\ 3)$ = $0.05\,\text{sTSS}$. **Brick** = run leg on the run model + the remainder on the bike model (run leg alone is `brick_run_strain`, which feeds ACWR).
- **Strength** is set-intensity based, $S = \kappa_s \sum_e c_e\, \text{sets}\,\text{reps}\,p_e^{4}$ over the per-row set specs `STRENGTH_SET_SPECS` ($c$: lower 1.0, upper 0.4, core 0.15, plyometric 1.0; $p$ = fraction of 1RM; reps per limb). $\kappa_s$ anchors the mean of the four cap-compatible heavy variants (`TASK-033`) at 145.8. A heavy squat is now a heavy squat in every library (hybrid rows were 12–32, i.e. an order of magnitude too low).

**Strength TSS** is 10–20 aerobic TSS per hour (10 when all Zone 1, 20 when none is), not 42–58; its cost lives in strain, axial and neuromuscular load. Sport strain keeps `neuromuscular_load` / `axial_load` rules (run $0.65 / 0.38 \times$ strain when heavy).

**Separate PMCs (Vance):** Bike and Run CTL/ATL/TSB are tracked independently; Bike→Run bricks split TSS proportionally in `step_day()` (`ADR-030`). `total_ctl` / `total_atl` integrate all-sport TSS including strength, the same load as $M(t)$ (they previously left strength out).

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
- **Interval-Parsed Durability Score (`TASK-023`, `ADR-036`):** `durability_score` is no longer typed in (it used two scales, `0..10` vs `0..85`). `library/intervals.py` parses each endurance row's ordered interval description (validated against its stored zone minutes; free-form rows fall back to zone columns) into $T^{\text{thr}}_i$ (Z3/ZY/Z4/Z5 minutes, alactic strides excluded), $T^{\text{cont}}_i$ (longest continuous threshold block), $\text{late}_i$ (threshold minutes after 60% of elapsed time) and $\text{brick}_i$, then $D_i = \min(10,\ 1.5 + 5\,\text{sat}\tfrac{T-30}{210} + 2.5\,\text{sat}\tfrac{T^{\text{cont}}}{30} + 1\,\text{sat}\tfrac{T^{\text{thr}}}{45} + 1.5\,\text{sat}\tfrac{\text{late}}{20} + 1.5\,\text{brick})$. The weights are priors (`EXP-006`); strength stays `2.0`.
- **Walk-Jog Impact Model (`TASK-023`):** `load_model.run_walk_mechanical_strain(run_zone_minutes, walk_min)` = run-zone strain + `walk_min` x `walk_dose_fraction()` x one Zone 1 run minute, where the fraction is the stride-count x (peak GRF)$^3$ ratio of walking to easy running (110 vs 170 steps/min, 1.2 vs 2.2 BW) inflated by a x2 safety factor (about 0.21): deliberately conservative so a walk component can never loosen the in-model ACWR / strain / ODI rows. All constants are priors pending `EXP-006`.
- **Lumbar & Shoulder Protection:** Heavy deadlifts impose $18.45\text{ kN}$ compressive / $3.28\text{ kN}$ shear load (`HEUR-004` $\pm 24\text{h}$ run lockout; synergistic with swim); heavy upper press excludes same-day CSS threshold swim; barbell sets $\ge 60\%$ keep capped $\le 75\text{ lb}$ dumbbells for unilateral/hypertrophy work (`ADR-008`).

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs; edit narrative sections above, not this block. -->

## Registered Lineage (`physiology`)

### Active Architectural Decisions (`ADR`)
- **`ADR-004` — Decouple Aerobic TSS and Mechanical Strain with Asynchronous Tissue Remodeling:** Model metabolic load (tau=35d) separately from mechanical strain (tau=120d) with an Orthopedic Dissociation Index <= 1.35.
- **`ADR-009` — Adopt Canonical 80/20 7-Zone Power System and Two-Point Critical Power Protocol:** Enforce 80/20 7-zone power scale with Zone X/Y penalties and calibrate CP via 3m+12m protocol due to high W' from heavy lifting.
- **`ADR-011` — Integrate Closed-Loop Critical Power Retesting Engine with Dual Telemetry Triggers and Mesocycle Synchronization:** Retest CP every 8-12w in recovery deloads or via cardiac decoupling/EF triggers; enforce <=10% step jump caps and re-entry testing lockouts.
- **`ADR-020` — Sport-specific quality q_i classifies hard and key sessions; lift+easy combos are compound candidates; canonical 80/20 is a hard engine rule:** Hard/key = minutes-based q_i, not IF; lift+easy combos are compound candidates; Z1+Z2>=78% and Z3+ZY+Z4+Z5<=20% are hard rows in both facades.
- **`ADR-034` — Unified ANS cost, zone-power mechanical strain, set-intensity strength strain, 10-20 TSS/h strength and exact Banister PMC decay:** ANS, strain, strength TSS are computed once in physiology/load_model.py; Banister PMC uses alpha=1-exp(-1/tau); 48h ANS cap 120 (charter) / 55.

### Empirical Experiments (`EXP`)
- **`EXP-006` [`ACTIVE` | `TIER_1_ISOLATED`] — Load-Model Calibration Against Historical Garmin and Lifting Telemetry:** Calibrated load model on 2,020 FIT files (2,014 deduped) + 270 HRV nights: exact Banister decay removes +36.8% CTL/+37.1% ATL step-response bias, Bike easy IF 0.753 vs 0.765 (+1.6%), beta=3.0 retained as Tier-0 prior, and TASK-025 alpha_d places 14.45% of nights <0.85.
<!-- LODE_AUTOGEN_END -->
