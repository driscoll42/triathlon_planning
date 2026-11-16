# Audit & Roadmap — `triathlon_planning`

**Audit date:** 2026-11-12 · **Scope:** `src/`, data schemas in `data/`, the lineage docs in `docs/`, `SPEC.md`, packaging, and the integration scripts' rate-limit/credential handling.

**How this was produced.** Every file in `src/` was read in full. The stress suite (`src/stress_test_optimizer.py`) and four probe scripts were executed against the real library and the real `CURRENT_ATHLETE` profile on this machine (Python 3.14.7, highspy installed, 32 logical cores). Findings marked **[run]** were observed by execution; findings marked **[read]** come from reading code only.

**Not done.** `src/main.py` was not run end to end (it spawns 30 processes and overwrites CSVs in `training_plans/`); its steps were exercised individually. No TrainingPeaks or Garmin network call was made. `config/*.env` and the books in `references/` were not opened. `scripts/` other than `sync_garmin_data.py` was not reviewed in depth.

**A correction to the brief.** The brief describes the codebase as randomized/greedy sampling that needs replacing with a MILP. That is no longer the state of the repo. It already contains **three separate HiGHS MILP engines** with linearized ratios, slack-variable goals, `x[i,d]` day indexing and no-good cuts. The legacy random sampler still exists but currently crashes. The work ahead is therefore consolidation, correctness and calibration, not a first MILP port. The audit grades against the target architecture on that basis.

---

## Section 1: Executive Summary & Architecture Maturity Matrix

### 1.1 Bottom line

The design ideas are sound and mostly match the target architecture. The implementation does not yet deliver them for the athlete it is configured for. Five findings dominate everything else:

1. **The season planner produces no usable plan for the real profile. [run]** With `CURRENT_ATHLETE` (base 160 TSS, 45–75 min days), all 16 Olympic weeks are infeasible in the primary solve. Each falls through to a generic "recovery" config that ignores the profile, and the output is the same 393-TSS week sixteen times against targets of 88–240. Simulated running ACWR ends week 1 at 1.66 and ODI climbs to 2.44, with `SAFETY_RECALIBRATION_APPLIED` logged every week and changing nothing.
2. **Mixing HiGHS thread counts in one process silently returns empty plans. [run]** `TriathlonMILPScheduler` hardcodes `threads=4`; `highs_optimization` uses `threads=2`. After one has run, the other returns `kNotset`, which the code reports as an all-`REST` week or "infeasible" with no error.
3. **The "closed loop" is open. [read]** There is no `.fit` parser anywhere in the repo (no FIT library import, no `is_dev`, `length_type` or `left_right_balance` handling). The season generator feeds its own plan back as if executed perfectly. `STATE_SUMMARY.md`, `ADR-010` and `HEUR-005..007` describe a parser that does not exist.
4. **Hard safety invariants are soft, relaxed, or absent in code. [run]** The 32% single-run cap is exceeded in 5 of the 10 stress scenarios that solve (up to 42.9%) while the suite prints "Strict Constraints Met". The hybrid optimizer schedules a heavy deadlift the day after the long run. Weekly neuromuscular load reaches 447 against the documented cap of 360, which is not implemented.
5. **Solves are not proven optimal, and are not sub-second on the real library. [run]** On the 324-candidate pool, four seeds at a 4 s limit returned four different plans (scores 97.67–98.08) in 8.1 s each. A top-5 run returned non-monotone scores (98.53, 98.43, 98.46, …), which proves at least one "optimal" solve was not. This contradicts the recorded result of `EXP-001`.

There are also zero automated tests, `pytest` is not installed, and almost the entire current tree is untracked in git.

### 1.2 Maturity matrix

| Dimension | Score (0–5) | Basis |
| :--- | :---: | :--- |
| Data Ingestion & Schema | **2** | 493-workout 80/20 library is parsed into a usable zone-minute table. The scraper currently crashes, all 28 long-run workouts are dropped as TSS 0, there is no schema validation, and thresholds are defined in two conflicting places. |
| Optimization Engine | **3** | Real MILPs with exact ratio linearization, goal programming, day indexing and integer cuts. Three divergent engines, time-limit terminations reported as optimal, silent failure modes. |
| Physiological Modeling | **2** | Dual-load, ANS cost, PMC, ACWR and ODI all exist as concepts. ANS has three incompatible definitions, PMC time constants are roughly half the intended values, ODI is dimensionally inconsistent, and the 7-zone scale is never used by any solver. |
| Schedule & Life Constraints | **2** | Daily caps, facility gating and rest days are modeled. The real profile is infeasible, there is no carry-over between weeks, and there is no AM/PM slot model. |
| Strength Integration | **2** | The hybrid optimizer has a thoughtful conflict matrix and set accounting, on an 18-workout hand-coded library. It is not connected to the season planner, which schedules zero strength sessions for the real profile. |
| Telemetry Feedback Loop | **1** | Garmin JSON sync and readiness formulas exist. No FIT parsing, no deduplication, no path from completed activities into solver state. |
| Test Coverage / Maintainability | **1** | No tests. One 2,468-line module. Flat imports via `sys.path` edits. Documentation asserts behavior the code does not have. |

### 1.3 Which engine enforces what

The three engines each implement a different subset of the rules, so no single code path satisfies the charter.

| Rule | `highs_optimization.optimize_weekly_schedule` | `TriathlonMILPScheduler` | `HybridAthleteMILPOptimizer` |
| :--- | :--- | :--- | :--- |
| Library | Real xlsx (391 rows) | Hand-coded, 24 workouts | Hand-coded, 18 workouts |
| Sessions per day | Exactly ≤ 1 | ≤ 2 (3 with weekend brick) | ≤ 2, ≤ 1 per sport |
| Single run ≤ 32% | Absent (observed 50–64%) | Soft slack; auto-relaxed if run frequency < 4 | Hard, but only for workouts flagged `long_run` |
| Hard/easy adjacency | Bike/run only | All sports + heavy strength | Absent |
| Rolling 48 h caps | Absent | Soft | Absent |
| Deadlift ↔ run lockout | Absent | Heavy strength vs heavy run, ±1 day | Deadlift vs heavy run ±1 day; long run same-day only |
| Strength | Not modeled | Session count + lockouts | Sets, barbell share, axial caps, conflict matrix |
| Strides | Absent | 1–2 per week | Absent |
| Readiness α | Absent | Absent | Scales daily neuro cap |
| Top-K semantics | ≥ m different workouts | Exact assignment (day swaps count as new) | Canonical cut + forced split flip (max 2 plans) |
| Carry-in from prior week | Absent | Absent | Absent |

---

## Section 2: What Is Good (Strengths to Preserve)

**Exact ratio linearization is done correctly and consistently.** Sport splits, time-in-zone shares, the barbell share and the single-run cap are all written as $\sum_i (a_i - \rho\, b_i)\,x_i \lessgtr 0$ rather than computed as fractions. See `highs_optimization.py:225,229-259` and `closed_loop_optimizer.py:996-1053,1453-1483`. This is the hardest conceptual step in the target architecture and it is already right.

**The `add_soft_goal` helper** (`highs_optimization.py:179-183`) is the correct weighted-goal-programming primitive: one equality, two non-negative deviations, a scale and a weight. It should become the single way soft goals are added.

**`CandidateWorkout`** (`closed_loop_optimizer.py:34-62`) is a frozen dataclass carrying aerobic TSS, mechanical strain, ANS cost, zone minutes, axial and neuromuscular load, and behavior flags. This is the right in-memory shape for a solver row. It needs validation and a single construction path, not replacement.

**`_add_clean_row`** (`closed_loop_optimizer.py:732-745`) builds sparse rows directly through `addRow` with consolidated indices. It is far faster than the expression API used in `highs_optimization.py`, where wall-clock time ran at roughly double the solver limit.

**The biomechanical conflict matrix** (`closed_loop_optimizer.py:1400-1436`) encodes deadlift/run, squat/high-torque bike, and press/threshold swim exclusions as plain pairwise set-packing rows keyed on workout flags. The flag-driven pattern generalizes well.

