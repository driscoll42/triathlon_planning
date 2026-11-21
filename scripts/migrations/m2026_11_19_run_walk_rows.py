"""
MIGRATION (TASK-058, 2026-11-19). Run AFTER m2026_11_19_unify_load_model.py (idempotent).

Adds the run/walk (walk-jog) re-entry workouts ``RUN_RW_*`` to ``data/workout_library.csv``: 1:1 and 2:1 run:walk at 20
and 30 minutes. All are ``agent_authored``, ``lib_default``, ``needs_review``. Their mechanical strain comes from the
TASK-023 impact model (``load_model.run_walk_mechanical_strain``); duration and TSS conservatively count walked minutes as
Zone 1 running; specs and the derivation live in ``triplan.library.build`` (RUN_WALK_SPECS, run_walk_rows) so a rebuild
reproduces them. Existing rows are untouched. The table is schema-validated and the CSV is replaced atomically (temp file +
os.replace); re-running replaces the same codes with identical rows.

Command (repo root): ``PYTHONPATH=src python scripts/migrations/m2026_11_19_run_walk_rows.py``
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from triplan.library.build import RUN_WALK_CODES, run_walk_rows  # noqa: E402
from triplan.library.schema import WORKOUT_COLUMNS, validate_table  # noqa: E402


def main():
    path = os.path.join(ROOT, "data", "workout_library.csv")
    df = pd.read_csv(path, keep_default_na=True)
    df = df[~df["code"].isin(RUN_WALK_CODES)]
    new = pd.DataFrame(run_walk_rows(), columns=WORKOUT_COLUMNS)
    out = validate_table(pd.concat([df[WORKOUT_COLUMNS], new], ignore_index=True))
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    print(f"wrote {path}: {len(out)} rows (+{len(new)} run/walk workouts)")


if __name__ == "__main__":
    main()
