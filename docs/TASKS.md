# Active Engineering Queue (`docs/TASKS.md`)

> One line per open item. **Full specs live in `docs/BACKLOG.md`**; read a task's entry there before starting it. Completed work: `docs/archive/TASKS_COMPLETED.md` + `docs/.context/lode.db`.
> **Directive:** the three MILP solvers are one engine (`solver/engine/`, `ADR-017`). Add rules as builder modules, never a 4th engine.

## P0 — User Decisions (`[!]`)
None open. `DECISION-001..022` resolved 2026-11-15 (`ADR-012`); review on **2027-05-15** (time budget, pool access).

## P1 — Engineering Queue
Phases 0–1 + follow-ups ✅ (2026-11-16): stabilized repo, canonical library + YAML profile (`ADR-014`), `src/triplan/` package (`ADR-015`), repo-anchored paths (`triplan.paths`), token budget (`ADR-016`). Tests: `python -m pytest` (250 pass).

### Phase 2 — Unified MILP Engine
- [ ] `TASK-034` (`optimizer`): weekly TSS floor — recovery/taper/race weeks solve at ~126-146 TSS vs 72-112 targets; find the binding floor (duration splits / min frequencies / anchors).
- [ ] `TASK-033` (`workouts`): library has no heavy lift within the Tier-0 axial 65 / neuro 75 caps (`STR_MS1_HEAVY_DEF` is 85/85); race-block weeks run 0 heavy lifts. Add/rescale compliant heavy lifts (with `TASK-028`).
- [ ] `TASK-027` (`perf`): multi-distance season validation on real + unconstrained profiles.

### Phase 3 — Physiology Calibration
- [ ] `TASK-021` (`physiology`): unify ANS cost, strain, strength TSS; exact Banister decay.
- [ ] `TASK-022` (`safety`): fix or replace ODI (dimensional mismatch, wrong-sign controller).
- [ ] `TASK-023` (`mpc`): interval-parsed TTE durability, variety goal, `Phase` enum, walk-jog re-entry.

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