**The split-coherence binary** `z_split` (`closed_loop_optimizer.py:1324-1334`) cleanly partitions Upper/Lower from Heavy-Light-Medium templates with two rows per workout.

**The pigeonhole insight** at `closed_loop_optimizer.py:977-987` is correct: with N runs the largest share is at least 1/N, so a 32% cap needs at least 4 runs. The response to it (quietly raising the cap) is wrong, but the analysis should be kept and turned into a pre-solve diagnostic.

**The scenario suite** (`stress_test_optimizer.py`) covers the right edge cases: time starvation, pool closure, rehab runner, masters athlete, large library. It is the natural seed for a real test suite.

**`AthleteProfile`** (`athlete_profile.py`) gathers thresholds, time caps, facility access and frequency bounds in one typed object, with a guarded two-point CP update (`apply_cp_retest_result`, lines 240-299) that validates inputs and caps upward jumps.

**The parsed 80/20 library.** `data/Run.xlsx`, `Bike.xlsx` and `Swim.xlsx` hold 194, 225 and 74 workouts with unique codes and per-zone minutes. 76 run, 77 bike and 28 swim workouts are pure Z1/Z2, so there is ample material for strict aerobic fill.

**Integration hygiene.** The TrainingPeaks client defaults to dry-run, never logs the cookie, and backs off on 429/5xx (`sync_8020_to_tp.py:651-687`). Secret files are correctly gitignored.

**The lineage discipline** (`scripts/lodectx.py`, ADR/HEUR/EXP templates, token budget check) is a good mechanism. Its current weakness is content accuracy, covered in Section 3.2.

---

## Section 3: What Needs Improvement — Code, Math & Engineering

### 3.1 Algorithmic & mathematical critique

#### A. Defects confirmed by execution

| # | Finding | Location | Effect |
| :-: | :--- | :--- | :--- |
| A1 | Real profile infeasible every week; fallback discards the profile | `closed_loop_optimizer.py:2398-2404` | Identical 393-TSS week × 16; targets, caps, facility access and safety recalibration all ignored |
| A2 | `required_heavy_strength_sessions = 2` with one heavy workout in the library and a ≤ 1-per-week uniqueness row | `:843-847`, `:896-902`, season matrices `:2241-2318` | Weeks 1–4 infeasible by construction |
| A3 | Mandatory 75-min long-run anchor conflicts with the profile. Confirmed: weeks solve only with anchors removed. Inferred mechanism: the anchor fills the whole Saturday cap, so the required swim and stride session cannot both fit the remaining weekend time; its strain (82) also exceeds the profile's run cap (75) | `:714-729`, `athlete_profile.py:72-98` | Every week infeasible until anchors are removed |
| A4 | HiGHS global scheduler conflict between `threads=4` and `threads=2` | `:1060`, `highs_optimization.py:164-166,406-408` | Second engine returns no solution; caller prints all-REST or "infeasible" |
| A5 | No solution-status check before reading `col_value` | `highs_optimization.py:533-541` | Failure is indistinguishable from a rest week |
| A6 | 32% run cap is a slack row whose usage is never reported | `:989-1008`, `:1096-1104` | 33–43% long-run shares labelled "Strict Constraints Met" |
| A7 | Cap auto-relaxed to `1.15 / max_runs` when run frequency < 4 | `:981-987` | With the profile's (1, 2) runs the cap becomes 57.5% |
| A8 | Top-K cut removes only the exact `(i,d)` assignment | `:1118-1122` | Top-3 baseline plans are the same ten workouts on shuffled days |
| A9 | Hybrid cut forces `z_split` to flip with accumulating equality rows | `:1567` | Asking for 4 plans always returns 2 |
| A10 | Deadlift adjacency covers `heavy_run` but not `long_run` | `:1412-1422` | Long run Thursday, heavy deadlift Friday in the default hybrid plan |
| A11 | Legacy sampler and the library scraper reference attributes that exist only on `HybridVariables` | `util.py:235`, `clean_file.py:176-177` | Both raise `AttributeError`; `main.py` cannot finish, and the library cannot be rebuilt |
| A12 | `time_limit` terminations returned as results and printed as `OPTIMAL` | `:1069-1070`, `stress_test_optimizer.py:57` | Scenario 8 took 30 s for 91 workouts; optimality unproven |

A13, lower severity: weekend/weekday pools filter TSS against the *time* cap (`util.py:85,88`), so the weekend TSS ceiling is 140 rather than 200.

#### B. Constraint hierarchy is inverted

Session frequency bounds, anchors, rest-day counts and stride counts are hard rows. The 48 h load caps and the run-volume cap are soft. The solver will therefore break a safety cap to keep a session, and when nothing fits it becomes infeasible rather than dropping a session. The target is the opposite.

Refactor to a three-tier lexicographic model:

- **Tier 0, never relaxed:** time caps, facility access, one copy of a workout per week, all orthopedic limits (single-run share, lockouts, 48 h caps, axial, neuromuscular, ACWR), max two sessions per day.
- **Tier 1, relaxable in a fixed order with a logged reason:** anchors, minimum frequencies, stride count, heavy-strength count, minimum rest days.
- **Tier 2, weighted goals:** TSS, sport split, zone distribution, durability, friction.

Implement Tier 1 as slack variables with weights two to three orders of magnitude above Tier 2, or as two sequential solves (minimize Tier 1 violation, fix it, then optimize Tier 2). Report every non-zero Tier 1 slack in the plan dict. Delete the fallback at `:2398-2404`; with this structure it has no purpose.

Add a pre-solve feasibility check that runs before HiGHS and names the conflict in plain language, for example "2 heavy strength sessions required, 1 available" or "anchor RUN_AE2_LONG (75 min) leaves 0 min on Sat for required Swim".

#### C. One model builder, not three

Consolidate into a single `WeekModel` with pluggable constraint modules (`calendar`, `orthopedic`, `intensity_structure`, `strength`, `goals`). Each module takes the variable index maps and adds rows through `_add_clean_row`. The three current classes become thin presets. This removes the situation in Section 1.3 where each rule exists in one engine only.

Concrete mapping of existing functions:

- `util.calculate_plan_score` → retire as an objective. Its terms $1 - |1 - r/r^\*|$ become `add_soft_goal` rows on $\sum_i (a_i - r^\* b_i)x_i$ scaled by nominal weekly duration. Keep it only as a reporting metric.
- `highs_optimization.optimize_weekly_workouts` (unordered bag) → the same `WeekModel` with day constraints disabled, used only for benchmarking.
- `optimize_weekly_schedule` → `WeekModel` preset with `max_sessions_per_day = 1`.
- `_solve_olympic_week_worker` and the multi-seed race → delete. A deterministic model solved to a stated gap does not need a seed portfolio, and parallel per-week solves cannot share state between weeks.

#### D. Missing mathematics

**Carry-in state.** Every adjacency and rolling row runs over `d in range(6)` with no knowledge of the previous Sunday. Add constants for days −1 and −2 (hard flag, heavy-run flag, heavy-lower-strength flag, TSS, strain, ANS) and include them in the `d = 0` rows:

$$h_{-1} + h_0 \le 1, \qquad L_{-1} + \sum_i \ell_i\, x_{i,0} \le L^{48h}_{\max}$$

**In-model ACWR.** The EWMA is affine in the daily load, so the ratio bound is exactly linear. With $m_d = \sum_{i \in \text{Run}} s_i\, x_{i,d}$:

$$A_d = (1-\lambda_a)A_{d-1} + \lambda_a m_d, \qquad C_d = (1-\lambda_c)C_{d-1} + \lambda_c m_d$$

$$A_d - \rho_{\max}\, C_d \le \varepsilon_{\text{floor}} \quad \forall d$$

$A_{-1}$ and $C_{-1}$ are constants from history, and $\varepsilon_{\text{floor}}$ is a small allowance so that an athlete with near-zero chronic load can still start running. Today the model uses a static weekly strain ceiling (`:968-971`) and only reacts the following week.

**Ramp limit.** Bound the weekly change in chronic load the same way: $\text{CTL}_6 - \text{CTL}_{-1} \le r_{\max}$.

**Top-K on workout selection.** Define $s_i = \sum_d x_{i,d}$. For each found plan $k$ with selected set $S_k$:

