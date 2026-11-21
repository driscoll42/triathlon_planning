# Engineering Backlog — Full Task Specifications (`docs/BACKLOG.md`)

> **On-demand, not part of the startup core.** `docs/TASKS.md` is the one-line queue index; this file holds each open task's full specification (line references, formulas, acceptance criteria). Read the entry for a task before starting it. When a task closes, delete its entry here and archive it to `docs/archive/TASKS_COMPLETED.md`.
> **Architectural Directive:** The three MILP solvers are consolidated into **ONE unified engine** (`src/triplan/solver/engine/`, `ADR-017`, `TASK-015`). New rules are builder modules on that engine.
> Line references predate the `ADR-015` package split unless noted (`closed_loop_optimizer.py` → `src/triplan/{solver,control,physiology}/`).

## Phase 3: Physiology Calibration & Periodization

## Phase 4: Telemetry, Readiness & Closed-Loop Integrations
- [ ] **`TASK-025` (`mpc`):** **Actuals Store, Unified Morning Readiness ($\alpha_d$) & Receding-Horizon Re-Solve** — Store completed activities with 7-zone TiZ, TSS, and strain to advance `AthletePhysiologicalState` from actuals instead of planned workouts; fix `calculate_garmin_morning_readiness` bug (`:1761` dividing RHR deviation by `rmssd_baseline_std * 0.5` instead of RHR SD); unify the two readiness scalars into a single $\alpha_d \in [0.60, 1.00]$ wired to `data/garmin/wellness/hrv_summaries.json` (absorbs `TASK-003`); add intra-week receding-horizon re-solve fixing completed days $d < \text{today}$ and re-optimizing remaining days.
- [ ] **`TASK-003` (`mpc`):** *[Merged into `TASK-025`]* Integrate morning HRV RMSSD, RHR baseline SD, and sleep telemetry into unified $\alpha_d$ readiness scaling and intra-week re-solve.
- [ ] **`TASK-002` (`workouts`):** **Swim Structured Workout Ingestion & TrainingPeaks Export** — Ingest and calibrate 80/20 swimming structured workouts into TrainingPeaks JSON format; enforce 1.0–3.0s random jitter rate limiting in both `src/sync_8020_to_tp.py:617` (currently fixed 0.5s) and `scripts/sync_garmin_data.py` (currently 0.05–0.12s); wire `evaluate_cp_retest_triggers` to real parsed decoupling/EF telemetry.

---

## Queued Empirical Experiments (`docs/experiments/`)

- [ ] **`EXP-003` (`perf`):** **Solver Correctness, Sparse Build & Dominance Pruning Scaling (Run First — Retests `EXP-001`)** — Benchmark `addRow` numpy construction + dominance/cluster candidate pruning across 40, 80, 160, 324, and synthetic 500 workouts; compare 15-seed $\times$ 2-thread race vs single deterministic solve at equal CPU budget; verify monotone Top-5 objectives and p95 $\le 2\text{s}$ at $\le 1\%$ gap.
- [ ] **`EXP-001` (`perf`):** **[`NEEDS_RETEST` via `EXP-003`]** Benchmark 15-seed $\times$ 2-thread HiGHS race vs single-seeded branching search on full candidate space (audit observed 8.1s timeouts and non-monotone Top-5 scores).
- [ ] **`EXP-004` (`optimizer`):** **Goal-Weight Sensitivity & Session-Structure Constraints** — Sweep 81 combinations ($\times 0.25, \times 1, \times 4$) of dimensionless soft-goal weights across 300/500/700 TSS with and without key-session ($2 \le \text{key} \le 3$) and grey-zone pre-filtering constraints; establish Pareto frontier and default weights.
- [ ] **`EXP-005` (`integrations`):** **FIT Ingestion Parser & Multi-Device Validation** — Validate `src/fit_ingest.py` against synthetic and real `.fit` fixtures covering Stryd developer `Power`, active swim lengths, 300s ride deduplication, Neo 2T `left_right_balance` exclusion, and treadmill `Air Power = 0`.
- [ ] **`EXP-006` (`physiology`):** **Load-Model Calibration Against Athlete's Historical Garmin & Lifting Telemetry** — Calibrate planned vs actual zone midpoints, Banister PMC $\alpha = 1 - e^{-1/\tau}$ vs $2/(N+1)$ divergence, running mechanical strain exponent $\beta \in \{1, 1.5, 2, 3\}$, and 28-day rolling HRV/RHR readiness z-score mapping across the 2,020 local Garmin activities and 270 HRV nights on disk.
