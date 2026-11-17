"""
MIGRATION (TASK-051, 2026-11-16). Run AFTER m2026_11_16_long_course_library.py (idempotent).

Adds the gap-fill workouts found by the per-sport library audit (docs/experiments/EXP-008.md) to
``data/workout_library.csv``: short easy runs (15 / 20 / 25 min), 40-55 min easy / stride / tempo / threshold runs,
20-120 min trainer rides (recovery, base, tempo, threshold, VO2), a 120 min outdoor ride, five pool sessions and three
maintenance / prehab lifts. All are ``agent_authored``, ``lib_default``, ``needs_review``; specs and the load derivation
live in ``triplan.library.build`` (GAP_ENDURANCE_SPECS, GAP_STRENGTH_SPECS, gap_fill_rows) so a rebuild reproduces them.
Existing rows are untouched. The table is schema-validated and the CSV is replaced atomically (temp file + os.replace);
re-running replaces the same codes with identical rows.

Command (repo root): ``PYTHONPATH=src python scripts/migrations/m2026_11_16_library_gap_fill.py``
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from triplan.library.build import GAP_FILL_CODES, gap_fill_rows  # noqa: E402
from triplan.library.schema import WORKOUT_COLUMNS, validate_table  # noqa: E402


def main():
    path = os.path.join(ROOT, "data", "workout_library.csv")
    df = pd.read_csv(path, keep_default_na=True)
    df = df[~df["code"].isin(GAP_FILL_CODES)]
    new = pd.DataFrame(gap_fill_rows(), columns=WORKOUT_COLUMNS)
    out = validate_table(pd.concat([df[WORKOUT_COLUMNS], new], ignore_index=True))
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    print(f"wrote {path}: {len(out)} rows (+{len(new)} gap-fill workouts)")


if __name__ == "__main__":
    main()