$$\sum_{i \in S_k} s_i \le |S_k| - m$$

This guarantees each alternative differs by at least $m$ workouts and removes day-permutation duplicates. Offer the exact `(i,d)` cut as a separate "same workouts, different layout" mode.

**Session slots.** The 6-hour separation rule exists only as a printed string (`main.py:368`). The cheapest correct model is a per-day parameter `two_windows[d]` with $w^{double}_d \le \texttt{two\_windows}[d]$, plus a deterministic post-solve labeling rule (hard endurance AM and lift PM; if the endurance session is easy, lift AM).

#### E. Objective scaling

- `Score = 100·(1 − penalty/8)` (`highs_optimization.py:293`) uses an arbitrary divisor. Report the individual deviations instead.
- The hybrid objective mixes units: `w_tiz = 1.0` per *minute* of deviation, `w_tss = 150` per *fraction*, and durability enters raw at `0.35 × score` where hybrid-library scores run to 85 while the dataclass documents a 0–10 range. A long ride earns −30 by itself.
- 48 h slack penalties are hardcoded as `500/650`, `500/295`, `500/260` (`:829-837`) regardless of the configured caps.

Normalize every goal deviation by its target so it is dimensionless, give every weight a documented meaning ("1.0 = one percentage point of weekly TSS"), and store weights in one config object.

#### F. Performance

The sub-second pre-filtering invariant is not met on the real library. Measured: 8.1 s per seed at a 4 s limit, 10.4 s for the day model at a 5 s limit, 30 s for three plans on 91 candidates. Three changes address it:

1. Build rows through `addRow`/`addRows` everywhere.
2. Reduce candidates by dominance and clustering before the model is built (within a sport, workouts with near-identical duration, zone vector and flags are interchangeable; keep one representative and expand afterwards). `prune_candidate_library` currently samples by sort order, which is not dominance-aware.
3. Break symmetry: when several days have identical caps and facility access, order them.

Always record `mip_gap` and model status with each plan.

### 3.2 Software architecture & data engineering

**Two sources of truth for thresholds.** `classes.py:28-30` has run CP 295 W, bike 170 W, CSS 35.29 m/min. `athlete_profile.py:31-36` has 215 W, 150 W, 24.0 m/min. This violates the repo's own single-source invariant. Power TSS is unaffected because CP cancels in `clean_file.calculate_TSS`, but swim duration and sTSS in `Swim.xlsx` are baked at 35.29. The profile comment also does not match its own value: "24.0 = 2:47 / 100m" is wrong, since 2:47/100 m is 35.9 m/min and 24.0 m/min is 4:10/100 m. One of the two numbers is an error and it changes every swim duration by roughly 47%.

**Library defects.**
- All 28 long-run workouts (`RL1–15`, `RLFF1–6`, `RLSP1–6`, `RLMS`) are prescribed in miles, parsed as TSS 0 (`clean_file.py:203-205,270-271`) and then dropped by `load_workouts`. The real library offered to the solver has no long runs.
- `rebuild_all_files()` slices tables by hardcoded position (`clean_file.py:323-336`) and would overwrite the xlsx files, deleting the 14 hand-authored rows (`RUN_SS1_STR`, `BK_TH_OU1`, `SW_CSS_TH1`, …).
- `data/*.csv` are stale copies (190/218/71 rows against 194/225/74). `recommended_workout_legos.csv` sits at the repo root against `ADR-007`. Two `brick_workouts.csv` files differ in size.
- `enrich_workouts_from_df` infers behavior from substrings. `'SS' in code` (`closed_loop_optimizer.py:477`) marks every `SSP*`, `SSI*` and `SW_CSS_*` swim as a strides session, which exempts their Z5 minutes from ANS cost. `is_indoor = duration <= 75` (`:480`) is a guess. The strength lookup table is rebuilt inside the row loop (`:506`).
- Hand-set flags are inconsistent: `RUN_TB1_BRICK` is 50% Zone 3 with `is_hard_endurance=False`.

**Recommended data layer.** One canonical library file (Parquet or CSV) with a Pandera schema, and one Pydantic `Workout` model. Required checks: unique code, sport in an enum, zone minutes ≥ 0, zone sum within 1 min of duration, TSS > 0, flags from a closed vocabulary. Behavior flags become explicit columns, authored once, never inferred from code strings at solve time. The athlete profile moves to a YAML file validated by Pydantic, and `triVariables` threshold fields are deleted.

**Global mutable state.** `triVariables` is a class used as a mutable global (`set_distance_profile`, and `triv.target_tss` is reassigned inside `evaluate_plan_with_original_score` and inside worker processes, `highs_optimization.py:782-788,797-799`). `process_weekly_execution_and_recalibrate` mutates the config it was handed (`:2070-2081`). `TriathlonMILPScheduler.__init__` mutates `cfg` (`:699-712`). Pass immutable config objects and return new ones.

**Module structure.** `closed_loop_optimizer.py` is 2,468 lines holding data models, two libraries, two solvers, the controller and the season generator. `classes.workout` defines every attribute twice, once on the class and once on the instance (`classes.py:231-373`). Suggested layout: `model/` (schemas), `library/` (load, enrich, prune), `solver/` (builder and constraint modules), `physiology/` (load metrics, state), `control/` (season, receding horizon), `io/` (FIT, Garmin, TrainingPeaks).

**Packaging.** `pyproject.toml` packages `src*`, which installs a top-level package literally named `src`, while modules import each other flat (`from classes import …`) through `sys.path` edits. `garmin_personal_api` is imported by `scripts/sync_garmin_data.py` but declared nowhere. `requirements.txt` and `pyproject.toml` list different dependencies. Rename the package (`triplan/`), use absolute package imports, and keep one dependency list.

**Rate limiting against the repo's own invariant.** The invariant requires 1.0–3.0 s random jitter. TrainingPeaks uses a fixed 0.5 s with no jitter (`sync_8020_to_tp.py:617`); Garmin sync sleeps 0.05–0.12 s between calls.

**Hardcoded results in output.** `main.py:193-203` prints "46% valid", "Pruned 61 unviable items" and "None (draws all 293)" as string literals.

**Documentation drift.** This matters more than usual here because agents are instructed to trust these files.
- `STATE_SUMMARY.md` and `ADR-010` describe `clean_file.py` as a multi-device FIT parser. It is an HTML scraper.
- `EXP-001` records a 15-seed race proving optimality in under 1.1 s on 480 workouts. Single-seed runs here hit the time limit with differing objectives. The 15-way race itself was not re-run, so this is an inconsistency rather than a refutation; mark it `NEEDS_RETEST`.
- `INVARIANTS.md` gives $N_{day} \le 130$; code uses 140 (`classes.py:185`, `closed_loop_optimizer.py:101`). $N_{wk} \le 360$ is not implemented.
- `stress_test_optimizer.py:264` labels `default_lib[6]` as the hard over-under session. Index 6 is the easy Z2 spin, so the "sick athlete on a hard day" scenario tests an easy day.

**Version control.** `git status` shows the entire restructured tree as untracked and the four committed root modules as deleted. None of the current code has history or a backup.

**Tests.** None exist. Section 7, Phase 0 lists the first ones to write.

---

## Section 4: What Needs Improvement — Physiology & Training Science

### 4.1 Load accounting

**ANS cost has three incompatible definitions.**

| Source | Z1 | Z2 | Z3 | Z4 | Z5 |
| :--- | :-: | :-: | :-: | :-: | :-: |
| `SPEC.md` table / `classes.ans_cost_7zones` | 0 | 0 | 1.0 | 1.8 | 3.5 |
| `calculate_ans_cost` ($e^{0.65(z-1)}$) | 1.0 | 1.92 | 3.67 | 7.03 | 13.46 |
| Hybrid library hand-entered values | about 5× lower than the formula (`SW_CSS_TH`: 40 stored, 203 computed) | | | | |

Under the exponential formula, easy minutes carry cost, so a 60-min Z2 session scores 101 and the 48 h "autonomic" cap of 700 behaves as a volume cap. Under the hybrid library's numbers the same cap can never bind. Adopt one definition, computed and never typed in:

