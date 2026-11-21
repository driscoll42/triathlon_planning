# Domain Lode: `integrations`

## 1. Scope & Responsibility
TrainingPeaks private REST API authentication and workout syncing, 80/20 Endurance `.xlsx`/HTML library ingestion (`clean_file.py`), and multi-device hardware telemetry / `.fit` ingestion specifications (`SPEC.md`, `ADR-010`).

> **Implementation Status:** `TrainingPeaksClient` (`io/trainingpeaks.py`, including Run and Swim 80/20 structured-workout ingestion, CSS-calibrated TP JSON export, and 1.0–3.0s random jitter), the 80/20 library scraper/builder (`library/clean_file.py`), the multi-device `.fit` activity parser (`io/fit_ingest.py`, `TASK-024`, validated by `EXP-005` across 8 synthetic + 6 real-file tests and 2,020/2,020 corpus `.fit` files), the deterministic post-workout & block diagnostic brief generator (`io/workout_review.py`, `PRD-003`, paired with `.agents/skills/post-workout-review/` and `.agents/skills/block-review/`), and the `ActualsStore` -> `evaluate_cp_retest_triggers` telemetry wiring (`TASK-002`, `TASK-025`) are implemented.

## 2. Key Modules & Entrypoints
- [`src/triplan/io/trainingpeaks.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/io/trainingpeaks.py): `TrainingPeaksClient` (1.0–3.0s random jitter + 429/5xx exponential backoff), repo-root-anchored credential loading via `triplan.paths.CONFIG_DIR` (`TASK-031`), Run & Swim structured step serialization (`Warmup`, `Active`, `Rest`, `Recovery`, `Cooldown`), CSS-calibrated swim `sTSS`/`sIF` payload export (`workoutTypeId=1`), `load_8020_swim_workouts_from_library()`, and folder sync.
- [`src/triplan/io/fit_ingest.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/io/fit_ingest.py): Multi-device `.fit` activity parser (`parse_fit_file`, `ParsedActivity`, `compute_aerobic_decoupling`, `deduplicate_activities`, `audit_corpus`, CLI `python -m triplan.io.fit_ingest --audit-corpus <dir>`) enforcing `SPEC.md` §3 and `ADR-010` invariants (Stryd CIQ `Power`/`Form Power`/`Leg Spring Stiffness`/`Air Power` without native `record.power` fallback, treadmill flag, active-only pool swim duration/sTSS with drill-excluded SWOLF, Neo 2T 0W inclusion & `left_right_balance` ignore, 80/20 7-zone TiZ, `efficiency_factor` & `decoupling_pct`, and 300s ride deduplication preferring Edge 530).
- [`src/triplan/io/workout_review.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/io/workout_review.py): Post-workout & multi-week block diagnostic brief generator (`analyze_fit_diagnostics`, `summarize_lifting_window`, `build_review_brief`, CLI `python -m triplan.io.workout_review --fit <file> | --days 7|28`; read-only by default with opt-in `--record` to `ActualsStore`) feeding the [`post-workout-review`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/skills/post-workout-review/SKILL.md) skill and [`block-review`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/.agents/skills/block-review/SKILL.md) 4-specialist parallel workflow.
- [`src/triplan/library/clean_file.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/src/triplan/library/clean_file.py): 80/20 `.xlsx`/HTML scraper, mile/swim prescription parsers, `calculate_TSS()`, `calculate_sTSS()`, and `refresh_from_8020_scrape()`.
- [`SPEC.md`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/SPEC.md): Authoritative hardware telemetry specification, `.fit` field extraction rules, and 7-zone math for the queued `TASK-024` parser.
- [`config/`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/config/): Gitignored secrets (`config/.env`, `config/trainingpeak.env`, `config/garmin.env`); example template at `config/.env.example`.
- [`scripts/sync_garmin_data.py`](file:///C:/Users/drisc/PycharmProjects/triathlon_planning/scripts/sync_garmin_data.py): Garmin sync helper using `triplan.paths`.

## 3. Implemented API & Ingestion Rules

### A. API Hygiene & Credential Security
- Credentials (`TP_USERNAME`, `TP_PASSWORD`, `TP_COOKIE`) live exclusively in `config/.env`, `config/trainingpeak.env`, or `config/garmin.env` (never cwd fallbacks, never committed to git or logged; `TASK-031`).
- Outbound REST sync calls apply 1.0–3.0s random jitter (`TrainingPeaksClient._rate_limit()` and `scripts/sync_garmin_data.py::polite_jitter_sleep()`) and exponential backoff on HTTP 429 (`TASK-002`).
- Structured workout steps serialize to standard TrainingPeaks step schemas (`Warmup`, `Active`, `Recovery`, `Cooldown`).

### B. Planned Power, Running & Swimming Telemetry Math
- **Cycling Power (Coggan / Vance):** 30s rolling NP ($\big(\frac{1}{N}\sum P_{30s,t}^4\big)^{1/4}$), $\text{IF} = \text{NP}/\text{bFTP}$, $\text{VI} = \text{NP}/\text{AvgP}$, Efficiency Factor $\text{EF} = \text{NP}/\text{AvgHR}$, decoupling $Pw:HR < 5.0\%$ steady Z2 target, and Vance Quadrants I–IV / cadence-power fade ($\le 13\%$ 70.3, $\le 20\%$ 140.6).
- **Running Power (Stryd):** $\text{rIF}$ from Stryd CP, $\text{ECOR} = \text{Power} / (\text{Speed} \cdot \text{Mass})$, Form Power Ratio, GCT, and Leg Spring Stiffness (`LSS`) decay markers.
- **Swimming (`sTSS`):** Cubic hydrodynamic drag scaling ($sIF = \text{NSS}/\text{CSS}$, $sTSS = \frac{T \cdot (sIF)^3 \cdot 100}{3600}$).
- **Execution Compliance (Target for `TASK-025`):** Fractional duration/TSS/IF errors ($\epsilon_{\text{dur}}, \epsilon_{\text{tss}}, \epsilon_{\text{int}}$) and Moderate-Intensity Rut reclassification ($IF_{\text{real}} > 0.78$ or $HR > LT_1 \implies hard_d = 1$).

## 4. Hardware Telemetry & FIT Ingestion (`SPEC.md`, `ADR-010` — `io/fit_ingest.py`, `TASK-024`)

1. **Stryd Foot Pod (Wind Model 17, `HEUR-005`):**
   - Extract Connect IQ developer field `Power` (`field.is_dev == True`, `field.name == 'Power'`); **never** read native `record.power` (+20% to +30% inflation). Also extract `Form Power`, `Leg Spring Stiffness`, and `Air Power` (`0W` on treadmills).
   - Pair via **ANT+** (`FP-XXXXX`, Pace & Distance = Always) to leave single-channel BLE open for the Stryd app; keep Stryd user weight locked mid-block.
2. **Garmin Enduro 3 Watch:**
   - Set native Running Power = `Off`. Primary hub for Physio TrueUp 2.0 and enhanced Pool Swim workouts (2 CIQ data field slots max).
3. **Garmin Swim 2 Watch (`HEUR-007`):**
   - Compute active swim duration strictly by summing `length` messages with `length_type == 'active'` (never `session.total_elapsed_time`).
   - Exclude drill lengths (`swim_stroke == 'drill'` / `event == 'drill'`) from SWOLF/stroke rate while retaining their duration in low-intensity aerobic swim time.
4. **Garmin Edge 530 Bike Computer (`HEUR-007`):**
   - Record at `Every Second` with zeros included for power and cadence.
   - **300s Cross-Device Deduplication:** If both Edge 530 and Enduro 3 record a ride with `abs(start_time_A - start_time_B) < 300s`, ingest exactly ONE `.fit` file.
5. **Tacx Neo 2T Smart Trainer (`HEUR-006`):**
   - Zero spin-down calibration; execute all CP/MLSS tests in **Slope/Incline mode** (+1.5% to +2.0%, Road Feel Off, Downhill Drive Off — never ERG); keep plugged into AC power; discard inductive `left_right_balance`.

<!-- LODE_AUTOGEN_START -->
<!-- Auto-rendered by lodectx.py sync-docs; edit narrative sections above, not this block. -->

## Registered Lineage (`integrations`)

### Active PRDs
- **`PRD-003` — Post-Workout Telemetry Diagnostics, Review Skill, and Multi-Sport Block Review Agents:** Add deterministic post-workout/block telemetry briefs, a single-session review skill, and 4 parallel sport-specialist review agents.

### Active Architectural Decisions (`ADR`)
- **`ADR-010` — Adopt Multi-Device Hardware Telemetry Rules and FIT Ingestion Invariants:** Standardize FIT ingestion across Stryd Model 17, Garmin Enduro 3/Swim 2, Edge 530, and Tacx Neo 2T with strict dev-field and deduplication rules.

### Active Operational Heuristics (`HEUR`)
- **`HEUR-005` — Disambiguate Stryd CIQ Developer Power Field and Enforce ANT+ Pairing:** Never read record.power for running; extract CIQ dev field 'Power' and pair Stryd Model 17 via ANT+ to prevent BLE lockouts.
- **`HEUR-006` — Tacx Neo 2T Incline-Mode Testing and Invalidate Left-Right Balance:** Test CP in Slope mode (+1.5-2.0%), keep AC power plugged in, and discard Neo 2T inductive left_right_balance stream.
- **`HEUR-007` — Pool Swim Active Length Parsing and Multi-Device Deduplication Window:** Sum pool swim time strictly across active lengths (exclude drills from SWOLF) and deduplicate multi-device FIT files within 300s window.

### Empirical Experiments (`EXP`)
- **`EXP-005` [`ACTIVE` | `TIER_1_ISOLATED`] — Multi-Device FIT Ingestion Parser and Hardware Invariant Validation:** Validated triplan.io.fit_ingest on 8 synthetic tests (Fixtures A-F) + 6 real FIT fixtures and 2,020-file corpus sweep: 100% parse success (0 errors), 660 Stryd CIQ runs, 64 treadmill runs, 85 pool swims (mean elapsed/active 1.272x), and 6 <300s duplicates dropped.
<!-- LODE_AUTOGEN_END -->
