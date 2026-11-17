# Active Engineering Queue (`docs/TASKS.md`)

> One line per open item. **Full specs live in `docs/BACKLOG.md`**; read a task's entry there before starting it. Completed work: `docs/archive/TASKS_COMPLETED.md` + `docs/.context/lode.db`.
> **Directive:** the three MILP solvers are one engine (`solver/engine/`, `ADR-017`). Add rules as builder modules, never a 4th engine.

## P0 — User Decisions (`[!]`)
None open. `DECISION-001..022` resolved 2026-11-15 (`ADR-012`); review on **2027-05-15** (time budget, pool access).

## P1 — Engineering Queue
Phases 0–1 + follow-ups ✅ (2026-11-16): stabilized repo, canonical library + YAML profile (`ADR-014`), `src/triplan/` package (`ADR-015`), repo-anchored paths (`triplan.paths`), token budget (`ADR-016`). Tests: `python -m pytest` (317 pass; `-m slow` adds the 8-season `EXP-007` matrix).

### Phase 2 — Unified MILP Engine
- [ ] `TASK-045` (`safety`): `starting_longest_run_min` profile field (week-1 long-run cap seed) + owner review of the 45 min re-entry ceiling (`ADR-022`).
- [ ] `TASK-049` (`safety`): the unconstrained reference profile never leaves re-entry mode (run CTL decays below the 15 exit): runs stay 33-60 min x 2-4 so the 90-150 min long runs are never chosen; give it a real run ramp (`TASK-045` seed) or a no-re-entry setting.
- [ ] `TASK-058` (`workouts`): walk-jog (run/walk) re-entry workouts: the schema has no walk component and a reduced impact load is an invented number; build them on the `TASK-023` impact model (`EXP-008`).
- [ ] `TASK-059` (`safety`): a `Brick` run leg is invisible to `run_minutes` / `run_strain` / ACWR / run-share rows, so no Brick can enter the default library until its run leg is modelled (`EXP-008`).
- [ ] `TASK-060` (`perf`): candidate-set growth is a solve-time tax (+31 default rows tripled season wall time before pruning; lift x cardio combos dominate); add dominance / symmetry pruning of near-identical rows (`EXP-008`).

### Phase 3 — Physiology Calibration
- [ ] `TASK-021` (`physiology`): unify ANS cost, strain, strength TSS; exact Banister decay.
- [ ] `TASK-022` (`safety`): fix or replace ODI (dimensional mismatch, wrong-sign controller).
- [ ] `TASK-023` (`mpc`): interval-parsed TTE durability, variety goal, `Phase` enum, walk-jog re-entry.
- [ ] `TASK-050` (`physiology`): Ironman build weeks stay 64-139 TSS under target because the rolling 48h ANS cap (700) binds at 690; low-intensity long sessions are ANS-expensive (`e^(0.65(z-1))`, with `TASK-021`). Owner decision: keep, or re-weight ANS for Z1/Z2 long sessions; plus distance-aware anchors (IM long-ride anchor). **Deferred by owner to `TASK-021` (ANS weighting of Z1/Z2 long sessions; keep the 700 cap).**

### Phase 4 — Telemetry & Closed Loop
- [ ] `TASK-024` (`integrations`): multi-device `.fit` activity parser + 300s ride dedup (`SPEC.md`, `ADR-010`).
- [ ] `TASK-025` (`mpc`): actuals store, unified readiness α_d (absorbs `TASK-003`), intra-week re-solve.
- [ ] `TASK-003` (`mpc`): *merged into `TASK-025`*.
- [ ] `TASK-002` (`workouts`): swim structured-workout ingestion + TP export; 1–3s jitter in all sync scripts.

## P2 — Queued Experiments (`docs/experiments/`)
- [ ] `EXP-003` (`perf`): solver correctness, sparse build and pruning scaling. **Run first**; retests `EXP-001`.
- [ ] `EXP-001` (`perf`): `NEEDS_RETEST` via `EXP-003`. 15-seed race vs single solve.
- [ ] `EXP-004` (`optimizer`): goal-weight sensitivity + session-structure constraints (81-point sweep).
- [ ] `EXP-005` (`integrations`): FIT parser validation on synthetic + real fixtures.
- [ ] `EXP-006` (`physiology`): load-model calibration against historical Garmin/lifting telemetry.
- [ ] `EXP-002` (`safety`): closed-loop 16-week season simulation under perturbations.
