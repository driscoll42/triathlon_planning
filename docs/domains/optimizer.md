# Domain Lode: `optimizer`

## 1. Scope & Responsibility
Mixed-Integer Linear Programming (MILP) scheduling models, the HiGHS C++ solver interface ([`highspy`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py)), exact algebraic linearization of non-linear physiological ratios, auxiliary variable linking, dimensionless multi-objective optimization, candidate workout pruning, and selection-based Top-K cuts.

## 2. Key Modules & Entrypoints
Since `TASK-015` there is ONE engine (`ADR-017`): `UnifiedWeekEngine` builds a buffered sparse `WeekModel` from modular constraint builders, bulk-builds HiGHS (one `addCols`, one `addRows`), solves lexicographically (Tier-1 slack, then Tier-2), and extracts Top-K plans. Semantics are selected by `EngineOptions` presets (`meso_options`, `hybrid_options`, `legacy_options`); option names are documented in `engine/config.py`. Add new rules as builder modules (seams: `TASK-018` landed carry-in/ACWR/CTL-ramp as `builders/load_dynamics.py` after `add_tier1`, `ADR-019`, `TASK-019` in `intensity_structure`, `TASK-020` landed selection cuts in `UnifiedWeekEngine._add_cut`, `pruning.py` and `builders/symmetry.py`, `ADR-018`), never as a fourth engine.
- [`src/triplan/solver/engine/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/engine/engine.py): `model.py` (`WeekModel`: numpy column/CSR row buffers, duplicate summing, dimensionless `add_soft_goal`), `config.py` (`WorkoutTable`/`WeekConfig` accessors, `EngineOptions` presets), `engine.py` (`UnifiedWeekEngine`, `solve_week`), `builders/{calendar,orthopedic,intensity_structure,strength,goals,symmetry}.py`, with candidate dominance pruning in `solver/pruning.py`.
- [`src/triplan/solver/scheduler.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py): [`TriathlonMILPScheduler`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py), thin facade (`meso_options`).
- [`src/triplan/solver/hybrid.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/hybrid.py): `HybridAthleteMILPOptimizer`, thin facade (`hybrid_options`).
- [`src/triplan/solver/safety_constraints.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/safety_constraints.py): the shared Tier-0/Tier-1 layer that `builders/orthopedic.py` delegates to.
- [`src/triplan/solver/highs_optimization.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/highs_optimization.py): `optimize_weekly_schedule` is a DataFrame adapter over the engine (`legacy_options`); candidate pruning (`filter_viable_workouts`). The selection-only `optimize_weekly_workouts`, the multi-seed race and the parallel olympic workers were deleted in `TASK-020` (`ADR-018`).
- [`src/triplan/perf/stress.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/perf/stress.py): Scenario benchmark testbed verifying solver convergence across the 11 stress scenarios.

## 3. Core Architectural Rules & Mathematical Formulations

### A. Sets, Indices, & Decision Variables
- **Sets:** Discrete candidate workouts $W$ (indexed by $w$), 7-day planning horizon $D = \{0..6\}$ (Monday=0 .. Sunday=6), weekday subset $D_{wd} = \{0..4\}$, weekend subset $D_{we} = \{5, 6\}$, sports $S = \{\text{Swim, Bike, Run, Strength, Brick}\}$, intensity zones $Z = \{1..5\}$.
- **Primary Binary Assignment:** $x_{w,d} \in \{0, 1\}$, where $x_{w,d} = 1$ if workout $w$ is scheduled on day $d$.
- **Auxiliary Binary Structural Variables:**
  - $y_{s,d} \in \{0, 1\}$: Equals 1 if sport $s$ occurs on day $d$.
  - $double_d \in \{0, 1\}$: Equals 1 if day $d$ contains two sessions (e.g. AM Endurance + PM Strength).
  - $rest_d \in \{0, 1\}$: Equals 1 if day $d$ contains zero sessions.
  - $hard_d \in \{0, 1\}$: Equals 1 if day $d$ contains a high-intensity endurance or heavy resistance session.
  - $brick_d \in \{0, 1\}$: Equals 1 if day $d$ pairs both a Bike and a Run session.
- **Continuous Non-Negative Slack & Surplus Variables:** $s^+_{tss}, s^-_{tss}, s^+_{z}, s^-_{z}, s^+_{s}, s^-_{s}, s^+_{split}, s^-_{split} \ge 0$.

### B. Exact Algebraic Linearization of Non-Linear Ratio Constraints
Fractional ratios whose denominators depend on decision variables $x_{w,d}$ are non-linear. Multiplying through by the strictly positive linear denominator yields an exact linear formulation solvable by HiGHS without approximation error:

1. **Sport Duration Allocations:** To achieve sport duration fraction $P_s$ of realized weekly duration:
   $$\sum_{w \in W, d \in D} (D_{w,s} - P_s \cdot D_w) \cdot x_{w,d} - s^+_s + s^-_s = 0 \quad \forall s \in S$$