$$\text{ANS}_i = \sum_{z \in \{1,2,X,3,Y,4,5\}} w_z\, t_{i,z} + \gamma \max(0,\ D_i - D_0)$$

with $w = (0, 0, 0.7, 1.0, 1.4, 1.8, 3.5)$ from `SPEC.md`, alactic efforts ≤ 20 s excluded, and a small duration term ($D_0 \approx 90$ min) so very long easy sessions are not free. It is precomputed per workout, so the model stays linear. Recalibrate the 48 h cap after the change; 700 was tuned to the other scale.

**The 7-zone scale is not used by any solver.** `CandidateWorkout.zone_durations` is a 5-tuple and Zone X/Y exist only as dictionary constants. Planned 80/20 workouts are written in Zones 1–5, so plans need no X/Y minutes. Actuals do. Extend the tuple to seven entries now so that completed-activity time-in-zone, including grey-zone drift, lands in the same structure.

**Mechanical strain.** `calculate_mechanical_strain` uses run = TSS × IF, bike = 0.1 × TSS × IF^0.2, and a strength formula with stacked constants (`1.5 × max(TSS,15) × dur/45 × 2.5`). The two hand libraries then disagree with it and with each other: a heavy squat session is 65 in one and 28 in the other, so the 260-unit 48 h cap means different things per library. Proposed, computable from existing columns:

$$S^{run}_i = \sum_z t_{i,z}\, p_z^{\beta}, \qquad S^{bike}_i = \kappa_b \sum_z t_{i,z}\, p_z \ (+\ \text{torque flag surcharge}), \qquad S^{swim}_i \approx 0$$

$$S^{str}_i = \sum_{e \in i} c_e \cdot \text{sets}_e \cdot \text{reps}_e \cdot (\%1\text{RM}_e)^2$$

Here $p_z$ is the zone midpoint as a fraction of CP and $c_e$ weights lower-body eccentric lifts above upper-body work. Start with $\beta = 2$ and treat it as a calibration parameter; tissue-fatigue models put a much steeper exponent on load magnitude than the current linear term, so intensity is probably under-weighted today. Fit against the athlete's own history (EXP-006).

**Strength is given endurance TSS.** `strength_workouts.csv` assigns a heavy barbell session 56 TSS from an IF² formula. That inflates CTL with work that is not aerobic. Give strength a small fixed aerobic TSS (roughly 10–20 per hour) and carry its real cost in strain, axial and neuromuscular load.

**PMC time constants are about half the intended values. [run]** `step_day` uses $\alpha = 2/(N+1)$ (`:1628-1629`). That gives an effective time constant of 21 days for CTL and 3.5 days for ATL. After 42 days at a constant 100 TSS/day the code reports CTL 86.5; the standard model gives 63. Use $\alpha = 1 - e^{-1/\tau}$ with $\tau = 42$ and 7. The $2/(N+1)$ form is the published convention for EWMA ACWR and can stay there. Also note `total_ctl` excludes strength while `metabolic_fitness_state` includes it.

**ODI is dimensionally inconsistent and its controller has the wrong sign.** $M$ accumulates all-sport aerobic TSS; $C$ accumulates run-only mechanical strain. Their ratio has no fixed meaning, starts from arbitrary values (15 and 20 in the profile), and rises without bound in any bike-heavy plan. The response to a high ODI is to cut run strain (`:2370-2375`), which lowers $C$ and raises ODI further. In the 16-week run it climbed from 1.13 to 2.44 with the correction applied every week. Replace with a run-specific pair in the same units:

$$\text{ODI}^{run} = \frac{\text{EWMA}_{35}(S^{run})}{\text{EWMA}_{120}(S^{run})}$$

or drop ODI in favor of in-model ACWR plus the ramp limit, which constrain the same risk directly.

**ACWR ceiling.** Code and invariants use 1.30; the target standard is 1.20–1.25. The simulated plan for the real profile opens at 1.66. ACWR is unstable at very low chronic load, which is exactly this athlete's situation (no runs in 90 days), so the first weeks need an absolute-minutes progression rule, not a ratio.

### 4.2 Intensity distribution and session structure

**Three different definitions of "80/20" coexist.**
- `triVariables`: low 85% / high 15%, where "high" is Z3 + Z4 + Z5.
- Invariants and the hybrid optimizer: Z1–2 ≥ 78%, Z4–5 ≤ 20%. Z3 is unconstrained, so 22% tempo passes.
- `target_zone_pcts`: five per-zone targets per phase.

Pick one. Recommended: low = Z1 + Z2 by time, with a floor (0.78–0.80) in Tier 0 and a phase target as a goal; a separate cap on Z4 + Z5 minutes; and the session-structure rule below, which is what actually prevents grey-zone weeks.

**Nothing prevents six "medium" workouts.** The solvers hit zone ratios in aggregate. In one day-by-day run on the real library, five of six sessions carried 10–16 intensity minutes each. Define per workout, from existing columns:

$$q_i = z3_i + z4_i + z5_i \ (\text{excluding alactic efforts}), \qquad \text{key}_i = \mathbb{1}[q_i \ge q^{\min}_{sport}] \ \lor\ \mathbb{1}[D_i \ge D^{long}_{sport}]$$

Then:
- Remove "medium" workouts from the candidate pool in pre-filtering: those with $0 < q_i < q^{\min}$. The library has few (6 run, 3 bike), so nothing of value is lost.
- $2 \le \sum_{i,d} \text{key}_i\, x_{i,d} \le 3$.
- All other selected workouts have $q_i = 0$, which holds automatically after the pre-filter.
- $\sum_{i,d} \text{strides}_i\, x_{i,d} \ge 2$.

The default config asks for one stride session, and the real library contains only two stride workouts, both runs. Add bike cadence-burst and short run-stride variants.

**The hard/easy flag is not sport-aware.** `is_hard = high_dur ≥ 15 or IF ≥ 0.85`. Swim IF comes from zone speed multipliers that start at 0.795, so the median swim IF is 0.87 and nearly every swim is "HARD", including `SF7` with zero intensity minutes. Classify by $q_i$ per sport, not by IF.

**Hard-days-hard is not enforced anywhere.** `TriathlonMILPScheduler` forbids adjacent hard days, which sometimes pushes strength onto a hard day. The hybrid optimizer has no such rule and placed heavy sessions on easy days in the default plan. Enforce it directly with a daily indicator $k_d$ for "a key endurance session is scheduled":

$$\sum_{i \in \text{HeavyLower}} x_{i,d} \le k_d \quad \forall d$$

with the squat/high-torque and deadlift/run exclusions deciding *which* key session it may pair with (in practice a hard swim or a non-torque bike session).

**Library TSS is likely overstated for easy work.** TSS is computed from zone midpoints, with every Z2 run minute at 0.82 CP. That is 67 TSS per hour for an easy run, toward the top of the zone. If real easy running sits lower, planned TSS exceeds delivered TSS and weekly hours are under-prescribed. Calibrate midpoints from history (EXP-006).

### 4.3 Periodization, durability and progression

**No memory across weeks except scalars.** The season generator carries CTL/ATL/ACWR numbers forward but not which workouts were done. With the real profile, nine distinct workouts were used across 128 sessions. Add a variety goal (penalize repeating the same key workout within N weeks) and the carry-in adjacency state from Section 3.1.D.

**No time-to-exhaustion progression.** `durability_score` is a hand-typed scalar on two scales (0–10 and 0–85). Replace with computed features from the ordered interval text in `Description`:

- $T^{thr}_i$: total Z3 minutes, and $T^{cont}_i$: longest continuous Z3 block.
- $\text{late}_i$: Z3+ minutes that begin after 60% of session duration.
- $\text{brick}_i$: run immediately following bike.

Progression goal for the designated weekly key session of each type, with a 3-week step cycle:

$$T^{thr}_{w} \ge (1+g)\, T^{thr}_{w-1} - e^-_w \quad (\text{build weeks}), \qquad T^{thr}_{w} \le 0.6\, T^{thr}_{w-1} \quad (\text{recovery week})$$

$T^{thr}_{w-1}$ is a constant from the previous solve, so the row is linear.

