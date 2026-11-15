---
id: ADR-007
type: adr
domain: general
title: "Modularize Repository Layout, Data Assets, and Reference Artifacts"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
affects_services: ["general", "workouts", "integrations"]
one_liner: "Organize repository assets into src/, config/, .agents/, data/, references/, scripts/, and training_plans/, eliminating root-level pollution."
---

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
