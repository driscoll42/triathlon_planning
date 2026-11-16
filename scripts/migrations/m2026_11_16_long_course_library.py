"""
MIGRATION (TASK-040, 2026-11-16). Run AFTER m2026_11_15_build_canonical_library.py (idempotent).

Adds long-course long sessions to ``data/workout_library.csv`` (agent_authored, ``lib_default``): easy Z1/Z2 long runs of
90 / 105 / 120 / 150 min and outdoor Z1/Z2 long rides of 210 / 240 / 300 / 360 min. Specs and the load derivation live in
``triplan.library.build`` (LONG_RUN_SPECS, LONG_RIDE_SPECS, long_endurance_rows) so a rebuild reproduces the same rows.
Existing rows (incl. the anchors BIKE_DUR_OUT_DEF / RUN_AE2_LONG) are untouched. The table is schema-validated and the
CSV is replaced atomically (temp file + os.replace); re-running replaces the same codes with identical rows.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from triplan.library.build import LONG_RIDE_SPECS, LONG_RUN_SPECS, long_endurance_rows  # noqa: E402
from triplan.library.schema import WORKOUT_COLUMNS, validate_table  # noqa: E402


def main():
    path = os.path.join(ROOT, "data", "workout_library.csv")
    df = pd.read_csv(path, keep_default_na=True)
    codes = set(LONG_RUN_SPECS) | set(LONG_RIDE_SPECS)
    df = df[~df["code"].isin(codes)]
    new = pd.DataFrame(long_endurance_rows(), columns=WORKOUT_COLUMNS)
    out = validate_table(pd.concat([df[WORKOUT_COLUMNS], new], ignore_index=True))
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    print(f"wrote {path}: {len(out)} rows (+{len(new)} long-course sessions)")


if __name__ == "__main__":
    main()
