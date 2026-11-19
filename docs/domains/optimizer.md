# Domain Lode: `optimizer`

## 1. Scope & Responsibility
Unified Mixed-Integer Linear Programming (MILP) weekly scheduling engine (`ADR-017`), HiGHS C++ solver interface (`highspy`, `ADR-002`), pre-solve candidate pruning (`TASK-060`, `ADR-029`), exact algebraic ratio linearization, three-tier lexicographic constraint hierarchy (`ADR-013`), selection-based Top-K plan extraction and week-reversal symmetry breaking (`ADR-018`).

## 2. Key Modules & Entrypoints
- [`src/triplan/solver/engine/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/engine/engine.py) — **The single unified MILP engine (`ADR-017`):**
  - `model.py`: `WeekModel` (NumPy column buffers, CSR row buffers, duplicate summing, zero dropping, single bulk `addCols` + `addRows`, dimensionless `add_soft_goal` with symmetric or asymmetric weights, `ModelHooks` adapter).
  - `config.py`: `WorkoutTable`, `WeekConfig`, and `EngineOptions` presets (`meso_options`, `hybrid_options`, `legacy_options`).
  - `context.py`: `BuildContext` and `Tier1Registry` shared across builders.
  - `session_quality.py`: Sport-specific `q_i` quality minutes, grey-zone pre-filtering, and hard/key masks (`ADR-020`).
  - `carry_in.py`: `DayLoad`, `LoadState`, and `WeekCarryIn` boundary structs (`ADR-019`).
  - `engine.py`: `UnifiedWeekEngine` and `solve_week()` orchestrating pre-filtering, builder assembly, lexicographic solve, selection/layout Top-K cuts, and AM/PM slotting.
  - `builders/`: `calendar.py`, `goals.py`, `orthopedic.py`, `load_dynamics.py`, `session_structure.py`, `intensity_structure.py`, `strength.py`, `symmetry.py`.
- [`src/triplan/solver/scheduler.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/scheduler.py): `TriathlonMILPScheduler` thin facade (`meso_options`).
- [`src/triplan/solver/hybrid.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/hybrid.py): `HybridAthleteMILPOptimizer` thin facade (`hybrid_options`).
- [`src/triplan/solver/highs_optimization.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/highs_optimization.py): `optimize_weekly_schedule()` DataFrame adapter (`legacy_options`) and `filter_viable_workouts()`.
- [`src/triplan/solver/safety_constraints.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/safety_constraints.py): Shared Tier-0/Tier-1 constraint layer, `lexicographic_solve()` (with Phase-B MIP start), and `explain_feasibility()` plain-English diagnostics.
- [`src/triplan/solver/pruning.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/pruning.py): `filter_feasible_candidates()`, `prune_8020_candidates()`, and `prune_dominated_candidates()`.
- [`src/triplan/solver/highs_runtime.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/solver/highs_runtime.py): Process-wide HiGHS thread control (`apply_threads`) and status-checked `run_and_check()`.

## 3. End-to-End Weekly Solve Pipeline (`UnifiedWeekEngine`)

All three historical solver entrypoints (`TriathlonMILPScheduler`, `HybridAthleteMILPOptimizer`, and `optimize_weekly_schedule`) execute the exact same pipeline configured by `EngineOptions` (new rules are always added as builder modules, never a 4th engine):

1. **Pre-Filtering & Candidate Preparation (before `WorkoutTable`):**
   - **Grey-Zone Drop (`session_quality.py`):** Workouts with incidental-adjusted quality $0 < q_i < q_{\text{hard}}$ (`5.0` Z4-equivalent min) are dropped (`prefilter_grey_zone`).
   - **Hard/Key Classification (`ADR-020`):** `is_hard_endurance` is computed from `q_i >= 5.0` rather than IF; key sessions include hard sessions, anchors, and long endurance (`Run >= 70 min`, `Bike >= 120 min`).
   - **Lossless Feasibility Filter (`filter_feasible_candidates`):** Drops non-anchor, non-required-test candidates that cannot fit *any* day's time cap, facility gate, or daily axial/neuro cap (and drops heavy strength when `required_heavy_strength_sessions == 0`; `TASK-060`). Full pre-filter inputs are retained for `explain_feasibility()`.
   - **Stratified 80/20 Pool Pruning (`prune_8020_candidates`, `ADR-029`, `TASK-060`, `TASK-062`):** Drops `8020_library` near-duplicates within `(<= 2.0 min, <= 3.0 TSS)` of a core workout in the same `(sport, intensity_bucket, is_indoor_bike)`, then selects evenly spaced representatives across `(duration, aerobic_tss)` up to `easy=6, tempo=4, hi=4` per sport. When canonical long-course runs (`RUN_AE2_LONG`, `RUN_LC_Z2_*`) are present, `8020_library` `easy` runs `> 70.0 min` are excluded from the `easy` Run bucket so slots stratify across `<= 70.0 min` support runs (explicitly retaining the lowest-strain `~60 min` Z1 run `RRe9`). Curated core rows, anchors, tests, and stride-bearing workouts are never dropped.
   - **Lift + Easy Cardio Combos (`library/compound.py`, `DECISION-021`, `TASK-060`):** Synthesizes lift + `<= 20 min` Z1/Z2 Bike (or Run for non-heavy lifts) compound candidates capped at `max_non_heavy_bases=2` and `max_combos_per_lift=2`, then filters combos through `filter_feasible_candidates` before matrix assembly.
   - **Exact-Clone Dominance Pruning (`prune_dominated_candidates`, `ADR-018`):** Drops only exact structural clones beyond a sport's weekly maximum (preferring lower `preference_penalty`, higher `durability_score`, and curated provenance over `8020_library`). Duration/TSS/ANS/strain are never used as dominance orders because Tier-2 goals are two-sided.
2. **Modular Model Assembly (`build_model()`):**
   - Builders run in fixed order: `calendar` (assignment binaries $x_{w,d}$, uniqueness, day/partition/brick/session links, facility & time caps) → `goals` (declare dimensionless Tier-2 soft deviations) → `orthopedic` (Tier-0 caps + Tier-1 named slacks) → `load_dynamics` (`ADR-019` carry-in boundary rows, in-model running ACWR, CTL ramp, re-entry run-minutes guard) → `session_structure` (one-hard/day, key sessions, Hard-Days-Hard stacking, AM/PM window gating, combo/Brick exclusivity) → `intensity_structure` (`add_canonical_8020`) → `strength` (barbell share `>= 60%`, 4-pattern sets `>= 10`, split coherence $z_{\text{split}}$) → `symmetry` (week-reversal ordering row when palindromic).
3. **Two-Phase Lexicographic Solve (`ADR-013`, `ADR-029`):**
   - **Phase A (Tier-1):** Minimizes lexicographically weighted (`LEX_BASE = 8` by `tier1_relaxation_order`) integer/binary Tier-1 slacks (anchors, sport frequencies, strides, rest days, heavy strength, key sessions, heavy-strength unstacking, ACWR 0.80 floor waiver). Tier-0 caps are never relaxed.
   - **Phase B (Tier-2):** Locks the Phase-A slack values, warm-starts HiGHS from Phase A's solution (`h.setSolution`, `ADR-029`), and minimizes the dimensionless Tier-2 objective to `mip_rel_gap` (`1%` default).
4. **Top-K Extraction & Diagnostics (`ADR-018`):**
   - Default `cut_mode='selection'` appends $\sum_{i \in S^{(k)}} s_i \le |S^{(k)}| - m$ ($s_i = \sum_d x_{i,d}$, $m = 1$) so each plan selects a genuinely distinct set of workouts (rather than merely permuting days). `cut_mode='layout'` remains optional; `'none'` runs a single solve.
   - If Phase A is infeasible, `explain_feasibility()` reports the conflicting Tier-0 caps in plain English.

## 4. Key Mathematical Formulations & Invariants

### A. Decision Variables & Exact Ratio Linearization
- **Primary Binary Assignment:** $x_{w,d} \in \{0, 1\}$ for candidate $w \in W$ and day $d \in \{0..6\}$ (Mon=0 .. Sun=6).
- **Auxiliary Variables:** $y_{s,d}$ (sport occurrence), $double_d$, $rest_d$, $hard_d$, $brick_d$, and $z_{\text{split}} \in \{0,1\}$ (HLM vs Upper/Lower split coherence).
- **Linearized Ratios:** Non-linear ratios (sport duration splits, TiZ targets, barbell set share $\ge 60\%$, and `ADR-003` single-run share $\le 32\%$ using sparse $r_{\text{tot}}$ in `_add_run_share_cap` per `ADR-029`) are multiplied through by their linear denominators so the entire problem stays exact MILP.
- **Brick Run-Leg Accounting (`ADR-030`):** Bike→Run bricks (`brick_run_min > 0`) contribute their run leg to run minutes, run mechanical strain, heavy/long-run adjacency, Bike+Run frequency counts, and `Brick`/cardio same-day exclusivity (`SW_T1_BRICK` with `brick_run_min == 0` stays non-run).

### B. Calendar, 48-Hour Caps & Dimensionless Tier-2 Objective
- **Rolling 48h Hard Caps (Profile-Bounded by Charter `ADR-024`):** Aerobic TSS $\le 295.0$, Mechanical Strain $\le 260.0$, ANS Cost $\le 120.0$ (`ADR-034` units; across every $(d, d+1)$ pair, including Sun→Mon via `WeekCarryIn`).
- **Session & Facility Gating:** Weekday single-workout cap (`weekday_single_workout_cap`), daily time caps (`daily_time_caps`), pool-day gates, indoor/outdoor bike gating, and `two_session_windows` AM/PM gating (`am_pm` slot assignment in plans).
- **Dimensionless Objective (`WeekModel.add_soft_goal`):** Normalized fractional deviations for weekly TSS ($W_{\text{tss}} = 100$), TiZ ($W_{\text{tiz}} = 80$), sport duration splits ($W_{\text{sport\_dur}} = 50$), Bike/Run TSS split ($W_{\text{tss\_split}} = 45$), durability stimulus ($W_{\text{durab}} = 25$), schedule friction ($W_{\text{pref}} = 5$), and double-day aversion ($W_{\text{double}} = 0.5$).
- **Week-Level Target Policies (driven by `control/season.py`):**
  - *Re-entry build/overload overshoot (`ADR-021`):* Scales structure minimums down in two relief levels if solved TSS exceeds `1.15x` target.
  - *Deload / taper / race weeks (`ADR-023`):* Key minimum `0`, Bike/Run/Swim minimums halved, optional anchors, no ACWR `0.80` floor charge.
  - *Race-block build/overload structural floor (`ADR-025`):* When solved TSS exceeds `1.15x` matrix target, probes structural floor (`TSS` goal $\approx 0$), raises week target to `max(matrix target, floor)`, and re-solves.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs; edit narrative sections above, not this block. -->

## Registered Lineage (`optimizer`)

### Active PRDs
- **`PRD-002` — Dual-Objective Hybrid Athlete MILP and MPC Optimization:** Optimize concurrent triathlon and heavy barbell/dumbbell strength training via HiGHS MILP and 3-Tier closed-loop MPC.

### Active Architectural Decisions (`ADR`)
- **`ADR-002` — Adopt HiGHS Mixed-Integer Linear Programming Solver via highspy:** Use HiGHS C++ MILP solver via highspy with binary assignment variables instead of unconstrained greedy search.
- **`ADR-008` — Adopt Dual-Objective Hybrid Athlete Optimization with Equipment Partitioning and Biomechanical Conflict Matrix:** Enforce exact linear hybrid MILP with equipment set partitioning, axial load caps, and biomechanical conflict exclusions.
- **`ADR-013` — Enforce Three-Tier Lexicographic Constraint Hierarchy with Shared Tier-0 Safety Layer:** Tier-0 safety caps are hard in every engine (src/safety_constraints.py); targets relax only via named, reported Tier-1 slacks solved lexicographically.
- **`ADR-017` — Unified WeekModel engine with modular constraint builders:** All week MILPs are built by solver/engine (WeekModel + builders, bulk HiGHS build); facades stay thin; new rules are new builder modules, not a 4th engine.
- **`ADR-018` — Single deterministic solve with selection cuts, dominance pruning and symmetry breaking replaces the multi-seed race:** One deterministic engine solve per week; Top-K via workout-selection cuts; exact-clone pruning and week-reversal symmetry rows; no seed race, no random generator.
- **`ADR-021` — Re-entry weeks scale the structure minimums down when the full-week structure floor exceeds the TSS target:** Re-entry build weeks re-solve with halved, then zero, key/stride minimums only while solved TSS exceeds 1.15x target; Tier-0 caps never change.
- **`ADR-023` — Deload weeks carry no key minimum, anchor obligation or running-ACWR floor charge:** Recovery/taper/race weeks solve with key min 0, halved Bike/Run/Swim min, anchors optional and no ACWR-floor Tier-1 charge; Tier-0 caps unchanged.
- **`ADR-025` — Race-block build weeks raise their TSS target to the structural floor:** Race-block base/build/overload weeks solve at target = max(matrix target, structural floor); no structure relief there; deload/re-entry relief unchanged.

### Empirical Experiments (`EXP`)
- **`EXP-004` [`ACTIVE` | `QUEUED_SPEC`] — Goal-Weight Sensitivity and Session-Structure Constraints Sweep:** Queued spec: 81-point soft-goal weight sensitivity sweep across 300/500/700 TSS with and without ADR-020 session-structure constraints.

> **Superseded (`optimizer`):** `PRD-001` (`python scripts/lodectx.py trace <ID>`).
<!-- LODE_AUTOGEN_END -->