**Step-cycle definitions disagree.** The brief and Fitzgerald use 3-week cycles (build, build, recover); `INVARIANTS.md` mandates 3:1; `configure_hybrid_mesocycle_phase` implements 4 weeks; the season matrices implement 3. Choose one per athlete profile.

**Phase selection by substring.** `configure_mesocycle_phase` matches `"base" in phase_name`. The Olympic matrix names its first phase "General Foundation", which matches nothing and falls to the Build 1 default. Use an enum.

**Strength periodization.** The target is two heavy sessions in base and one in build at 80–90% 1RM. The season matrix requests this, but the library cannot supply two (A2), and with the real profile the season output contains zero strength sessions. Taper and recovery weeks zero strength entirely (`athlete_profile.py:168-171`); a short maintenance session is more conventional than full removal, but that is a coaching preference to confirm.

**Re-entry is not modeled.** The profile describes a detrained athlete (CTL 5, no runs in 90 days), yet the default library's shortest run is 30 minutes continuous and the default anchor is a 75-min long run. A return-to-run block needs walk-jog sessions and a minutes-per-week progression, with ratio-based limits switched on only once chronic load is meaningful.

### 4.4 Readiness and control

- **Bug:** `calculate_garmin_morning_readiness` divides the resting-HR deviation by half the *HRV* standard deviation (`:1761`). It needs its own RHR baseline SD.
- Two readiness scalars with different ranges ([0.3, 1.3] and [0.70, 1.05]); the target is $\alpha \in [0.6, 1.0]$. Keep one.
- Only the hybrid optimizer consumes α. Tier 1 adjusts the single day's workout and never re-solves the rest of the week. A receding-horizon re-solve with completed days fixed is the missing piece: fix $x_{i,d}$ for $d <$ today to actuals, scale today's caps by $\alpha_d$, re-optimize the remainder.
- 270 nights of Garmin HRV are already on disk (`data/garmin/wellness/hrv_summaries.json`) and unused.

---

## Section 5: What Needs More Information (User Questions & Data Probes)

### 5.1 Questions, in priority order

**Blocking (the model cannot be specified correctly without these)**

1. [ ] **Swim CSS.** Is it 35.29 or 24.0 m/min, and are your pool lengths in meters or yards? What were the 400 and 200 times? The profile's value and its comment disagree.
2. [ ] **Race.** Date, distance and priority of the target race, and any B races. Is "16 weeks, Olympic" real or a placeholder?
3. [ ] **Current state.** Is the profile right that you have done no runs or rides in the last 90 days? Any current injury or a history that should cap running (Achilles, knee, bone stress)?
4. [ ] **Real time budget.** The profile allows 45–75 min per day, 6.5 h per week. Is that a re-entry setting or your true ceiling? Which days can hold two sessions at least six hours apart?
5. [ ] **Thresholds.** Have run CP 215 W and bike CP 150 W been tested with the 3 + 12 minute protocol, and when? Was bike CP tested in aero position on the Neo 2T? Is W′ = 18 kJ measured or assumed?
6. [ ] **Safety limits when nothing fits.** When a week cannot satisfy everything, which should give first: number of sessions, weekly TSS, long-run length, or a strength session? This sets the Tier 1 relaxation order.

**Shaping (defaults exist but your answer changes the plan)**

7. [ ] Pool access: weekends only, as in the profile, or Mon/Wed/Fri as in the hybrid defaults?
8. [ ] Strength: preferred split (Upper/Lower or Heavy-Light-Medium), which days the rack is available, current working weights, and whether deadlift and squat can share a session.
9. [ ] Is strength a co-equal goal or in service of the triathlon? In build and taper, should it drop to maintenance or to zero?
10. [ ] Step cycle: 2 build + 1 recovery, or 3 + 1?
11. [ ] Fixed commitments: group rides, masters swim, a standing rest day, travel weeks.
12. [ ] Indoor versus outdoor: are weekday rides always on the trainer? Treadmill use?
13. [ ] Which 80/20 definition do you want enforced (Section 4.2)?
14. [ ] ACWR ceiling: keep 1.30 or move to 1.20–1.25?

**Library and data**

15. [ ] The 28 mile-based long runs: convert using your easy pace, or author duration-based replacements? What is your easy pace?
16. [ ] Who authored the 14 custom rows (`RUN_SS1_STR`, `BK_TH_OU1`, …) and `recommended_workout_legos.csv`, and are they final?
17. [ ] Do you have original activity `.fit` files from the Enduro 3, Edge 530, Swim 2 and Stryd? The only `.fit` files in the repo are 80/20 structured-workout definitions (643 bytes each), not recordings.
18. [ ] Which device records which sport today, and do the Edge and watch both record rides?
19. [ ] Is TrainingPeaks the system of record for completed workouts, or Garmin Connect?
20. [ ] Should plans be pushed to TrainingPeaks automatically or reviewed first?

### 5.2 Data inspection commands

Run from the repo root in PowerShell. Each prints structure or statistics only, never workout description text.

Column headers and row counts for every library file:

```powershell
python -c "import pandas as pd, glob; [print(f, pd.read_excel(f, engine='openpyxl').shape, list(pd.read_excel(f, engine='openpyxl').columns)) for f in glob.glob('data/*.xlsx')]"
```

Distributions of duration, TSS and intensity minutes by code family:

```powershell
python -c "import pandas as pd; d=pd.concat([pd.read_excel(f'data/{s}.xlsx',index_col=0,engine='openpyxl') for s in ['Run','Bike','Swim']]); d['fam']=d.Code.str.extract(r'^([A-Za-z_]+)')[0]; d['q']=d[['Zone 3','Zone 4','Zone 5']].sum(axis=1); print(d.groupby(['Type','fam']).agg(n=('Code','size'),dur=('Duration','median'),tss=('TSS','median'),q=('q','median')).round(1).to_string())"
```

Schema violations (zero TSS, zone minutes not summing to duration, duplicate codes):

```powershell
python -c "import pandas as pd; [print(s, 'tss0', int((d.TSS==0).sum()), 'zone_mismatch', int(((d[['Zone 1','Zone 2','Zone 3','Zone 4','Zone 5']].sum(axis=1)-pd.to_numeric(d.Duration,errors='coerce')).abs()>1).sum()), 'dup', int(d.Code.duplicated().sum())) for s in ['Run','Bike','Swim'] for d in [pd.read_excel(f'data/{s}.xlsx',index_col=0,engine='openpyxl')]]"
```

How the long runs are prescribed (unit words only):

```powershell
python -c "import pandas as pd, re, collections; d=pd.read_excel('data/Run.xlsx',index_col=0,engine='openpyxl'); z=d[d.TSS==0]; print(len(z), collections.Counter(w for t in z.Description for w in re.findall(r'(mile|miles|km|minutes|Zone \d)', t)))"
```

Garmin history: activity counts by type and year, and how many carry power:

```powershell
python -c "import json, collections; a=json.load(open('data/garmin/activities/activities_summary.json',encoding='utf-8')); c=collections.Counter((x['startTimeLocal'][:4], x['activityType']['typeKey']) for x in a); [print(k, v) for k, v in sorted(c.items()) if k[0] >= '2024']; print('with avgPower:', sum(1 for x in a if x.get('avgPower')))"
```

HRV coverage and baseline:

```powershell
python -c "import json, statistics as s; h=json.load(open('data/garmin/wellness/hrv_summaries.json',encoding='utf-8')); rows=h if isinstance(h,list) else [r for v in h.values() for r in (v if isinstance(v,list) else [v])]; v=[r['lastNightAvg'] for r in rows if isinstance(r,dict) and r.get('lastNightAvg')]; print('nights', len(v), 'mean', round(s.mean(v),1), 'sd', round(s.stdev(v),1), 'min', min(v), 'max', max(v))"
```

Lifting history structure:

```powershell
python -c "import json; d=json.load(open('data/lifting/lifting_summary.json',encoding='utf-8')); print(type(d).__name__, list(d.keys())[:20] if isinstance(d,dict) else d[:2])"
```

