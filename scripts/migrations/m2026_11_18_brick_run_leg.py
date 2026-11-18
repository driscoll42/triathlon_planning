"""
MIGRATION (TASK-059, 2026-11-18). Idempotent.

1. Adds ``brick_run_min``, ``brick_run_strain``, and ``is_brick_run_hard`` to ``data/workout_library.csv``
   and backfills the 9 existing ``Brick`` rows (``RUN_TB1_BRICK``, ``RUN_TB2_BRICK``, ``SW_T1_BRICK``,
   ``BR1``..``BR6``) from ``EXISTING_BRICK_RUN_ZONES`` via ``brick_run_loads``.
2. Upserts the short Bike->Run brick rows from ``SHORT_BRICK_SPECS`` / ``short_brick_rows``.

Command (repo root): ``python scripts/migrations/m2026_11_18_brick_run_leg.py``
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from triplan.library.build import (  # noqa: E402
    EXISTING_BRICK_RUN_ZONES,
    SHORT_BRICK_SPECS,
    brick_run_loads,
    short_brick_rows,
)
from triplan.library.schema import WORKOUT_COLUMNS, validate_table  # noqa: E402
from triplan.physiology.athlete_profile import CURRENT_ATHLETE  # noqa: E402


def main() -> None:
    path = os.path.join(ROOT, "data", "workout_library.csv")
    df = pd.read_csv(path, keep_default_na=True)
    for col, default in (("brick_run_min", 0.0), ("brick_run_strain", 0.0), ("is_brick_run_hard", False)):
        if col not in df.columns:
            df[col] = default

    # Remove any prior short-brick rows so re-running replaces them cleanly.
    df = df[~df["code"].isin(SHORT_BRICK_SPECS)].copy()

    # Backfill existing Brick rows explicitly from EXISTING_BRICK_RUN_ZONES.
    for code, z_run in EXISTING_BRICK_RUN_ZONES.items():
        mask = df["code"] == code
        if mask.any():
            loads = brick_run_loads(z_run, CURRENT_ATHLETE.run_cp)
            for k, v in loads.items():
                df.loc[mask, k] = v

    new = pd.DataFrame(short_brick_rows(CURRENT_ATHLETE), columns=WORKOUT_COLUMNS)
    out = validate_table(pd.concat([df[WORKOUT_COLUMNS], new], ignore_index=True))
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    print(f"wrote {path}: {len(out)} rows (+{len(new)} short brick rows)")


if __name__ == "__main__":
    main()
