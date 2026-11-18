# Domain Lode: `general`

## 1. Scope & Responsibility
Cross-cutting system architecture, src-layout package boundaries (`ADR-015`), repository-anchored paths (`TASK-031`), progressive-disclosure context governance (`ADR-001`, `ADR-016`), documentation/PR workflow, and foundational exercise physiology literature grounding.

## 2. Key Modules & Entrypoints
- [`GEMINI.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/GEMINI.md) (and [`.agents/AGENTS.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/AGENTS.md), [`.agents/GEMINI.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/GEMINI.md), [`.agents/rules/operating_charter.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/rules/operating_charter.md)): Agent operating charter, guardrails, PR workflow, and documentation-sync rules.
- [`src/triplan/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/): Installable src-layout package (`model/`, `library/`, `solver/`, `physiology/`, `control/`, `io/`, `perf/`) with [`cli.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/cli.py) (`triplan` / `python -m triplan`) and [`paths.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/paths.py) (`REPO_ROOT`, `CONFIG_DIR`, `DATA_DIR`, `TRAINING_PLANS_DIR`, `training_plan_path()`).
- [`pyproject.toml`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/pyproject.toml) & [`requirements.txt`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/requirements.txt): `pyproject.toml` is the sole dependency source (`test` and `garmin` extras); `requirements.txt` is a thin `-e .[test,garmin]` pointer.
- [`config/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/config/): Authoritative [`athlete_profile.yaml`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/config/athlete_profile.yaml) and gitignored credential files (`config/.env`, `config/trainingpeak.env`, `config/garmin.env`).
- [`scripts/lodectx.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/lodectx.py) & [`docs/lode.json`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/lode.json): Zero-dependency SQLite WAL lineage engine, BM25 FTS5 search, domain briefings, and deterministic doc renderer.
- [`docs/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/): Startup core (`STATE_SUMMARY.md`, `LODE_MAP.md`, `INVARIANTS.md`, `TASKS.md`, plus root [`SPEC.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/SPEC.md)), full open-task specs in [`docs/BACKLOG.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/BACKLOG.md), completed archive in [`docs/archive/TASKS_COMPLETED.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/docs/archive/TASKS_COMPLETED.md), domain references in `docs/domains/`, ADRs/heuristics in `docs/lode/`, PRDs in `docs/specs/`, and experiments in `docs/experiments/`.

## 3. Core Architectural Rules & Workflows

### A. Repository Layout & Path Resolution (`ADR-007`, `ADR-015`, `TASK-031`)
- **Clean Layout:** Source code lives in `src/triplan/`, configuration in `config/`, canonical and raw data in `data/` (personal telemetry and copyrighted literature in `data/` and `references/` are gitignored), CLI tools/migrations in `scripts/`, generated plans in `training_plans/`, tests in `tests/`, and documentation in `docs/`. Never write transient artifacts to the repository root.
- **Cross-Platform, CWD-Independent Paths:** All file I/O resolves through `triplan.paths` (`REPO_ROOT` anchored from `paths.py`) and `pathlib.Path` rather than current-working-directory relative paths, ensuring `triplan` and `scripts/*.py` work identically on Windows and Linux from any working directory.
- **Immutability & Strict Typing:** Model boundaries use frozen dataclasses / Pydantic models (`AthleteProfile`, `Workout`, `CandidateWorkout`) and explicit `typing` annotations; solvers deep-copy mutable inputs rather than mutating caller configs.

### B. Progressive Disclosure & Documentation Sync Workflow (`ADR-001`, `ADR-016`)
1. **Startup Core Budget (< 7,500 Tokens):** `docs/STATE_SUMMARY.md` + `docs/LODE_MAP.md` + `docs/INVARIANTS.md` + `docs/TASKS.md` + `SPEC.md` must stay under 7,500 estimated tokens (`python scripts/lodectx.py stats`). `TASKS.md` stays one line per open item; detailed specifications live in `docs/BACKLOG.md` and completed outcomes in `docs/archive/TASKS_COMPLETED.md`.
2. **Domain Lodes (`docs/domains/*.md`):** Each domain file serves two roles:
   - **Narrative Architecture & Workflow (human-maintained above the `LODE_AUTOGEN` markers):** Explains what the subsystem does, which modules own which steps, and how workflows execute end-to-end so readers can understand system behavior without reading source code. Whenever a task, PRD, ADR, or experiment changes module responsibilities, data flows, solver constraints, or user-visible behavior, the corresponding `docs/domains/<domain>.md` (and `STATE_SUMMARY.md` / `INVARIANTS.md` / `SPEC.md` if top-level architecture or invariants change) is updated in the same PR.
   - **Registered Lineage Footer (`<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs; edit narrative sections above, not this block. -->

## Registered Lineage (`general`)

### Active Architectural Decisions (`ADR`)
- **`ADR-001` — Adopt Progressive Disclosure & SQLite Lineage Architecture:** Replace monolithic memory files with <7,500 token startup core and an append-only SQLite WAL lineage engine.
- **`ADR-007` — Modularize Repository Layout, Data Assets, and Reference Artifacts:** Organize repository assets into src/, config/, .agents/, data/, references/, scripts/, and training_plans/, eliminating root-level pollution.
- **`ADR-015` — Adopt src-layout triplan package with domain subpackages:** All code lives in the src/triplan package (model, library, solver, physiology, control, io, perf); import triplan.* absolutely, never sys.path hacks.
- **`ADR-016` — Scope the Startup Token Budget to the Charter's Read List:** Startup core = STATE_SUMMARY + LODE_MAP + INVARIANTS + TASKS + SPEC.md < 7,500 tokens; charter excluded; task specs live in docs/BACKLOG.md.
<!-- LODE_AUTOGEN_END -->`):** Auto-rendered deterministically from `docs/.context/lode.db` by `python scripts/lodectx.py sync-docs`.
3. **Standard Change Lifecycle:**
   - Branch off `master` (`<type>/<TASK-ID>-<short-slug>`).
   - Brief (`python scripts/lodectx.py brief <domain>`), scaffold/ingest any PRD, ADR, HEUR, or EXP (`lodectx.py init` / `ingest`), implement and verify (`pytest` + `season_validation` where applicable).
   - Update affected `docs/domains/<domain>.md`, `docs/TASKS.md`, and `docs/archive/TASKS_COMPLETED.md`, run `python scripts/lodectx.py sync-docs` and `python scripts/lodectx.py stats`, and open/merge the PR.

### C. Foundational Literature Grounding
- **Joe Friel (*The Triathlete's Training Bible*):** 5-2 rule, abilities taxonomy (`AE`, `TE`, `ME`, `AC`, `SS`, `MF`), periodization phases (`AA -> MT -> MS -> SM`), and missed-workout absorption rules.
- **Jim Vance (*Triathlon 2.0*):** Separate Bike PMC vs Run PMC, Vance Quadrant Analysis (`Q1`–`Q4`), cadence/power fade metrics, and running power pacing rules off the bike.
- **Patrick Hagerman (*Strength Training for Triathletes*):** Big-before-small, multi-joint compound movement ordering, heavy low-volume resistance protocols (85%–90% 1RM) paired with low-volume plyometrics for tendon stiffness.
- **Matt Fitzgerald (*80/20 Running*, *Triathlon Science*):** 80/20 intensity distribution, moderate-intensity rut avoidance, and systemic IL-6 inflammation from consecutive glycogen-depleting days.
- **Stephen Seiler:** 3-zone intensity demarcation, polarized training models, and post-exercise autonomic parasympathetic reactivation (`RMSSD`) threshold behavior at $LT_1$.
- **Ed Maunder et al. (2021):** Physiological durability (fatigue resistance) and threshold degradation under prolonged mechanical work (`> 1,500–2,000 kJ`).

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs -->
<!-- LODE_AUTOGEN_END -->
