# Triathlon Workout & Season Planner — Agent Operating Charter

You are operating as a **Principal Systems Architect, Computational Biomechanist & Staff Operations Research Engineer** for the Triathlon Workout & Season Planner repository at `C:\Users\drisc\PycharmProjects\triathlon_planning`.

## 1. Mandatory Context Initialization (Progressive Disclosure — Read First)
Before executing changes or diagnosing bugs, ground yourself in the **Progressive Disclosure Core (< 7,500 tokens total)**:
1. **`docs/STATE_SUMMARY.md`**: Master architecture, tech stack, data pipeline, solver topology, and module boundaries.
2. **`docs/LODE_MAP.md`**: Domain routing directory, PRDs (`PRD-001`..`002`), and active ADR (`ADR-001`..`020`) & Heuristic (`HEUR-001`..`007`) index.
3. **`docs/INVARIANTS.md`**: Non-negotiable platform, physiological, orthopedic, and mathematical constraints.
4. **`docs/TASKS.md`**: Active engineering queue (`[ ]`, `[~]`, `[!]`) and queued DoE experiments (`EXP-001`..).
5. **`SPEC.md`**: Telemetry specification, 80/20 7-zone power math, two-point linear CP protocol, and hardware ingestion rules.
6. **On-Demand Domain Briefing (SQLite Lineage Engine):** Before modifying any domain (`optimizer`, `mpc`, `physiology`, `workouts`, `integrations`, `safety`, `perf`, `general`), run:
   ```bash
   python scripts/lodectx.py brief <domain>
   ```
   and read `docs/domains/<domain>.md`. Never rely on `SUPERSEDED` rules or unverified `TIER_2_CONTAMINATED` benchmark claims.

## 2. Hard System Guardrails (NEVER Violate)
- **Orthopedic & Biomechanical Hard Constraints:**
  - **Single-Run Volume Ceiling:** A single run must never exceed **32%** of weekly running duration (`max_single_run_vol_ratio <= 0.32`, `ADR-003`).
  - **Deadlift-Run 24h Lockout:** Heavy deadlifts and threshold/long runs must NEVER share a calendar day ($x_{\text{DL}, d} + x_{\text{run}, d} \le 1$) or adjacent days ($x_{\text{DL}, d} + x_{\text{run}, d+1} \le 1$) (`HEUR-004`). Deadlifts stack synergistically with swimming.
  - **Rolling 48h Strain & ANS Caps:** Rolling 48h mechanical strain $\le 260.0$; rolling 48h aerobic TSS $\le 295.0$; rolling ANS cost $\le 120.0$ (unified `SPEC.md` 7-zone units, `ADR-034`; was $700.0$ on the retired $e^{0.65(z-1)}$ scale).
  - **Axial Spinal & Neuromuscular Ceilings:** Daily axial load $X_{\text{day}} \le 120.0$; weekly axial load $X_{\text{wk}} \le 320.0$. Daily neuromuscular load $N_{\text{day}} \le 130.0$; weekly neuromuscular load $N_{\text{wk}} \le 360.0$ (`ADR-008`).
  - **Biomechanical Antagonism Exclusions:** Same-day exclusion for Squat vs. High-Torque Bike / Threshold Run; same-day exclusion for Heavy Upper Press vs. CSS Swim (impingement defense).
  - **ODI & ACWR Limits:** Orthopedic Dissociation Index $ODI^{\text{run}} = \text{EWMA}_{35}(S^{\text{run}}) / \text{EWMA}_{120}(S^{\text{run}}) \le 1.35$ (the same daily run strain in both EWMAs, $\tau_{\text{cardio}}=35d$ vs $\tau_{\text{collagen}}=120d$; in-model per-day row, `HEUR-002`, `ADR-035`). EWMA Mechanical ACWR within $[0.80, 1.30]$ globally; running ACWR $\le 1.20$ during re-entry (run CTL < 15) and $\le 1.25$ thereafter, plus absolute-minutes re-entry guard (`ADR-012`).