FIT metadata for a real activity file. This needs `pip install fitdecode` and a recording at a path of your choice. It prints message counts, every field name seen on `record` messages (a second `Power` alongside `power` is the Stryd developer field), swim length types, and device/sport identifiers. **This one command is untested**: no FIT library is installed here and no recording is on disk, so it may need a small adjustment.

```powershell
python -c "import fitdecode, collections, sys; m=collections.Counter(); rec=set(); lt=collections.Counter(); info=[]; [(m.update([f.name]), rec.update(x.name for x in f.fields) if f.name=='record' else None, lt.update([str(x.value) for x in f.fields if x.name=='length_type']) if f.name=='length' else None, info.append({x.name: x.value for x in f.fields if x.name in ('manufacturer','product','garmin_product','sport','sub_sport')}) if f.name in ('file_id','device_info','sport') else None) for f in fitdecode.FitReader(sys.argv[1]) if isinstance(f, fitdecode.FitDataMessage)]; print('messages', dict(m)); print('record fields', sorted(map(str, rec))); print('length_type', dict(lt)); print('info', info[:6])" path\to\activity.fit
```

Overlapping recordings from two devices (candidates for the 300 s deduplication rule):

```powershell
python -c "import json, datetime as dt; a=sorted(json.load(open('data/garmin/activities/activities_summary.json',encoding='utf-8')), key=lambda x:x['startTimeGMT']); t=[dt.datetime.fromisoformat(x['startTimeGMT']) for x in a]; print('pairs starting within 300 s:', sum(1 for i in range(1,len(t)) if (t[i]-t[i-1]).total_seconds() < 300))"
```

---

## Section 6: Delegated Experiments & Benchmarks for Other Models (`EXP-XXX`)

**Numbering.** `EXP-001` is already recorded and `EXP-002` is queued in `docs/TASKS.md`. The designs below give `EXP-002` a concrete specification and add `EXP-003` through `EXP-006`. Each prompt is self-contained. Suggested order: EXP-003 first, because EXP-002 and EXP-004 depend on a solver whose results can be trusted.

Common rules to include with any hand-off: work only inside the repository; never read `config/*.env`; never overwrite existing files in `training_plans/` or `data/*.xlsx`; put scripts in `scripts/` and outputs in `data/experiments/<EXP-ID>/`; register the write-up with `python scripts/lodectx.py init exp`.

### EXP-003 — Solver correctness and scaling on the real library

- **Objective.** Establish how solve time and proven optimality gap scale with candidate count for the day-indexed model, and whether a multi-seed race adds anything once the gap is reported. Re-test `EXP-001`.
- **Hypothesis.** With sparse row construction and dominance pruning to ≤ 80 candidates, a single deterministic solve reaches a 1% relative gap in under 2 s; a seed race does not improve the objective at equal total CPU time.
- **Inputs.** Real pool from `util.load_workouts(include_strength=False)`, restricted to Run/Bike/Swim. Candidate counts 40, 80, 160, 324 produced by (a) the existing `filter_viable_workouts`, (b) a new dominance/cluster pruner. A synthetic 500-workout library generated by jittering real rows (duration ±10%, zone minutes re-normalized), fixed seed.
- **Success metrics.** Per cell (n = 10): wall time split into build and solve, model status, `mip_gap`, objective. Pass if p95 ≤ 2 s at ≤ 80 candidates with gap ≤ 1%, and every returned plan carries status and gap. Top-5 objective values must be monotone non-improving.
- **Artifact.** `data/experiments/EXP-003/results.csv`, a scaling chart, and `docs/experiments/EXP-003.md`.

```text
You are working in the repository C:\Users\drisc\PycharmProjects\triathlon_planning (Python 3.14, highspy installed).
Read CLAUDE.md, docs/AUDIT_AND_ROADMAP.md sections 3.1 and 6, src/highs_optimization.py and src/closed_loop_optimizer.py first.

Task: benchmark the day-indexed weekly MILP (highs_optimization.optimize_weekly_schedule) on the real workout library.
1. Write scripts/exp003_solver_scaling.py. Load the pool with util.load_workouts(include_strength=False) and keep Type in Run/Bike/Swim.
2. Add a dominance/cluster pruner: within a sport, group workouts whose duration is within 5 minutes and whose zone-minute vectors are within 3 minutes per zone; keep one per group.
3. For candidate counts 40, 80, 160 and the full pool, with both the existing filter_viable_workouts and your pruner, run 10 repetitions each at target_tss=500 and record: build seconds, solve seconds, HiGHS model status, mip_gap from getInfo(), objective, and the chosen codes. Do the same for a synthetic 500-workout library made by jittering real rows with a fixed seed.
4. Rebuild the same model using addRow with numpy index/value arrays (see TriathlonMILPScheduler._add_clean_row) and compare build time against the expression API.
5. Run a 15-process x 2-thread seed race and a single 30-thread solve at equal wall-clock budget; compare best objective and gap.
6. Request top-5 plans and check objectives are monotone.
Rules: use ONE thread setting per process (HiGHS fails silently if two Highs instances in a process set different thread counts). Always check model status before reading the solution. Do not read config/*.env. Do not write to training_plans/ or modify data/*.xlsx. Write outputs to data/experiments/EXP-003/.
Deliverables: results.csv, one scaling chart (PNG), and docs/experiments/EXP-003.md created via "python scripts/lodectx.py init exp --id EXP-003 --domain perf --title ..." following docs/templates/EXP_TEMPLATE.md. State plainly whether EXP-001's sub-1.1-second optimality claim reproduces.
```

### EXP-004 — Goal-weight sensitivity and session-structure check

- **Objective.** Determine how sensitive plan composition is to soft-goal weights, and whether the key-anchor/pure-fill structure can be obtained by constraints with the weights left at neutral values.
- **Hypothesis.** With key-session bounds (2–3) and a pre-filter removing workouts with small non-zero intensity minutes, the weekly low-intensity share lands in 78–84% for any weight vector in a ±4× range, and plan composition changes by at most two workouts.
- **Inputs.** Real library. Weight grid: each of the TSS, sport-split, zone and durability weights at ×0.25, ×1, ×4 (81 combinations), at 300, 500 and 700 TSS.
- **Success metrics.** Per run: count of key sessions, count of sessions with $0 < q_i < q^{\min}$ (must be 0), Z1+Z2 share, per-goal deviations, Jaccard similarity of selected workouts against the neutral-weight plan. Deliver the Pareto set of (TSS deviation, zone deviation, split deviation).
- **Artifact.** `data/experiments/EXP-004/grid.csv`, a Pareto plot, recommended default weights with a one-line meaning for each.

```text
You are working in C:\Users\drisc\PycharmProjects\triathlon_planning. Read CLAUDE.md and docs/AUDIT_AND_ROADMAP.md sections 3.1.E and 4.2 first.

Task: sensitivity analysis of soft-goal weights in the weekly MILP, with and without session-structure constraints.
1. Write scripts/exp004_weight_sensitivity.py using the day-indexed model in src/highs_optimization.py as the base (copy the model into your script; do not edit src/).
2. Add per-workout features: q = Zone 3 + Zone 4 + Zone 5 minutes; key = (q >= 15 for Run/Bike, q >= 12 for Swim) or (duration >= 90 for Run, >= 150 for Bike). Treat workouts whose Code contains "_SS" and "STR" as stride sessions and exclude their Zone 5 minutes from q.
3. Build two variants: (A) current model; (B) remove workouts with 0 < q < threshold from the pool, require 2 <= number of key sessions <= 3, require >= 2 stride sessions if available, allow up to 8 sessions per week with at most 2 per day.
4. For each variant, sweep each of four goal weights (TSS, sport split, overall zone ratio, per-sport zone detail) over 0.25x, 1x, 4x (81 combinations) at target TSS 300, 500, 700. Normalize every deviation by its target so weights are dimensionless.
5. Record per run: status, mip_gap, codes, key-session count, medium-session count, Z1+Z2 share of time, each goal's deviation, and Jaccard similarity to the 1x/1x/1x/1x plan.
Rules: one thread setting per process; check model status; no reads of config/*.env; outputs to data/experiments/EXP-004/ only.
Deliverables: grid.csv, a Pareto plot of TSS deviation vs zone deviation vs split deviation, and docs/experiments/EXP-004.md (init via scripts/lodectx.py, domain optimizer). Conclude with recommended default weights and whether variant B makes the result insensitive to them.
```

