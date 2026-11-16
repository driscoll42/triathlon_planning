# Domain Lode: `workouts`

## 1. Scope & Responsibility
80/20 workout library loading, 5-zone power/pace/HR mapping, Polarized vs. Pyramidal Time-in-Zone allocations, candidate filtering, brick workout modeling, and custom workout design archetypes.

## 2. Key Modules & Entrypoints
- [`data/Bike.xlsx`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/data/Bike.xlsx) (225 workouts), [`data/Run.xlsx`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/data/Run.xlsx) (194 workouts), [`data/Swim.xlsx`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/data/Swim.xlsx) (74 workouts): Curated 80/20 Endurance structured workout libraries.
- [`data/brick_workouts.csv`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/data/brick_workouts.csv) (9 workouts): Multi-sport combinations (Bike-to-Run and Swim-to-Bike transitions).
- [`data/strength_workouts.csv`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/data/strength_workouts.csv) (10 workouts): Hybrid resistance training library covering Maximum Strength, Hypertrophy, Maintenance, Plyometrics, and Deload.
- [`recommended_workout_legos.csv`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/recommended_workout_legos.csv): Master catalog of modular workouts across all 5 disciplines.
- [`src/triplan/library/util.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/util.py): [`load_workouts()`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/util.py), [`calculate_plan_score()`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/util.py).
- [`src/triplan/library/clean_file.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/clean_file.py): [`calculate_TSS()`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/clean_file.py), [`calculate_sTSS()`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/clean_file.py).
- [`src/triplan/solver/scheduler.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py): [`CandidateWorkout`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/model/candidates.py), [`enrich_workouts_from_df()`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py).

## 3. Core Architectural Rules & Workout Formulations

### A. Canonical 80/20 7-Zone Power Scale
Commercial 5-zone systems (such as Stryd 5-Zone and MyProCoach 5-Zone) inappropriately place Zone 2 at 80%–90% CP, crossing above $LT_1$ into the moderate grey zone. All workout prescriptions and `.fit` ingestion pipelines map strictly to the **80/20 7-Zone Scale** anchored to verified Critical Power ($CP$):

| Zone | Physiological Role | Run Power (% Stryd CP) | Bike Power (% Bike CP) | Optimizer Bucket | ANS Cost Weight |
|---|---|---|---|---|---|
| **Zone 1** | Active Recovery / Low Aerobic | 50% – 76% | 50% – 70% | Low (`z1_z2_dur`) | **0.0** |
| **Zone 2** | Aerobic Endurance (Ceiling = $LT_1$) | 76% – 88% | 70% – 83% | Low (`z1_z2_dur`) | **0.0** |
| **Zone X** | Moderate Drift Grey Zone *(Penalize)* | 88% – 94% | 83% – 91% | Grey Drift (`zx_dur`) | **0.7** (Unplanned Penalty) |
| **Zone 3** | Sub-Threshold / Tempo (Ceiling = $LT_2$) | 94% – 100% | 91% – 100% | Moderate/High (`z3_dur`) | **1.0** |
| **Zone Y** | Threshold Over-Reach Gap *(Penalize)* | 100% – 103% | 100% – 102% | High (`z4_dur`) | **1.4** (Gap Penalty) |
| **Zone 4** | Supra-Threshold / $\text{VO}_2\text{max}$ | 103% – 120% | 102% – 110% | High (`z4_dur`) | **1.8** |
| **Zone 5** | Anaerobic / Repetition ($W'$ Depletion) | > 120% | > 110% | High (`z5_dur`) | **3.5** |

- **Physiological Sanity Checkpoints:**
  - **Run $LT_1$ (88% CP):** Must pass the Talk Test and keep HR $\le 89\%$ LTHR.
  - **Run/Bike $LT_2$ (96%–100% CP):** HR must plateau near LTHR during a 15–20 minute interval.
- **Single Source of Truth Rule:** Disable `Auto-CP` in Stryd PowerCenter and `Auto-FTP` on Garmin Edge 530/Enduro 3 during training blocks. The exact same manual $CP$ integer must be locked across: (1) Stryd PowerCenter, (2) TrainingPeaks Run/Bike Power Zones, and (3) Optimizer configuration (`athlete_profile.py` / `config.yaml`).
- **Alactic Strides Exemption:** Alactic strides ($\le 15-20\text{s}$) in Zone 5 rely exclusively on the ATP-PCr system without glycolytic metabolite accumulation and receive zero ANS penalty ($0.0$).

### B. Polarized vs. Pyramidal Time-in-Zone Distributions
- **80/20 Baseline Invariant:** Both distributions allocate 75%–85% of total training time to low intensity (Zones 1–2, below $LT1$).
- **Polarized TID (~80% Z1–Z2 / 4%–6% Z3 / 14%–16% Z4–Z5):** Emphasizes work above $LT2$ ($VO_2\text{max}$ intervals) while suppressing Zone 3 tempo. Ideal for Sprint/Olympic distance triathlons and general Base phases to expand maximal aerobic capacity.
- **Pyramidal TID (~75%–78% Z1–Z2 / 15%–20% Z3 / 4%–7% Z4–Z5):** Concentrates moderate-to-hard volume in Zone 3 (Tempo TE1 and SweetSpot/Low-ME1). Ideal for the Specific Build phase of Half-Ironman (70.3) and Ironman (140.6) athletes (Vance 70.3 Bike IF 0.70–0.85; IM Bike IF 0.65–0.80).
- **Long-Course Inverted Periodization:** Transition from Polarized Base (ceiling building) $\to$ Pyramidal Build (prolonged race-pace durability in Zone 3) over the final 8–12 weeks prior to race taper.

### C. Modular LEGO Catalog & Strength Specialization (`recommended_workout_legos.csv`)
The modular LEGO catalog standardizes sessions across all 5 disciplines:
1. **Strength Disciplines (`data/strength_workouts.csv`):**
   - *Anatomical Adaptation (`STR_AA1`):* High-volume (15–20 reps) dumbbell/core movements establishing tendon stiffness.
   - *Maximum Strength (`STR_MS1_HEAVY`, `STR_MS2_HEAVY`):* 85%–88% 1RM barbell compound drivers (Back Squat, Conventional Deadlift, OHP, Bench; no trap bar per `TASK-028`).
   - *Cap-compatible heavy lifts (`STR_HV1_SQUAT`, `STR_HV2_DEADLIFT`, `STR_HV3_SQUAT_SL`, `STR_HV4_DEADLIFT_SL`, `TASK-033`):* 87.5% 1RM main lift, 2-3 heavy lower-body barbell sets + bench, axial 53-62 / neuro 66-73 so they fit CURRENT_ATHLETE's daily caps (65/75). Loads from `library.build.strength_loads`; added by `scripts/migrations/m2026_11_16_cap_compatible_heavy_lifts.py` (the MS rows at axial 80-95 remain but are unschedulable under those caps).
   - *Long-course long sessions (`RUN_LC_Z2_90/105/120/150`, `BIKE_LC_OUT_210/240/300/360`, `TASK-040`, `ADR-024`):* 100% Z1/Z2 (80/20), `agent_authored`, `lib_default`; long runs `is_long_run` + `is_heavy_run`, rides outdoor-only. Values from `library.build.power_tss` / `endurance_flags` / `endurance_loads` (`long_endurance_rows`), added by `scripts/migrations/m2026_11_16_long_course_library.py`. Profiles whose day caps cannot host them never see them (`control/periodization.py::drop_unhostable_long_sessions`).
   - *In-Season Maintenance (`STR_SM1_MAINT`, `STR_SM2_MAINT`):* 30–45m sessions sustaining neural drive with minimal DOMS.
   - *Plyometrics (`STR_PLYO1`):* Depth jumps (<200ms GCT), pogo hops, and broad jumps for leg-spring stiffness ($LSS$).
   - *Upper Hypertrophy (`STR_UPPER_HYP`):* DB incline press, weighted pull-ups, and DB rear-delt flys for aero and swimming posture.
   - *Unilateral Lower (`STR_LOWER_UNI`):* DB Bulgarian split squats and single-leg RDLs avoiding home dumbbell limits.
   - *Posterior & Core (`STR_POST_CORE`):* Barbell hip thrusts and Nordic curls with zero axial spinal compression.
   - *Regeneration Deload (`STR_DELOAD`):* Mobility flows and prone Y-T-W (no bands, `DECISION-022`) for Step Week 4 recovery.
2. **Endurance Disciplines (`Run.xlsx`, `Bike.xlsx`, `Swim.xlsx`, `brick_workouts.csv`):**
   - *Bike:* Lactate shuttle over-unders (`BK_TH_OU1/2`), low-cadence force & torque (`BK_FR_TQ1/2`), and $VO_2\text{max}$ (`BK_MAP_VO2`).
   - *Run:* Base with alactic strides (`RUN_SS1/2_STR`), progression long runs (`RUN_FF_PROG`), and track intervals (`RUN_VO2_MAP`).
   - *Swim:* CSS threshold sets (`SW_CSS_TH1/2`) and in-water propulsive paddle force (`SW_FORCE1`).
   - *Bricks:* T2 exit control (`RUN_TB1_BRICK`), multi-hour durability (`RUN_TB2_BRICK`), and Swim-to-Bike T1 transitions (`SW_T1_BRICK`).

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
