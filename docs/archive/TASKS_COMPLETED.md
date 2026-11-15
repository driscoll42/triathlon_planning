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

## Phase 2 safety items — completed 2026-11-15 (branch `phase0-stabilize`, `ADR-013`)

- [x] **`TASK-016` (`optimizer`):** **3-Tier Lexicographic Constraint Hierarchy & Pre-Solve Feasibility Explainer** — Enforce Tier 0 as strictly hard (time caps, facilities, workout uniqueness, all orthopedic/48h/axial/neuro/ACWR caps, single-run $\le 32\%$ when $\ge 4$ runs, max 2 sessions/day); move anchors, min frequencies, stride counts, heavy-strength counts, and rest-day counts to Tier 1 named high-weight slacks (`w=1000`) reported in plan output; keep normalized TSS/split/zone/durability in Tier 2; add pre-solve diagnostic explaining conflicts in plain English (replace the silent frequency relaxation in `TriathlonMILPScheduler.__init__`, `closed_loop_optimizer.py:~702-706`; **xfail** `test_sport_frequency_bounds[6a]`, `test_current_athlete_week_solves_without_fallback`); delete the profile-discarding recovery fallback (`closed_loop_optimizer.py:2398-2404`) and silent `rho` relaxation (`:981-987`).
- [x] **`TASK-017` (`safety`):** **Port Missing Endurance & Orthopedic Rules into Unified Engine** — Ensure the unified engine enforces every rule missing from `HybridAthleteMILPOptimizer`:
  - All-sport + heavy-strength Hard/Easy day adjacency (`h_hard[d] + h_hard[d+1] <= 1`) and $\ge 48\text{h}$ heavy-run separation.
  - Extend Deadlift $\pm 24\text{h}$ adjacency lockout (`:1414-1422`) to cover `long_run` (`lrun_ids`) as well as `heavy_run`.
  - Apply single-run $\le 32\%$ volume cap across *all* runs (`run_ids`), not just `lrun_ids`, with explicit pigeonhole diagnostic when `max_runs < 4`. Confirmed violations with 4 runs: scenarios 1 / 2b (33.3%) and 8 (32.4%) — **xfail** `test_single_run_share_cap`.
  - Enforce rolling 48h TSS ($\le 295$), ANS ($\le 700$), and mechanical strain ($\le 260$) caps, plus weekly neuromuscular load cap ($N_{\text{wk}} \le 360.0$, `ADR-008`).
  - Enforce weekday single-workout duration cap and sport frequency bounds.
  - Read running ACWR caps from the profile (`max_running_acwr_reentry=1.20` while run CTL < 15, then `1.25`) instead of the hardcoded `1.30` (`closed_loop_optimizer.py:2076,2368`). Add the absolute-minutes re-entry guard: 60 min/week floor, +15 min/week max (`ADR-012`, `DECISION-014`).
  - Apply `tier1_relaxation_order` and `sport_priority` from the profile for the Tier-1 slack weights (`DECISION-006`).

### Outcomes
- **TASK-017**: Both engines enforce one shared Tier-0 rule set: run-share cap on all runs (32% with >= 4 runs; reported 40%/60% pigeonhole caps), deadlift +-24h vs threshold AND long runs, hard/easy adjacency, 48h heavy-run separation, hard 48h TSS/ANS/strain caps, daily+weekly axial/neuro caps, weekday cap, frequency bounds, antagonism matrix. Old hybrid demo plans had broken the weekly neuro cap (464/447 > 360) and HEUR-004.
- **TASK-016**: Tier-1 named integer slacks (anchors, min frequencies, strides, heavy strength floor 1, rest days floor 1) weighted 8^rank by DECISION-006 order; two-phase lexicographic solve; `explain_feasibility()` plain-English diagnostics; silent rho/frequency/anchor relaxations, config mutation, and the profile-discarding season fallback deleted.
