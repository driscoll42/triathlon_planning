---
id: ADR-015
type: adr
domain: general
title: "Adopt src-layout triplan package with domain subpackages"
status: ACTIVE
created_at: 2026-11-16
supersedes: []
validated_by: []
affects_services: ["general", "optimizer", "mpc", "physiology", "workouts", "integrations", "safety", "perf"]
one_liner: "All code lives in the src/triplan package (model, library, solver, physiology, control, io, perf); import triplan.* absolutely, never sys.path hacks."
---

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