2. **Time-in-Zone (TiZ) Allocations (Polarized vs. Pyramidal):** To enforce zone fractions $P_z$ across total weekly training time:
   $$\sum_{w \in W, d \in D} (D_{w,z} - P_z \cdot D_w) \cdot x_{w,d} - s^+_z + s^-_z = 0 \quad \forall z \in Z$$
   The net linear coefficient for each variable $x_{w,d}$ is the precomputed scalar $(D_{w,z} - P_z \cdot D_w)$.
3. **Single-Session Run Volume Ceiling ($\le 32\%$ of Weekly Run Duration):**
   $$\forall w \in W_{run}, \forall d \in D: \quad (1 - P_{run\_cap}) \cdot D_w \cdot x_{w,d} - P_{run\_cap} \sum_{(w',d') \ne (w,d), w' \in W_{run}} D_{w'} \cdot x_{w',d'} \le 0$$
   This eliminates excessive long-run orthopedic spikes while scaling naturally with total weekly running volume.

### C. Auxiliary Variable Linking & Structural Constraints
- **Sport Occurrence Linking:** $\sum_{w \in W_s} x_{w,d} = y_{s,d} \quad \forall s \in S, d \in D$. At most one session per sport per day is permitted.
- **Session Count Linking:** $\sum_{w \in W} x_{w,d} = 1 - rest_d + double_d \quad \forall d \in D$. Days can have 0 workouts ($rest_d=1$), 1 workout, or at most 2 workouts ($double_d=1$).
- **Brick Transition Linking:** $brick_d \le y_{bike,d}$, $brick_d \le y_{run,d}$, and $brick_d \ge y_{bike,d} + y_{run,d} - 1$.
- **Hard Day Linking:** $x_{w,d} \le hard_d \quad \forall w \in W_{high} \cup W_{heavy\_str}, d \in D$.
- **Hard/Easy Adjacency Invariant:** $hard_d + hard_{d+1} \le 1 \quad \forall d \in \{0..5\}$. Consecutive breakthrough or heavy resistance days are strictly prohibited.
- **Heavy Run & Post-Strength Separation:**
  - Consecutive heavy runs: $heavy\_run_d + heavy\_run_{d+1} \le 1 \quad (\ge 48\text{h separation})$.
  - Post-strength lockout: $heavy\_strength_d + heavy\_run_d \le 1$ and $heavy\_strength_d + heavy\_run_{d+1} \le 1$.

### D. Calendar, Facility, & Rolling 48-Hour Boundaries
- **Weekday Single-Session Cap:** Weekday sessions are capped at 60 minutes ($D_w \cdot x_{w,d} \le 60 \quad \forall d \in D_{wd}$). On designated double-session days, a secondary slot allows up to 105 min total across 2 sessions.
- **Weekend Expansion:** Saturday and Sunday expand to 180–240 minute windows for long outdoor rides, runs, and bricks.
- **Facility Availability Gating:** $x_{w,d} \le A_{facility(w), d}$. Pool-gated workouts are locked to open days (e.g. Sat/Sun only). Outdoor cycling is restricted strictly to weekends; weekday cycling defaults to indoor smart trainers.
- **Rolling 48-Hour Stress Windows:** Across all adjacent day pairs $(d, d+1)$:
  - Rolling Aerobic TSS $\le 295.0$
  - Rolling ANS Cost $\le 650.0$
  - Rolling Mechanical Strain $\le 260.0$

### E. Dimensionless Multi-Objective Function & Calibrated Priority Weights
Because decision metrics span incompatible units (TSS points 400–700, minutes 500–900, durability scores 0–10), all terms are normalized by nominal constants ($TSS_{target}$, $D_{nominal}$) into dimensionless fractional deviations:
$$J = W_{tss} \cdot \frac{s^+_{tss} + s^-_{tss}}{TSS_{target}} + W_{tiz} \sum_{z=1}^5 \frac{s^+_z + s^-_z}{D_{nominal}} + W_{sport\_dur} \sum_{s \in S} \frac{s^+_s + s^-_s}{D_{nominal}} + W_{tss\_split} \cdot \frac{s^+_{split} + s^-_{split}}{TSS_{target}} - W_{durab} \sum_{w,d} \text{durab}_w \cdot x_{w,d} + W_{pref} \sum_{w,d} c_{w,d} \cdot x_{w,d} + W_{double} \sum_{d} double_d$$

- **Calibrated Weight Hierarchy:**
  - $W_{tss} = 100.0$: Primary anchor ensuring weekly volume target compliance.
  - $W_{tiz} = 80.0$: High priority enforcing Polarized or Pyramidal Time-in-Zone fidelity.
  - $W_{sport\_dur} = 50.0$: Sport duration proportion balance.
  - $W_{tss\_split} = 45.0$: Bike-to-Run TSS proportion balance (60/40 or 67/33).
  - $W_{durab} = 25.0$: Specific Build phase reward for high durability stimulus workouts.
  - $W_{pref} = 5.0$: Schedule friction penalty minimizing off-day athlete preferences.
  - $W_{double} = 0.5$: Low-priority aversion penalty against unnecessary double days.

