# Domain Lode: `physiology`

## 1. Scope & Responsibility
Dual-load physiological modeling (metabolic/aerobic TSS vs peripheral orthopedic mechanical strain), exponential Autonomic Nervous System (ANS) recovery costing, asynchronous multi-tissue remodeling dynamics, physiological durability (fatigue resistance), and concurrent endurance–resistance signaling interference (AMPK–mTORC1).

## 2. Key Modules & Entrypoints
- [`src/triplan/physiology/athlete_profile.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/athlete_profile.py): [`AthleteProfile`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/athlete_profile.py), baseline thresholds (Bike FTP, Run FTP/CP, Swim CSS), chronic/acute starting states, and `Masters Mode` (`age >= 45`).
- [`src/triplan/model/classes.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/model/classes.py): [`triVariables`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/model/classes.py), intensity zone boundaries, power/speed multipliers, and sport target splits.
- [`src/triplan/solver/scheduler.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py): [`AthletePhysiologicalState`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/state.py), convolutional tissue remodeling states ($M(t), C(t), ODI$), Banister PMC accumulators, EWMA mechanical ACWR, and exponential ANS costing.

## 3. Core Architectural Rules & Physiological Laws

### A. Asynchronous Tissue Remodeling Timelines
Human organ systems and connective tissues remodel along disparate biological timelines governed by local vascularity, cellular turnover rates, and mechanotransduction pathways. The platform models adaptation across four distinct first-order exponential subsystems:
1. **Autonomic Nervous System (ANS) & Glycogen Resynthesis ($\tau_1 = 3.0\text{ days}$, range 2–7d):** Parasympathetic vagal reactivation (HRV RMSSD) and skeletal/hepatic glycogen re-esterification via GLUT4 translocation resolve over 24–72 hours, extending to 7 days following severe eccentric muscle trauma.
2. **Cardiovascular System, Mitochondrial Density, & Plasma Volume ($\tau_2 = 35.0\text{ days}$, range 28–42d):** Hypervolemia, left ventricular eccentric hypertrophy, neo-angiogenesis, and PGC-1α mitochondrial biogenesis adapt across 4–6 week mesocycles. In the Coggan Performance Management Chart (PMC), this is standardized to the 42-day Chronic Training Load (CTL) constant.
3. **Muscular & Neuromuscular Force Development ($\tau_3 = 42.0\text{ days}$, range 21–56d):** High-threshold motor unit recruitment, firing rate synchronization, rate of force development (RFD), and Type I/IIa myofibrillar remodeling require 3–8 weeks of progressive heavy resistance overload.
4. **Tendon Collagen Synthesis & Bone Mineral Matrix Remodeling ($\tau_4 = 120.0\text{ days}$, range 90–180d; $\tau_4 = 210.0\text{ days}$ for Masters $\ge 45$):** Load-bearing tendons (Achilles, patellar, plantar fascia) and cortical bone (tibia, metatarsals) rely on slow interstitial diffusion. Tensile cross-linking and osteoblast bone mineralization require 3–6 months to achieve structural maturity.

### B. Engine-Chassis Decoupling & Orthopedic Dissociation Index (ODI)
Because central aerobic fitness ($\tau=35\text{d}$) adapts more than three times faster than avascular connective tissue ($\tau=120\text{d}$), an athlete's aerobic engine can rapidly outpace their structural chassis. The platform tracks parallel first-order exponential impulse-response accumulators across days $t$:
$$M(t) = M(t-1) \cdot e^{-1/\tau_m} + \text{aerobic\_tss}_t \cdot (1 - e^{-1/\tau_m})$$
$$C(t) = C(t-1) \cdot e^{-1/\tau_c} + \text{mechanical\_strain}_t \cdot (1 - e^{-1/\tau_c})$$
$$\text{ODI}(t) = \frac{M(t)}{C(t)}$$

- **Production Invariant:** The Orthopedic Dissociation Index must satisfy $\text{ODI}(t) \le 1.35$.
- **Action Threshold:** If $\text{ODI} > 1.35$, the optimizer tightens the weekly mechanical strain ceiling and routes excess metabolic volume into non-impact swimming or cycling.
- **Intra-Session Microscale Decoupling:** Pace-to-HR drift ($Pa:HR$) or Power-to-HR drift ($Pw:HR$) exceeding 5%–6% during a steady Zone 2 session, or leg-spring stiffness ($LSS$) degradation, flags acute elastic recoil failure and triggers an immediate single-session duration cap.

### C. Dual-Load Formulation & Sport Strain Exponents
Every candidate workout $w$ is characterized by a two-component workload vector:
$$\mathbf{L}_w = \begin{bmatrix} \text{aerobic\_tss}_w \\ \text{mechanical\_strain}_w \end{bmatrix}$$
$$\text{mechanical\_strain}_w = \text{duration}_w \cdot S_{sport(w)} \cdot (\text{IF}_w)^{\beta_{sport(w)}}$$
- **Swimming ($S_{swim} = 0.05, \beta_{swim} = 1.2$):** Buoyant, weight-supported concentric upper-body propulsion.
- **Cycling ($S_{bike} = 0.10, \beta_{bike} = 2.0$):** Weight-supported, closed-chain concentric pedaling with zero ground impact.
- **Running ($S_{run} = 1.00, \beta_{run} = 2.5$):** Ballistic ground impacts of 2.5–3.0× body weight per footstrike with rapid eccentric stretch-shortening cycles.
- **Heavy Resistance ($S_{str} = 1.50, \beta_{str} = 3.0$):** High axial skeletal compression, tendon shear, and myofibrillar tension.
- **Single-Sport PMCs (Vance):** Bike and Run workloads are tracked on separate PMCs. Sustainable Bike CTL ranges 50–90+ TSS/day, whereas Run CTL is capped at 30–50 TSS/day for long-course safety.

### D. Critical Power (CP), Anaerobic Work Capacity ($W'$), and Threshold Calibration
Because the athlete engages in heavy barbell strength training (back squats, deadlifts), their Anaerobic Work Capacity ($W'$) is substantially larger than that of an endurance-only triathlete. Single-effort proxy tests relying on fixed statistical multipliers (such as the cycling ramp test at $0.75 \times \text{MAP}$ or un-cleared 20-minute TTs at $0.95 \times P_{20}$) draw heavily upon this enlarged anaerobic reserve, causing them to systematically overestimate sustainable aerobic threshold ($LT_2$). Single-effort tests with fixed multipliers are prohibited (`ADR-009`).

1. **Two-Point Linear Work-Time Critical Power Protocol (Running & Cycling):**
   - $P_3$: Mean maximal power (Watts) over a 180-second ($t_1 = 180\text{s}$) all-out effort.
   - $P_{12}$: Mean maximal power (Watts) over a 720-second ($t_2 = 720\text{s}$) all-out effort (after 30-min active/passive recovery).
   - **Critical Power ($CP$, Watts):**
     $$CP = \frac{t_2 P_{12} - t_1 P_3}{t_2 - t_1} = \frac{4 P_{12} - P_3}{3}$$
   - **Anaerobic Work Capacity ($W'$, Joules):**
     $$W' = 180 \cdot (P_3 - CP)$$
2. **Kolie Moore Empirical Baseline (Cycling Alternative):**
   A 35–45 minute progressive test at Maximum Lactate Steady State ($MLSS$). Test duration equals baseline Time-to-Exhaustion ($TTE$), with $\text{FTP} = 1.00 \times P_{\text{avg}}$.
3. **Indoor / Aero Positional Rule (Cycling):**
   Testing must be executed in the aero position if target workouts are in aero. Upright road-hoods power is 3%–7% (10–20W) higher; do not mix upright and aero targets without a positional scaling factor.
4. **Critical Swim Speed (CSS):**
   Derived from 400m/yd ($T_{400}$) and 200m/yd ($T_{200}$) time trials:
   $$CSS = \frac{400 - 200}{T_{400} - T_{200}} = \frac{200}{T_{400} - T_{200}} \quad [\text{m/s}], \quad \text{Pace}_{100} = \frac{T_{400} - T_{200}}{2} \quad [\text{s}]$$

### E. Canonical 80/20 7-Zone System & Exponential ANS Costing
Autonomic perturbation exhibits threshold behavior at $LT_1$: sub-$LT_1$ training permits rapid vagal reactivation, while intensity above $LT_1$ (Zone X drift) and especially $LT_2$ causes delayed parasympathetic recovery (24–72 hours). Commercial 5-zone systems (Stryd 5-Zone, MyProCoach 5-Zone) inappropriately place Zone 2 at 80%–90% CP, which breaches $LT_1$ and causes overtraining.

The platform enforces the **80/20 7-Zone Power Scale** anchored to verified $CP$:

| Zone | Physiological Role | Run Power (% Stryd CP) | Bike Power (% Bike CP) | Optimizer Bucket | ANS Cost Weight |
|---|---|---|---|---|---|
| **Zone 1** | Active Recovery / Low Aerobic | 50% – 76% | 50% – 70% | Low (`z1_z2_dur`) | **0.0** |
| **Zone 2** | Aerobic Endurance (Ceiling = $LT_1$) | 76% – 88% | 70% – 83% | Low (`z1_z2_dur`) | **0.0** |
| **Zone X** | Moderate Drift Grey Zone *(Penalize)* | 88% – 94% | 83% – 91% | Grey Drift (`zx_dur`) | **0.7** (Unplanned Penalty) |
| **Zone 3** | Sub-Threshold / Tempo (Ceiling = $LT_2$) | 94% – 100% | 91% – 100% | Moderate/High (`z3_dur`) | **1.0** |
| **Zone Y** | Threshold Over-Reach Gap *(Penalize)* | 100% – 103% | 100% – 102% | High (`z4_dur`) | **1.4** (Gap Penalty) |
| **Zone 4** | Supra-Threshold / $\text{VO}_2\text{max}$ | 103% – 120% | 102% – 110% | High (`z4_dur`) | **1.8** |
| **Zone 5** | Anaerobic / Repetition ($W'$ Depletion) | > 120% | > 110% | High (`z5_dur`) | **3.5** |

- **Sanity Checkpoints:**
  - Run $LT_1$ (88% CP): Must pass the Talk Test and keep HR $\le 89\%$ LTHR.
  - Run/Bike $LT_2$ (96%–100% CP): HR must plateau near LTHR during a 15–20 minute interval.
- **Alactic Strides Exemption:** Alactic strides ($\le 15-20\text{s}$) in Zone 5 rely exclusively on the ATP-PCr system without glycolytic metabolite accumulation and receive zero ANS penalty ($0.0$).

### F. Physiological Durability (Fatigue Resistance — Maunder et al., 2021)
Durability is defined as the time of onset and magnitude of deterioration in physiological characteristics during prolonged exercise. Thresholds degrade after 150–180 minutes of work (e.g. $LT2$ power drops ~10% / 21W, heart rate drifts +9 bpm, and running velocity at $LT1$ drops).
- **Vance Cadence & Power Fade:** Long-course durability requires combined cadence and power fade $\le 13\%$ over a 70.3 bike leg and $\le 20\%$ over a 140.6 bike leg.
- **Three Durability Stimulus Architectures:**
  1. *Late-Session Threshold Intervals:* Zone 3/4 intervals placed in the final 45–60 min of a 2.5–4.0h ride after accumulating $>1,500-2,000\text{ kJ}$ ($>15-25\text{ kJ/kg}$) of mechanical work.
  2. *Low-Cadence Torque Manipulation:* Sustained 55–65 rpm intervals in Zone 3/4 (Quadrant II loading) late in an endurance ride to train Type IIa recruitment under glycogen depletion.
  3. *T2 Brick Conditioning:* Transitions from bike to run within $\le 3\text{ minutes}$ to condition neuromuscular motor unit recruitment off the bike.

### G. Molecular Signaling Conflict: The AMPK–mTORC1 Axis
Concurrent endurance and resistance training triggers competing intracellular signaling cascades:
- Endurance exercise depletes glycogen and elevates AMP/ATP, activating **AMPK** and upregulating **PGC-1α** for mitochondrial biogenesis.
- Heavy resistance training stimulates **mTORC1** and **p70S6K1** via Akt/Rheb to drive myofibrillar protein synthesis and RFD.
- **Intracellular Conflict:** Active AMPK phosphorylates **TSC2** and **Raptor**, directly inhibiting mTORC1 and attenuating strength/power gains.
- **Evidence-Based Protocol:** Heavy resistance (80%–95% 1RM, 3–6 reps, 3–4 sets, 2–4 min rest, 1–2 RIR) and low-volume plyometrics (20–30 footstrikes, ground contact $<200\text{ ms}$) improve running economy (+5.0%), time to exhaustion (+21%–33%), and gross cycling efficiency without inducing muscle hypertrophy or blunting aerobic adaptation.
- **Dry-Land vs. In-Water Swim Strength:** Dry-land upper-body strength training does not improve free-swimming velocity (Tanaka et al., Trappe et al.), whereas in-water resistance (paddles, drag chutes) does. Gym strength prioritizes cycling (#1) and running (#2); swim force is developed in the pool (MF1/MF3).
- **Sequencing & Spacing Rules:** Key endurance sessions are performed first (AM). Heavy strength is scheduled in the afternoon/evening with $\ge 6\text{ hours}$ separation ("Keep Hard Days Hard") allowing AMPK to return to baseline before stimulating mTORC1. If lifting is scheduled in the AM, it must only be paired with easy Zone 1/2 aerobic recovery in the PM. Heavy runs are locked out for $\ge 24\text{ hours}$ after heavy leg strength due to transient running kinematics disruption and spinal fatigue.
- **Periodization Cadence:** Preparation (AA: 40%–60% 1RM, 2–3x/wk) $\to$ Base (MS: 80%–95% 1RM, 2x/wk) $\to$ Build (SM: 80%–85% 1RM, 1x/wk) $\to$ Taper (0 sessions/wk $\ge 7-14\text{ days}$ prior to race).

### H. Axial Spinal Kinematics & Equipment-Aware Loading
1. **L4/L5 Lumbar Compressive & Shear Mechanics:**
   Heavy conventional and Romanian deadlifts impose up to $18.45\text{ kN}$ compressive force and $3.28\text{ kN}$ shear load on the lumbar spine. Acute spinal erector fatigue impairs ground reaction shock attenuation ($2.5-3.0\times$ BW during running), transmitting impact directly to intervertebral discs. A strict $\pm 24\text{h}$ lockout between deadlifting and threshold/long running is non-negotiable (`HEUR-004`). Stacking deadlifts with swimming is physiologically synergistic due to hydrostatic spinal decompression and non-impact active recovery.
2. **Subacromial Impingement Prevention:** Heavy overhead pressing and bench pressing cause transient deltoid and rotator cuff micro-trauma. Same-day CSS threshold swimming creates subacromial impingement and is strictly excluded.
3. **Capped Dumbbell Physiology ($\le 75\text{ lb}$):** Home gym setups with $75\text{ lb}$ max dumbbells cannot safely provide sufficient bilateral mechanical tension for advanced hip hinge and knee extension. The system enforces unilateral progression (Bulgarian split squats, single-leg DB RDLs) and reserves heavy bilateral compound drivers ($\ge 60\%$ weekly sets) for commercial Olympic barbells (`ADR-008`).

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
