# Domain Lode: `safety`

## 1. Scope & Responsibility
Non-negotiable Tier-0 orthopedic safety guardrails (`ADR-013`), single-run volume ratio and minute caps (`ADR-003`, `ADR-022`), in-model EWMA running mechanical ACWR and re-entry run-minutes guards (`ADR-019`), rolling 48-hour multi-stress caps (`ADR-024`), biomechanical conflict exclusions (`ADR-008`, `HEUR-004`), Brick run-leg accounting (`ADR-030`), Masters athlete adaptations, and independent season verification (`TASK-027`).

## 2. Key Modules & Entrypoints
- [`src/triplan/solver/safety_constraints.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/safety_constraints.py): Shared Tier-0/Tier-1 orthopedic layer (`SafetyLimits`, `classify_workouts`, `resolve_long_run_cap`, `run_share_report`, `run_minutes`, `run_strain`, `lexicographic_solve`, `explain_feasibility`).
- [`src/triplan/solver/engine/builders/orthopedic.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/engine/builders/orthopedic.py), [`load_dynamics.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/engine/builders/load_dynamics.py), & [`session_structure.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/engine/builders/session_structure.py): Wire Tier-0/Tier-1 caps, cross-week carry-in ACWR/ramp/run-minutes rows, and Hard-Days-Hard / exclusivity rules into `UnifiedWeekEngine`.
- [`src/triplan/physiology/athlete_profile.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/physiology/athlete_profile.py): `AthleteProfile`, charter bounds (`CHARTER_MAX_48H_AEROBIC_TSS`, `CHARTER_MAX_48H_MECH_STRAIN`, `CHARTER_MAX_48H_ANS_COST`), `orthopedic_status` flags, and Masters Mode (`age >= 45`).
- [`src/triplan/control/feasibility.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/control/feasibility.py): Plain-English season infeasibility explanations, `attribute_single_run_week()` (`TASK-046`), and `attribute_keystone_run_week()` (`TASK-062`).
- [`src/triplan/perf/season_validation.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/perf/season_validation.py): Independent `check_week()` / `check_season()` verifier re-computing every Tier-0 quantity directly from solved schedules (never trusting solver self-reports).

## 3. Core Architectural Rules & Orthopedic Safety Invariants

### A. Global Guardrail Domain & Three-Tier Hierarchy (`ADR-013`)
Rules in `safety` are automatically injected into briefings across all domains (`scripts/lodectx.py`). Inside the MILP engine:
- **Tier 0 (Hard, Never Relaxed):** Time & facility gating, rolling 48h caps, daily/weekly axial & neuro caps, single-run ceiling, hard/easy adjacency, 48h heavy-run spacing, deadlift $\pm 24\text{h}$ lockout, antagonism exclusions, canonical 80/20 bounds, and in-model ACWR/CTL-ramp/run-minutes limits.
- **Tier 1 (Named Integer Slacks):** Only anchors, sport minimum frequencies, strides, rest-day count, key-session count, heavy-strength unstacking, and the 0.80 ACWR floor waiver may relax, strictly ordered by `tier1_relaxation_order` (`DECISION-006`) and reported explicitly.

### B. Single-Run Volume Ceiling & Low-Run-Count Cap (`ADR-003`, `ADR-022`)
- **4+ Run Weeks (`ADR-003`):** Every run (and Bike→Run brick run leg) must satisfy $\text{RunMin}_{w,d} \le 0.32 \cdot \sum_{d'} \text{RunMin}_{d'}$ (`max_single_run_vol_ratio <= 0.32`, tightening to `<= 0.25`/`0.20` when `orthopedic_status != 'HEALTHY'`).
- **Fewer Than 4 Runs (`ADR-022`, `TASK-045`):** Because a 32% ratio is mathematically vacuous with 1–3 runs, every run is capped in minutes via the $z_4$ ($\ge 4$ runs) indicator:
  $$L = \min\big(\max(\text{floor}=30,\ \text{prev\_longest} + \text{step}=10),\ \text{reentry\_max}=45\ \text{if } \text{reentry}\big)$$
  seeded in week 1 by `AthleteProfile.starting_longest_run_min` and carried across run-free weeks in `WeekCarryIn`. `run_share_report()` records which regime (`absolute`, `pigeonhole`, or `ratio`) applied.

### C. EWMA Running Mechanical ACWR & Re-Entry Guard (`ADR-012`, `ADR-019`)
Running mechanical strain uses coupled EWMAs ($\lambda_a = 2/8 = 0.25$, $\lambda_c = 2/29 \approx 0.069$):
- **In-Model Enforcement (`builders/load_dynamics.py`):** Every day enforces $A_d - \rho C_d \le \varepsilon$ where $\rho = 1.20$ during active re-entry (`reentry_phase=True` and run CTL $< 15$), $\rho = 1.25$ post-re-entry, and $\rho = 1.15$ for Masters ($\varepsilon$ accommodates a single minimal-run bump from a detrained chronic baseline). A named Tier-1 waiver covers the $0.80$ lower floor when needed (waived automatically in deloads per `ADR-023`).
- **Absolute-Minutes Re-Entry Guard (`ADR-012`):** While `LoadState.reentry` is active, weekly run minutes are bounded by $\max(60,\ \text{prev\_week\_run\_min} + 15)$.
- **Run-Strain ODI (`HEUR-002`, `ADR-035`, `TASK-022`):** $\text{ODI}^{\text{run}} = F/S$ with $F, S$ the EWMAs of the **same daily run strain** at $\tau = 35$ d and $\tau = 120$ d ($210$ d Masters), $\text{ODI}^{\text{run}} \le 1.35$ ($1.20$ Masters; charter limit unchanged). Numerator and denominator are both run-strain units, so steady-state training reads $1.0$ and cutting run strain lowers $F$ first (right-sign feedback). The old $M(t)/C(t)$ divided all-sport TSS by run strain and its season-loop throttle raised ODI when it cut run strain (1.13 $\to$ 2.44 drift; 4-6 for `CURRENT_ATHLETE`); that throttle and the `ADR-032` run-keystone bypass are gone. Enforcement is an in-model Tier-0 row per day, $F_d - \rho_{\text{odi}} S_d \le \varepsilon_{\text{odi}}$ (`_odi_rows` in `builders/load_dynamics.py`, constants in `LoadState.odi_*` / `control/carry.py`; rhs clamped at 0 like the ACWR row; `check_season` re-derives it as `run_odi`). The ACWR rows keep it slack in practice. `mpc.process_weekly_execution_and_recalibrate` keeps its `ODI > 1.35 or ACWR > 1.30` strain-cap backstop for ACTUAL data (`TASK-025`).
- **Rolling 3-Week Progression Guard (`ADR-031`, `TASK-062`):** Before solving each week, `progression_guard_bounds()` (`control/periodization.py`) bounds `target_weekly_tss` and `max_weekly_run_mech_strain` to `1.25x` (`1.15x` Masters) of the max solved TSS / mechanical strain over the last `PROGRESSION_GUARD_WINDOW = 3` solved weeks (with a `150.0` floor, never loosening above the profile ceiling), preventing a 1-week deload or `ADR-028` ride keystone from ratcheting down the next long-run keystone.

### D. Rolling 48-Hour Multi-Stress Ceilings (`ADR-024`)
Across every adjacent day pair $(d, d+1)$ including the cross-week Sun→Mon boundary (`WeekCarryIn`):
- **Aerobic TSS Ceiling:** $\le 295.0$ (charter ceiling; profile `max_48h_aerobic_tss` must be $\le 295.0$).
- **Mechanical Strain Ceiling:** $\le 260.0$ (charter ceiling; profile `max_48h_mech_strain` must be $\le 260.0$).
- **ANS Cost Ceiling:** $\le 120.0$ in the unified `ADR-034` units (charter ceiling; profile `max_48h_ans_cost` must be $\le 120.0$; `CURRENT_ATHLETE` tightens this to `55.0`; both were 700 / 450 on the retired $e^{0.65(z-1)}$ scale).

### E. Hard Adjacency, Biomechanical Conflicts & Brick Run-Leg Accounting (`ADR-008`, `ADR-030`, `HEUR-004`)
1. **Hard/Easy Principle:** At most one hard session (`q_i >= 5` or heavy strength) per day, and no two hard days back-to-back ($hard_d + hard_{d+1} \le 1$, including Sun→Mon).
2. **Heavy Run Spacing:** $\ge 48\text{h}$ between heavy runs (threshold/interval runs, runs $\ge 70\text{ min}$, anchored `long_run` sessions, or hard/$\ge 70\text{ min}$ Bike→Run brick legs). Non-anchor easy Z1/Z2 runs under `70 min` (e.g. `60 min` Z1 `RRe9`) are excluded from `heavy_or_long_runs` so they do not trigger 48h spacing or $\pm 24\text{h}$ deadlift/heavy-strength lockouts (`TASK-062`).
3. **Deadlift-Run 24h Lockout (`HEUR-004`):** Heavy deadlifts and threshold/long runs (including Bike→Run brick run legs) cannot share a day or adjacent days ($x_{i,d} + x_{j,d} \le 1$, $x_{i,d} + x_{j,d+1} \le 1$, $x_{i,d+1} + x_{j,d} \le 1$). Deadlifts stack synergistically with swimming.
4. **Same-Day Biomechanical Exclusions:** Squat vs High-Torque Bike / Threshold Run; Heavy Upper Press vs CSS Threshold Swim.
5. **Axial Spinal & Neuromuscular Ceilings (`ADR-008`):** Daily axial $X_{\text{day}} \le 120.0$, weekly $X_{\text{wk}} \le 320.0$; daily neuro $N_{\text{day}} \le 130.0$, weekly $N_{\text{wk}} \le 360.0$ (profile may tighten, e.g., `CURRENT_ATHLETE` `65 / 75` daily).
6. **Brick Run-Leg Accounting (`ADR-030`):** Workouts with `sport == "Brick"` carry explicit `brick_run_min`, `brick_run_strain`, and `is_brick_run_hard` columns wired into run-share (`ADR-003`/`ADR-022`), `HEUR-004`, heavy-run 48h spacing, weekly run strain, ACWR, run-minutes guard, Bike+Run frequency bounds, and same-day cardio exclusivity (`SW_T1_BRICK` has `brick_run_min == 0` and stays excluded from run sets).

### F. Masters Mode (`age >= 45`) & Morning Red Alert Override
- Masters expands connective-tissue remodeling constant $\tau_c$ from `120d` to `210d`, lowers ACWR $\rho$ to `1.15`, and caps weekly CTL ramp at `5.0`.
- Morning Visual Analog Pain $> 3/10$ or Red Alert readiness ($R_t < 0.80$) overrides hard sessions in-place with Zone 1 Active Recovery (`0.50x` duration) or rest.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs; edit narrative sections above, not this block. -->

## Registered Lineage (`safety`)

### Active Architectural Decisions (`ADR`)
- **`ADR-003` — Enforce Single-Run Volume Ceiling at 32 Percent of Weekly Running Duration:** Cap single run duration at <=32% of total weekly running volume in solver constraints to prevent bone stress injuries.
- **`ADR-022` — Absolute long-run cap in minutes for weeks with fewer than 4 runs:** Weeks with <4 runs cap the longest run at max(30, prev longest+10) min (and 45 min in re-entry); >=4 runs keep the ADR-003 32% ratio.
- **`ADR-030` — Model Brick Run Leg Across Safety and Load-Dynamics Constraints:** Brick workouts carry explicit schema columns (brick_run_min, brick_run_strain, is_brick_run_hard) enforced across safety, ACWR/CTL, frequency, and meso goals.
- **`ADR-031` — Three-week rolling window for receding-horizon progression guard:** Tier-2 progression guard bounds target TSS and weekly run strain using the max of the last 3 solved weeks, preserving keystones after deload/ride weeks.
- **`ADR-032` — Support-run pool stratification and 90/105-min keystone run build-up:** Stratify 8020 easy runs <=70m (keeping RRe9), align 48h heavy-run spacing to >=70m/anchors, allow 6 runs for 90+m keystones, and guard run keystones from ODI throttle.
- **`ADR-035` — Run-strain ODI: EWMA_35 / EWMA_120 of daily run strain, in-model Tier-0 ceiling, wrong-sign throttle removed:** ODI^run = EWMA_35 / EWMA_120 of the SAME daily run strain, <= 1.35 (1.20 masters), an in-model per-day row; no post-hoc ODI throttle.

### Active Operational Heuristics (`HEUR`)
- **`HEUR-002` — Prevent Orthopedic Dissociation Index Spikes in Build Cycles:** Never ramp weekly running mechanical strain by >10% week-over-week even when aerobic fitness allows higher volume.
- **`HEUR-004` — Axial Spinal Fatigue and Running Impact Interaction: Enforcing 24h Buffer Between Heavy Deadlifts and Threshold/Long Runs:** Never schedule heavy deadlifts and threshold/long runs within 24 hours of each other due to catastrophic lumbar shear and spinal fatigue.

### Empirical Experiments (`EXP`)
- **`EXP-002` [`ACTIVE` | `QUEUED_SPEC`] — Closed-Loop 16-Week Season Simulation Under Real Profile and Perturbations:** Queued spec: verify 16-week closed-loop season simulation on CURRENT_ATHLETE under nominal, illness, pool-closure, travel, and stochastic execution noise.
<!-- LODE_AUTOGEN_END -->
