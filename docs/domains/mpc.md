# Domain Lode: `mpc`

## 1. Scope & Responsibility
3-Tier Closed-Loop Model Predictive Control (MPC), receding-horizon schedule updates, daily morning biometrics readiness scaling, calendar slot stability invariant, real-time compliance monitoring ("moderate-intensity rut" detection), mesocycle step-cycles, and fast-decay tapering.

## 2. Key Modules & Entrypoints
- [`src/closed_loop_optimizer.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py): [`ClosedLoopMPCController`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py#L655-L850), [`MesoCycleTargetsAndConstraints`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py#L60-L90), [`AthletePhysiologicalState`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py#L95-L160), and [`AthleteBiometrics`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py#L165-L185).
- [`src/main.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/main.py): Driver script executing 16-week closed-loop season simulation under stochastic readiness perturbations.

## 3. Core Architectural Rules & Control Theory Mechanics

### A. Tier 1: Daily Micro-Adjustments & Morning Readiness Scalar ($R_t$)
Every morning prior to training, the controller ingests morning HRV RMSSD (against the athlete's 7-day rolling baseline), sleep duration/quality score, and subjective muscle soreness / Visual Analog Pain scale (or Anderson 6-item overtraining questionnaire) to compute the dimensionless readiness scalar:
$$R_t = 0.50 \cdot \left(\frac{\text{RMSSD}_t}{\text{RMSSD}_{7\text{d\_mean}}}\right) + 0.30 \cdot \text{SleepRatio}_t + 0.20 \cdot \text{SorenessRatio}_t \quad \in [0.70, 1.10]$$

- **Calendar Slot Stability Invariant:** To protect the athlete's work schedule and facility bookings, Tier 1 never reshuffles scheduled workouts mid-week. It scales intensity and duration in-place:
  - **Red Alert ($R_t < 0.80$ or Pain > 3/10):** Scheduled Zone 4/5 or heavy resistance workouts are automatically converted to Zone 1 Active Recovery (AE1, duration scaled by $0.50\times$) or transformed into a complete rest day if $R_t < 0.70$.
  - **Amber Caution ($0.80 \le R_t < 0.95$):** Retains workout structure, reduces high-intensity interval volume by 25%, and scales Zone 4/5 power/pace targets by $R_t$.
  - **Green Nominal ($0.95 \le R_t \le 1.05$):** Workout executes exactly as prescribed.
  - **Supercompensation Peak ($R_t > 1.05$):** Interval targets may be nudged upward $+3\%\text{ to }+5\%$, provided rolling 48-hour stress caps are respected.

### B. Tier 2: Weekly Receding-Horizon Re-Optimization (Sunday 23:59 Solve)
Every Sunday night at 23:59, the controller closes the weekly feedback loop using actual completed loads from Monday through Sunday:
1. **State Vector Updates:** Computes single-sport and composite CTL, ATL, and TSB; updates metabolic and connective tissue convolution states $M(t), C(t)$, and $ODI(t)$; updates running chassis EWMA mechanical ACWR.
2. **Dynamic Strain Clamping:** If $\text{ACWR} > 1.30$ or $\text{ODI} > 1.35$, the controller lowers the upcoming week's running mechanical strain ceiling to bring tissues back into the safe adaptation zone.
3. **Joe Friel Missed-Workout Rule:** Skipped workouts are never crammed into subsequent days if doing so would breach the 48-hour multi-stress caps or ACWR ceiling. Instead, the deficit is absorbed during the Sunday receding-horizon re-solve.
4. **Receding Horizon Re-Solve:** Executes the HiGHS MILP scheduler across the upcoming 7-to-14-day rolling window to generate the top-$K$ optimal plans.

### C. Tier 3: Mesocycle Periodization & Step-Cycle Progression
Across macrocycle blocks, Tier 3 adjusts the target vector across structured 3:1 step cycles (3 building weeks, 1 recovery week) and executes a fast-decay exponential taper:

| Mesocycle Phase / Step Week | Weekly $\text{TSS}_{target}$ Progression | Intensity Distribution (POL / PYR) | Durability Weight ($W_{durab}$) | Heavy Strength ($F_{str\_req}$) | Nutritional Carbohydrate Target |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **Base 1–2 (Load Wk 1–2)** | +4 to +6 CTL/wk (+5%–8% TSS) | **Polarized:** 25% / 55% / 4% / 10% / 6% | 10.0 (Low) | 2 sessions/wk (MS) | 5–7 g/kg/day (Optional 1x carb-fasted AE2) |
| **Build 1 (Step Wk 1)** | 100% Baseline Build TSS (+6 CTL/wk) | **Pyramidal (70.3/IM):** 25% / 55% / 14% / 5% / 1% | 25.0 (High) | 1 session/wk (SM) | 7–10 g/kg/day (30–60 g/h in >2h sessions) |
| **Build 2 (Step Wk 2)** | 108%–110% Build TSS (+6 to +8 CTL/wk) | **Pyramidal Peak:** 22% / 53% / 18% / 6% / 1% | 35.0 (Maximal) | 1 session/wk (SM) | 7–12 g/kg/day (Up to 90–105 g/h dual-source) |
| **Recovery Week (Step Wk 3)** | 60%–70% Build TSS (-30% to -40% drop) | **Restoration:** 45% / 55% / 0% / 0% / 0% | 0.0 (Off) | 0 sessions/wk | 3–5 g/kg/day (High Omega-3 & antioxidant) |
| **Peak / Taper (14 Days Out)** | **Fast-Decay Exponential:** -41%–60% Swim/Bike, -21%–40% Run | **Race Sharpening:** 30% / 50% / 10% / 7% / 3% (Maintain intensity touches, frequency $\ge 80\%$) | 5.0 (Rehearsal) | 0 sessions/wk | 5–7 g/kg/day (200–300g carb 1–4h pre-race) |

- **Fast-Decay Taper Parameters:** 14-day duration, exponential volume decay (Bosquet/Mujika meta-analysis), preserving training frequency ($\ge 80\%$) and race-pace intensity touches to prevent neuromuscular deconditioning, targeting race-day $\text{TSB} \in [+10, +20]$ with $\le 7\%-10\%$ CTL decay.

### D. Real-Time Compliance Engine & "Moderate-Intensity Rut" Detection
Post-workout ingestion calculates three fractional execution error metrics:
$$\epsilon_{dur} = \frac{|D_{real} - D_{plan}|}{D_{plan}}, \quad \epsilon_{tss} = \frac{|\text{TSS}_{real} - \text{TSS}_{plan}|}{\text{TSS}_{plan}}, \quad \epsilon_{int} = \frac{|\text{IF}_{real} - \text{IF}_{plan}|}{\text{IF}_{plan}}$$
- **Intensity Creep Detection:** If an athlete executes a prescribed Zone 1/2 easy session with intensity creep ($IF_{real} > 0.78$ or $HR > LT1$), the session is dynamically reclassified as a Hard Day ($hard_d = 1$).
- **Dynamic Throttle:** Realized exponential ANS cost is injected into the 48-hour rolling stress window, automatically down-regulating the following day's prescription to prevent sympathetic overtraining.

### E. Hybrid Athlete Closed-Loop MPC Dynamics
The controller in `ClosedLoopMPCController` auto-regulates both endurance and strength:
1. **Tier 1 Biometric Auto-Regulation:** Computes Garmin morning readiness scalar:
   $$\alpha_d = 0.40 \cdot \left(\frac{\text{HRV}_d}{\text{HRV}_{7\text{d}}}\right) + 0.35 \cdot \left(\frac{\text{SleepScore}_d}{100}\right) + 0.25 \cdot \left(\frac{10 - \text{Soreness}_d}{10}\right) \quad \in [0.50, 1.20]$$
   - $\alpha_d < 0.65$: Downgrade strength session (remove 1–2 working sets, cap intensity at RPE 6–7); reduce run interval volume by 30%.
   - $\alpha_d < 0.50$: Hard override converts day to Active Recovery (AE1) or complete rest.
2. **Tier 2 Dynamic e1RM Kalman Filtering:** Tracks true underlying 1RM strength under noisy daily performance:
   $$e1RM_t = \text{load} \cdot \left(1 + \frac{\text{reps}}{30}\right)$$
   $$K_t = \frac{P_{t|t-1}}{P_{t|t-1} + \sigma_v^2}, \quad \hat{x}_{t|t} = \hat{x}_{t|t-1} + K_t(y_t - \hat{x}_{t|t-1}), \quad P_{t|t} = (1 - K_t) P_{t|t-1}$$
   where process noise $\sigma_w = 0.5\text{ kg}$ and observation noise $\sigma_v = 3.5\text{ kg}$.
3. **Tier 3 Mesocycle Wave Undulation:** Synchronizes resistance loading with endurance 3:1 step cycles:
   - *Week 1 (Volume Accumulation):* 70% 1RM, higher sets/reps, moderate axial strain.
   - *Week 2 (Hypertrophy / Load):* 75%–80% 1RM, target TSS $105\%$.
   - *Week 3 (Intensity Peak):* 85%–90% 1RM, heavy compound triples/singles, maximum durability emphasis.
   - *Week 4 (Deload):* 60% 1RM, volume reduced by 40%–50%, zero heavy axial deadlifts or squats.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