### EXP-005 — FIT ingestion parser and device validation

- **Objective.** Build and validate the missing `.fit` parser against the hardware rules in `SPEC.md` section 3.
- **Hypothesis.** A single parser can (a) take run power only from the Stryd developer field, (b) compute swim duration from active lengths, (c) deduplicate overlapping ride files, and (d) ignore trainer left/right balance, with field-level tests for each rule.
- **Inputs.** Synthetic FIT files generated in the test suite with a FIT-writing library (one per rule: run with both native and developer `Power`; pool swim with active, idle and drill lengths; two rides starting 120 s apart; a trainer ride with a `left_right_balance` stream; a treadmill run with `Air Power` at 0). Real files if the user supplies them under `data/fit_activities/`.
- **Success metrics.** All rule tests pass. On synthetic files: extracted run power equals the developer stream exactly; swim seconds equal the sum of active lengths; exactly one of two overlapping rides is ingested; 7-zone time-in-zone sums to moving time within 1 s. On real files: report, per device, which fields were actually present.
- **Artifact.** `src/fit_ingest.py` (or the package equivalent), `tests/test_fit_ingest.py`, a device field matrix in `docs/experiments/EXP-005.md`.

```text
You are working in C:\Users\drisc\PycharmProjects\triathlon_planning. Read CLAUDE.md, SPEC.md section 3, docs/lode/ADR-010*.md and docs/lode/HEUR-005..007 first. Note: despite what docs/STATE_SUMMARY.md says, no FIT parser exists in the repo; src/clean_file.py is an HTML scraper.

Task: implement and test a FIT activity parser.
1. Create src/fit_ingest.py exposing parse_activity(path) -> dataclass with: sport, sub_sport, start_time, device, moving_seconds, per-second power series (or None), time in each of the seven 80/20 zones (1, 2, X, 3, Y, 4, 5) given a CP value, and for swims: active_seconds, drill_seconds, length count, SWOLF excluding drills. Use fitdecode or the official garmin-fit-sdk; add the dependency to pyproject.toml.
2. Rules to implement, each with its own test: (a) running power comes ONLY from the developer field named exactly "Power"; never from native record.power; also extract "Form Power", "Leg Spring Stiffness", "Air Power" when present; (b) pool swim duration = sum over length messages with length_type == "active"; drill lengths count toward easy time but not SWOLF or stroke rate; (c) deduplicate(list_of_parsed) keeps one file when two same-sport activities start within 300 s, preferring the bike computer for rides; (d) ignore left_right_balance when the source is a Tacx Neo trainer; (e) treadmill sub_sport is flagged so Air Power of 0 is expected, not an error; (f) records with missing power are zeros for averaging on rides.
3. Generate synthetic FIT fixtures inside the tests with a FIT-encoding library (for example fit-tool) so the suite needs no private data. Add pytest to the dev dependencies and put tests in tests/test_fit_ingest.py.
4. If files exist under data/fit_activities/, run the parser over them and tabulate per device which native and developer fields are present. Print field names and counts only.
Rules: do not read config/*.env; do not touch data/fit_files/ (those are workout definitions, not recordings); no network calls.
Deliverables: the module, passing tests, and docs/experiments/EXP-005.md (init via scripts/lodectx.py, domain integrations) with the device field matrix and any rule in SPEC.md that the real files contradict.
```

### EXP-006 — Load-model calibration against the athlete's own history

- **Objective.** Replace asserted constants with fitted ones: zone-midpoint multipliers used for planned TSS, PMC time-constant implementation, the mechanical-strain exponent, and the readiness scalar.
- **Hypothesis.** (a) Planned TSS from zone midpoints overstates delivered TSS for easy runs by more than 10%. (b) The current `step_day` CTL deviates from a τ = 42 d reference by more than 25% after six weeks. (c) A z-score readiness scalar from nightly HRV and resting HR maps to [0.6, 1.0] with fewer than 15% of days below 0.85.
- **Inputs.** `data/garmin/activities/activities_summary.json` (2,020 activities since 2013, 680 with power), `data/garmin/activities/details/*.json`, `data/garmin/wellness/*.json` (270 nights of HRV), the workout library.
- **Success metrics.** A table of fitted versus current constants with confidence intervals; a corrected `step_day` validated against a closed-form reference to within 0.5%; a readiness function with its empirical distribution; an explicit statement of which quantities the data cannot identify.
- **Artifact.** `data/experiments/EXP-006/` with fitted parameters as JSON, plots, and `docs/experiments/EXP-006.md`.

```text
You are working in C:\Users\drisc\PycharmProjects\triathlon_planning. Read CLAUDE.md, SPEC.md, docs/AUDIT_AND_ROADMAP.md section 4, and AthletePhysiologicalState.step_day plus calculate_garmin_morning_readiness in src/closed_loop_optimizer.py first.

Task: calibrate the load model from local Garmin exports. All data is already on disk; make no network calls.
1. Write scripts/exp006_load_calibration.py. Load data/garmin/activities/activities_summary.json and data/garmin/wellness/hrv_summaries.json, daily_summaries.json and sleep_summaries.json. Inspect the keys first and report which fields exist; do not assume.
2. PMC check: implement a reference exponentially weighted load with alpha = 1 - exp(-1/tau) for tau = 42 and 7 days. Feed the same daily series to step_day and to the reference; report the divergence over time. Propose the one-line fix and verify it matches the reference within 0.5%.
3. Planned-vs-actual intensity: for activities with average or normalized power, compute intensity relative to the CP values in src/athlete_profile.py and compare the distribution for easy sessions with the zone midpoints in classes.triVariables (run 0.63, 0.82, 0.97, 1.115, 1.35; bike 0.60, 0.765, 0.955, 1.06, 1.30). State whether the thresholds on file are plausible for the period analysed; the athlete may have been fitter or less fit then, so say what cannot be concluded.
4. Mechanical strain: build a daily running load series from duration and intensity with exponent beta in {1, 1.5, 2, 3}; compute EWMA ACWR (7 d / 28 d) for each; report how often each exceeds 1.25 and 1.30 historically. If the data holds no injury labels, say that beta cannot be identified from it and report sensitivity only.
5. Readiness: compute a rolling 28-day baseline mean and SD for overnight HRV and for resting HR (each with its OWN SD; the current code divides the RHR deviation by the HRV SD). Define alpha = clip(1 + a*z_hrv - b*z_rhr + c*sleep_term, 0.6, 1.0), choose a, b, c so that roughly 10-15% of days fall below 0.85, and plot the distribution.
Rules: do not read config/*.env; do not modify src/; outputs to data/experiments/EXP-006/ only; aggregate statistics only in the write-up, no raw personal records.
Deliverables: fitted_parameters.json, plots, and docs/experiments/EXP-006.md (init via scripts/lodectx.py, domain physiology) with a table "constant, current value, fitted value, evidence strength".
```

### EXP-002 (queued; concrete design) — Closed-loop season simulation under the real profile

- **Objective.** Verify that a consolidated solver with the tiered constraint hierarchy produces a feasible, safe, non-degenerate 16-week plan for `CURRENT_ATHLETE`, including under perturbations.
- **Hypothesis.** With Tier 0 safety hard, Tier 1 relaxable in a fixed order, carry-in state and in-model ACWR: zero fallback weeks, solved TSS within ±10% of target in at least 14 of 16 weeks, running ACWR ≤ 1.25 on every simulated day after the re-entry block, single-run share ≤ 32% whenever four or more runs are scheduled, and at least 20 distinct workouts used.
- **Inputs.** `AthleteProfile()` as on disk; the default candidate library extended with the real library's pure-aerobic and key workouts; perturbation sets from `scripts/stress_test_season_perturbations.py` (illness week, pool closure, travel week); execution noise (each session completed with probability 0.9, duration scaled by a factor drawn from 0.8–1.1).
- **Success metrics.** The hypothesis thresholds above, plus: every relaxed Tier 1 constraint is named in the output; no cross-week adjacency violations (hard Sunday followed by hard Monday; heavy lower-body lift within a day of a long or hard run); baseline today is 16 of 16 fallback weeks and ACWR 1.66 in week 1.
- **Artifact.** `data/experiments/EXP-002/season_*.csv`, trajectory plots for CTL, ACWR and single-run share, `docs/experiments/EXP-002.md`.

