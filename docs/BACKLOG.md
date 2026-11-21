# Engineering Backlog — Full Task Specifications (`docs/BACKLOG.md`)

> **On-demand, not part of the startup core.** `docs/TASKS.md` is the one-line queue index; this file holds each open task's full specification (line references, formulas, acceptance criteria). Read the entry for a task before starting it. When a task closes, delete its entry here and archive it to `docs/archive/TASKS_COMPLETED.md`.
> **Architectural Directive:** The three MILP solvers are consolidated into **ONE unified engine** (`src/triplan/solver/engine/`, `ADR-017`, `TASK-015`). New rules are builder modules on that engine.
> Line references predate the `ADR-015` package split unless noted (`closed_loop_optimizer.py` → `src/triplan/{solver,control,physiology}/`).

## Phase 3: Physiology Calibration & Periodization

## Phase 4: Telemetry, Readiness & Closed-Loop Integrations

*(No open tasks; `TASK-002` archived to `docs/archive/TASKS_COMPLETED.md`.)*

---

## Queued Empirical Experiments (`docs/experiments/`)

*(No open experiments; see `docs/experiments/INDEX.md` and `docs/archive/TASKS_COMPLETED.md`.)*
