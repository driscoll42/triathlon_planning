---
id: PRD-003
type: prd
domain: integrations
title: "Post-Workout Telemetry Diagnostics, Review Skill, and Multi-Sport Block Review Agents"
status: COMPLETED
created_at: 2026-11-21
updated_at: 2026-11-21
supersedes: []
depends_on: ["ADR-005", "ADR-008", "ADR-009", "ADR-010", "ADR-011", "ADR-012", "HEUR-005", "HEUR-006", "HEUR-007"]
affects_services: ["integrations", "mpc", "physiology", "safety"]
one_liner: "Add deterministic post-workout/block telemetry briefs, a single-session review skill, and 4 parallel sport-specialist review agents."
---

# Engineering PRD: Post-Workout Telemetry Diagnostics, Review Skill, and Multi-Sport Block Review Agents (`PRD-003`)

## 1. Overview & Architectural Objective
Build a three-layer closed-loop workout analysis system anchored on deterministic Python telemetry extraction (`src/triplan/io/workout_review.py`), a progressive-disclosure single-workout coaching skill (`.agents/skills/post-workout-review/`), and a persistent 4-specialist multi-agent block review workflow (`.agents/skills/block-review/`) for weekly (7-day) and mesocycle (28-day) audits. All raw `.fit` parsing, 7-zone classification, decoupling math, and cross-sport load aggregation remain strictly deterministic in Python; LLM skills and specialist agents interpret structured JSON/Markdown briefs against `SPEC.md` and `docs/INVARIANTS.md`.

## 2. Problem Statement & Empirical Evidence
- **Current Bottleneck / Risk:** `src/triplan/io/fit_ingest.py` (`parse_fit_file`, `deduplicate_activities`) and `src/triplan/control/actuals.py` (`ActualsStore`, `rolling_cp_telemetry`) extract session-level totals (`tss`, `mechanical_strain`, `ans_cost_val`, `efficiency_factor`, `decoupling_pct`, `zones7_min`), which are sufficient for PMC state updates (`TASK-025`) but discard within-session diagnostics needed for coaching feedback:
  - **Run:** No first-half vs. second-half Stryd `Leg Spring Stiffness` (LSS) decay, `Form Power` ratio (`Form Power / Power`), `ECOR`, cadence drift, lap-by-lap pacing, or Moderate-Intensity Rut (`IF > 0.78`) flagging.
  - **Bike:** No Variability Index (`VI = NP / AvgP`), cadence-power fade across halves, zero-power coasting share, lap-level NP/IF, or Neo 2T indoor vs. Edge 530 outdoor (`aero` vs. `hoods`) positional context.
  - **Swim:** No active-set / quarter-split pace per 100m, `SWOLF`, or stroke-rate fade across `length` messages (drill lengths excluded per `HEUR-007`).
  - **Strength:** Garmin strength `.fit` files lack reliable exercise/load semantics; structured lifting metrics reside in `data/lifting/parsed_lifting_history.json` (419 sessions, movement patterns, barbell vs. dumbbell tonnage, `best_e1rm_lbs`) and are not yet surfaced alongside endurance telemetry.
- **Telemetry / Proof:** `fit_ingest.py` parses 2,020/2,020 corpus `.fit` files cleanly (`EXP-005`), and `ActualsStore.rolling_cp_telemetry()` + `ClosedLoopMPCController.evaluate_cp_retest_triggers()` (`ADR-011`) already exist, so diagnostic extensions can layer directly on top without altering ingestion invariants.

## 3. Target Goals (Measurable Outcomes)
- **[Performance / Efficiency]:** Single-activity diagnostic brief generation completes in `< 0.5s` per `.fit` file; 28-day block brief (up to ~40 activities + lifting history) completes in `< 3.0s` with compact JSON output (`< 4 KB` per session summary) to preserve agent context windows.
- **[Security / Correctness]:** 100% adherence to `SPEC.md` §2–§3 and `ADR-010` (`HEUR-005` Stryd CIQ `Power` only, `HEUR-006` Neo 2T L/R balance ignored & 0W included, `HEUR-007` active-only swim lengths & 300s ride dedup). Zero LLM arithmetic on raw `.fit` binary streams.
- **[Reliability / Operability]:** Graceful degradation when optional streams (`Form Power`, `Leg Spring Stiffness`, `HR`, `cadence`, or `lifting` logs) are absent (`None` fields + explicit `missing_streams` notice, never silent zeros). Zero regressions in existing `pytest` suite.

