# Active Engineering Queue (`docs/TASKS.md`)

> One line per open item. **Full specs live in `docs/BACKLOG.md`**; read a task's entry there before starting it. Completed work: `docs/archive/TASKS_COMPLETED.md` + `docs/.context/lode.db`.
> **Directive:** merge the three MILP solvers (`hybrid`, `scheduler`, `highs_optimization`) into ONE superset engine.

## P0 — User Decisions (`[!]`)
None open. `DECISION-001..022` resolved 2026-11-15 (`ADR-012`); review on **2027-05-15** (time budget, pool access).

## P1 — Engineering Queue
Phases 0–1 + follow-ups ✅ (2026-11-16): stabilized repo, canonical library + YAML profile (`ADR-014`), `src/triplan/` package (`ADR-015`), repo-anchored paths (`triplan.paths`), token budget (`ADR-016`). Tests: `python -m pytest` (157 pass).

### Phase 2 — Unified MILP Engine
- [ ] `TASK-015` (`optimizer`): unified `WeekModel` engine, sparse `addRow` build, modular constraint builders.
- [ ] `TASK-018` (`mpc`): cross-week carry-in state; in-model linearized ACWR and CTL-ramp caps.
- [ ] `TASK-019` (`physiology`): key-session/stride structure, hard-days-hard, AM/PM gating, lift+easy combo, full 80/20 TiZ rule.
- [ ] `TASK-020` (`optimizer`): selection-based Top-K cuts, dominance pruning, symmetry breaking; fix `z_split` flip.
- [ ] `TASK-026` (`mpc`): 3:1 season matrices, profile-driven week configs, ≥24-wk re-entry block, `ADR-011` test lockouts.
- [ ] `TASK-027` (`perf`): multi-distance season validation on real + unconstrained profiles.
- [ ] `TASK-004` (`perf`): *superseded by `TASK-020`/`EXP-003`*. Retire the multi-seed race.

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