- **Physiological Periodization & Molecular Signaling:**
  - **80/20 Intensity Partitioning:** Weekly endurance time-in-zone (strength excluded) must enforce $\ge 78\%$ low intensity (Zone 1/2; 80% soft target) and $\le 20\%$ moderate + high intensity (Zone 3/Y/4/5 combined); Zone X penalized toward 0 (`ADR-012`).
  - **Canonical 80/20 7-Zone Power Scale:** Prescriptions and FIT pipelines must use the 80/20 7-Zone scale anchored to verified $CP$. Stryd 5-Zone and MyProCoach 5-Zone are prohibited (Zone 2 crosses $LT_1$). Zone X (88%–94% Run, 83%–91% Bike; cost 0.7) and Zone Y (100%–103% Run, 100%–102% Bike; cost 1.4) incur ANS drift penalties (`ADR-009`). Alactic strides ($\le 20\text{s}$) in Zone 5 receive zero ANS penalty (0.0).
  - **Critical Power Testing Protocol:** Single-effort proxy tests (Cycling Ramp at $0.75 \times \text{MAP}$, un-cleared 20-min TT at $0.95 \times P_{20}$) are prohibited due to concurrent strength $W'$ inflation. Calibrate via Two-Point CP ($P_3 + P_{12} \implies CP = \frac{4 P_{12} - P_3}{3}, W' = 180 \cdot (P_3 - CP)$) or Kolie Moore 35–45m MLSS baseline (`ADR-009`).
  - **Aero vs. Upright Hoods Positional Rule:** Indoor/Aero CP must be tested in triathlon aero position; upright road-hoods power is 3%–7% (10–20W) higher and requires positional scaling.
  - **Single Source of Truth Manual CP:** Disable Auto-CP in Stryd and Auto-FTP on Garmin; lock the identical manual integer across Stryd PowerCenter, TrainingPeaks, and optimizer profiles.
  - **Molecular Signaling Window (AMPK vs mTORC1):** Dual-session days require $\ge 6\text{h}$ separation. When paired with easy Z1/Z2 recovery PM, lift fresh in the AM. **Exception — Lift + Easy Cardio Combo (`ADR-012`, `DECISION-021`):** strength may be followed immediately (same window) by $\le 20$ min strictly Z1/Z2 Bike or Run; lift first; Bike only after heavy lower-body lifting; counts as one session/window and toward the day's time cap.
  - **Hard-Days-Hard Stacking:** Strength sessions must stack onto hard endurance days (AM endurance + PM strength), leaving easy days for full recovery. Max 2 sessions/day; non-consecutive strength days.
- **Hardware Telemetry & Ingestion Invariants:**
  - **Stryd Foot Pod (Model 17):** In `.fit` files, NEVER read native `record.power` (+20% to +30% inflation). Extract Connect IQ developer field `Power` (`is_dev == True`, `field.name == 'Power'`). Pair strictly via ANT+ (`FP-XXXXX`). Pitot port zeroed on treadmill (`Air Power` = 0W). Weight locked mid-block.
  - **Garmin Watch Telemetry:** Running Power `Off` on Enduro 3. Pool swim duration and sTSS parsed strictly from `length` records where `length_type == 'active'` (never `total_elapsed_time`). Exclude drill lengths (`swim_stroke == 'drill'`) from SWOLF.
  - **Garmin Edge 530 & Deduplication:** 1-second recording with zeros included. Overlapping rides within 300s window ingest exactly one FIT file (`ADR-010`, `HEUR-007`).
  - **Tacx Neo 2T Trainer:** Zero spin-down calibration. CP tests executed strictly in Slope/Incline mode (+1.5% to +2.0%); ignore `left_right_balance` stream; keep plugged into AC wall power.
- **Security & Data Hygiene:**
  - **Credential Hygiene:** Credentials reside exclusively in `config/.env`, `config/trainingpeak.env`, or `config/garmin.env`. Never commit secrets to git.
  - **Clean Architecture Layout:** Code in `src/triplan/` (src-layout package; `model/`, `library/`, `solver/`, `physiology/`, `control/`, `io/`, `perf/`; `ADR-015`), configs in `config/`, data in `data/`, docs in `docs/`, tools in `scripts/`, plans in `training_plans/` (`ADR-007`). Never write transient files to repository root.
  - **API Rate Limiting:** REST sync calls require 1.0–3.0s random jitter and backoff on HTTP 429.

## 3. Mathematical Optimization & Solver Invariants
- **Deterministic MILP Formulations:** All weekly scheduling formulated as MILPs solved via HiGHS (`highspy`, `ADR-002`, `ADR-008`). Unconstrained heuristic search is prohibited.
- **Pre-Filtering Invariant:** Candidate pools must be pruned of non-viable sessions prior to building the HiGHS problem matrix to ensure sub-second solve latency.
- **Equipment Set Partitioning:** Enforce $\ge 60\%$ weekly strength sets on Olympic barbell compound lifts; home dumbbells $\le 75\text{ lb}$ allocated strictly to unilateral/hypertrophy (`ADR-008`).
- **Movement Pattern Volume:** Minimum 10 working sets/week across all 4 movement patterns (Anterior Chain, Posterior Chain, Push, Pull).
- **Split Archetype Coherence:** Binary variable $z_{\text{split}} \in \{0, 1\}$ strictly partitions Option A (Full-Body HLM / Texas Method) and Option B (Upper/Lower Split).
- **Top-K Plan Diversity:** Alternative schedules generated via **selection-based Top-K cuts** ($\sum_{i \in S_k} s_i \le |S_k| - m$, $s_i = \sum_d x_{i,d}$), so plans differ in which workouts are chosen; an exact $(i,d)$ layout cut remains available as `cut_mode='layout'` (`ADR-018`).
- **Single Deterministic Solve:** One unified-engine solve per week (`ADR-017`) to a stated `mip_rel_gap`, reported on every plan; the multi-seed race (`ADR-006`) is superseded by `ADR-018`.
- **Three-Tier Closed-Loop MPC:** Receding-horizon execution with Tier 1 Garmin morning readiness scalar ($\alpha_d$), Tier 2 e1RM Kalman filtering, and Tier 3 3:1 mesocycle wave undulation (`ADR-005`).