### F. Top-K Diversity via Selection Cuts, Pruning and Symmetry Breaking (`ADR-018`)
Top-$K$ plans are extracted sequentially from one deterministic model. With $s_i = \sum_d x_{i,d}$ (at most 1 by uniqueness) and $S^{(k)}$ the workouts chosen by plan $k$, the default `cut_mode='selection'` appends
$$\sum_{i \in S^{(k)}} s_i \le |S^{(k)}| - m \qquad (m = \texttt{cut\_min\_diff},\ \text{default } 1)$$
so successive plans differ in WHICH workouts are chosen, not merely their day layout (supersets of an earlier selection are excluded too). `cut_mode='layout'` keeps the exact $(i,d)$ no-good $\sum_{(i,d) \in P^{(k)}} x_{i,d} \le |P^{(k)}| - 1$ (day permutations count as distinct); `none` is a single solve. The hybrid `z_split` flip equality was removed (it capped hybrid Top-K at 2); `split_architecture` is still reported from $z_{split}$.
- **Dominance pruning (`prune_dominated`, on):** only exact structural clones beyond a sport's weekly maximum are dropped (they differ at most in `preference_penalty` / `durability_score`, where the cheaper clone is kept). Duration/TSS/ANS/strain order is NOT a dominance order (two-sided goals); anchors and test workouts are never pruned.
- **Symmetry breaking (`symmetry_breaking`, on):** day permutations are not symmetries (48h and adjacency rows couple consecutive days). Only the week reversal $d \mapsto 6-d$ can be; when caps, readiness $\alpha$ and `allowed(i,d)` are palindromic and there is no weekday cap or weekend brick, the row $\sum_d (d-3) \sum_i dur_i x_{i,d} \le 0$ is added. It never fires for the meso facade or `CURRENT_ATHLETE`.

### G. Dual-Objective Hybrid Athlete Optimization (`HybridAthleteMILPOptimizer`)
The hybrid extension balances running, cycling, swimming, and heavy resistance training:
1. **Equipment Set Partitioning ($\ge 60\%$ Barbell):**
   $$\sum_{d \in D} \sum_{i \in \mathcal{W}_{\text{STR}}} \left((1 - \beta_{\text{BB}}) V_i^{\text{BB}} - \beta_{\text{BB}} V_i^{\text{DB}}\right) x_{i,d} \ge 0 \quad (\beta_{\text{BB}} = 0.60)$$
   Dumbbells capped at $75\text{ lb}$ are strictly allocated to unilateral assistance and accessory volume.
2. **Movement Pattern Volume Guarantees:**
   $$\sum_{d \in D} \sum_{i \in \mathcal{W}_{\text{STR}}} V_{i,m} \cdot x_{i,d} \ge 10.0 \quad \forall m \in \{\text{Anterior, Posterior, Push, Pull}\}$$
3. **Axial & Neuromuscular Load Ceilings:**
   $$\sum_i X_i x_{i,d} \le 120.0 \quad (\forall d), \quad \sum_{d,i} X_i x_{i,d} \le 320.0$$
   $$\sum_i N_i x_{i,d} \le 130.0 \quad (\forall d), \quad \sum_{d,i} N_i x_{i,d} \le 360.0$$
4. **Biomechanical Conflict Prevention Matrix:**
   - Deadlift vs Threshold/Long Run: $x_{i,d} + x_{j,d} \le 1$ and $x_{i,d} + x_{j,d+1} \le 1$ ($\pm 24\text{h}$ lockout).
   - Squat vs High-Torque Bike / Threshold Run: $x_{i,d} + x_{j,d} \le 1$.
   - Heavy Press vs CSS Threshold Swim: $x_{i,d} + x_{j,d} \le 1$.
5. **Split Archetype Coherence Indicator Cuts:** Binary indicator $z_{\text{split}} \in \{0,1\}$ prevents mixing Full-Body HLM with Upper/Lower splits within the same week:
   $$x_{i,d} \le z_{\text{split}} \quad \forall i \in \mathcal{W}_{\text{FULL\_BODY}}, \quad x_{j,d} \le 1 - z_{\text{split}} \quad \forall j \in \mathcal{W}_{\text{UPPER\_LOWER}}$$

### Session structure and 80/20 builders (TASK-019, `ADR-020`)
Engine pipeline: grey-zone drop -> `q_i` hard classification -> lift+easy combo candidates -> dominance pruning -> `WorkoutTable`. Builders `session_structure` (one-hard/day, key sessions, Hard-Days-Hard, window gating, combo exclusivity) and `intensity_structure.add_canonical_8020` run after `add_tier1` / `load_dynamics`; each has an `EngineOptions` switch (all off in `legacy_options`). Plans carry `am_pm` slots. A new day-asymmetric rule must keep `is_week_reversal_symmetric` False.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
