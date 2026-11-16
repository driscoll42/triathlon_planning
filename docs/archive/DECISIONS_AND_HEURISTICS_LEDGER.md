# Triathlon Workout & Season Planner — Verbatim Architectural Decisions & Heuristics Ledger

> *Auto-rendered from `docs/.context/lode.db`.*

## Part 1: Architectural Decisions
### `ADR-001` — Adopt Progressive Disclosure & SQLite Lineage Architecture [`ACTIVE` | `general`]

# `ADR-001`: Adopt Progressive Disclosure & SQLite Lineage Architecture

## 1. Context & Problem
As autonomous coding agents interact with long-running engineering projects, monolithic state documentation (`MEMORY.md` or unbounded changelogs) rapidly grows past 20,000+ tokens. This causes severe reasoning degradation, temporal state collisions (re-introducing discarded ideas or invalidated benchmark claims), and high token consumption on every turn.

## 2. Considered Alternatives
1. **Option A (Monolithic MEMORY.md):** Single markdown file containing all state. Rejected due to context bloating, compaction loss, and lack of temporal edge tracking.
2. **Option B (Vector Database RAG):** External vector DB. Rejected because similarity embeddings fail to distinguish active decisions from superseded historical notes.
3. **Option C (ADOPTED):** Progressive Disclosure Core (<7,500 tokens) backed by zero-dependency Python SQLite WAL + FTS5 BM25 lineage engine (`scripts/lodectx.py`).

## 3. Decision & Production Invariant
- **Target Component / Path:** `docs/`, `scripts/lodectx.py`, `docs/.context/lode.db`.
- **Locked Parameters / Contract:** Startup Core (`GEMINI.md`, `STATE_SUMMARY.md`, `LODE_MAP.md`, `INVARIANTS.md`, `TASKS.md`) must strictly stay under 7,500 estimated tokens (`chars / 4`). *(File set re-scoped 2026-11-16 by `ADR-016`: charter excluded, `SPEC.md` included.)*
- **Enforcement Mechanism:** Automated token budget check via `python scripts/lodectx.py stats`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Token metrics in `lodectx.py stats` demonstrating < 4,000 tokens for baseline startup core.
- **Supersedes:** None. Foundational architecture decision.

## 5. Consequences & Trade-Offs
- **Positive:** Agents only load active, verified decisions and domain manuals on-demand; zero temporal collisions.
- **Negative / Operational Cost:** Engineering decisions and heuristics must be scaffolded and ingested through `lodectx.py`.
- **When to Revisit:** If repository domain structure exceeds 25 domains or token budget needs recalibration.

### `ADR-002` — Adopt HiGHS Mixed-Integer Linear Programming Solver via highspy [`ACTIVE` | `optimizer`]

# `ADR-002`: Adopt HiGHS Mixed-Integer Linear Programming Solver via highspy

## 1. Context & Problem
Generating weekly triathlon schedules across 71 swim, 218 bike, and 190 run workouts yields $1.2 \times 10^{16}$ possible combinations. The original greedy random replacement search required 60 seconds on 30 CPU threads to evaluate 10,000 combinations, often failing to satisfy tight multi-zone and TSS targets simultaneously.

## 2. Considered Alternatives
1. **Option A (Greedy Random Perturbation):** Random sampling with 25 replacement iterations. Rejected because it takes 60 seconds, provides no mathematical optimality guarantee, and struggles with hard daily time caps.
2. **Option B (Genetic Algorithms):** Stochastic search across chromosomes. Rejected due to convergence inconsistency and slow constraint handling for rolling strain caps.
3. **Option C (ADOPTED):** Formulate weekly scheduling as a Mixed-Integer Linear Program (MILP) solved via HiGHS (`highspy`).

## 3. Decision & Production Invariant
- **Target Component / Path:** `highs_optimization.py`, `closed_loop_optimizer.py`.
- **Locked Parameters / Contract:** Model workout selection as binary decision variables $x_{w,d} \in \{0, 1\}$. Candidate pool must be pre-pruned using `filter_viable_workouts`.
- **Enforcement Mechanism:** Unit test suite in `stress_test_optimizer.py` verifying optimal solve status in < 3.0 seconds.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-001` benchmarks demonstrating optimal solutions in 0.25 to 1.5 seconds vs. 60+ seconds for greedy search.
- **Supersedes:** None (replaces legacy greedy search in `util.py:generate_random_workout_plan`).

## 5. Consequences & Trade-Offs
- **Positive:** Guaranteed global optimality within defined tolerances; sub-second solution time; deterministic constraint satisfaction.
- **Negative / Operational Cost:** Non-linear physiological relationships must be linearized or pre-computed into static coefficients.
- **When to Revisit:** If non-linear dynamic constraints (e.g. continuous hydration curves) cannot be accurately linearized.

### `ADR-003` — Enforce Single-Run Volume Ceiling at 32 Percent of Weekly Running Duration [`ACTIVE` | `safety`]

# `ADR-003`: Enforce Single-Run Volume Ceiling at 32 Percent of Weekly Running Duration

## 1. Context & Problem
In endurance triathlon training, disproportionately long runs account for the majority of bone stress injuries, tibial stress syndrome, and plantar fasciitis. When the optimizer attempts to satisfy total weekly running TSS on a weekend, it can inadvertently allocate 45-55% of weekly volume to a single long run.

## 2. Considered Alternatives
1. **Option A (Uncapped Duration):** Rely only on absolute daily caps (e.g. 140 min). Rejected because 140 min inside a 200 min weekly run budget represents 70% of weekly load, an extreme orthopedic hazard.
2. **Option B (Fixed Duration Ceiling e.g. 90m):** Rigid duration limit. Rejected because as total volume scales from 4 to 8 hours of running, a 90m ceiling prevents adequate long-run stimuli for half/full Ironman athletes.
3. **Option C (ADOPTED):** Dynamic proportional ceiling: $\forall d, \text{Run Duration}_d \le 0.32 \cdot \sum_t \text{Run Duration}_t$.

## 3. Decision & Production Invariant
- **Target Component / Path:** `closed_loop_optimizer.py:MesoCycleTargetsAndConstraints.max_single_run_vol_ratio`.
- **Locked Parameters / Contract:** Single run volume ratio is hard-locked to $\le 0.32$ in standard profiles and $\le 0.25$ in `RESTRICTED_RUN` mode.
- **Enforcement Mechanism:** Exact linear inequality constraint inside `TriathlonMILPScheduler._build_milp_model()`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Empirical orthopedic injury data and stress test scenario `Scenario 1: Baseline Nominal` in `stress_test_optimizer.py`.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** Mathematically eliminates excessive long-run orthopedic spikes while scaling naturally with weekly volume.
- **Negative / Operational Cost:** Forces the optimizer to distribute running volume across 3-4 days rather than 1-2 days.
- **When to Revisit:** When preparing ultra-distance trail athletes where single runs intentionally exceed 35% of weekly volume.

### `ADR-004` — Decouple Aerobic TSS and Mechanical Strain with Asynchronous Tissue Remodeling [`ACTIVE` | `physiology`]

# `ADR-004`: Decouple Aerobic TSS and Mechanical Strain with Asynchronous Tissue Remodeling

## 1. Context & Problem
Standard Performance Management Charts (PMC) model athlete fitness using a single exponentially weighted moving average of TSS ($\tau=42\text{ days}$). In multi-sport triathlon training, cycling and swimming generate high cardiovascular TSS with zero eccentric mechanical impact, while running induces severe ground-reaction impacts ($2.5-3.0\times$ body weight). Single-load models cause overuse injuries when high aerobic fitness outpaces tendon and bone collagen synthesis.

## 2. Considered Alternatives
1. **Option A (Monolithic TSS):** Single metric for all sports. Rejected because 100 TSS of cycling has completely different injury kinetics than 100 TSS of downhill intervals.
2. **Option B (Sport-Specific Uncoupled PMCs):** Three separate PMCs for Swim, Bike, Run. Rejected because aerobic cardiovascular adaptations transfer cross-sport, whereas peripheral tissue strain does not.
3. **Option C (ADOPTED):** Dual-load convolution model separating Aerobic TSS from Mechanical Strain with differential remodeling rates ($\tau_m=35d$ vs $\tau_c=120d$) and tracking the Orthopedic Dissociation Index ($ODI \le 1.35$).

## 3. Decision & Production Invariant
- **Target Component / Path:** `closed_loop_optimizer.py:CandidateWorkout`, `athlete_profile.py`.
- **Locked Parameters / Contract:** Cardiovascular time constant $\tau_m = 35\text{ days}$; connective tissue constant $\tau_c = 120\text{ days}$ ($\tau_c = 210\text{ days}$ for Masters athletes); $ODI = \text{Metabolic Load} / \text{Connective Tissue Load} \le 1.35$.
- **Enforcement Mechanism:** Daily receding-horizon constraint evaluation inside `ClosedLoopMPCController`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Maunder & Seiler durability research and Friel triathlete injury epidemiology.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** Accurately detects and prevents "cardio-orthopedic mismatch" where high aerobic fitness destroys fragile connective tissue.
- **Negative / Operational Cost:** Candidate workouts must include both `aerobic_tss` and `mechanical_strain` attributes.
- **When to Revisit:** If wearable biomechanics sensors (e.g. Stryd impact shock or force plates) provide direct real-time tibial acceleration data.

### `ADR-005` — Implement Three-Tier Closed-Loop Model Predictive Control with Daily Readiness Scaling [`ACTIVE` | `mpc`]

# `ADR-005`: Implement Three-Tier Closed-Loop Model Predictive Control with Daily Readiness Scaling

## 1. Context & Problem
Static multi-month training plans fail rapidly in practice due to accumulated autonomic fatigue, illness, poor sleep, or life schedule disruptions. Static plans either force an exhausted athlete into high-injury workouts or fall apart completely when missed sessions throw off downstream periodization.

## 2. Considered Alternatives
1. **Option A (Static 16-Week Pre-Computed Schedule):** Generate the entire season once. Rejected because static plans cannot accommodate biological readiness fluctuations or life disruptions.
2. **Option B (Reactive Heuristic Rulebook):** Hardcoded conditional checks (e.g., if missed workout, push to tomorrow). Rejected because shifting workouts blindly cascades into consecutive hard days or volume spikes.
3. **Option C (ADOPTED):** 3-Tier Closed-Loop Model Predictive Control (MPC) with daily morning readiness scaling ($R_t \in [0.70, 1.10]$) and weekly receding-horizon MILP re-solving.

## 3. Decision & Production Invariant
- **Target Component / Path:** `closed_loop_optimizer.py:ClosedLoopMPCController`.
- **Locked Parameters / Contract:**
  - Tier 1: Daily Morning Readiness Scalar $R_t \in [0.70, 1.10]$ computed from HRV RMSSD, sleep, and soreness.
  - Tier 2: Weekly Receding-Horizon re-optimization using actual executed load history.
  - Tier 3: Mesocycle 3:1 step cycles with 15-25% loading reductions on recovery weeks.
- **Enforcement Mechanism:** `ClosedLoopMPCController.step_day()` and `replan_weekly_schedule()`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Control theory benchmarks demonstrating zero acute overload violations over 16-week simulation runs in `stress_test_optimizer.py`.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** Dynamically protects athlete from overtraining syndrome while preserving season macrocycle progression.
- **Negative / Operational Cost:** Requires daily biometric inputs or sensible defaults when wearables are unavailable.
- **When to Revisit:** When automated bidirectional syncing with Garmin/Whoop/Oura cloud APIs is integrated.

### `ADR-006` — Adopt 15-Seed Parallel HiGHS Race with Per-Worker Timeout Limits [`SUPERSEDED` | `perf`]

# `ADR-006`: Adopt 15-Seed Parallel HiGHS Race with Per-Worker Timeout Limits

## 1. Context & Problem
Branch-and-cut MILP solvers exhibit extreme performance variability depending on the initial random seed chosen for simplex perturbations and branching tie-breaking. A single thread with an unlucky seed can take 15-30+ seconds to close the MIP gap on constrained combinatorial problems, whereas a favorable seed discovers an optimal basis in 0.3 seconds.

## 2. Considered Alternatives
1. **Option A (Single-Seed HiGHS Solver):** Standard execution with default seed 0. Rejected due to long tail latency when branching heuristics stall.
2. **Option B (Deterministic Multi-Threading):** Running 1 worker with 32 internal threads. Rejected because internal tree parallelism suffers from lock contention and cannot explore radically different root heuristics concurrently.
3. **Option C (ADOPTED):** Parallel portfolio race spawning 15 independent workers with randomized seeds and 2 threads each, taking the first solutions to prove optimality or lowest MIP gap within 4.0 seconds.

## 3. Decision & Production Invariant
- **Target Component / Path:** `highs_optimization.py:optimize_weekly_workouts_multiseed()`.
- **Locked Parameters / Contract:** Worker count = 15; threads per worker = 2 (30 total cores); per-worker time limit = 4.0s; candidate pruning active.
- **Enforcement Mechanism:** Subprocess pool isolation with explicit join timeouts.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-001` benchmarks demonstrating 95% of runs finding global optimum in < 1.2s total wall-clock time.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** Reduces p99 scheduling latency from 25s down to < 2.0s; leverages high-core CPUs (e.g. AMD Ryzen 5950X).
- **Negative / Operational Cost:** Higher instantaneous CPU utilization across multiple cores for 1-2 seconds.
- **When to Revisit:** When deploying on low-core serverless environments where worker pool size must be scaled down to 2-4 workers.

### `ADR-007` — Modularize Repository Layout, Data Assets, and Reference Artifacts [`ACTIVE` | `general`]

# `ADR-007`: Modularize Repository Layout, Data Assets, and Reference Artifacts

