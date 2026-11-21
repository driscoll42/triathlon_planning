# Engineering Backlog — Full Task Specifications (`docs/BACKLOG.md`)

> **On-demand, not part of the startup core.** `docs/TASKS.md` is the one-line queue index; this file holds each open task's full specification (line references, formulas, acceptance criteria). Read the entry for a task before starting it. When a task closes, delete its entry here and archive it to `docs/archive/TASKS_COMPLETED.md`.
> **Architectural Directive:** The three MILP solvers are consolidated into **ONE unified engine** (`src/triplan/solver/engine/`, `ADR-017`, `TASK-015`). New rules are builder modules on that engine.
> Line references predate the `ADR-015` package split unless noted (`closed_loop_optimizer.py` → `src/triplan/{solver,control,physiology}/`).

## Phase 3: Physiology Calibration & Periodization

## Phase 4: Telemetry, Readiness & Closed-Loop Integrations

*(No open tasks; `TASK-002` archived to `docs/archive/TASKS_COMPLETED.md`.)*

---

## Queued Empirical Experiments (`docs/experiments/`)

- [ ] **`EXP-002` (`safety`):** **Closed-Loop 16-Week Season Simulation Under Real Profile and Perturbations** — Verify 16-week closed-loop season simulation on `CURRENT_ATHLETE` (`config/athlete_profile.yaml`) via `generate_closed_loop_season_schedule` and `scripts/stress_test_season_perturbations.py` under nominal, illness, pool-closure, travel, and $n=20$ Monte Carlo stochastic execution noise regimes.
- [ ] **`EXP-005` (`integrations`):** **FIT Ingestion Parser & Multi-Device Validation** — Validate `src/fit_ingest.py` against synthetic and real `.fit` fixtures covering Stryd developer `Power`, active swim lengths, 300s ride deduplication, Neo 2T `left_right_balance` exclusion, and treadmill `Air Power = 0`.
- [ ] **`EXP-006` (`physiology`):** **Load-Model Calibration Against Athlete's Historical Garmin & Lifting Telemetry** — Calibrate planned vs actual zone midpoints, Banister PMC $\alpha = 1 - e^{-1/\tau}$ vs $2/(N+1)$ divergence, running mechanical strain exponent $\beta \in \{1, 1.5, 2, 3\}$, and 28-day rolling HRV/RHR readiness z-score mapping across the 2,020 local Garmin activities and 270 HRV nights on disk.
