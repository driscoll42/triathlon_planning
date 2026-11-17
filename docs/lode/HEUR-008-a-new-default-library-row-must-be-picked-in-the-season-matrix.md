---
id: HEUR-008
type: heuristic
domain: workouts
title: "A New Default-Library Row Must Be Picked In The Season Matrix"
status: ACTIVE # ACTIVE | SUPERSEDED
created_at: 2026-11-16
supersedes: []
validated_by: []
one_liner: "Never add a lib_default row without a season-matrix pick count and wall-time delta; drop rows never picked: each costs solve time (EXP-008)."
---

# `HEUR-008`: A New Default-Library Row Must Be Picked In The Season Matrix

- **Target Subsystem / Path:** `data/workout_library.csv` (`lib_default` rows), `library/build.py` gap-fill specs, `library/compound.py` lift x cardio combos.
- **Symptom / Failure Mode:** `EXP-008`: 31 added default rows (22 endurance, 3 strength, near-duplicate easy runs) tripled `CURRENT_ATHLETE` season wall time (33 s -> 101 s) and made several reference weeks end in "Lexicographic Phase B found no incumbent" at the 3.5 s limit; the relaxations they bought were identical without 17 of them.
- **Root Physics / Mechanism:** The week MILP has one binary per (row, day); each non-heavy lift also expands into lift x {Bike, Run} x {10, 20 min} combos, and near-identical rows (15 / 20 / 25 min Z1 runs) add symmetric branches. Wall time grows faster than the row count.
- **Permanent Operational Rule:** Before shipping a `lib_default` row, run the 8-season matrix and count its picks (`WeekRecord.schedule`); drop rows with zero picks and strength rows whose removal leaves relaxations unchanged. Prefer rows that satisfy two structure rows at once (hard + stride) over more of the same.
- **Verification Check:** `PYTHONPATH=src python -m triplan.perf.season_validation` (compare relaxations and wall time with and without the row).
