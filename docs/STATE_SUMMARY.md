# System State Summary (`docs/STATE_SUMMARY.md`)

## 1. System Overview & Architecture
The **Triathlon Workout & Season Planner** is a high-performance mathematical modeling and closed-loop scheduling platform. It combines empirical **80/20 endurance principles** (Seiler, Fitzgerald, Friel) and **Hybrid Athlete strength science** with **Mixed-Integer Linear Programming (MILP)** and **Model Predictive Control (MPC)** to synthesize optimal, injury-resistant multi-sport schedules balancing Running, Cycling, Swimming, and Barbell/Dumbbell lifting.

- **Primary Stack:** Python 3.14+, `highspy` (HiGHS C++ MILP solver), `pandas`, `numpy`, `openpyxl`.
- **Integrations:** TrainingPeaks Private REST API, Garmin Health API / Connect, 80/20 Endurance Library scraper. *(Recorded-activity FIT parsing is specified in `SPEC.md`/`ADR-010` but **not yet implemented** — `TASK-024`.)*
- **Lineage & Context Engine:** SQLite WAL + FTS5 BM25 (`docs/.context/lode.db`) managed via `scripts/lodectx.py`.
- **Primary Data Sources:** `data/workout_library.csv` (single validated workout library, 7-zone, explicit flags; `ADR-014`) and `config/athlete_profile.yaml` (validated athlete profile). `data/{Run,Bike,Swim}.xlsx` are the raw 80/20 scrape cache, read only at build time.

## 2. Core Subsystems & Module Boundaries

| Subsystem | Primary Modules | Key Responsibilities |
| :--- | :--- | :--- |
| `optimizer` | `src/triplan/solver/highs_optimization.py`, `scheduler.py`, `hybrid.py` | HiGHS MILP formulations, candidate pruning, multi-seed parallel race, and `HybridAthleteMILPOptimizer` (4-sport balancing, equipment set partitioning, biomechanical conflict matrix, Canonical No-Good Integer Cuts). |
| `mpc` | `src/triplan/control/mpc.py`, `control/season.py` | 3-tier closed-loop Model Predictive Control: Tier 1 Garmin morning readiness scalar ($\alpha_d$), Tier 2 e1RM Kalman filtering, Tier 3 3:1 mesocycle wave undulation. |
| `physiology` | `src/triplan/physiology/athlete_profile.py`, `state.py`, `model/classes.py` | Dual-load modeling (Aerobic TSS vs Mechanical Strain), ANS costing ($e^{0.65(z-1)}$), asynchronous tissue remodeling ($\tau=35d$ vs $\tau=120d$), and movement-pattern tracking (Anterior/Posterior Chain, Push, Pull). |
| `workouts` | `src/triplan/library/util.py`, `loader.py`, `model/classes.py` | 80/20 workout library loading, canonical 80/20 7-zone power/pace/CSS conversions, duration and TSS standardization, brick synthesis, and hybrid strength library (15 blueprint templates). |
| `integrations` | `src/triplan/io/trainingpeaks.py`, `src/triplan/library/clean_file.py` | TrainingPeaks private API sync, workout structure serialization, and 80/20 HTML library scraping (`clean_file.py` parses workout *definitions*, not recorded activities). Multi-device activity FIT parser (Stryd CIQ developer power, swim active lengths, 300s dedup) is **planned, not implemented** (`TASK-024`, input `data/garmin/activities/fit_files/`). |
| `safety` | `src/triplan/solver/safety_constraints.py`, `scheduler.py` | Single-run volume ceiling ($\le 32\%$), 24h deadlift-run buffer, axial spinal load caps ($X_{\text{day}} \le 120, X_{\text{wk}} \le 320$), neuromuscular fatigue caps, rolling 48h mechanical strain caps, mechanical ACWR $\le 1.30$, ODI $\le 1.35$. |
| `perf` | `src/triplan/perf/stress.py` | Parallel core scaling benchmarks (1-32 cores), solver timeout enforcement, and scenario resilience testing (11 stress scenarios). |

## 3. Data Pipeline & Optimization Flow
```mermaid
flowchart TD
    A["Canonical Library (data/workout_library.csv, Pandera+Pydantic)"] --> B["library.loader + Pruning"]
    B --> C["Candidate Library (CandidateWorkout)"]
    D["Athlete Profile (config/athlete_profile.yaml)"] --> E["MPC Controller & Targets (src/triplan/control/mpc.py)"]
    C & E --> F["HiGHS MILP Formulation (highspy)"]
    F --> G["Status-Checked Solve (highs_runtime) / No-Good Cuts"]
    G --> H["Feasible Weekly Schedule (Top-K Plans)"]
    H --> I["Biomechanics & Remodeling Validation (ACWR, ODI)"]
    I --> J["TrainingPeaks REST API Sync (src/triplan/io/trainingpeaks.py)"]
```

## 4. Directory & Repository Topology
- `src/triplan/`: src-layout package (ADR-015; `pip install -e ".[test]"`): `model/` (`candidates.py`, `classes.py`), `library/` (`schema.py`, `build.py`, `loader.py`, `candidates.py`, `clean_file.py`, `util.py`), `solver/` (`scheduler.py`, `hybrid.py`, `pruning.py`, `highs_optimization.py`, `safety_constraints.py`, `highs_runtime.py` (process-wide HiGHS threads + status-checked solves)), `physiology/` (`athlete_profile.py`, `state.py`), `control/` (`mpc.py`, `season.py`), `io/` (`trainingpeaks.py`), `perf/` (`stress.py`), `cli.py`.
- `tests/`: pytest suite (`python -m pytest`): stress-scenario safety assertions, profile contract, HiGHS runtime, workout loading. Known engine defects are strict/non-strict `xfail`s naming their owning task.
- `config/`: Secure environment files (`.env.example`, `trainingpeak.env`, `garmin.env`). All secret `.env` files are gitignored.
- `.agents/`: Agent charters and operational rules (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `rules/operating_charter.md`).
- `data/`: `workout_library.csv` (canonical workout library), raw 80/20 scrape cache (`Run.xlsx`, `Bike.xlsx`, `Swim.xlsx`, `workout_library.html`), and gitignored telemetry (`garmin/`, `lifting/`, `fit_files/`).
- `docs/`: Progressive disclosure core, domain briefing lodes, ADRs, PRDs, heuristics, hardware/threshold specification (`SPEC.md`), and SQLite lineage DB (`.context/lode.db`).
- `references/`: Empirical endurance literature, textbooks, and physiological research documents (large binaries gitignored).
- `scripts/`: Operational tools, SQLite lineage engine (`lodectx.py`), core scaling benchmarks, and multi-plan simulation comparators.
- `training_plans/`: Periodized season output schedules (`.csv`) and canonical race distance specifications (`.md`).
- Project Root: Packaging specification (`pyproject.toml`), dependencies (`requirements.txt`), telemetry specification (`SPEC.md`), repository README (`ReadMe.md`), and `.gitignore`.