## 4. Non-Goals (Strict Negative Scope — DO NOT TOUCH)
- **DO NOT** modify HiGHS MILP solver constraints, `WeekModel` builders, or `fitness`/`strain` formulas in `src/triplan/solver/` or `src/triplan/physiology/load_model.py`.
- **DO NOT** replace `parse_fit_file()` or alter existing `ParsedActivity` field semantics in `src/triplan/io/fit_ingest.py`.
- **DO NOT** auto-mutate `config/athlete_profile.yaml` CP/CSS values or auto-push changes to TrainingPeaks/Garmin; all agent recommendations are advisory unless explicitly executed by the user.
- **DO NOT** spin up 4 separate subagents for single-workout reviews (cross-sport fatigue, 48h rolling caps, and ODI require unified single-agent context).

## 5. System Invariants & Guardrails (`docs/INVARIANTS.md`)
- **Hardware Telemetry & FIT Invariants (`ADR-010`):** Stryd CIQ developer field `Power` (never native `record.power`), treadmill `Air Power == 0W` annotation, active swim lengths only (drills excluded from SWOLF/stroke rate), Edge 530 / Neo 2T 0W inclusion, ignore Neo 2T `left_right_balance`, 300s ride deduplication.
- **Canonical 80/20 7-Zone Scale (`ADR-009`):** All zone summaries and Zone X / Zone Y drift penalties anchored to `CURRENT_ATHLETE` manual CP/CSS (`Z1, Z2, ZX, Z3, ZY, Z4, Z5`).
- **Cross-Sport Orthopedic & Signaling Caps (`ADR-003`, `ADR-008`, `ADR-012`, `ADR-034`, `ADR-035`):** Surface 48h rolling strain (`<= 260.0` charter / profile cap), 48h TSS (`<= 295.0`), 48h ANS cost (`<= 120.0` charter / `55.0` athlete), deadlift-run 24h buffer (`HEUR-004`), squat vs. high-torque bike/run separation, barbell set share (`>= 60%`), and `ODI^run` (`<= 1.35`).
- **Data Hygiene:** Never commit `data/garmin/`, `data/lifting/`, `config/*.env`, or `actuals` telemetry to git.

## 6. Deterministic Technical Requirements & Tasks
- [x] **[Task 1 - MUST] Post-Workout & Block Diagnostic Extractor (`src/triplan/io/workout_review.py`):**
  - *System Benefit:* Separates deterministic telemetry math from LLM interpretation and surfaces lap/half-split mechanics and cross-sport context.
  - *Implementation Spec:*
    - Build `analyze_fit_diagnostics(file_path, ...)` wrapping `parse_fit_file()` plus stream-level half-splits and `lap` summaries:
      - **Run:** `form_power_ratio`, `lss_first_half`, `lss_second_half`, `lss_decay_pct`, `ecor`, `cadence_first_half`, `cadence_second_half`, `cadence_drift_pct`, `zone_xy_min`, `moderate_rut_flag` (`IF > 0.78` on planned easy), `laps[]`.
      - **Bike:** `variability_index` (`NP / AvgP`), `zero_power_pct`, `cadence_first_half`, `cadence_second_half`, `power_cadence_fade_pct`, `aero_vs_hoods_note` (device/indoor context), `laps[]`.
      - **Swim:** `active_duration_min`, `rest_duration_min`, `active_ratio`, `pace_per_100m_sec`, split `pace_per_100m` / `swolf` / `stroke_rate` across 1st vs. 2nd half of active lengths, `drill_count`.
    - Build `summarize_lifting_window(start_date, end_date, lifting_path=...)` reading `data/lifting/parsed_lifting_history.json` (when present) for session count, sets per movement pattern (`Anterior_Chain`, `Posterior_Chain`, `Push`, `Pull`), barbell vs. dumbbell working-set ratio, estimated axial load, and `best_e1rm_lbs` progression.
    - Build `build_review_brief(...)` supporting single-workout mode (`--fit <path>` or `--date YYYY-MM-DD`) and block mode (`--days 7|28` / `--start`..`--end`) over a FIT directory + lifting history + `training_plans/*.csv` / `--planned-code` matching + `ActualsStore` + `evaluate_cp_retest_triggers()` + `garmin_wellness.py`, outputting JSON or Markdown via `python -m triplan.io.workout_review`. Read-only by default; opt-in `--record` flag persists reviewed `.fit` activities to `ActualsStore` (`data/actuals.json`).
- [x] **[Task 2 - MUST] Unit & Fixture Tests (`tests/test_workout_review.py`):**
  - *System Benefit:* Guarantees diagnostic calculations (half-split LSS decay, VI, swim split SWOLF, lifting aggregation, missing-stream handling, 300s dedup) are accurate and regression-free.
  - *Implementation Spec:* Add synthetic FIT & lifting-log fixture tests covering Run, Bike, Swim, Strength, and multi-day cross-sport context.