```text
You are working in C:\Users\drisc\PycharmProjects\triathlon_planning. Read CLAUDE.md, docs/AUDIT_AND_ROADMAP.md sections 1, 3.1 and 4, and generate_closed_loop_season_schedule plus TriathlonMILPScheduler in src/closed_loop_optimizer.py first.

Context: with the on-disk AthleteProfile, generate_closed_loop_season_schedule currently finds every week infeasible and falls back to one generic week repeated 16 times (393 TSS against 88-240 targets, week-1 running ACWR 1.66). Causes: 2 heavy strength sessions required with 1 in the library; a mandatory 75-minute long-run anchor that does not fit the time caps alongside the required swim and stride sessions; frequency bounds that are hard while safety caps are soft.

Task: in a NEW module scripts/exp002_season_sim.py (do not edit src/), build a revised weekly model and season loop, then measure it.
1. Constraint tiers. Tier 0 hard: daily time caps, facility access, one copy of a workout per week, max 2 sessions per day, single-run share <= 0.32 when >= 4 runs are scheduled, heavy-lower-strength vs heavy/long run lockout on the same and adjacent days, rolling 48 h caps. Tier 1 slack with weight 1000 and a named slack per constraint: anchors, minimum frequencies, stride count, heavy-strength count, minimum rest days. Tier 2 goals: TSS, sport split, zone shares, each normalized by its target.
2. Carry-in: pass the previous week's last two days (hard flag, heavy-run flag, heavy-strength flag, TSS, mechanical strain) as constants into the day-0 adjacency and 48 h rows.
3. In-model ACWR: daily running strain m_d is linear in x; A_d and C_d follow EWMA recursions with lambda = 2/8 and 2/29 from carried-in values; add A_d - 1.25*C_d <= floor, with a floor that allows about 60 minutes of easy running per week when chronic load is near zero.
4. Replace the fallback: if the solve is infeasible, report which Tier 0 rows conflict and stop.
5. Simulate 16 weeks for the default profile under: nominal; illness in week 5; pool closure in week 8; travel in week 11; and execution noise (session completion probability 0.9, duration factor uniform 0.8-1.1, fixed seed). Update state from simulated actuals, not from the plan.
6. Record per week: target and solved TSS, each Tier 1 slack that is non-zero, max daily ACWR, single-run share, number of runs, distinct workouts used to date, cross-week adjacency violations.
Rules: use a single HiGHS thread setting throughout the process; check model status before reading solutions; record mip_gap; do not read config/*.env; do not write to training_plans/; outputs to data/experiments/EXP-002/.
Deliverables: season CSVs, plots of CTL, ACWR and single-run share, and docs/experiments/EXP-002.md (init via scripts/lodectx.py, domain safety) with a pass/fail line for each threshold in docs/AUDIT_AND_ROADMAP.md section 6, EXP-002.
```

---

## Section 7: Phased Implementation Roadmap

Each bullet is one PR. Existing entry points keep working throughout: old functions are retained as wrappers until Phase 2 ends, and the scenario suite is converted to tests first so that every later PR is checked against it.

### Phase 0 — Stabilize and make the repo trustworthy

- **0.1 Commit the tree.** Stage and commit the current layout so there is a baseline. Confirm `config/*.env`, `data/garmin/`, `data/lifting/` and the reference books stay ignored.
- **0.2 Fix crashes.** `util.py:235` and `clean_file.py:176-177` attribute errors; the TSS-versus-time filter at `util.py:85,88`. Guard `rebuild_all_files()` so it cannot overwrite hand-authored rows.
- **0.3 Solver hygiene.** One module-level thread setting used by every `Highs()` instance. Status check before every solution read; raise a typed error on anything other than optimal or feasible-at-limit. Return `status` and `mip_gap` in every plan.
- **0.4 Test harness.** Add `pytest`. Convert the eleven stress scenarios to tests with real assertions (for example, single-run share ≤ 0.32, no deadlift adjacent to a long run, requested K plans returned). Several will fail; mark them expected-fail and link each to its fix. Add a test that the on-disk profile yields a non-fallback week.
- **0.5 Documentation truth pass.** Correct `STATE_SUMMARY.md` on FIT parsing; mark `ADR-010` as accepted-not-implemented; set `EXP-001` to `NEEDS_RETEST`; reconcile $N_{day}$ 130 versus 140; remove hardcoded result strings from `main.py`.

*Exit:* `pytest` runs; `main.py` completes; no silent empty plans.

### Phase 1 — Data layer and single source of truth

- **1.1 Canonical schemas.** Pydantic `Workout` and `AthleteProfile`; Pandera schema for the library table; seven-zone minutes.
- **1.2 One library file.** Merge the three xlsx files, strength, bricks and the root-level CSV into one validated table under `data/`. Explicit flag columns; delete substring inference. Remove stale CSV copies.
- **1.3 Long runs.** Convert the 28 mile-based workouts using the athlete's easy pace, or add duration-based equivalents.
- **1.4 Thresholds.** Delete `run_ftp`, `bike_ftp`, `swim_css` from `triVariables`; derive swim durations from the profile's CSS at load time. Resolve the CSS value with the athlete first.
- **1.5 Package layout.** Rename to a real package, absolute imports, one dependency list, declare `garmin_personal_api`.

*Exit:* library loads through one validated function; no threshold is defined twice.

### Phase 2 — One solver

- **2.1 `WeekModel` builder** with constraint modules and sparse row construction. Port `TriathlonMILPScheduler` first, behind a flag, and require identical objectives on the scenario tests.
- **2.2 Constraint tiers** and the pre-solve feasibility explainer. Remove the fallback path. Make the 32% cap hard whenever four or more runs exist, and report it as unmet-by-design otherwise.
- **2.3 Carry-in state, in-model ACWR and ramp limit.**
- **2.4 Session structure:** key-session bounds, pure-aerobic fill pre-filter, stride minimum, hard-days-hard row, `two_windows` gating.
- **2.5 Strength module:** port the conflict matrix, set targets, barbell share, axial and neuromuscular caps (including the weekly neuromuscular cap) from the hybrid optimizer. Extend deadlift adjacency to long runs.
- **2.6 Top-K on selection variables**; retire the forced split flip and the multi-seed race.
- **2.7 Retire** `highs_optimization.py`'s duplicate models, the hybrid class, and the random sampler, leaving thin wrappers. Run EXP-003 and EXP-004 here.

*Exit:* every rule in the charter is enforced in one code path; the Section 1.3 table has one column.

### Phase 3 — Physiology calibration and periodization

- **3.1 Load metrics:** single ANS definition, computed strain model, strength TSS correction, PMC fix. Recalibrate the 48 h caps afterward. Informed by EXP-006.
- **3.2 Replace or redefine ODI.**
- **3.3 Durability features** parsed from interval order; TTE progression goal; variety goal.
- **3.4 Season layer:** phase enum, a single step-cycle definition, re-entry block with walk-jog progression, strength phases with a maintenance option.
- **3.5 Run EXP-002** as the acceptance test for the season planner.

*Exit:* the 16-week simulation for the real profile passes the EXP-002 thresholds.

### Phase 4 — Telemetry and the closed loop

- **4.1 FIT parser** and deduplication (EXP-005).
- **4.2 Actuals store:** completed sessions with seven-zone time, TSS, strain; state update from actuals.
- **4.3 Readiness:** one α in [0.6, 1.0] from Garmin wellness data, with the RHR baseline fix.
- **4.4 Receding-horizon re-solve:** fix completed days, scale today's caps by α, re-optimize the remainder of the week.
- **4.5 Plan export** to TrainingPeaks with review-before-push and rate limiting that meets the 1–3 s jitter invariant; bring the Garmin sync to the same standard.
- **4.6 CP retest loop** wired to real decoupling and efficiency metrics from parsed files.

*Exit:* a completed week of real activities changes next week's plan with no manual data entry.