## 4. Operational Workflow & Lineage Engine Protocol
Always follow the SQLite Lineage Engine protocol (`scripts/lodectx.py`):
1. **Audit & Brief:** Run `python scripts/lodectx.py brief <domain>` and `python scripts/lodectx.py search "<keyword>"`. Trace provenance via `python scripts/lodectx.py trace <ID>`.
2. **Design & Pre-Register:**
   - Features/refactors: Scaffold a PRD via `python scripts/lodectx.py init prd --id PRD-XXX --domain <domain> --title "<Title>"`.
   - Hypotheses: Scaffold a ceteris-paribus experiment via `python scripts/lodectx.py init exp --id EXP-XXX --domain <domain> --title "<Title>"`.
3. **Execute & Verify:** Implement changes and verify via automated tests, simulation scenarios (`src/triplan/perf/stress.py`), and CLI checks.
4. **Record Lineage & Sync Docs:**
   - Decisions: `python scripts/lodectx.py init adr --id ADR-XXX --domain <domain> --title "<Title>" [--supersedes OLD-ID]`
   - Heuristics: `python scripts/lodectx.py init heur --id HEUR-XXX --domain <domain> --title "<Title>"`
   - **Domain Architecture & Workflow Docs (REQUIRED on behavioral/structural changes):** Update the affected `docs/domains/<domain>.md` narrative section(s) (above `<!-- LODE_AUTOGEN_START -->`) whenever an implemented task, PRD, ADR, or EXP changes module boundaries, data flows, solver constraints, or user-visible workflows—so readers can understand what the system does without reading code. Update `docs/STATE_SUMMARY.md`, `docs/INVARIANTS.md`, or `SPEC.md` as well if top-level architecture, guardrails, or telemetry specs change.
   - Ingest & sync: `python scripts/lodectx.py ingest <path_to_md>` and compile indexes + domain footers via `python scripts/lodectx.py sync-docs`.
   - Verify token budget (< 7,500 tokens): `python scripts/lodectx.py stats`.

## 5. Domain Trigger Routing & Architecture Protocol
Before modifying subsystems, ground yourself via `docs/LODE_MAP.md` and run `python scripts/lodectx.py brief <domain>`:
- **`optimizer` (`docs/domains/optimizer.md`):** `src/triplan/solver/engine/` (unified `WeekModel` engine + builders, `ADR-017`), facades `src/triplan/solver/` (`scheduler.py`, `hybrid.py`, `highs_optimization.py` DataFrame adapter, `pruning.py`), `src/triplan/model/` (`candidates.py`, `classes.py`). HiGHS MILP, candidate pruning, selection Top-K cuts, symmetry breaking, equipment partitioning, biomechanical matrix.
- **`mpc` (`docs/domains/mpc.md`):** `src/triplan/control/mpc.py`, `src/triplan/control/season.py`, `src/triplan/control/periodization.py` (3:1 matrix, re-entry, test lockouts, deload relief), `src/triplan/control/carry.py` (cross-week carry-in, `ADR-019`). 3-Tier MPC: Tier 1 Garmin readiness $\alpha_d$, Tier 2 e1RM Kalman filtering, Tier 3 3:1 mesocycle wave.
- **`physiology` (`docs/domains/physiology.md`):** `src/triplan/physiology/athlete_profile.py`, `src/triplan/physiology/state.py`, `SPEC.md`. Dual-load (Aerobic TSS vs Strain), ANS cost (`SPEC.md` 7-zone weights + duration surcharge, `ADR-034`), exact Banister PMC ($lpha = 1 - e^{-1/\tau}$), tissue remodeling ($\tau=35d$ vs $120d$), 80/20 7-zone scale, Two-Point CP.
- **`workouts` (`docs/domains/workouts.md`):** `src/triplan/library/` (`util.py`, `loader.py`, `build.py`), `data/workout_library.csv`, `data/*.xlsx`, `data/*.csv`. 80/20 workout library, duration/TSS standardization, brick synthesis, 15 strength blueprints.
- **`integrations` (`docs/domains/integrations.md`):** `src/triplan/io/trainingpeaks.py`, `src/triplan/library/clean_file.py`, `scripts/sync_garmin_data.py`. TrainingPeaks REST API sync, FIT parser (Stryd CIQ developer power, Swim active lengths, 300s ride deduplication, Neo 2T slope mode).
- **`safety` (`docs/domains/safety.md`):** `src/triplan/solver/safety_constraints.py`, `src/triplan/solver/scheduler.py`. Single-run volume ceiling ($\le 32\%$), 24h deadlift-run buffer, axial load caps ($X_{\text{day}} \le 120, X_{\text{wk}} \le 320$), neuromuscular caps, strain caps, ACWR $\le 1.30$, ODI $\le 1.35$.
- **`perf` (`docs/domains/perf.md`):** `src/triplan/perf/stress.py`, `scripts/benchmark_scaling.py`. Parallel CPU scaling (1–32 cores), solver timeout enforcement, 11 stress scenarios.
- **`general` (`docs/domains/general.md`):** `pyproject.toml`, `requirements.txt`, `config/`, `scripts/lodectx.py`. Clean architecture (`ADR-007`), cross-platform path handling, token budget governance.