- [x] **[Task 3 - MUST] Single-Session Coaching Skill (`.agents/skills/post-workout-review/`):**
  - *System Benefit:* Enables on-demand, cross-sport-aware single-workout review using progressive disclosure without cluttering the base context window.
  - *Implementation Spec:*
    - `SKILL.md`: Step-by-step runbook (run `python -m triplan.io.workout_review`, read the sport-specific reference rubric, check 48h cross-sport context & `docs/INVARIANTS.md`, produce structured Execution Grade + Mechanics + Cross-Sport Impact + Next-Session Recommendation).
    - `references/run.md`, `references/bike.md`, `references/swim.md`, `references/strength.md`: Thresholds, failure modes, and coaching cues anchored to `SPEC.md` and `docs/INVARIANTS.md`.
- [x] **[Task 4 - MUST] Weekly / 4-Week Multi-Sport Block Review Agents & Workflow (`.agents/skills/block-review/`):**
  - *System Benefit:* Provides persistent, reusable 4-specialist parallel analysis (`Run Analyst`, `Bike Analyst`, `Swim Analyst`, `Strength Analyst`) plus a `Cross-Sport MPC Synthesizer` for weekly and 4-week mesocycle reviews.
  - *Implementation Spec:*
    - `SKILL.md`: Entry skill explaining how to invoke a 7-day or 28-day review using `run_workflow` (or `invoke_subagent` fallback).
    - `agents/run_analyst.md`, `agents/bike_analyst.md`, `agents/swim_analyst.md`, `agents/strength_analyst.md`: Dedicated specialist role prompts, diagnostic checklists, and JSON output schemas.
    - `scripts/block_review_workflow.py`: `run_workflow` script (`ScriptPath` + `Args: {"days": 7|28, "as_of": ...}`) that generates the Python block brief once, fans out all 4 sport analysts concurrently via `parallel(...)`, and runs a final synthesis step checking 80/20 partitioning, 3:1 mesocycle phase, `ODI`, `ACWR`, and `evaluate_cp_retest_triggers()` recommendations.
- [x] **[Task 5 - MUST] Documentation & Lineage Sync:**
  - *System Benefit:* Keeps progressive-disclosure docs and `lode.db` synchronized within the `< 7,500`-token startup budget.
  - *Implementation Spec:* Update `docs/domains/integrations.md`, `docs/LODE_MAP.md`, ingest `PRD-003`, run `python scripts/lodectx.py sync-docs` and `python scripts/lodectx.py stats`.

## 7. Linked Empirical Experiments (If Applicable)
- Builds on: `EXP-005` (Multi-Device FIT Ingestion Parser and Hardware Invariant Validation). No new solver experiment required unless MPC auto-adjustment thresholds are later tuned.

## 8. Acceptance Gates (Definition of Done)
- [x] **Gate 1 (Rollback Ready):** Clean feature branch off `master` (`feat/PRD-003-workout-review-agents`) prior to implementation.
- [x] **Gate 2 (Tests & Verification):** Full `python -m pytest` suite (existing 452+ tests plus `tests/test_workout_review.py`) passes 100%.
- [x] **Gate 3 (Target Metric Verified):** CLI `python -m triplan.io.workout_review --help` and dry-run against sample fixtures produce valid JSON/Markdown briefs in `< 0.5s` (single) / `< 3.0s` (28-day); `.agents/skills/post-workout-review/SKILL.md` and `.agents/skills/block-review/SKILL.md` pass frontmatter & script checks.
- [x] **Gate 4 (Lineage & Docs Synced):** `python scripts/lodectx.py ingest docs/specs/PRD-003-post-workout-telemetry-diagnostics-review-skill-and-multi-sport-block-review-agents.md`, `sync-docs`, and `stats` pass with startup core `< 7,500` tokens.

## 9. Open Questions (Halt Gate)
> **Agent Rule:** If any checkbox below is unchecked `[ ]`, HALT and ask the user before executing Section 6.
- [x] **Strength Log Source Going Forward:** Resolved (2026-11-21) — Read re-parsed `data/lifting/parsed_lifting_history.json` (from `PPL Routine.xlsx`).
- [x] **Planned vs. Actual Workout Lookup:** Resolved (2026-11-21) — Match against local `training_plans/*.csv` + `data/workout_library.csv` with optional `--planned-code` override.
- [x] **Read-Only Review vs. State Mutation:** Resolved (2026-11-21) — Read-only by default with an opt-in `--record` flag to update `ActualsStore`.
