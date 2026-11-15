# Completed Engineering Tasks (`docs/archive/TASKS_COMPLETED.md`)

## Phase 0: Stabilize & Make Repository Trustworthy — completed 2026-11-15 (branch `phase0-stabilize`)

- [x] **`TASK-005` (`general`):** **Commit Baseline Tree & Verify `.gitignore`** — Stage and commit restructured repo layout; verify `config/*.env`, `data/garmin/`, `data/lifting/`, and `references/` remain gitignored.
- [x] **`TASK-006` (`workouts`):** **Fix `triVariables` Attribute Crashes & TSS Filter Bug** — Add missing `high_duration_head`, `low_duration_head`, `intensity_factor`, `normalized_power` to `triVariables` (`util.py:235`, `clean_file.py:176-177`); fix `util.py:85,88` filtering `TSS` against `max_time_*` instead of `max_tss_*`; guard `rebuild_all_files()` (`clean_file.py:314`) from overwriting custom rows.
- [x] **`TASK-007` (`optimizer`):** **HiGHS Thread Unification & Solution Status Checks** — Enforce a single process-wide HiGHS thread setting (eliminating the `threads=4` vs `threads=2` silent `kNotset` failure); check `getModelStatus()` before reading `col_value` in all solvers (`highs_optimization.py:533`); return `status` and `mip_gap` in every plan dict; stop labeling `kTimeLimit` solves as `OPTIMAL`.
- [x] **`TASK-008` (`perf`):** **Pytest Harness & Scenario Assertions** — Add `pytest`; convert all 11 scenarios in `src/stress_test_optimizer.py` into assertions (single-run share $\le 0.32$, no deadlift adjacent to long run, $K$ distinct plans returned, non-fallback solve on `CURRENT_ATHLETE`); fix `stress_test_optimizer.py:264` indexing `default_lib[6]` (easy spin) instead of `default_lib[9]` (hard over-under).
- [x] **`TASK-009` (`general`):** **Documentation & Lode Truth Pass** — Update `docs/STATE_SUMMARY.md` and `ADR-010` to reflect that FIT activity parsing is not yet implemented (`clean_file.py` is an HTML scraper); mark `EXP-001` as `NEEDS_RETEST`; reconcile $N_{\text{day}} \le 130$ vs $140$ (`INVARIANTS.md` vs `classes.py:185`); replace hardcoded benchmark strings in `src/main.py:193-203`.

### Outcomes
- **TASK-005** (`2b4ec09`): Baseline committed; `config/*.env`, `data/garmin/`, `data/lifting/`, `data/fit_files/`, reference binaries verified ignored; credential scan clean.
- **TASK-006** (`687e7af`): Column aliases added to `triVariables`; TSS filter uses `max_tss_*` (weekend pool now keeps 140–200 TSS sessions); `preserve_custom_rows()` guards 14 custom rows.
- **TASK-007** (`4f53e80`): `src/highs_runtime.py` (`apply_threads`, `run_and_check`); all four solvers return `status` + `mip_gap`. Revealed time-limited weekly solves at 81–100% gap and stress scenarios 5/8 at 60%/8.6% gap previously labeled OPTIMAL.
- **TASK-008** (`ac495c6`): `tests/` pytest harness, 69 pass / 6 xfail; fixed scenario 9 hard-session index.
- **TASK-009**: FIT-parser claims corrected (STATE_SUMMARY, ADR-010); EXP-001 → `NEEDS_RETEST`; $N_{day}$ code defaults 140 → 130 per ADR-008; `main.py` comparison table computed from the run (399 candidates / 69 pruned vs hardcoded 293 / 61).