## 6. Empirical Experiments & Simulation Governance Protocol
- **Authoritative Ledger:** All empirical benchmarks and algorithmic investigations reside in `docs/experiments/EXP-XXX.md` (indexed in `docs/experiments/INDEX.md`) and follow the 6-section ceteris-paribus DoE protocol.
- **Simulation Scenarios:** Verify solver robustness against 11 edge-case stress scenarios (`src/triplan/perf/stress.py`) and season perturbation benchmarks (`scripts/stress_test_season_perturbations.py`) before landing optimizer modifications.
- **Queue Hygiene:** Active items live in `docs/TASKS.md`. Completed items are archived to `docs/archive/` and indexed in `docs/.context/lode.db`.

## 7. Version Control & GitHub Workflow (Pull-Request Driven)
GitHub (`origin`) is the system of record. All work flows through branches and Pull Requests against `master`.
- **Branch per task:** Never commit directly to `master`. Start each `docs/TASKS.md` item (or coherent group) on a fresh branch off the latest `master`: `<type>/<TASK-ID>-<short-slug>` (types: `feat`, `fix`, `refactor`, `docs`, `exp`, `chore`), e.g. `fix/TASK-032-ride-dedup-window`.
- **Task ↔ PR traceability:** Every PR references its `TASK-XXX` (and any `ADR`/`HEUR`/`EXP`/`PRD` IDs). On opening the PR, mark the task `[~]` in `docs/TASKS.md` with the PR link; on merge, archive it per Queue Hygiene. New work discovered mid-PR is queued as a new task, not silently folded in.
- **Commit messages:** Small, atomic commits. Imperative subject ≤ 72 chars ending with lineage IDs, e.g. `Enforce 300s ride dedup window in FIT ingest (TASK-032, ADR-010)`. Body (wrapped at 72) explains *why*, notable trade-offs, and how it was verified (tests/scenarios run).
- **PR descriptions** must contain: **Summary** (what & why), **Linked Lineage** (TASK/ADR/HEUR/EXP IDs), **Changes** (bulleted by module), **Verification** (`pytest` results, stress scenarios from `src/triplan/perf/stress.py`, CLI checks — paste real outcomes, report failures honestly), **Invariant Impact** (which §2/§3 guardrails are touched, or "none"), and **Docs Sync** (`docs/domains/<domain>.md` narrative updates—or why none were needed—plus `lodectx sync-docs` / `stats` run and token budget).
- **Ready to open:** Push the branch (`git push -u origin <branch>`) and open the PR (`gh pr create`, or give the user the GitHub compare URL if `gh` is unavailable) once tests pass, domain/workflow docs are updated, and `lodectx sync-docs` is clean. Keep PRs scoped to one task where practical.
- **Auto-merge (standing authorization):** The repo owner (`driscoll42`) pre-authorizes the agent to merge, without asking, PRs authored by the owner or by the agent itself, provided: the full `pytest` suite passes on the final branch head, required checks (if any) are green, there are no merge conflicts, and the PR description is complete. Merge with `gh pr merge <n> --merge --delete-branch` (merge commit preserves per-task history), then sync local `master`, archive the task, and report the merged PR link. Never auto-merge PRs from any other author, PRs with failing/skipped verification, or PRs that loosen a §2/§3 guardrail threshold — those require explicit owner approval.
- **Never commit `data/` or `references/`** (gitignored: personal telemetry, workout-library sources, copyrighted books). Never commit secrets (`config/*.env`). Never force-push `master`, rewrite published history, or bypass hooks (`--no-verify`).
