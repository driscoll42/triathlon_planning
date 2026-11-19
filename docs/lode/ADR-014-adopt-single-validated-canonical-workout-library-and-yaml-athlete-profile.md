---
id: ADR-014
type: adr
domain: workouts
title: "Adopt Single Validated Canonical Workout Library and YAML Athlete Profile"
status: ACTIVE
created_at: 2026-11-15
supersedes: []
validated_by: []
affects_services: ["workouts", "optimizer", "physiology", "general"]
one_liner: "All workouts live in data/workout_library.csv (Pandera+Pydantic, explicit flags); the athlete lives in config/athlete_profile.yaml. Never infer flags at solve time."
---

# `ADR-014`: Adopt Single Validated Canonical Workout Library and YAML Athlete Profile

## 1. Context & Problem
Workouts lived in seven places: three 80/20 xlsx files, stale CSV copies of them, a root-level legos CSV, two diverging brick CSVs, a strength CSV, and two hand-coded Python lists. `enrich_workouts_from_df` guessed behavior flags at solve time. `'SS' in code` marked CSS swims as strides, `duration <= 75` decided whether a ride was indoor, and swim IF made 87% of swims "hard". Five codes named different workouts in different sources, and two rows' zone minutes did not match their duration. Nothing validated any of it.

## 2. Considered Alternatives
1. **Option A (Rejected):** Keep the sources and add validation at each loader. That leaves duplicate truths and solve-time inference in place.
2. **Option B (Rejected):** A Parquet library. It is not hand-editable or reviewable in a diff.
3. **Option C (ADOPTED):** One hand-editable CSV with a 7-zone schema, explicit flag columns, `provenance` and library-set membership. It is validated by Pandera (table) and Pydantic (rows) on every load. The athlete profile moves to YAML, validated by Pydantic types plus `AthleteProfile.validation_errors()`.

## 3. Decision & Production Invariant
- **Target Component / Path:** `data/workout_library.csv`, `src/library/{schema,build,loader}.py`, `config/athlete_profile.yaml`, and `src/athlete_profile.py` (`load_athlete_profile`, `save_athlete_profile`).
- **Locked Parameters / Contract:**
  1. **Columns (`WORKOUT_COLUMNS`):** 7-zone minutes `z1, z2, zx, z3, zy, z4, z5` (80/20 prescriptions keep ZX = ZY = 0). Engines see ZX folded into Z3 and ZY into Z4. Plus 18 explicit boolean flags, `provenance` ∈ {`8020_library`, `agent_authored`, `hand_coded_default`, `hand_coded_hybrid`}, and set membership `lib_default` / `lib_hybrid`.
  2. **Checks:**
     - Pandera: unique code; sport and provenance from closed vocabularies; TSS > 0; zone sum within 1 minute of duration.
     - Pydantic: cross-field rules (no strength flags on a ride, a ride is exactly one of indoor/outdoor, swims need a pool).
  3. **Build-time derivation:** `library.build.derive_8020_rows` applies documented rules (key session = ≥ 15 min Z3+ or ≥ 5 min Z4+; strides only where the description says "stride"; every 80/20 ride is trainer-eligible). After a re-scrape, `refresh_from_8020_scrape` replaces only 80/20 rows and keeps authored rows untouched. The xlsx files are a raw scrape cache, read only at build time.
  4. **Engines** load via `library.loader.load_candidates(library="default"|"hybrid"|provenance=...)`. `util.load_workouts()` reads the same table. Solve-time inference is deleted.
  5. **Profile:** every YAML field is required, so nothing silently falls back to a default. Unknown fields are rejected. Semantic rules: 7-day lists, ratio ranges, splits summing to 1, frequency min ≤ max, FTP = CP, known relaxation vocabulary.
- **Enforcement Mechanism:** `tests/test_workout_library.py` (schema rejects, audit fixes, idempotent refresh, YAML contract). The one-time migration is recorded in `scripts/migrations/m2026_11_15_build_canonical_library.py`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** 528 rows validate. Of these, 451 are 80/20, 35 agent-authored, 24 hand-coded default and 18 hand-coded hybrid. Stride workouts are corrected from 30+ (swims tagged by code substring) to 7. Corrections: `RTa1` 10 → 11 min, `BK_FR_TQ2` 75 → 81 min. The default and hybrid engine libraries load value-identical to the former hand-coded lists, apart from the documented renames, the `RUN_TB1_BRICK` hard flag, and two new stride rows.
- **Supersedes:** None. Do not reintroduce per-source loaders or substring flag inference.

## 5. Consequences & Trade-Offs
- **Positive:** One reviewed source of truth with lineage (`provenance`, `source`, `notes`, `needs_review`). Bad data fails loudly at load.
- **Negative / Operational Cost:** Flag rules changed some 80/20 classifications (swims, long rides), so engine behavior on the large library shifts. Durability is still on two scales (0–10 vs 0–85) until TASK-023. Strength TSS recalibrated by TASK-021. Swim minutes are still precomputed with the legacy CSS until TASK-013.
- **When to Revisit:** When ingested actual sessions (TASK-024) need their own table, or the library outgrows CSV review.