## 1. Context & Problem
Early prototyping led to significant root-directory file sprawl:
1. All Python source modules (`classes.py`, `closed_loop_optimizer.py`, `highs_optimization.py`, etc.) were flat in the root directory.
2. Raw workout dataset files (`Bike.xlsx`, `Run.xlsx`, `Swim.xlsx`, `parsed_workouts_preview.json`, `workout_library.html`) and raw FIT download caches (`fit_files/`) resided in the project root.
3. Environment files (`.env.example`, `trainingpeak.env`, `.garmin_cookie.env`) sat in the root directory.
4. Agent operating charters (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`) cluttered the root.
5. Initial bootstrapping generated a 120 KB duplicate scaffolding kit (`lode_starter_kit/`) containing duplicate templates and bash scripts that became redundant after Lode initialization.
6. Rapid experimental iteration left 35 ad-hoc diagnostic and exploratory scripts in `./scratch/` and an untracked `all_known_codes.txt` in the root.
7. Heavy reference literature (over 300 MB of PDFs and EPUBs in `Books/`) was unignored and at risk of accidental git staging.

This cluttered the top-level directory, obscured primary system modules, and broke clear separation of concerns between code, static data, reference literature, configurations, and operational scripts.

## 2. Considered Alternatives
1. **Option A (Rejected - Flat Root):** Keep all files flat in the project root. Rejected because it impairs discoverability, creates confusion about what is active code versus transient data/debug artifacts, and risks committing massive binary blobs or secrets.
2. **Option B (ADOPTED):** Modularize into clean top-level directories:
   - `src/`: Standard Python package containing all core modules (`classes.py`, `closed_loop_optimizer.py`, `highs_optimization.py`, `athlete_profile.py`, `main.py`, `stress_test_optimizer.py`, `clean_file.py`, `sync_8020_to_tp.py`, `util.py`, `__init__.py`) managed via root `pyproject.toml`.
   - `config/`: Environment configuration (`.env.example`, `trainingpeak.env`, `garmin.env`), keeping all credential files out of root and strictly gitignored.
   - `.agents/`: Agent operating charters and rules (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `rules/operating_charter.md`).
   - `data/`: Centralized workout spreadsheets (`Bike.xlsx`, `Run.xlsx`, `Swim.xlsx`, `brick_workouts.csv`), scrape caches, JSON payloads, and FIT downloads.
   - `references/`: Empirical endurance literature and textbooks (gitignored for large binaries).
   - `scripts/`: Operational tools, SQLite lineage engine (`lodectx.py`), benchmarks, and plan comparators.
   - `training_plans/`: Generated season CSV schedules and race markdown specifications.
   - Root: Minimalist entry points (`pyproject.toml`, `requirements.txt`, `ReadMe.md`, `.gitignore`).

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/classes.py:_resolve_data_path()`, `src/clean_file.py`, `src/sync_8020_to_tp.py`, `src/util.py`, `config/`, `.agents/`, `.gitignore`, `pyproject.toml`.
- **Locked Parameters / Contract:**
  1. All workout dataset ingestion MUST check `data/<file>` first, falling back to root for backwards compatibility.
  2. All Python library modules MUST reside in `src/` with `src/__init__.py` and root `pyproject.toml`.
  3. Environment credentials MUST reside in `config/` (`config/.env`, `config/trainingpeak.env`, `config/garmin.env`) and NEVER in the root directory.
  4. Agent instructions MUST reside in `.agents/` and `.agents/rules/*.md`.
  5. Temporary/scratch scripts MUST NOT be committed to the root or tracked in git; permanent benchmarks must live in `scripts/` with `if __name__ == '__main__':` execution guards.
  6. External reference literature MUST reside in `references/` and large binaries (`*.pdf`, `*.epub`, `*.azw3`) MUST remain gitignored.
  7. Redundant initialization scaffolding (`lode_starter_kit/`) and obsolete ad-hoc files (`all_known_codes.txt`, throwaway diagnostic dumps) MUST be pruned.
- **Enforcement Mechanism:** Verified by `src/stress_test_optimizer.py` automated suite and deterministic import checks.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Execution of 10-scenario `src/stress_test_optimizer.py` suite; all 10 scenarios solved with `OPTIMAL` status with zero regressions under `src/` and `data/` layout.
- **Supersedes:** None (New architectural hygiene baseline).

## 5. Consequences & Trade-Offs
- **Positive:**
  - Immaculate root directory with zero file clutter.
  - Standard `src/` layout compatible with `pip install -e .` and modern tooling.
  - Zero risk of committing 300+ MB book binaries or credentials to git.
  - Backward-compatible path resolution across all modules and scripts.
- **Negative / Operational Cost:** Execution commands must use `python src/main.py` or `python src/stress_test_optimizer.py` (or `-m src.main`).
- **When to Revisit:** If the platform is packaged as a namespace package or published to PyPI.

### `ADR-008` — Adopt Dual-Objective Hybrid Athlete Optimization with Equipment Partitioning and Biomechanical Conflict Matrix [`ACTIVE` | `optimizer`]

# `ADR-008`: Adopt Dual-Objective Hybrid Athlete Optimization with Equipment Partitioning and Biomechanical Conflict Matrix

## 1. Context & Problem
Classical triathlon training schedules treat strength training as an auxiliary or unmodeled afterthought. When athletes concurrently pursue endurance goals (Swim, Bike, Run) and maximal barbell/dumbbell strength (Squat, Bench, Deadlift, Overhead Press), naive scheduling creates severe physiological and orthopedic failure modes:
1. **Lumbar Shear Catastrophe:** Heavy deadlifts produce up to $18.45\text{ kN}$ compressive and $3.28\text{ kN}$ shear load on L4/L5. Stacking or placing a heavy deadlift within 24 hours of high-impact threshold or long running causes lumbar erector failure and acute disc injury.
2. **Molecular Signaling Interference (AMPK vs. mTORC1):** Endurance training activates AMPK (inhibiting protein synthesis), whereas resistance training activates mTORC1. Uncoordinated same-day scheduling without a $\ge 6\text{h}$ intra-day separation blunts hypertrophy and strength gains.
3. **Equipment Asymmetry:** Athletes often possess an Olympic barbell at a commercial facility and a home gym capped at $75\text{ lb}$ dumbbells. Failing to partition movements forces inappropriate loading paradigms (e.g., trying to execute heavy bilateral leg training on light dumbbells).
4. **Split Incoherence:** Unconstrained optimization blends incompatible split archetypes (e.g. mixing Texas Method full-body workouts with Upper/Lower split workouts in the same week).

## 2. Considered Alternatives
1. **Option A (Rejected — Sequential Heuristic Post-Pass):** Solve the endurance schedule first, then greedily insert resistance workouts into rest days.
   - *Reason Rejected:* Violates the "Hard-Days-Hard" principle by turning easy endurance days into high-strain lifting days, causing chronic autonomic nervous system (ANS) non-functional overreaching. Furthermore, it cannot enforce joint weekly axial load ceilings ($X_{\text{wk}} \le 320$) across endurance and lifting.
2. **Option B (Rejected — Non-Linear / NLP Modeling):** Model continuous fatigue interaction decay curves via non-linear solvers (e.g. Ipopt / SciPy `minimize`).
   - *Reason Rejected:* Non-convex optimization suffers from local minima, slow convergence ($> 30\text{s}$), and inability to enforce discrete binary workout assignment invariants with mathematical guarantees.
3. **Option C (ADOPTED — HiGHS MILP with Exact Linearizations & Split Cuts):** Formulate the problem as a deterministic Mixed-Integer Linear Program in HiGHS (`HybridAthleteMILPOptimizer`), using exact linear ratio transformations, binary split coherence cuts, Canonical No-Good Integer Cuts, and hard biomechanical conflict matrices.

## 3. Decision & Production Invariant
The primary hybrid optimizer is standardized in `src/closed_loop_optimizer.py` as `HybridAthleteMILPOptimizer`:
- **Equipment Partitioning:** Enforce $\ge 60\%$ weekly strength set volume on Olympic barbell compound movements via exact linear ratio constraint:
  $$\sum_{d=1}^7 \sum_{i \in \mathcal{W}_{\text{STR}}} \left((1 - \beta_{\text{BB}}) V_i^{\text{BB}} - \beta_{\text{BB}} V_i^{\text{DB}}\right) x_{i,d} \ge 0 \quad (\beta_{\text{BB}} = 0.60)$$
  Dumbbells capped at $75\text{ lb}$ are strictly allocated to unilateral assistance and accessory hypertrophy.
- **Biomechanical Conflict Prevention Matrix:**
  1. *Deadlift vs. Heavy/Long Run:* Absolute same-day exclusion ($x_{i,d} + x_{j,d} \le 1$) and mandatory adjacent-day lockout ($x_{i,d} + x_{j,d+1} \le 1$ and $x_{i,d+1} + x_{j,d} \le 1$). Stacking with swimming is preferred for hydrostatic spinal decompression.
  2. *Squat vs. High-Torque Bike / Threshold Run:* Absolute same-day exclusion ($x_{i,d} + x_{j,d} \le 1$).
  3. *Heavy Press vs. CSS Threshold Swim:* Absolute same-day exclusion ($x_{i,d} + x_{j,d} \le 1$) to prevent rotator cuff and subacromial impingement.
  4. *Strength Frequency & Rest:* Maximum 2 workouts/day, non-consecutive strength days ($\sum_{i \in \mathcal{S}} x_{i,d} + \sum_{i \in \mathcal{S}} x_{i,d+1} \le 1$).
- **Axial Spinal & Neuromuscular Ceilings:**
  - Daily axial load $X_d \le 120.0$; weekly axial load $\sum_d X_d \le 320.0$.
  - Daily neuromuscular load $N_d \le 130.0$; weekly neuromuscular load $\sum_d N_d \le 360.0$.
- **Split Archetype Coherence & Canonical No-Good Cuts:** Binary variable $z_{\text{split}} \in \{0, 1\}$ strictly selects between Option A (Full-Body HLM / Texas Method) and Option B (Upper/Lower Split). The solver yields secondary distinct Pareto solutions via Canonical No-Good cuts:
  $$\sum_{(i,d) \in S_A} x_{i,d} - \sum_{(i,d) \notin S_A} x_{i,d} \le |S_A| - 1$$
- **Polarized Time-in-Zone Invariant:** Exact linear constraints enforce $\ge 78\%$ weekly aerobic duration in $Z_1/Z_2$ and $\le 20\%$ in $Z_4/Z_5$.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `src/stress_test_optimizer.py` Scenario 11 ("Dual-Objective Hybrid Athlete Optimization"). Option A (Full-Body HLM) solves in $0.182\text{s}$ with objective value $-40.70$. Option B (Upper/Lower) solves in $0.155\text{s}$. Both solutions achieve $100\%$ compliance across all 4 movement patterns ($\ge 10$ sets), $0$ axial or neuromuscular violations, and strict 24h deadlift-run spacing.
- **Supersedes:** Extends and formalizes single-discipline optimizer constraints previously specified in `ADR-002` and `ADR-003`.

## 5. Consequences & Trade-Offs
- **Positive:** Mathematically proves absence of overtraining and conflicting muscular recruitment; enables athletes to concurrently pursue Ironman/triathlon endurance and 1,000-lb barbell club strength safely.
- **Negative / Operational Cost:** Requires candidate workouts to carry axial, neuromuscular, and movement pattern metadata; un-tagged custom workouts must be enriched via `enrich_workouts_from_df()`.
- **When to Revisit:** Revisit if the athlete acquires heavier home dumbbells ($>100\text{ lb}$) allowing bilateral home compound loading, or if integrating continuous velocity-based training (VBT) sensor feedback into Tier 2 MPC.

### `ADR-009` — Adopt Canonical 80/20 7-Zone Power System and Two-Point Critical Power Protocol [`ACTIVE` | `physiology`]

# `ADR-009`: Adopt Canonical 80/20 7-Zone Power System and Two-Point Critical Power Protocol

## 1. Context & Problem
Because the athlete engages in concurrent heavy barbell strength training (squats and deadlifts), their anaerobic work capacity ($W'$) is substantially elevated relative to pure endurance specialists. Standard single-effort aerobic proxy tests with fixed multipliers (such as the cycling ramp test at $0.75 \times \text{MAP}$ or un-cleared 20-minute time trials at $0.95 \times P_{20}$) draw heavily upon this enlarged anaerobic battery. Consequently, single-effort tests systematically overestimate sustainable aerobic threshold ($LT_2$) and Maximum Lactate Steady State ($MLSS$), leading to severe overtraining, premature glycogen depletion, and elevated orthopedic strain.

Furthermore, commercial 5-zone systems (such as Stryd 5-Zone or MyProCoach 5-Zone) define Zone 2 as spanning 80%–90% of CP. This improperly extends above the true first lactate threshold ($LT_1$), prescribing training in the moderate-intensity "grey zone" and defeating the polar/pyramidal 80/20 recovery architecture.

## 2. Considered Alternatives
1. **Option A (Fixed-Multiplier Ramp or 20-min TT):** Relying on $0.75 \times \text{MAP}$ or $0.95 \times P_{20}$. Rejected because the hybrid athlete's high anaerobic capacity ($W'$) inflates short-duration output, producing non-sustainable threshold targets.
2. **Option B (Commercial 5-Zone Scale - Stryd / MyProCoach):** Standard 5-zone power partitioning. Rejected because their Zone 2 extends past $LT_1$ into Zone X (80%–90% CP), causing chronic autonomic fatigue and blunting base volume.
3. **Option C (ADOPTED):** Calibrate Critical Power using the Two-Point Linear Work-Time Model ($P_3$ 180s + $P_{12}$ 720s) or Kolie Moore 35–45m MLSS baseline, and enforce the canonical **80/20 7-Zone Scale** with explicit penalization of Zone X (moderate drift) and Zone Y (threshold gap).

## 3. Decision & Production Invariant
- **Target Component / Path:** `SPEC.md`, `docs/domains/physiology.md`, `docs/domains/workouts.md`, `src/athlete_profile.py`, `src/classes.py`.
- **Locked Parameters / Contract:**
  1. **Two-Point CP Math:**
     $$CP = \frac{4 P_{12} - P_3}{3}, \quad W' = 180 \cdot (P_3 - CP)$$
  2. **80/20 7-Zone System:**
     - Zone 1 (Active Recovery): Run 50%–76% CP, Bike 50%–70% CP (ANS weight 0.0)
     - Zone 2 (Aerobic Endurance, Ceiling = $LT_1$): Run 76%–88% CP, Bike 70%–83% CP (ANS weight 0.0)
     - Zone X (Moderate Drift Grey Zone): Run 88%–94% CP, Bike 83%–91% CP (ANS weight 0.7 penalty)
     - Zone 3 (Sub-Threshold / Tempo, Ceiling = $LT_2$): Run 94%–100% CP, Bike 91%–100% CP (ANS weight 1.0)
     - Zone Y (Threshold Gap): Run 100%–103% CP, Bike 100%–102% CP (ANS weight 1.4 penalty)
     - Zone 4 (Supra-Threshold / $\text{VO}_2\text{max}$): Run 103%–120% CP, Bike 102%–110% CP (ANS weight 1.8)
     - Zone 5 (Anaerobic / Repetition): Run >120% CP, Bike >110% CP (ANS weight 3.5)
  3. **Positional Aero Rule:** Indoor/Aero CP must be tested in triathlon aero position; upright road hoods power is 3%–7% (10–20W) higher and must not be mixed without scaling.
  4. **Single Source of Truth:** Disable `Auto-CP` in Stryd and `Auto-FTP` in Garmin; lock manual CP integer identically across Stryd, TrainingPeaks, and optimizer profiles.
- **Enforcement Mechanism:** System invariants in `docs/INVARIANTS.md`, athlete profile validations, and workout parser zone mapping.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Monod & Scherrer two-parameter work-time linear formulation; empirical Kolie Moore MLSS baselining; 80/20 Endurance physiological demarcation (Fitzgerald & Warden).
- **Supersedes:** None. Establishes the authoritative multi-sport power and zone calibration baseline.

## 5. Consequences & Trade-Offs
- **Positive:** Eliminates anaerobic bias from threshold calculation; guarantees low-intensity training stays strictly below $LT_1$; penalizes accidental grey-zone drift.
- **Negative / Operational Cost:** Testing requires two all-out efforts separated by 30 minutes (or a dedicated 35–45m MLSS test), and manual synchronization across three platforms.
- **When to Revisit:** If athlete ceases heavy compound barbell lifting or switches to laboratory gas-exchange / blood lactate testing.

### `ADR-010` — Adopt Multi-Device Hardware Telemetry Rules and FIT Ingestion Invariants [`ACTIVE` | `integrations`]

# `ADR-010`: Adopt Multi-Device Hardware Telemetry Rules and FIT Ingestion Invariants

## 1. Context & Problem
The athlete's telemetry pipeline spans five distinct hardware devices:
1. Stryd "Wind" Foot Pod (Model 17, 2019)
2. Garmin Enduro 3 Watch
3. Garmin Swim 2 Watch
4. Garmin Edge 530 Bike Computer
5. Tacx Neo 2T Smart Trainer

Without rigorous hardware-specific ingestion rules, catastrophic data corruption occurs:
- Garmin watches write native wrist running power to standard `.fit` `power` (inflated +20% to +30%), while Stryd power is stored in Connect IQ developer fields.
- Pool swim duration parsed from `session.total_elapsed_time` includes rest intervals at the wall, inflating active swim time and distorting sTSS.
- Concurrent recording on bike computer and watch produces duplicate FIT uploads for the same ride, artificially doubling weekly TSS.
- Tacx Neo 2T inductive sensor `left_right_balance` stream exhibits violent spurious spikes, falsely indicating orthopedic asymmetry.

## 2. Considered Alternatives
1. **Option A (Generic FIT Field Ingestion):** Parse standard record fields (`power`, `total_elapsed_time`) across all devices indiscriminately. Rejected due to Garmin native power contamination and wall-rest time inflation.
2. **Option B (Manual Device Tagging):** Require the athlete to manually tag and reformat each FIT file before ingestion. Rejected as error-prone and unscalable.
3. **Option C (ADOPTED):** Hardware-aware ingestion harness enforcing explicit Developer Field extraction, active length parsing for pool swims, 300-second multi-device timestamp deduplication, and sensor-specific exclusions.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/clean_file.py`, `src/sync_8020_to_tp.py`, `SPEC.md`, `docs/domains/integrations.md`.
- **Locked Parameters / Contract:**
  1. **Stryd Model 17 Extraction:** Read power strictly from Connect IQ developer field (`field.is_dev == True` and `field.name == 'Power'`). Ingest auxiliary fields `Form Power`, `Leg Spring Stiffness` (LSS, kN/m), and `Air Power`. Account for 0W `Air Power` on treadmill. Pair to Enduro 3 strictly via ANT+ (`FP-XXXXX`).
  2. **Garmin Enduro 3 & Swim 2 Ingestion:** In pool swims, active duration and sTSS must be computed strictly by summing `length` records where `length_type == 'active'`. Drills (`swim_stroke == 'drill'`) are excluded from SWOLF/stroke rate. Enduro 3 is the primary Unified Training Status hub.
  3. **Garmin Edge 530 & Tacx Neo 2T:** 1-second recording with zeros included. Overlapping rides between Edge 530 and Enduro 3 within $\le 300\text{s}$ are deduplicated to exactly one file. Neo 2T optical brake requires zero spin-down calibration; execute CP tests in Slope/Incline mode (+1.5% to +2.0%); ignore `left_right_balance` stream; keep connected to AC power during recovery.
- **Enforcement Mechanism:** *Planned* `src/fit_ingest.py` (`TASK-024`, validated by `EXP-005`). **Implementation status (2026-11-15 truth pass, `TASK-009`): not implemented** — `src/clean_file.py` is the 80/20 HTML workout-library scraper and does not parse recorded activity `.fit` files. Since `ADR-012`, Swim 2 is retired and devices do not dual-record; the 300s dedup rule applies to historical files.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Comparative `.fit` stream analysis between Garmin wrist power and Stryd Connect IQ developer field; empirical pool swim lap vs length analysis; Tacx Neo 2T inductive balance telemetry verification.
- **Supersedes:** None. Establishes the authoritative multi-device hardware ingestion architecture.

## 5. Consequences & Trade-Offs
- **Positive:** Guarantees 100% clean, uncontaminated power and biomechanical metrics across all 5 devices; prevents artificial double-counting of load.
- **Negative / Operational Cost:** Parser must handle variable developer field schemas and implement spatial-temporal deduplication windows.
- **When to Revisit:** If athlete replaces Stryd Model 17 with Next-Gen pod or upgrades bike computer / trainer hardware.

### `ADR-011` — Integrate Closed-Loop Critical Power Retesting Engine with Dual Telemetry Triggers and Mesocycle Synchronization [`ACTIVE` | `physiology`]

# `ADR-011`: Integrate Closed-Loop Critical Power Retesting Engine with Dual Telemetry Triggers and Mesocycle Synchronization

## 1. Context & Problem
Athletes often intuitively push to retest threshold power too frequently or artificially force their Critical Power (CP) upward under the misconception that elevating CP is required to "force" physiological development. In polarized/pyramidal 80/20 training, inflating CP shifts prescribed Zone 2 workouts into the moderate-intensity grey zone (Zone X / Zone 3, above $LT_1$), inducing chronic autonomic suppression, glycogen depletion, and elevated orthopedic strain.

Conversely, static CP assignments lead to undertraining over multi-month seasons as cardiovascular stroke volume and peripheral capillarization improve. A deterministic closed-loop mechanism is required to govern retesting cadence: balancing periodic mesocycle check-ins against data-driven physiological triggers (cardiovascular decoupling suppression and Efficiency Factor gains) while strictly forbidding high-intensity testing during deconditioned re-entry phases.

## 2. Considered Alternatives
1. **Option A (Fixed Arbitrary Testing Schedule, e.g. Every 4 Weeks):** Imposing maximal 3m/12m testing every mesocycle. Rejected because frequent maximal tests exact high autonomic/mechanical toll, disrupt training continuity, and risk tissue injury during build cycles.
2. **Option B (Unrestricted Telemetry Auto-FTP Jumps):** Automatically incrementing CP on every ride/run breakthrough (e.g. Garmin Auto-FTP). Rejected because single high-motivation bursts or un-cleared spikes contaminate baseline zones with anaerobic work ($W'$), violating `ADR-009`.
3. **Option C (ADOPTED):** Dual-Trigger Retesting Engine:
   - **Re-Entry Protection Guard:** Absolute lockout of all-out testing during initial re-entry ($\le 4\text{ weeks}$ bike, $\le 6\text{ weeks}$ run).
   - **Calendar Mesocycle Cadence:** Synchronized retesting every 8 to 12 weeks during recovery deload weeks.
   - **Data-Driven Telemetry Trigger:** Trigger testing or $+3\text{–}5\%$ calibration prompts when rolling aerobic decoupling drops below $3.0\%$ and 28-day Efficiency Factor ($EF$) trends $\ge +5.0\%$.
   - **Runaway Ceiling Guard:** Hard clamp capping single-test CP step increases at $\le 10\%$ to prevent injury and overreaching.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/athlete_profile.py`, `src/closed_loop_optimizer.py`, `docs/domains/physiology.md`.
- **Locked Parameters / Contract:**
  1. **Candidate Test Library:** Expose canonical benchmark test workouts (`BK_CP_TEST`, `RN_CP_TEST`, `SW_CSS_TEST`) adhering to the Monod-Scherrer Two-Point Work-Time protocol ($P_3 + P_{12}$).
  2. **Re-Entry Lockout:** `ClosedLoopMPCController.evaluate_cp_retest_triggers` locks testing during early re-entry.
  3. **Telemetry Adaptation Criteria:** Trigger flag requires at least two concordant signals:
     - Rolling aerobic decoupling ($Pw:HR$ or $Pa:HR$) $< 3.0\%$ across steady Zone 2 sessions $\ge 60\text{ min}$.
     - Rolling 28-day Efficiency Factor trend $\ge +5.0\%$.
     - $\ge 3$ consecutive breakthrough interval sessions completed with low RPE / suppressed HR.
  4. **Max Jump Cap:** Single retest step update capped at $\le 10.0\%$ via `apply_cp_retest_result`.
- **Enforcement Mechanism:** Unit tests in `test_athlete_profile.py` / `stress_test_optimizer.py` and MPC Tier 2 recalibration checks.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Monod-Scherrer work-time linearity; Seiler Polarized Training and aerobic decoupling ($Pa:HR < 5\%$ ceiling); Coggan Efficiency Factor formulations.
- **Supersedes:** None. Complements and operationalizes `ADR-009`.

## 5. Consequences & Trade-Offs
- **Positive:** Eliminates premature testing injuries; dynamically accommodates fitness gains without manual guesswork; prevents grey-zone overtraining caused by vanity threshold spikes.
- **Negative / Operational Cost:** Requires consistent heart rate and power telemetry streams to compute decoupling and Efficiency Factor metrics.
- **When to Revisit:** If athlete transitions to continuous blood-lactate biosensors or direct VO2 metabolic cart testing.

### `ADR-012` — Lock Athlete Decision Record for Nov 2026 Re-Entry and Olympic Season [`ACTIVE` | `mpc`]

# `ADR-012`: Lock Athlete Decision Record for Nov 2026 Re-Entry and Olympic Season

## 1. Context & Problem
`docs/TASKS.md` held 20 blocking or shaping athlete decisions (`DECISION-001..020`). Without them, the optimizer ran on conflicting or placeholder inputs: three different swim CSS values, a 16-week Olympic placeholder, pool access on conflicting days, strength zeroed in recovery weeks, and CP values of unknown age. On 2026-11-15 the athlete answered all 20. Where they asked for a recommendation (001, 006, 012–014, 016, 017, 019), this ADR records the recommendation that was adopted.

## 2. Considered Alternatives
1. **Option A (Rejected):** Leave decisions inline in `TASKS.md`. Rejected because they are durable athlete facts, not engineering tasks, and they bloat the startup core.
2. **Option B (Rejected):** Hard-code the answers in the solver. Rejected because it breaks the single source of truth (`ADR-009`) and the answers will change in about 6 months.
3. **Option C (ADOPTED):** Record the answers here and encode them as typed fields in `src/athlete_profile.py`. The fields that the unified engine (`TASK-015..019`) must still read are listed in §3.

## 3. Decision & Production Invariant
**Target Component / Path:** `src/athlete_profile.py` (`AthleteProfile` / `CURRENT_ATHLETE`).

| ID | Decision (2026-11-15) | Profile encoding |
|---|---|---|
| 001 | No swimming since 2024-08. 2024 pool moving pace was ~2:18–2:27/100m, which puts CSS at ~2:12 then. Adding ~10 s/100m for more than 2 years off gives a **provisional CSS of 2:30/100m (40.0 m/min)**. This replaces 24.0 and 35.29 m/min. Do a 400/200 CSS test on the 2nd weekend after pool access returns. Default pool: 25 yd. | `swim_css=40.0`, `swim_pool_length="25y"` |
| 002 | **Olympic** is the next race. No date yet. Recommended: late Aug 2027, so the 16-week race block starts when pool access returns (~May 2027). The optimizer must also be validated on sprint, 70.3 and IM (`TASK-027`). | `target_race_date=None` |
| 003 | Confirmed no runs or rides for more than 90 days (last run 2026-07-13, last ride 2026-06-20). **No injury history**, so no orthopedic hard caps. | `orthopedic_status="HEALTHY"` |
| 004 | The 45–75 min/day budget is a **real ceiling for at least 6 months** (childcare), reviewed 2027-05-15. No AM/PM split days assumed, so the limit is 1 session/day. | `two_session_windows=[False]*7`, `weekday_single_workout_cap=60` |
| 005 | CP values are months old. Bike CP was tested **in aero** on the Neo 2T. **Provisional bike 135 W, run 200 W** (~10% / ~7% detraining discount; 2025 best 20-min power was 156 W). First tests follow `ADR-011` lockouts: bike Two-Point CP at the start of week 5, run CP at the week-8 deload. Until then, run easy runs by HR and the talk test. | `bike_cp=135`, `run_cp=200`, `thresholds_verified=False` |
| 006 | Safety first: Tier-0 caps are never relaxed. Sport priority: **Strength > Bike > Run > Swim**. Tier-1 relaxation order, first to last: weekly TSS → strides → Swim anchor/frequency → Run anchor/frequency → Bike anchor/frequency → rest days (never below 1) → heavy strength count (never below 1). | `sport_priority`, `tier1_relaxation_order` |
| 007 | **No pool for ~6 months.** After that, weekends only (Sat/Sun). | `facility_access["Swim"]` all False, `pool_open_days=[]` |
| 008 | Split chosen by the solver (`AUTO`). Rack available every day. Plates 2.5–45 lb (bar load up to ~400 lb, 5 lb jumps). Dumbbells 5–75 lb in 5 lb steps. **Squat and deadlift may share a session.** No trap bar. | `facility_access["Strength"]` all True, `barbell_*`, `dumbbell_increment_lbs`, `has_trap_bar=False` |
| 009 | Strength is **co-equal** with triathlon. Recovery, taper and race weeks keep **1 maintenance (non-heavy) session** instead of 0. | `strength_priority="COEQUAL"`, `recovery_week_strength_sessions=1` |
| 010 | **3:1** mesocycles (3 build weeks + 1 recovery week). | `mesocycle_build_weeks=3`, `mesocycle_recovery_weeks=1` |
| 011 | No fixed calendar commitments. | – |
| 012 | All rides on the trainer (Neo 2T). All runs outdoors by default. Treadmill is recommended on ice, in extreme cold or dark, and optionally for walk-run re-entry or intervals. On a treadmill, Stryd Model 17 `Air Power`=0, so power reads ~2–4% lower at the same speed. Steer by power, not belt speed, and tag `sub_sport=treadmill`. | `outdoor_bike_days=[]`, `default_run_surface="outdoor"` |
| 013 | **Canonical 80/20** (Fitzgerald, *80/20 Triathlon*; Seiler): measured as time-in-zone over endurance minutes only (strength excluded). Z1+Z2 ≥ 78% hard floor, 80% soft target (85–90% in re-entry/base). **Z3+ZY+Z4+Z5 ≤ 20% combined** (this corrects the current Z4+Z5-only rule). Zone X ≤ 5%, penalized toward 0. 2–3 key sessions/week (0–1 in the first 4 re-entry weeks). No "medium" grey-zone sessions. | `intensity_*`, `key_sessions_per_week` |
| 014 | Running ACWR ceiling **1.20 during re-entry** (run CTL < 15), then **1.25**. Re-entry also uses absolute minutes, because the ratio is unstable at near-zero chronic load: 60 min/week of run/walk is always allowed, and weekly run minutes rise by at most 15. | `max_running_acwr*`, `reentry_*`, `max_weekly_run_minutes_increase` |
| 015 | Convert mile-based long runs at **easy pace 14:30/mi** (2026 median was 15:08/mi at HR 143). Update this as fitness returns. | `easy_run_pace_min_per_mile=14.5` |
| 016 | The 14 custom library rows and `recommended_workout_legos.csv` were written by agents, not taken from the 80/20 library. Treat them as **provisional**. Replace Trap Bar DL with conventional DL. Pull-up bar, bands and a Nordic anchor still need confirming. | `TASK-028` |
| 017 | Raw completed activity FIT files already exist: `data/garmin/activities/fit_files/` (2,020 files, 2013–2026). `data/fit_files/` holds 80/20 workout *definitions*. No athlete action needed. | `TASK-024` input |
| 018 | One device per sport, no dual-recording: Edge 530 for bike; Enduro 3 for run, swim and strength. Swim 2 is retired. The 300 s dedup rule stays for historical data. | `recording_device` |
| 019 | **Garmin Connect** is authoritative for completed activities (raw FIT with Stryd developer fields). TrainingPeaks holds the *plan* only. | `completed_activity_source`, `planned_workout_target` |
| 020 | TrainingPeaks push mode: **dry run**. | `trainingpeaks_push_mode="dry_run"` |
| 021 | **Lift + easy cardio combo:** a strength session may be followed right away, in the same window, by ≤20 min of strictly Z1/Z2 Bike or Run. Lift comes first. After heavy lower-body lifting, Bike only (no impact on a loaded spine, per `HEUR-004`). The pair counts as one window toward the day's cap. This is an exception to the 6h AMPK/mTOR rule (`INVARIANTS.md`). | `allow_lift_cardio_combo`, `combo_cardio_*` |
| 022 | Equipment: pull-up bar ✔, treadmill ✔, Nordic anchor ✔, resistance bands ✘. Banded rows become DB rear-delt fly or prone Y-T-W. | `has_pullup_bar`, `has_treadmill`, `has_nordic_anchor`, `has_resistance_bands=False` |
| 023 | **Re-entry heavy lifting:** keep the conservative daily axial 65 / neuro 75 caps. During re-entry, **0 heavy lifts/week** are allowed, overriding the DECISION-006 floor of 1. Maintenance and accessory lifting continues. Flip `reentry_phase` off to restore phase defaults. | `reentry_phase=True`, `reentry_heavy_strength_sessions=0` |

**Enforcement Mechanism:** These profile fields are **not yet read by the solver** and must be wired in by the unified engine work: the 80/20 definition (013), ACWR caps and minute floors (014), relaxation order (006), the 3:1 season matrix (010) and two-window gating (004). Tracked in `TASK-016`, `TASK-018`, `TASK-019` and `TASK-026`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Garmin history in `data/garmin/activities/activities_summary.json` (2,020 activities): swim paces 2023–24, max 20-min bike power by year, run pace and HR by year. Athlete answers dated 2026-11-15.
- **Supersedes:** None. Operationalizes `ADR-005` (3:1), `ADR-009`/`ADR-011` (CP protocol, retest lockouts) and `ADR-010` (devices).

## 5. Consequences & Trade-Offs
- **Positive:** One coherent athlete model. Provisional thresholds are deliberately conservative, keeping Z2 below LT1.
- **Negative / Operational Cost:** With 1 window/day and ≥1 rest day there are 6 windows/week. Strength 3× + Bike 2× + Run 2× fits only through the `DECISION-021` lift + easy cardio combo, which the engine must model as a compound candidate (`TASK-019`).
- **When to Revisit:** 2027-05-15 (pool access, time budget), after the first CP or CSS tests, or when a race date is set.

### `ADR-013` — Enforce Three-Tier Lexicographic Constraint Hierarchy with Shared Tier-0 Safety Layer [`ACTIVE` | `optimizer`]

# `ADR-013`: Enforce Three-Tier Lexicographic Constraint Hierarchy with Shared Tier-0 Safety Layer

## 1. Context & Problem
The TASK-008 tests showed safety rules being broken while the solver still reported success:
- **Soft safety caps.** `TriathlonMILPScheduler` gave the 32% single-run cap and the rolling 48h TSS/ANS/strain caps penalty slacks (`enable_soft_48h_slacks`). The solver paid the penalty and scheduled a 33.3% run.
- **Silent rewrites.** It also silently relaxed `rho` when there were fewer than 4 runs, rewrote frequency minimums it could not meet (and wrote the change into the caller's config), and de-anchored oversized anchors.
- **Missing rules in the hybrid engine.** `HybridAthleteMILPOptimizer` had no hard/easy adjacency, no 48h caps, no weekly neuromuscular cap, no frequency bounds, and no deadlift lockout against long runs. It applied the run cap only to `long_run` workouts. Its 610-TSS demo plans exceeded the 360 weekly neuro cap and put a deadlift the day before a long run.
- **Fallback.** When a week was infeasible, the season planner replaced it with a generic recovery config that ignored the athlete profile.

## 2. Considered Alternatives
1. **Option A (Rejected):** Patch each engine separately. The two engines would drift apart again, and they are due to merge (TASK-015).
2. **Option B (Rejected):** A single weighted objective mixing Tier-1 slacks (weight 1000) with Tier-2 goals. The 1% relative MIP gap scales with that weight, so Tier-2 quality degrades whenever a slack is active. Large weights also let large TSS deviations trade against session minimums.
3. **Option C (ADOPTED):** A shared Tier-0 builder, named integer Tier-1 slacks weighted lexicographically, and a two-phase solve.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/safety_constraints.py`, used by both engines in `src/closed_loop_optimizer.py`. The season planner no longer has a fallback.
- **Locked Parameters / Contract:**
  1. **Tier 0 (hard, never relaxed):** the following hold in every engine.
     - Day caps and facility/modality gating (pre-filtered); weekday single-session cap.
     - Rolling 48h TSS ≤ 295, ANS ≤ 700, mechanical strain ≤ 260.
     - Daily/weekly axial ≤ 120/320; daily/weekly neuro ≤ 130/360 (profile values override these).
     - Hard/easy: no two consecutive hard days. At most one heavy/long run per day, with ≥ 48h between them.
     - HEUR-004: no deadlift within ±24h of a threshold or long run. Unflagged heavy strength is treated as a deadlift.
     - Same-day antagonism: squat vs. high-torque bike or threshold/long run; heavy press vs. CSS swim.
     - Non-consecutive strength days.
     - Single-run share across all runs: ≤ 32% with ≥ 4 runs (ADR-003). A week with N < 4 runs gets an explicit pigeonhole cap (3 runs: 40%, 2 runs: 60%; `pigeonhole_run_share`). The applied cap is reported in each plan's `run_share`.
  2. **Tier 1 (integer slacks with names and plain-English messages, reported in `plan["relaxations"]`):** anchors, minimum sport frequency, minimum strides, heavy-strength count (floor 1), and minimum rest days (floor 1). Exceeding the maximum rest days is the cheapest relaxation. Maximum bounds stay hard.
     - Weight = `8^rank` in the athlete's `tier1_relaxation_order` (DECISION-006). No category can exceed 7 units of slack, so these weights are exactly lexicographic.
  3. **Lexicographic solve:** Phase A minimizes Tier-1 slack only (`mip_rel_gap 0`, `abs_gap 0.5`). That optimum is then locked with a row, and Phase B optimizes Tier 2 (`mip_rel_gap 1%`). Top-K cuts reuse the lock.
  4. **Explainer:** `explain_feasibility()` runs before every solve. It names oversized anchors, workouts excluded by axial/neuro caps, unmeetable sport minimums, too few distinct workouts, heavy-strength conflicts, and pigeonhole run counts. An infeasible Tier 0 returns `[]` together with `last_diagnostics`. The season planner records such a week as INFEASIBLE with its reasons and treats it as a rest week.
  5. **Immutability:** the scheduler deep-copies its config. `enable_soft_48h_slacks` is now a deprecated no-op.
- **Enforcement Mechanism:** `tests/test_safety_constraints.py` (isolated rule tests); `tests/test_stress_scenarios.py` (run-share, 48h/axial/neuro caps, HEUR-004, frequency/relaxation consistency, and config immutability across all scenarios).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** pytest (113 pass). Stress scenarios 1/2b/8 dropped from 33.3%/33.3%/32.4% single-run shares to ≤ 32%. Scenario 3 (oversized anchor) and 6a (pool closed, Swim (2,2)) now return plans with named relaxations instead of INFEASIBLE or silent rewrites. Hybrid scenario-11 TSS fell from 610 to 434 because the old plans broke the 360 weekly neuro cap and HEUR-004.
- **Supersedes:** None formally. This replaces the soft-slack "infeasibility recovery" described in `TriathlonMILPScheduler`'s former docstring. Do not reintroduce soft Tier-0 caps or profile-discarding fallbacks.

## 5. Consequences & Trade-Offs
- **Positive:** Safety rules cannot be traded for TSS. Every shortfall is named. Both engines share one rule set, which is a step toward TASK-015.
- **Negative / Operational Cost:** About 3× more run-share rows (one per count level). Phase A adds an extra solve (sub-second on current libraries). Weeks that cannot meet Tier 0 now surface as INFEASIBLE and need a human decision. Example: the athlete profile's axial cap of 65 excludes the library's only heavy lift, while the heavy-strength floor is 1.
- **When to Revisit:** When TASK-015 merges the engines, or if the pigeonhole caps (40%/60%) should be tuned by evidence.

### `ADR-014` — Adopt Single Validated Canonical Workout Library and YAML Athlete Profile [`ACTIVE` | `workouts`]

# `ADR-014`: Adopt Single Validated Canonical Workout Library and YAML Athlete Profile

## 1. Context & Problem
Workouts lived in seven places: three 80/20 xlsx files, stale CSV copies of them, a root-level legos CSV, two diverging brick CSVs, a strength CSV, and two hand-coded Python lists. `enrich_workouts_from_df` guessed behavior flags at solve time. `'SS' in code` marked CSS swims as strides, `duration <= 75` decided whether a ride was indoor, and swim IF made 87% of swims "hard". Five codes named different workouts in different sources, and two rows' zone minutes did not match their duration. Nothing validated any of it.

## 2. Considered Alternatives
1. **Option A (Rejected):** Keep the sources and add validation at each loader. That leaves duplicate truths and solve-time inference in place.
2. **Option B (Rejected):** A Parquet library. It is not hand-editable or reviewable in a diff.
3. **Option C (ADOPTED):** One hand-editable CSV with a 7-zone schema, explicit flag columns, `provenance` and library-set membership. It is validated by Pandera (table) and Pydantic (rows) on every load. The athlete profile moves to YAML, validated by Pydantic types plus `AthleteProfile.validation_errors()`.

## 3. Decision & Production Invariant
- **Target Component / Path:** `data/workout_library.csv`, `src/library/{schema,build,loader}.py`, `config/athlete_profile.yaml`, and `src/athlete_profile.py` (`load_athlete_profile`, `save_athlete_profile`).
- **Locked Parameters / Contract:**
  1. **Columns (`WORKOUT_COLUMNS`):** 7-zone minutes `z1, z2, zx, z3, zy, z4, z5` (80/20 prescriptions keep ZX = ZY = 0). Engines see ZX folded into Z3 and ZY into Z4. Plus 18 explicit boolean flags, `provenance` ∈ {`8020_library`, `agent_authored`, `hand_coded_default`, `hand_coded_hybrid`}, and set membership `lib_default` / `lib_hybrid`.
  2. **Checks:**
     - Pandera: unique code; sport and provenance from closed vocabularies; TSS > 0; zone sum within 1 minute of duration.
     - Pydantic: cross-field rules (no strength flags on a ride, a ride is exactly one of indoor/outdoor, swims need a pool).
  3. **Build-time derivation:** `library.build.derive_8020_rows` applies documented rules (key session = ≥ 15 min Z3+ or ≥ 5 min Z4+; strides only where the description says "stride"; every 80/20 ride is trainer-eligible). After a re-scrape, `refresh_from_8020_scrape` replaces only 80/20 rows and keeps authored rows untouched. The xlsx files are a raw scrape cache, read only at build time.
  4. **Engines** load via `library.loader.load_candidates(library="default"|"hybrid"|provenance=...)`. `util.load_workouts()` reads the same table. Solve-time inference is deleted.
  5. **Profile:** every YAML field is required, so nothing silently falls back to a default. Unknown fields are rejected. Semantic rules: 7-day lists, ratio ranges, splits summing to 1, frequency min ≤ max, FTP = CP, known relaxation vocabulary.
- **Enforcement Mechanism:** `tests/test_workout_library.py` (schema rejects, audit fixes, idempotent refresh, YAML contract). The one-time migration is recorded in `scripts/migrations/m2026_11_15_build_canonical_library.py`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** 528 rows validate. Of these, 451 are 80/20, 35 agent-authored, 24 hand-coded default and 18 hand-coded hybrid. Stride workouts are corrected from 30+ (swims tagged by code substring) to 7. Corrections: `RTa1` 10 → 11 min, `BK_FR_TQ2` 75 → 81 min. The default and hybrid engine libraries load value-identical to the former hand-coded lists, apart from the documented renames, the `RUN_TB1_BRICK` hard flag, and two new stride rows.
- **Supersedes:** None. Do not reintroduce per-source loaders or substring flag inference.

## 5. Consequences & Trade-Offs
- **Positive:** One reviewed source of truth with lineage (`provenance`, `source`, `notes`, `needs_review`). Bad data fails loudly at load.
- **Negative / Operational Cost:** Flag rules changed some 80/20 classifications (swims, long rides), so engine behavior on the large library shifts. Durability is still on two scales (0–10 vs 0–85) until TASK-023. Strength TSS is unrecalibrated until TASK-021. Swim minutes are still precomputed with the legacy CSS until TASK-013.
- **When to Revisit:** When ingested actual sessions (TASK-024) need their own table, or the library outgrows CSV review.

### `ADR-015` — Adopt src-layout triplan package with domain subpackages [`ACTIVE` | `general`]

# `ADR-015`: Adopt src-layout triplan package with domain subpackages

## 1. Context & Problem
ADR-007 moved code into `src/` but left a flat module namespace: `from classes import ...`, `from library.loader import ...`. This forced `sys.path.insert` hacks in 11 files (src, scripts, tests), a 1,871-line `closed_loop_optimizer.py` monolith (candidate models, two MILP schedulers, pruning, physiology state, MPC controller, season generator), duplicated attributes in `classes.workout`, and two divergent dependency lists (`requirements.txt` vs `pyproject.toml`). Windows `spawn` multiprocessing pickles workers by module path (HEUR-001), so ambiguous flat module names were also fragile.

## 2. Considered Alternatives
1. **Option A (Rejected):** Keep flat modules and centralize the `sys.path` hack in a helper. Still non-installable, still ambiguous imports.
2. **Option B (Rejected):** Backward-compat shims (`closed_loop_optimizer.py` re-exporting new locations). Hides stale callers and keeps the monolith name alive.
3. **Option C (ADOPTED):** One installable src-layout package `triplan` with domain subpackages, absolute imports, editable install, `pyproject.toml` as the only dependency source. No shims; every caller updated.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/triplan/` (package), `pyproject.toml` (`[tool.setuptools.packages.find] where=["src"]`), `[project.scripts] triplan = triplan.cli:main`.
- **Module map (dependency direction model -> library/physiology -> solver -> control):**
  - `model/`: `candidates.py` (CandidateWorkout, MesoCycleTargetsAndConstraints), `classes.py` (triVariables, workout).
  - `library/`: `schema.py`, `build.py`, `loader.py`, `candidates.py` (build_default/hybrid_candidate_library), `clean_file.py`, `util.py`.
  - `solver/`: `scheduler.py` (TriathlonMILPScheduler), `hybrid.py` (HybridAthleteMILPOptimizer), `pruning.py` (prune_candidate_library), `highs_optimization.py`, `highs_runtime.py`, `safety_constraints.py` (Tier-0).
  - `physiology/`: `athlete_profile.py`, `state.py` (AthletePhysiologicalState).
  - `control/`: `mpc.py` (ClosedLoopMPCController), `season.py` (format_plan_to_dataframe, generate_closed_loop_season_schedule).
  - `io/`: `trainingpeaks.py`. `perf/`: `stress.py` (11 stress scenarios). `cli.py` / `__main__.py`: demo pipeline.
- **Locked Contract:** Intra-package imports are absolute (`from triplan.solver.scheduler import ...`); no `sys.path` manipulation anywhere in `src/`, `scripts/`, `tests/`. Repo-root `data/` and `config/` are resolved relative to `__file__` (four levels above `src/triplan/SUB/MODULE.py`). Multiprocessing worker functions stay top-level in importable modules. Dependencies are declared only in `pyproject.toml` (`test` and `garmin` extras); `requirements.txt` is the single line `-e .[test,garmin]`.
- **Enforcement Mechanism:** `pip install -e ".[test,garmin]"`; the 143-test pytest suite imports only `triplan.*`; a golden diff of the 11 stress scenarios (statuses, schedules, objectives) was byte-identical before and after the move.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** TASK-014 golden stress-suite diff (identical) and 143/143 passing tests.
- **Supersedes:** None. Refines ADR-007 (layout rule "Code in `src/`" becomes "Code in `src/triplan/DOMAIN/`").

## 5. Consequences & Trade-Offs
- **Positive:** Installable package, unambiguous imports, cohesive modules, one dependency source, scripts run from any cwd after the editable install.
- **Negative / Operational Cost:** `model.candidates` imports `DEFAULT_RELAXATION_ORDER` from `solver.safety_constraints` (default value of a dataclass field), a mild layering inversion; `physiology.athlete_profile` lazily imports `solver`/`control` inside methods to avoid cycles. Absolute historical paths in older ADR/PRD texts are left as written.
- **When to Revisit:** If `solver` grows beyond ~8 modules, or if the package is published (then drop `pythonpath=["src"]` and the `__file__`-relative repo-root lookups in favor of an explicit data-dir setting).

### `ADR-016` — Scope the Startup Token Budget to the Charter's Read List [`ACTIVE` | `general`]

# `ADR-016`: Scope the Startup Token Budget to the Charter's Read List

## 1. Context & Problem
`ADR-001` set the < 7,500-token startup core as `GEMINI.md` + `STATE_SUMMARY.md` + `LODE_MAP.md` + `INVARIANTS.md` + `TASKS.md`. By 2026-11-16 `lodectx.py stats` reported 10,758 tokens (`TASK-030`). Two scoping errors made the measured set diverge from what an agent actually reads:
1. The charter (`GEMINI.md`, symlinked as `CLAUDE.md`, `.agents/{GEMINI,CLAUDE,AGENTS}.md`; ~3,070 tokens) is injected by the agent harness every session. Agents do not open it through progressive disclosure, and it is user-owned instructions, so the lineage engine cannot trim it.
2. `SPEC.md` is item 5 of the charter's §1 mandatory read list but was not counted.
3. `TASKS.md` (~3,450 tokens) carried full multi-paragraph task specs for 20+ items.

## 2. Considered Alternatives
1. **Option A (Rejected): Keep the charter in the counted set.** The four docs would have to fit in ~4,400 tokens, which means cutting `INVARIANTS.md` and `SPEC.md` content that is load-bearing (hardware and safety rules).
2. **Option B (Rejected): Raise the budget.** This defeats `ADR-001`'s purpose.
3. **Option C (ADOPTED): Count exactly the charter's §1 read list, and move task specs to an on-demand `docs/BACKLOG.md`.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `docs/lode.json` (`startup_files`), `scripts/lodectx.py` (`DEFAULT_CONFIG`, `LODE_MAP.md` header rendered from config), `docs/TASKS.md`, `docs/BACKLOG.md`.
- **Locked Parameters / Contract:** Startup core = `docs/STATE_SUMMARY.md` + `docs/LODE_MAP.md` + `docs/INVARIANTS.md` + `docs/TASKS.md` + `SPEC.md`, and it must stay < 7,500 tokens (`chars / 4`). `TASKS.md` holds one line per open item. Full specs go in `docs/BACKLOG.md`, which is read on demand for the task being started and deleted on close. The charter is budgeted separately by its owner.
- **Enforcement Mechanism:** `python scripts/lodectx.py stats` (PASS/FAIL).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `lodectx.py stats` on 2026-11-16 measured 10,968 tokens (FAIL) before this change and 6,758 tokens (PASS) after it.
- **Supersedes:** None. This refines the scope of `ADR-001`'s locked contract; the rest of `ADR-001` stands.

## 5. Consequences & Trade-Offs
- **Positive:** The measured set matches what agents read. There are ~700 tokens of headroom, and `TASKS.md` growth is bounded to one line per item.
- **Negative / Operational Cost:** Task specs now live in two places (index + backlog), and both must be updated when tasks open or close.
- **When to Revisit:** If the charter's §1 read list changes, or the charter itself grows past ~4,000 tokens.

### `ADR-017` — Unified WeekModel engine with modular constraint builders [`ACTIVE` | `optimizer`]

# `ADR-017`: Unified WeekModel engine with modular constraint builders

## 1. Context & Problem
Three separate HiGHS formulations lived side by side: `TriathlonMILPScheduler` (incremental `addVar`/`addRow`, partition + brick session model), `HybridAthleteMILPOptimizer` (bulk `addCols`, at most 2 workouts per day, split architecture, barbell share, TiZ band) and `highs_optimization.optimize_weekly_schedule` (DataFrame, highspy expression API, one workout per day). Each re-implemented column registries, row hygiene (duplicate columns, zero coefficients), soft-goal deviations with different scaling conventions, Top-K cuts and result extraction. Every new rule (`TASK-018` carry-in, `TASK-019` session structure, `TASK-020` cuts) would have had to be written three times.

## 2. Considered Alternatives
1. **Option A (Rejected): Keep three engines, share only `safety_constraints`.** This is the pre-`TASK-015` state; rule drift between engines had already produced the HEUR-004 miss in the hybrid engine (`TASK-017`).
2. **Option B (Rejected): Rewrite as one new formulation with new semantics.** Impossible to verify; it would change plans silently.
3. **Option C (ADOPTED): One engine, semantics selected by explicit options, built from modular builders over a buffered sparse model.** Each legacy engine is an option preset that reproduces its formulation exactly, so the refactor is verifiable by diffing solutions.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/triplan/solver/engine/` (`model.py`, `config.py`, `context.py`, `engine.py`, `builders/{calendar,orthopedic,intensity_structure,strength,goals}.py`). `scheduler.py`, `hybrid.py` and `highs_optimization.optimize_weekly_schedule` are thin facades.
- **Locked Parameters / Contract:**
  - `WeekModel` buffers columns (lb/ub/cost/integrality) and CSR rows in numpy, sums duplicate columns, drops zero coefficients, and performs ONE `addCols` and ONE `addRows`. Builders never talk to HiGHS. Only Top-K cuts are added to a built model.
  - Soft goals are dimensionless: `add_soft_goal(coeffs, target, scale, weight, name, pos_weight=None, neg_weight=None)` costs each deviation `weight / scale`. Hard rules are never expressed as soft goals.
  - `EngineOptions` (see `engine/config.py`) selects `session_model` (`partition_brick` | `per_sport_cap` | `single_per_day`), gating rule, cost/goal profile, `cut_mode`, rest-day default window, and the strength/TiZ/adjacency builders. `meso_options()`, `hybrid_options()`, `legacy_options()` reproduce the three legacy engines.
  - Workout and config attributes are read in one place (`WorkoutTable`, `WeekConfig`); builders never call `getattr`/`cfg[...]` directly. The orthopedic builder delegates to `safety_constraints` and must not duplicate it.
  - New rules (carry-in state/ACWR, session structure, selection cuts, pruning) are added as builder modules or replace `UnifiedWeekEngine._add_cut`, never as a fourth engine.
- **Enforcement Mechanism:** `tests/test_week_model.py` (buffering, duplicate summing, soft-goal math, bulk-build row counts, facade plan keys); existing `tests/test_safety_constraints.py`, `tests/test_current_athlete.py`, `tests/test_stress_scenarios.py` exercise both facades.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Pre/post solution diff over the 11 stress scenarios, three CURRENT_ATHLETE phases, the hybrid athlete config, 9 safety unit cases (26 engine solves, Top-3 each): status, rank-1..k objective, `tier1_penalty` and every selected `(workout, day)` set were identical (relative objective difference 0.0). The legacy day scheduler was compared at the model level (identical columns, costs, bounds, integrality; identical rows except that the two one-sided TSS guardrail rows are one ranged row) and by solved objective on 6 instances (relative difference <= 2e-10). See `docs/archive/TASKS_COMPLETED.md` (`TASK-015`).
- **Supersedes:** None. `ADR-013` (tiers) and `ADR-008` (axial/neuro caps) are unchanged and still enforced through `safety_constraints`.

## 5. Consequences & Trade-Offs
- **Positive:** One code path per rule; new rules are one builder; the Top-K cut and candidate pruning of `TASK-020` have a single seam; sparse numpy construction scales with candidate count.
- **Negative / Operational Cost:** Legacy quirks are preserved verbatim behind options: the hybrid `z_split` flip cut (defect owned by `TASK-020`), the hybrid TiZ slacks measured in minutes with unit scale (not dimensionless; recalibrate in `EXP-004`), the scheduler's partially redundant adjacency rows, and the scheduler's deviation cap of infinity versus the hybrid's 1e4.
- **When to Revisit:** After `TASK-018`/`TASK-019`/`TASK-020` land, drop the options that only exist to reproduce a retired formulation.

### `ADR-018` — Single deterministic solve with selection cuts, dominance pruning and symmetry breaking replaces the multi-seed race [`ACTIVE` | `optimizer`]

# `ADR-018`: Single deterministic solve with selection cuts, dominance pruning and symmetry breaking replaces the multi-seed race

## 1. Context & Problem
`ADR-006` bought speed with a 15-seed x 2-thread race (30 cores) over the selection-only `optimize_weekly_workouts`. After `ADR-017` every weekly MILP is built by one engine, so the model itself is the lever: the race could not share state between weeks, its "best" plan was ranked by `util.calculate_plan_score` (a heuristic, not the objective), and the Top-K cuts were weak. `cut_mode='active_x'` / `'pair_pm1_split_flip'` excluded only one exact `(workout, day)` layout, so Top-K returned day permutations of the same workouts, and the hybrid `z_split` flip equality capped hybrid Top-K at two plans.

## 2. Considered Alternatives
1. **Option A (Rejected): Keep the seed race.** 30 cores to hide model weakness; non-deterministic; benchmarked best-of-5 plans all stopped at `TIME_LIMIT_FEASIBLE` with 62-87% gaps on the 337-candidate library, so it did not even prove quality.
2. **Option B (Rejected): Prune by "smaller is better" dominance** (shorter, lower TSS/ANS/strain). Unsound: TSS, zone and sport-split goals are two-sided and caps interact, so a lighter workout can make a plan worse.
3. **Option C (Rejected): Symmetry-break arbitrary day permutations.** Not symmetries: rolling 48h windows and hard/easy adjacency couple consecutive days. Only the week reversal `d -> 6 - d` is a candidate automorphism.
4. **Option D (ADOPTED): one deterministic solve through the unified engine, selection cuts, exact-clone dominance pruning, conditional reversal symmetry row.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/triplan/solver/engine/engine.py` (`_add_cut`), `engine/builders/symmetry.py`, `src/triplan/solver/pruning.py` (`prune_dominated_candidates`), `engine/config.py` (`EngineOptions`).
- **Locked Parameters / Contract:**
  - Top-K: `cut_mode='selection'` is the default for both facades. With `s_i = sum_d x[i,d]` and `S` the workouts of the plan just found, the cut is `sum_{i in S} s_i <= |S| - m` (`cut_min_diff = m`, default 1). `cut_mode='layout'` keeps the exact `(i,d)` no-good (day permutations count as distinct). `none` = single solve. The hybrid `z_split` flip equality is removed; `split_architecture` is still reported from `z_split`'s value.
  - Dominance pruning (`prune_dominated`, default on): workout j is dropped only if it is identical to at least `cap` kept workouts in every attribute except `preference_penalty` (lower is better) and `durability_score` (higher is better), the kept ones are no worse on both, and `cap` = that sport's maximum weekly frequency (or 7). Anchors, `test`-flagged workouts and non-standard sports are never pruned. Never prune on duration/TSS/ANS/strain order.
  - Symmetry breaking (`symmetry_breaking`, default on): one row `sum_d (d-3) sum_i dur_i x[i,d] <= 0` only when the week is provably invariant under reversal (palindromic caps, readiness alpha, `allowed(i,d) == allowed(i,6-d)`; no weekday-only cap, no weekend brick, no legacy day rules). Off for `layout` cuts.
  - No seed race: `optimize_weekly_workouts`, `optimize_weekly_workouts_multiseed`, `_solve_seed_worker`, `_solve_olympic_week_worker`, `generate_16_week_olympic_schedule` (solver module) and `util.generate_random_workout_plan` are deleted. `util.calculate_plan_score` is a reporting metric only. The CLI runs sequential single solves and prints status and `mip_gap`.
- **Enforcement Mechanism:** `tests/test_selection_cuts.py` (hybrid k=4 distinct selections, `m` difference, layout mode, pruning soundness and rank-1 objective, symmetry row conditions and optimum preservation with `tier2_gap=0`).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** Pre/post harness over 26 engine solves: rank-1 objective and `tier1_penalty` identical in 25; the one difference (`hybrid_dl_long`, +0.77%) was inside the Tier-2 1% gap and vanished (515.95 vs 515.95) with `tier2_gap=0`. 11/12 stress solves stay `OPTIMAL` at <= 1% gap in 0.3-3.6 s; the 200-workout scale scenario is `TIME_LIMIT_FEASIBLE` at 5.7% in both. See `docs/archive/TASKS_COMPLETED.md` (`TASK-020`, `TASK-004`).
- **Supersedes:** `ADR-006`. Do not reintroduce a seed portfolio to mask a weak model; improve the formulation (cuts, pruning, symmetry) or the gap/time policy instead. `EXP-003` (single solve vs race at scale) stays queued as the formal benchmark.

## 5. Consequences & Trade-Offs
- **Positive:** Deterministic, reproducible plans; 1 core instead of 30; Top-K are genuinely different weeks; hybrid Top-K no longer capped at 2.
- **Negative / Operational Cost:** Selection cuts exclude supersets of earlier selections and cost 1.1-1.5x solve time at k=3 (`benchmark_scaling` 50/100 workouts: 5.8 -> 7.1 s, 9.6 -> 11.5 s); clone pruning hides equivalent clone alternatives; the legacy DataFrame day scheduler (`optimize_weekly_schedule`) still ends `TIME_LIMIT_FEASIBLE` (gap ~100%) on a 337-candidate library, before and after.
- **When to Revisit:** If `EXP-003` shows a regression at >200 candidates, or `TASK-018`/`TASK-019` add day-asymmetric rows (re-audit `is_week_reversal_symmetric`).

### `ADR-019` — Carry-in state and in-model linearized running ACWR, CTL ramp and run-minutes rows [`ACTIVE` | `mpc`]

# `ADR-019`: Carry-in state and in-model linearized running ACWR, CTL ramp and run-minutes rows

## 1. Context & Problem
Every weekly MILP was an isolated week: Monday ignored Sunday (hard/easy adjacency, 48h heavy-run separation, HEUR-004 deadlift +-24h, non-consecutive strength days and the rolling 48h TSS/ANS/strain caps all stopped at the week boundary), and the EWMA running ACWR (`ADR-012`: <= 1.20 while run CTL < 15, <= 1.25 after) was enforced only after the fact. The season loop shrank `max_weekly_run_mech_strain` when the state's ACWR was already over the limit, i.e. it reacted one week late and could not shape the next week's day pattern. `AthletePhysiologicalState` was also never seeded with the profile's run CTL (it kept the dataclass default 35), so the "run CTL < 15" rule never fired outside the re-entry block.

## 2. Considered Alternatives
1. **Option A (Rejected): keep the post-hoc strain-cap shrink.** A weekly total cannot express a daily EWMA ratio; two weeks with equal run strain have very different peak ACWR depending on which days carry them.
2. **Option B (Rejected): a rolling 35-day multi-week MILP.** Exact, but multiplies the model by about 5 and defeats the receding-horizon structure (`ADR-005`); previous weeks are fixed history, so they are constants.
3. **Option C (Rejected): nonlinear ratio `A_d / C_d <= rho`.** `C_d` is affine in `x`, so for `C_d > 0` the ratio bound is exactly the linear row `A_d - rho*C_d <= 0`; the only difficulty is `C_d -> 0`, handled by `eps`.
4. **Option D (ADOPTED): `WeekCarryIn` constants plus linear rows in a new builder, `engine/builders/load_dynamics.py`.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `solver/engine/carry_in.py` (`DayLoad`, `LoadState`, `WeekCarryIn`), `solver/engine/builders/load_dynamics.py`, `control/carry.py` (season hand-off), `UnifiedWeekEngine(..., carry_in=)`, `TriathlonMILPScheduler(..., carry_in=)`, EWMA constants `ALPHA_ACUTE/CHRONIC/ATL/CTL` in `physiology/state.py` (single source for `step_day` and the rows).
- **Locked Parameters / Contract:**
  - **Boundary (Tier-0 constants).** `DayLoad` (days -1, -2: hard, heavy/long run, deadlift, heavy strength, strength, aerobic TSS, mech strain, ANS) comes from `classify_workouts`, the same sets as the safety layer. Day -1 hard -> no hard workout on day 0; day -1 heavy/long run -> no heavy/long run or deadlift on day 0; day -1 deadlift -> no heavy/long run on day 0; day -1 strength -> no strength on day 0; scheduler adjacency (heavy lift vs heavy/long run +-24h); rolling 48h caps over the window (-1, 0): `sum_i v_i x[i,0] <= max(0, cap - v_{-1})`. An empty `WeekCarryIn()` adds no rows (the isolated-week model, row for row).
  - **Linearized running ACWR.** With `a = 2/8`, `c = 2/29` (as `step_day`), carried `A0, C0` after day -1 and run mechanical strain `m_i`: `A_d = (1-a)^(d+1) A0 + a * sum_{k<=d} (1-a)^(d-k) sum_i m_i x[i,k]`, same for `C_d` with `c`. **Ceiling (Tier-0), every day d:** `A_d - rho_max C_d <= eps`, with `rho_max` = `max_running_acwr_reentry` (1.20) while start-of-week run CTL < `reentry_run_ctl_exit` (15), else `max_running_acwr` (1.25); 1.30 (INVARIANTS) when no profile; min(rho, 1.15) in masters mode. The rhs is clamped at `max(eps - const_d, 0)`: an already-overshooting start state is not charged to the week (the plan may only not worsen it) instead of making the week infeasible.
  - **`eps` (`epsilon_floor`).** `eps = (a - rho_max*c) * min_run_strain` of the library: the value of `A - rho*C` after ONE smallest run from a fully detrained state (CURRENT_ATHLETE: 3.345 strain units). The ratio is undefined near zero chronic load and `ADR-012` allows the 60 min/week floor regardless of ACWR, so a single floor-sized session is always admissible; above that the ratio rules. Stated plainly: with small `C` the raw *ratio* can exceed `rho` by design (`A - rho*C <= eps` is a hybrid absolute/relative bound), and with the current library's shortest run (30 min) and at most 2 runs/week the EWMA ratio of two spaced runs is mathematically about 1.4, so the cap bites only on top of that floor. Walk-jog candidates (`TASK-023`) are the fix, not a looser `rho`.
  - **Lower window bound 0.80 is a named Tier-1 slack, not Tier-0.** `A_d - 0.8 C_d + M_d s >= 0`, one binary `s` per week, category `relax_acwr_floor`, weight 1 (cheaper than every ranked relaxation, `Tier1Registry.add_named`), reported in `relaxations`. Undertraining is not an injury risk and deload/taper/re-entry weeks must stay feasible. This is a NEW in-model rule (none existed before), not a loosening of an existing one; the upper bound (1.30 global, 1.20/1.25 running) is Tier-0.
  - **Weekly CTL ramp (Tier-0).** `CTL_7 - CTL_0 <= ramp` with `CTL_7 = (1-b)^7 CTL_0 + b sum_d (1-b)^(6-d) T_d`, `b = 2/43`, `T_d` the bike+run+swim TSS (what `total_ctl` integrates). `ramp` = `AthleteProfile.max_weekly_ctl_ramp` = 7 CTL points/week (upper end of the usual 5-8 guidance; ADR-012 sets no value), 5 for masters. New profile field: flag for review.
  - **Absolute run-minutes guard (Tier-0, ADR-012 `max_weekly_run_minutes_increase`).** While run CTL < 15: `sum run duration <= max(reentry_run_minutes_floor, prev_week_run_minutes + max_weekly_run_minutes_increase)` = max(60, prev + 15).
  - **MPC backstop.** The season loop no longer shrinks the run-strain cap for ACWR (the in-model rows replace it); it keeps the ODI trigger (no in-model ODI until `TASK-022`). `process_weekly_execution_and_recalibrate` stays as the backstop for ACTUAL data (a plan that held can still be executed worse; `TASK-025`) with its 1.30 global trigger.
  - `is_week_reversal_symmetric` returns False whenever the carry-in is active (boundary constants and EWMA rows are time-oriented).
  - `AthleteProfile.to_athlete_state` seeds `run_ctl/atl` (new `starting_run_ctl/atl`) and splits the remainder of `starting_ctl/atl` over bike and swim 55:25.
- **Enforcement Mechanism:** `tests/test_carry_in.py`: boundary bans, 48h cap counting day -1, empty carry-in equals the isolated model (rows/objective/plan), ACWR row binds in a constructed ramp, eps floor, Tier-1 floor waiver, CTL-ramp row, run-minutes row, symmetry off, profile seeding, and a 40-week season with `A_d - rho*C_d <= eps` on every day of every week plus the run-minutes guard and CTL ramp every week.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** CURRENT_ATHLETE Olympic season (24-week re-entry + 16-week race block): 40/40 OPTIMAL in 7.9 s (8.9 s without the rows); `A_d - rho*C_d <= eps` held in 40/40 weeks (without the rows: 3 weeks over eps, max excess 3.75 vs 3.33). Floor waiver used in 31/40 weeks (the library cannot hold 0.80 with 1-2 runs/week).
- **Supersedes:** None. Do not re-add a post-hoc ACWR strain-cap shrink; it cannot see the daily pattern.

## 5. Consequences & Trade-Offs
- **Positive:** Weeks are solved in context (no Sunday-hard plus Monday-hard, correct 48h caps across the boundary); ACWR is a plan-time guarantee, not a next-week reaction; run CTL is seeded so ADR-012's 1.20/1.25 switch is real.
- **Negative / Operational Cost:** The ACWR ceiling is `eps`-relative at low chronic load (above). The run-minutes guard (60 + 15/wk) plus 30-minute minimum runs keeps CURRENT_ATHLETE inside the re-entry rules for the whole season (run CTL 2.8-8.5, never 15) and leaves a Run frequency shortfall or dropped Run anchor in many weeks (14/40 report a `Run:` relaxation); `TASK-023` (walk-jog) is the structural fix. State and model must share EWMA constants (`physiology/state.py`); `TASK-021` changes them in one place.
- **When to Revisit:** After `TASK-021` (exact Banister decay) and `TASK-023` (walk-jog), re-derive `eps` and re-check whether the 0.80 floor can be Tier-0; once ODI is in-model (`TASK-022`), move that trigger out of the season loop too.

### `ADR-020` — Sport-specific quality q_i classifies hard and key sessions; lift+easy combos are compound candidates; canonical 80/20 is a hard engine rule [`ACTIVE` | `physiology`]

# `ADR-020`: Sport-specific quality q_i classifies hard and key sessions; lift+easy combos are compound candidates; canonical 80/20 is a hard engine rule

## 1. Context & Problem
`ADR-012` (`DECISION-013`, `DECISION-021`, `DECISION-004`) fixed the athlete's session structure, but the engine did not model it. (a) "Hard" came from a library flag whose precursor was a raw `IF >= 0.85` test; swim IF starts at ~0.795, so easy swims looked hard, and the flag was never re-derived from the zone minutes the model schedules. (b) The hybrid time-in-zone band capped only `Z4+Z5` (Z3 and ZY escaped) with minute-scale soft slacks, and the meso facade had no TiZ rule beyond a 5-zone Tier-2 goal. (c) A lift followed by short easy cardio (the only way 3 lifts + 2 rides + 2 runs fit 6 windows) could not be expressed, because every constraint keys on `workout.sport`. (d) Key-session counts, Hard-Days-Hard stacking and AM/PM windows (`two_session_windows`) were not read at all.

## 2. Considered Alternatives
1. **Option A (Rejected): Keep classifying by IF with per-sport IF thresholds.** IF scales are not comparable across sports and mislabel mixed sessions.
2. **Option B (Rejected): Model a combo as two workouts tied by a same-window indicator.** Doubles columns, needs a coupling row per pair/day, and lets the solver count the pair as two sessions.
3. **Option C (Rejected): Strict Tier-0 Hard-Days-Hard `sum heavy <= sum hard`.** Infeasible whenever no hard session can legally share the lifting day (squat vs high-torque bike, time caps, no hard sessions), which makes the heavy-strength floor of `ADR-013` unreachable.
4. **Option D (ADOPTED):** minutes-based sport-specific quality `q_i`; combos as compound candidates (`sport` stays `Strength`, cardio component in `cardio_*` fields); each structure rule is its own builder function with an `EngineOptions` switch; canonical 80/20 as hard rows.

## 3. Decision & Production Invariant
- **Target Component / Path:** `src/triplan/solver/engine/session_quality.py`, `engine/builders/session_structure.py`, `engine/builders/intensity_structure.py` (`add_canonical_8020`), `src/triplan/library/compound.py`, `engine/engine.py` (pre-filter pipeline, AM/PM output), `safety_constraints.py` (`run_minutes`, `run_strain`, combo-aware `runs`).
- **Locked Parameters / Contract:**
  - `q_i = sum_z w_z * minutes_z` over (Z1, Z2, ZX, Z3, ZY, Z4, Z5), weights `(0, 0, 1/6, 1/3, 2/3, 1, 1)` (Z4-equivalent minutes; the library rule "15 min Z3+ or 5 min Z4+" is exactly `q = 5`). Strides' alactic Z5 is excluded. Incidental allowance: `q <= q_noise + q_noise_per_hour * hours` (defaults 2 and 1) counts as easy. `is_hard = q >= q_hard` (default 5). Grey zone `0 < q < q_hard` is removed before the model is built (anchors and tests never). Key = hard, anchor, or long (Run >= 70, Bike >= 120 min). Overridable per sport through config key `session_quality`. `is_hard_endurance` is re-derived from `q_i` at engine construction, never from IF or a stale flag.
  - Pipeline order in `UnifiedWeekEngine.__init__`: grey-zone drop -> `q_i` hard classification -> combo generation -> dominance pruning -> `WorkoutTable`.
  - One hard endurance session per day in both facades. Key sessions `key_sessions_per_week` (default `[2, 3]`): upper bound hard, lower bound the Tier-1 slack `reduce_key_sessions`. Strides: `min_stride_sessions` default 2 as the single Tier-1 `drop_stride_count` slack.
  - Hard-Days-Hard: on days with `two_session_windows[d]`, `sum_{heavy lower} x <= k_d = sum_{hard endurance} x`, only on days where stacking is physically possible, with a per-day named slack `unstack_heavy_strength`. `double[d] <= two_session_windows[d]` (default all True). Plan output carries `am_pm` slots and day lists are ordered AM -> PM (hard endurance AM + lift PM; lift AM with an easy PM session; gap `am_pm_min_gap_h`, default 6).
  - Combos (`DECISION-021`): `sport = "Strength"`, `cardio_sport` Bike or Run, `cardio_minutes <= 20`, strictly Z1/Z2, only Bike after heavy lower-body lifts. One session per window, full duration against the day cap, counts toward the cardio sport's Tier-1 frequency min and max; the run leg (`run_minutes`, `run_strain`) feeds the single-run share cap, weekly run strain cap, carry-in ACWR and run-minutes rows (`TASK-018`); at most one Bike (Run) carrier per day.
  - Canonical 80/20 over endurance minutes (strength excluded, combo cardio included): `Z1+Z2 >= 0.78 T` and `Z3+ZY+Z4+Z5 <= 0.20 T` HARD in both facades; `Z1+Z2 >= 0.80 T` soft (`w_8020_low`), Zone X soft toward 0 (`w_zone_x`). The meso 5-zone `target_zone_pcts` stays a Tier-2 goal; hybrid `tiz_band` is off.
  - Day-asymmetric rule audit (`ADR-018`): a non-palindromic `two_session_windows` disables the week-reversal symmetry row.
- **Enforcement Mechanism:** `tests/test_session_structure.py`, `tests/test_stress_scenarios.py`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** full pytest green (see `docs/archive/TASKS_COMPLETED.md`, `TASK-019`). Stress suite: every MILP scenario still solves; canonical 80/20 and 2-3 keys hold in every stress plan; hybrid scenario 11 rank-1 objective unchanged (60.68).
- **Supersedes:** None. Refines `ADR-009` (7-zone scale) and `ADR-012`/`ADR-013` (structure rules are Tier-1 named slacks; the 80/20 floor and ceiling are hard rows).

## 5. Consequences & Trade-Offs
- **Positive:** Hard/key classification is sport-comparable and auditable; 80/20 is enforced identically by both facades (hybrid Z3/ZY gap closed); the no-double-day, lift + easy cardio week is representable.
- **Negative / Operational Cost:** Combos add 2-3 columns per lift; the library flag `is_hard_endurance` is no longer the engine's source of truth (heavy-run, long-run, high-torque flags still are). Grey-zone pre-filtering removes real library workouts (31 of 556 with defaults).
- **When to Revisit:** If `q_hard` needs athlete calibration after a CP/CSS retest (`ADR-011`); if the unified durability scale (`TASK-023`) changes combo durability.

### `ADR-021` — Re-entry weeks scale the structure minimums down when the full-week structure floor exceeds the TSS target [`ACTIVE` | `optimizer`]

# `ADR-021`: Re-entry weeks scale the structure minimums down when the full-week structure floor exceeds the TSS target

## 1. Context & Problem
`EXP-007` (`TASK-037`): `CURRENT_ATHLETE` re-entry build weeks (targets 96-160 TSS from `base_weekly_tss` 160 x 0.60..1.00) solved at a median 1.26-1.37x target (up to +82 TSS). Per-week ablation on the real library: the full-week structure minimums (key >= 2, strides >= 2, Bike/Run >= 2, <= 2 rest days) are Tier-1 slacks that the lexicographic solve minimises BEFORE the soft Tier-2 TSS goal, so together they set a TSS floor of 159-168 (early re-entry) to 214 (weeks 19-23) that a lower target can never pull down (solving with target 1 returns the same schedule). Stride sessions (-17 TSS), key sessions + rest days and Bike/Run frequency each contribute; halving all (and rest days <= 4) gives ~120-153, dropping key/stride gives 95-153.

## 2. Considered Alternatives
1. **Option A (Rejected): raise the re-entry targets to the structure floor.** Makes target == solved by relabelling: the owner's conservative 96 -> 160 ramp becomes 160-215 for 24 weeks, and the floor is not a number the profile knows (it varies 159-214 with carry-in state).
2. **Option B (Rejected): fixed per-week relief by `tss_factor`.** The floor is not monotone in the factor (week 9: 159, week 21: 214), so any fixed schedule is wrong for some weeks.
3. **Option C (ADOPTED): re-solve a re-entry base/build/overload week at relief level 1 then 2 while solved > 1.15 x target.** Level 1 is the `TASK-034` deload relief (each minimum halved rounded up, never below 1; max rest days 4); level 2 also drops key and stride minimums to 0 (profile comment: "0-1 key sessions during the first 4 re-entry weeks"). The relieved plan is kept only if it lowers TSS.

## 3. Decision & Production Invariant
- **Target Component / Path:** `control/periodization.py` (`apply_reentry_relief`, `REENTRY_RELIEF_ROLES`, `STRUCTURE_OVERSHOOT_TOL`), `control/season.py` (re-solve loop; the week summary's `Recalibration` carries `STRUCTURE_RELIEF_Ln (...)`).
- **Locked Parameters / Contract:** only structure MINIMUMS change (key, strides, Bike/Run frequency, max rest days); Strength frequency, heavy-strength count, every maximum and every Tier-0 cap are untouched. Applies only to the re-entry block, only in base/build/overload roles, only when the full structure overshoots by > 15%. Race-block weeks keep the full structure.
- **Enforcement Mechanism:** `tests/test_season_periodization.py::test_reentry_relief_levels_scale_minimums_only`, `::test_reentry_build_weeks_track_their_targets` (median solved/target in [0.9, 1.15]); `tests/test_season_validation.py` slow matrix stays 0 violations.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` seasons re-run: re-entry build-week median solved/target 1.4 -> 1.02 (see `TASK-037` archive entry).
- **Supersedes:** None. Extends the `TASK-034` deload relief to re-entry build weeks.

## 5. Consequences & Trade-Offs
- **Positive:** the re-entry ramp the owner approved is what the athlete is actually prescribed; no cap loosened.
- **Negative / Operational Cost:** a re-solve (up to two extra MILPs) in overshooting re-entry weeks; early re-entry weeks may carry 1 key session / 1 stride session instead of 2 (OWNER DECISION to confirm, see PR).
- **When to Revisit:** if the structure floor is made a modelled quantity (a Tier-2 trade-off instead of Tier-1 slacks), or `base_weekly_tss` is re-derived from the structure.

### `ADR-022` — Deload weeks carry no key minimum, anchor obligation or running-ACWR floor charge [`ACTIVE` | `optimizer`]

# `ADR-022`: Deload weeks carry no key minimum, anchor obligation or running-ACWR floor charge

## 1. Context & Problem
After `TASK-034` (deload structure relief) `EXP-007` still showed recovery weeks up to +68 TSS (`CURRENT_ATHLETE`) / +244 (unconstrained reference) and taper/race weeks up to +44 / +275 over target. Single-week ablations on the real library (re-solving recorded `WeekRecord` configs and carry-ins) found three independent Tier-1 charges, each minimised BEFORE the soft Tier-2 TSS goal:
1. **Key-session minimum >= 1** (`CURRENT_ATHLETE`): the cheapest hard session forces ~160-170 TSS in a 72-141 target week (key min 0 alone: weeks 8-39 within +10 of target; stride min 0 alone did not help).
2. **Mandatory anchors** (reference profile, whose caps can host a 180 min ride and 75 min run): `shorten_or_drop_anchor` forces them every week (floor ~350-400 vs 186-220 target; dropping the obligation alone: reference recovery +0..+3).
3. **Running-ACWR 0.80 floor waiver** (`ADR-019`, binary Tier-1 slack of weight 1): whenever any plan can hold `A_d >= 0.8 C_d` the solver pays for extra running instead of waiving, although a deload week is *defined* by acute < chronic load. Reference recovery solved 275/269/269 vs 186/203/220 with the carry-in, 187/203/223 with the floor removed (`nocarry` and `nofloor` ablations coincide; CTL-ramp ablation changed nothing).
The reference swim minimum of 2 (not scaled by `TASK-034`) additionally held its race week +48.

## 2. Considered Alternatives
1. **Option A (Rejected): retarget deload weeks to the floor.** The floors (166-275 TSS) are larger than the build week they follow in several weeks; it would defeat the recovery week.
2. **Option B (Rejected): loosen/rank the ACWR floor in every week.** The floor stays in build weeks (`ADR-019`); only deload roles are exempt.
3. **Option C (ADOPTED): remove each charge in deload roles only.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `control/periodization.py` (`apply_deload_relief`: key min 0, Swim joins Bike/Run halving; `relax_deload_anchors`; `relax_deload_acwr_floor`), `control/season.py` (applied to the week library and the carry-in handed to the solver; the carry handed to the next week is the original).
- **Locked Parameters / Contract:** roles recovery/taper/race only. Anchors stay in the library as ordinary optional candidates. `LoadState.acwr_min` is set to 0 for the solve (no floor row, no waiver); the running-ACWR ceiling, boundary rows, CTL ramp, run-minutes guard, every cap and every maximum are untouched.
- **Enforcement Mechanism:** `tests/test_season_periodization.py` (relief only in deload roles; short 14-week `CURRENT_ATHLETE` season, deload weeks <= target + 25); slow `tests/test_season_validation.py::test_full_matrix_season` asserts recovery and taper/race max over-target <= 25 TSS and 0 violations for all 8 seasons.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` matrix re-run: recovery max over +68/+244 -> +8/+4, taper/race +44/+275 -> +11/+14 (see `TASK-038` archive entry). 0 independent Tier-0 violations in all 8 seasons.
- **Supersedes:** None; completes `TASK-034`.

## 5. Consequences & Trade-Offs
- **Positive:** deload weeks are deload weeks on both profiles; the ACWR floor no longer pushes extra running into recovery weeks.
- **Negative / Operational Cost:** a deload week may now contain no key session / no long keystone session (OWNER DECISION, reported in the PR); a deload week may fall below acute:chronic 0.80 without a reported relaxation.
- **When to Revisit:** if the structure floors become a modelled Tier-2 trade-off, or `TASK-023` walk-jog candidates change the floor economics.

### `ADR-023` — Deload weeks carry no key minimum, anchor obligation or running-ACWR floor charge [`ACTIVE` | `optimizer`]

# `ADR-023`: Deload weeks carry no key minimum, anchor obligation or running-ACWR floor charge

## 1. Context & Problem
After `TASK-034` (deload structure relief) `EXP-007` still showed recovery weeks up to +68 TSS (`CURRENT_ATHLETE`) / +244 (unconstrained reference) and taper/race weeks up to +44 / +275 over target. Single-week ablations on the real library (re-solving recorded `WeekRecord` configs and carry-ins) found three independent Tier-1 charges, each minimised BEFORE the soft Tier-2 TSS goal:
1. **Key-session minimum >= 1** (`CURRENT_ATHLETE`): the cheapest hard session forces ~160-170 TSS in a 72-141 target week (key min 0 alone: weeks 8-39 within +10 of target; stride min 0 alone did not help).
2. **Mandatory anchors** (reference profile, whose caps can host a 180 min ride and 75 min run): `shorten_or_drop_anchor` forces them every week (floor ~350-400 vs 186-220 target; dropping the obligation alone: reference recovery +0..+3).
3. **Running-ACWR 0.80 floor waiver** (`ADR-019`, binary Tier-1 slack of weight 1): whenever any plan can hold `A_d >= 0.8 C_d` the solver pays for extra running instead of waiving, although a deload week is *defined* by acute < chronic load. Reference recovery solved 275/269/269 vs 186/203/220 with the carry-in, 187/203/223 with the floor removed (`nocarry` and `nofloor` ablations coincide; CTL-ramp ablation changed nothing).
The reference swim minimum of 2 (not scaled by `TASK-034`) additionally held its race week +48.

## 2. Considered Alternatives
1. **Option A (Rejected): retarget deload weeks to the floor.** The floors (166-275 TSS) are larger than the build week they follow in several weeks; it would defeat the recovery week.
2. **Option B (Rejected): loosen/rank the ACWR floor in every week.** The floor stays in build weeks (`ADR-019`); only deload roles are exempt.
3. **Option C (ADOPTED): remove each charge in deload roles only.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `control/periodization.py` (`apply_deload_relief`: key min 0, Swim joins Bike/Run halving; `relax_deload_anchors`; `relax_deload_acwr_floor`), `control/season.py` (applied to the week library and the carry-in handed to the solver; the carry handed to the next week is the original).
- **Locked Parameters / Contract:** roles recovery/taper/race only. Anchors stay in the library as ordinary optional candidates. `LoadState.acwr_min` is set to 0 for the solve (no floor row, no waiver); the running-ACWR ceiling, boundary rows, CTL ramp, run-minutes guard, every cap and every maximum are untouched.
- **Enforcement Mechanism:** `tests/test_season_periodization.py` (relief only in deload roles; short 14-week `CURRENT_ATHLETE` season, deload weeks <= target + 25); slow `tests/test_season_validation.py::test_full_matrix_season` asserts recovery and taper/race max over-target <= 25 TSS and 0 violations for all 8 seasons.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` matrix re-run: recovery max over +68/+244 -> +8/+4, taper/race +44/+275 -> +11/+14 (see `TASK-038` archive entry). 0 independent Tier-0 violations in all 8 seasons.
- **Supersedes:** None; completes `TASK-034`.

## 5. Consequences & Trade-Offs
- **Positive:** deload weeks are deload weeks on both profiles; the ACWR floor no longer pushes extra running into recovery weeks.
- **Negative / Operational Cost:** a deload week may now contain no key session / no long keystone session (OWNER DECISION, reported in the PR); a deload week may fall below acute:chronic 0.80 without a reported relaxation.
- **When to Revisit:** if the structure floors become a modelled Tier-2 trade-off, or `TASK-023` walk-jog candidates change the floor economics.

---

## Part 2: Operational Heuristics
### `HEUR-001` — Guard Against Python Multiprocessing Worker Deadlocks on Windows in Parallel HiGHS Workers [`ACTIVE` | `perf`]

# `HEUR-001`: Guard Against Python Multiprocessing Worker Deadlocks on Windows in Parallel HiGHS Workers

- **Target Subsystem / Path:** `src/triplan/perf/stress.py:stress_test_suite()` (`ProcessPoolExecutor`, `TASK-032`) and any future process-parallel runner. The original target, `optimize_weekly_workouts_multiseed()`, was retired by `ADR-018`.
- **Symptom / Failure Mode:** Process hangs indefinitely on Windows when spawning multiprocessing workers, or throws `TypeError: cannot pickle 'highspy._core.Highs' object`.
- **Root Physics / Mechanism:** On Windows, Python uses `spawn` instead of `fork` to start worker processes. Any arguments passed across process boundaries must be serialized via `pickle`. C++ wrapped pointers inside `highspy.Highs` cannot be pickled across IPC channels.
- **Permanent Operational Rule:** Never pass instantiated `Highs` solver objects into `multiprocessing.Pool` or `Process`. Submit only top-level, picklable functions with sanitized `pandas.DataFrame` / dataclass / dict arguments, guard entry points with `if __name__ == "__main__"`, and instantiate the local `highspy.Highs()` solver instance entirely inside the target worker function.
- **Verification Check:** `TRIPLAN_STRESS_WORKERS=2 python -m triplan.perf.stress` completes without hanging (spawn start method).

### `HEUR-002` — Prevent Orthopedic Dissociation Index Spikes in Build Cycles [`ACTIVE` | `safety`]

# `HEUR-002`: Prevent Orthopedic Dissociation Index Spikes in Build Cycles

- **Target Subsystem / Path:** `closed_loop_optimizer.py:MesoCycleTargetsAndConstraints.max_weekly_run_mech_strain`.
- **Symptom / Failure Mode:** Athlete reports sudden Achilles tendinopathy or shin splints in week 6-8 of build phase despite low cardiovascular fatigue and positive HRV scores.
- **Root Physics / Mechanism:** Cardiovascular fitness ($\tau_m=35d$) adapts 3.4x faster than collagenous tendon/bone tissue ($\tau_c=120d$). As aerobic thresholds rise, athletes feel capable of absorbing high mileage, driving the Orthopedic Dissociation Index ($ODI$) above 1.35 and causing collagen micro-tearing before synthesis completes.
- **Permanent Operational Rule:** Never allow weekly running mechanical strain to increase by more than 10% between consecutive step-cycle loading weeks, regardless of how high aerobic CTL climbs. Maintain $ODI \le 1.35$.
- **Verification Check:** `python -c "from closed_loop_optimizer import MesoCycleTargetsAndConstraints; cfg=MesoCycleTargetsAndConstraints(); assert cfg.max_weekly_run_mech_strain <= 350.0"`.

### `HEUR-003` — Specify Openpyxl Engine Explicitly in pandas read_excel [`ACTIVE` | `workouts`]

# `HEUR-003`: Specify Openpyxl Engine Explicitly in pandas read_excel

- **Target Subsystem / Path:** `util.py:load_workouts()`, `clean_file.py`.
- **Symptom / Failure Mode:** `xlrd.biffh.XLRDError: Excel xlsx file; not supported` during workout library loading.
- **Root Physics / Mechanism:** Modern versions of `xlrd` (>=2.0.0) removed support for `.xlsx` files and only support legacy `.xls`. If pandas cannot infer or defaults to `xlrd`, reading `Run.xlsx` or `Bike.xlsx` crashes immediately.
- **Permanent Operational Rule:** Every call to `pd.read_excel()` on `.xlsx` files must explicitly specify `engine='openpyxl'`.
- **Verification Check:** `python -c "import pandas as pd; pd.read_excel('Run.xlsx', engine='openpyxl')"`.

### `HEUR-004` — Axial Spinal Fatigue and Running Impact Interaction: Enforcing 24h Buffer Between Heavy Deadlifts and Threshold/Long Runs [`ACTIVE` | `safety`]

# `HEUR-004`: Axial Spinal Fatigue and Running Impact Interaction: Enforcing 24h Buffer Between Heavy Deadlifts and Threshold/Long Runs

- **Target Subsystem / Path:** `src/closed_loop_optimizer.py` (`HybridAthleteMILPOptimizer`), `src/classes.py` (`HybridVariables`).
- **Symptom / Failure Mode:** Acute lumbar erector cramping, sacroiliac joint destabilization, disc herniation risk, hamstring strain during late run intervals, and severe biomechanical breakdown under repetitive running impact ($2.5-3.0\times$ BW).
- **Root Physics / Mechanism:** Heavy conventional and Romanian deadlifts impose up to $18.45\text{ kN}$ compressive force and $3.28\text{ kN}$ shear load on the L4/L5 lumbar spine. Following heavy deadlifting, lumbar muscular stabilization and proprioception are severely depressed for 24–48 hours. Running ground reaction forces ($160-180\text{ strides/min}$) then transfer directly to the passive spinal column rather than active musculature, inducing catastrophic shear stress. Conversely, attempting heavy deadlifts with running-fatigued posterior stabilizers leads to spinal flexion under load.
- **Permanent Operational Rule:**
  1. **Zero Same-Day Pairing:** Heavy deadlift workouts and threshold/long run workouts must NEVER share a calendar day ($x_{i,d} + x_{j,d} \le 1$).
  2. **24-Hour Adjacent Buffer:** Heavy deadlift workouts and threshold/long run workouts must NEVER be placed on consecutive calendar days ($x_{i,d} + x_{j,d+1} \le 1$ and $x_{i,d+1} + x_{j,d} \le 1$).
  3. **Synergistic Stacking:** Deadlifts should be stacked on days with swimming sessions (hydrostatic pressure and horizontal buoyancy decompress the spine while promoting active recovery) or on dedicated lower-body days separated from running.
- **Verification Check:**
  `python -c "import sys; sys.path.insert(0, 'src'); from closed_loop_optimizer import HybridAthleteMILPOptimizer; opt = HybridAthleteMILPOptimizer(); res = opt.optimize(); assert res['status'] in ('OPTIMAL', 'FEASIBLE') and res['deadlift_run_conflicts'] == 0, 'Deadlift-Run conflict detected'"`

### `HEUR-005` — Disambiguate Stryd CIQ Developer Power Field and Enforce ANT+ Pairing [`ACTIVE` | `integrations`]

# `HEUR-005`: Disambiguate Stryd CIQ Developer Power Field and Enforce ANT+ Pairing

- **Target Subsystem / Path:** `src/clean_file.py`, `src/athlete_profile.py`, Garmin Enduro 3 sensor pairing.
- **Symptom / Failure Mode:** Running power in `.fit` files reads 20% to 30% higher than Stryd values (e.g., 340W native vs 270W Stryd), or Stryd foot pod fails to connect to the Garmin Enduro 3 watch prior to a run.
- **Root Physics / Mechanism:**
  1. Garmin does not use a native ANT+ Running Power profile for third-party foot pods. The `Stryd Zones` Connect IQ data field writes Stryd wattage into a Connect IQ developer field named `'Power'` (capital `P`), while Garmin native wrist running power writes to standard field `'power'` (lowercase `p`). Naive iteration over `record` fields overwrites or confuses the two streams.
  2. Stryd Model 17 (2019 "Wind" release) features a single-channel Bluetooth LE chip. If a mobile phone app holds the BLE connection, a watch attempting BLE pairing is locked out.
- **Permanent Operational Rule:**
  1. In `.fit` parsers, running power MUST be extracted where `field.is_dev == True` and `field.name == 'Power'`.
  2. Also extract developer fields `Form Power`, `Leg Spring Stiffness` (LSS, kN/m), and `Air Power`.
  3. Turn Garmin Native Running Power `Off` in Enduro 3 Run Settings (`Activities & Apps -> Run -> Run Settings -> Running Power -> Off`).
  4. Pair Stryd Model 17 to Enduro 3 strictly via **ANT+** (`FP-XXXXX`), setting Pace & Distance = Always.
- **Verification Check:**
  `python -c "from src.clean_file import parse_fit_power; assert parse_fit_power('sample_run.fit')['source'] == 'stryd_dev_field'"`

### `HEUR-006` — Tacx Neo 2T Incline-Mode Testing and Invalidate Left-Right Balance [`ACTIVE` | `integrations`]

# `HEUR-006`: Tacx Neo 2T Incline-Mode Testing and Invalidate Left-Right Balance

- **Target Subsystem / Path:** `src/clean_file.py`, Tacx Training/Utility app settings, smart trainer testing protocols.
- **Symptom / Failure Mode:**
  1. Erg-mode resistance clamps athlete cadences during maximal 3-minute or 12-minute CP tests, causing early failure ("spiral of death").
  2. Tacx Neo 2T unexpectedly shuts down and drops ANT+ FE-C / BLE connections during the 30-minute recovery period between test intervals.
  3. Telemetry analysis falsely flags a severe single-leg strength deficit due to erratic balance numbers.
- **Root Physics / Mechanism:**
  1. The Neo 2T motor brake has $\pm 1\%$ accuracy and auto-calibrates without spin-down. However, ERG mode prevents self-paced pacing required for Monod-Scherrer CP testing.
  2. When operated unplugged, the Neo 2T relies on pedaling to power its electronics. When pedaling ceases for $>1-2$ minutes during recovery, the unit powers down completely.
  3. The Neo 2T estimates `left_right_balance` via internal inductive stator coil sensors rather than direct pedal-spindle strain gauges. Dynamic cadence and torque transients create spurious sensor spikes despite total wattage accuracy.
- **Permanent Operational Rule:**
  1. Never perform spin-down calibrations on Tacx Neo 2T.
  2. Execute all 3-min/12-min CP tests and Kolie Moore tests in **Slope/Incline Mode** (+1.5% to +2.0% fixed grade) with **Road Feel = Off** and **Downhill Drive = Off**.
  3. Keep the external AC power brick plugged in at all times during workouts and tests with rest intervals.
  4. Explicitly ignore and discard the `left_right_balance` stream from Neo 2T `.fit` files.
- **Verification Check:**
  `python -c "from src.clean_file import clean_bike_fit; df = clean_bike_fit('sample_ride.fit'); assert 'left_right_balance' not in df.columns or df['left_right_balance'].isna().all()"`

### `HEUR-007` — Pool Swim Active Length Parsing and Multi-Device Deduplication Window [`ACTIVE` | `integrations`]

# `HEUR-007`: Pool Swim Active Length Parsing and Multi-Device Deduplication Window

- **Target Subsystem / Path:** `src/clean_file.py`, `src/sync_8020_to_tp.py`, pool swim and multi-device ride ingestion.
- **Symptom / Failure Mode:**
  1. Pool swim duration and sTSS are severely inflated (by 20% to 50%), distorting weekly aerobic load and failing 80/20 intensity ratios.
  2. Weekly TSS is doubled when an athlete records a ride simultaneously on both a Garmin Edge 530 bike computer and a Garmin Enduro 3 watch.
  3. SWOLF calculations and average stroke rate collapse or yield divide-by-zero errors when swim drill logs are present.
- **Root Physics / Mechanism:**
  1. Garmin Swim 2 and Enduro 3 pool swim files store individual laps as `length` messages. With Auto-Rest enabled, `session.total_elapsed_time` includes all rest seconds at the lane wall.
  2. Drills (`swim_stroke == 'drill'` or `event == 'drill'`) record zero stroke counts (`total_strokes == None/0`), corrupting stroke efficiency metrics if not filtered out.
  3. Athletes frequently record rides on an Edge 530 (for handlebar cockpit viewing) and an Enduro 3 (for Garmin Unified Training Status). Syncing both creates duplicate activities in TrainingPeaks.
- **Permanent Operational Rule:**
  1. In pool swim `.fit` parsing, compute active swim duration strictly by summing `length` messages where `length_type == 'active'`.
  2. Filter out lengths where `swim_stroke == 'drill'` from SWOLF and stroke rate averages, but retain their duration in low-intensity aerobic swim time.
  3. Ingest only ONE `.fit` file per activity when start timestamps match within 300 seconds (`abs(start_time_A - start_time_B) < 300s`), prioritizing Edge 530 for cycling data quality.
- **Verification Check:**
  `python -c "from src.clean_file import deduplicate_fit_uploads, parse_pool_swim; assert deduplicate_fit_uploads(files) and parse_pool_swim('swim.fit')['active_only']"`

