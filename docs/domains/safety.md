# Domain Lode: `safety`

## 1. Scope & Responsibility
Global orthopedic safety guardrails, exponentially weighted mechanical ACWR limits, acute-to-chronic load ceilings, single-run volume bounds, rolling 48-hour multi-stress caps, hard adjacency safety rules, Masters athlete age adaptations, and injury mitigation modes.

## 2. Key Modules & Entrypoints
- [`src/closed_loop_optimizer.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py): [`TriathlonMILPScheduler`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/closed_loop_optimizer.py#L320-L650), rolling 48-hour strain bounds, and adjacency constraint enforcement.
- [`src/athlete_profile.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/athlete_profile.py): [`AthleteProfile`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/athlete_profile.py#L22-L75), `orthopedic_status` flags (`HEALTHY`, `ACHILLES_REHAB`, `PATELLAR_GUARD`, `RESTRICTED_RUN`), and Masters Mode (`age >= 45`).

## 3. Core Architectural Rules & Orthopedic Safety Invariants

### A. Global Guardrail Domain Invariant
Rules in this domain are automatically injected into agent briefings across all subsystems via [`scripts/lodectx.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/lodectx.py). Safety invariants are non-negotiable hard bounds.

### B. Single-Run Volume Ceiling Invariant (`ADR-003`)
In endurance triathlon training, disproportionately long runs account for the majority of bone stress injuries, tibial stress syndrome, and plantar fasciitis:
$$\forall w \in W_{run}, \forall d \in D: \quad \text{Duration}_{w,d} \le 0.32 \cdot \sum_{d' \in D} \text{Run Duration}_{d'}$$
- **Nominal Ceiling:** Single run duration must never exceed **32%** of total weekly running duration (`max_single_run_vol_ratio <= 0.32`).
- **Restricted/Rehab Ceiling:** Tightens to $\le 25\%$ (or $\le 20\%$) when `orthopedic_status != 'HEALTHY'`.
- **Mathematical Enforcement:** Exact algebraic linear inequality constraint embedded in the HiGHS MILP matrix.

### C. EWMA Running Mechanical ACWR Model
Coupled Exponentially Weighted Moving Averages (EWMA) provide high sensitivity for detecting tissue failure risk following workload spikes (Williams et al., 2017):
$$\text{Acute Mechanical Strain}_t = \text{Strain}_t \cdot \lambda_a + \text{Acute}_{t-1} \cdot (1 - \lambda_a), \quad \lambda_a = \frac{2}{7 + 1} = 0.25$$
$$\text{Chronic Mechanical Strain}_t = \text{Strain}_t \cdot \lambda_c + \text{Chronic}_{t-1} \cdot (1 - \lambda_c), \quad \lambda_c = \frac{2}{28 + 1} = 0.069$$
$$\text{ACWR}_t = \frac{\text{Acute Mechanical Strain}_t}{\text{Chronic Mechanical Strain}_t}$$

- **Four Operational Zones:**
  1. **Under-Loading Zone ($\text{ACWR} < 0.80$):** Inadequate mechanical loading causes connective tissue deconditioning, increasing vulnerability upon return to normal volume.
  2. **Sweet Spot / Safe Adaptation Zone ($0.80 \le \text{ACWR} \le 1.30$):** Progressive tendon collagen synthesis, cross-linking, and cortical bone mineral density adaptation.
  3. **Caution / Overreach Zone ($1.30 < \text{ACWR} \le 1.50$):** Permissible only briefly during functional overreaching for biomechanically resilient athletes.
  4. **Danger Zone ($\text{ACWR} > 1.50$):** High tissue failure risk; triggers an immediate hard upper ceiling on weekly and daily running mechanical strain.

### D. Rolling 48-Hour Multi-Stress Ceilings
Across every rolling 48-hour window (adjacent day pairs $d$ and $d+1$):
- **Aerobic TSS Ceiling:** $\le 295.0$ points.
- **ANS Cost Ceiling:** $\le 650.0$ points.
- **Mechanical Strain Ceiling:** $\le 260.0$ units.
- **Physiological Basis:** Consecutive days of severe glycogen depletion trigger excessive release of the inflammatory cytokine **Interleukin-6 (IL-6)** from working skeletal muscle, precipitating acute immune suppression (salivary IgA depression), central nervous system fatigue, and autonomic uncoupling.

### E. Hard Adjacency & Biomechanical Conflict Safety Rules
1. **Hard/Easy Principle (Friel & Bowerman):** Never schedule two breakthrough sessions on consecutive days across any sport ($hard_d + hard_{d+1} \le 1$).
2. **Heavy Run Spacing:** Maintain at least 48 hours between heavy run sessions ($heavy\_run_d + heavy\_run_{d+1} \le 1$), where heavy runs comprise Zone 3+ tempo/threshold intervals, track speedwork, and long runs $\ge 70$ minutes.
3. **Deadlift-Run 24h Lockout (`HEUR-004`):** Heavy deadlifts and threshold/long runs must NEVER share a calendar day ($x_{i,d} + x_{j,d} \le 1$) or adjacent days ($x_{i,d} + x_{j,d+1} \le 1$ and $x_{i,d+1} + x_{j,d} \le 1$) due to extreme lumbar compressive ($18.45\text{ kN}$) and shear ($3.28\text{ kN}$) strain. Pairing deadlifts with swimming is preferred for hydrostatic decompression.
4. **Squat vs. High-Torque Bike / Threshold Run Exclusion:** Same-day exclusion ($x_{i,d} + x_{j,d} \le 1$) to prevent patellofemoral and quadriceps overload.
5. **Heavy Upper Press vs. CSS Threshold Swim Exclusion:** Same-day exclusion ($x_{i,d} + x_{j,d} \le 1$) to protect the subacromial space and rotator cuff.
6. **Axial Spinal & Neuromuscular Ceilings (`ADR-008`):**
   - Daily axial load $X_{\text{day}} \le 120.0$, weekly axial load $X_{\text{wk}} \le 320.0$.
   - Daily neuromuscular load $N_{\text{day}} \le 130.0$, weekly neuromuscular load $N_{\text{wk}} \le 360.0$.

### F. Masters Athlete Adaptations (`age >= 45`)
- Load-bearing tendon collagen turnover and osteoblast bone remodeling slow markedly with age. Connective tissue time constant expands from $\tau_c = 120\text{ days}$ to $\tau_c = 210\text{ days}$.
- Orthopedic recovery spacing must be extended; where indicated, the system transitions from a 3:1 step-cycle to a 2:1 step-cycle (2 loading weeks, 1 recovery week).

### G. Visual Analog Pain & Red Alert Guardrail
- Morning Visual Analog Pain score $> 3/10$ or Red Alert HRV ($R_t < 0.80$) triggers an automatic safety override, substituting hard sessions with Zone 1 Active Recovery (AE1, 0.50x duration) or complete rest.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
