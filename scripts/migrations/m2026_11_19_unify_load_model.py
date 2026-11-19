"""
MIGRATION (TASK-021, ADR-034, 2026-11-19). Idempotent. Run AFTER the m2026_11_15..18 migrations.

Rewrites ``data/workout_library.csv`` load columns with the unified load model (``triplan.physiology.load_model``):
  * ``ans_cost``: SPEC 7-zone weights (0, 0, 0.7, 1.0, 1.4, 1.8, 3.5) + long-duration surcharge, alactic strides exempt;
  * ``mechanical_strain``: zone-additive power model (run / bike / swim / brick legs), set-intensity model (strength);
  * strength ``tss`` / ``intensity_factor``: 10-20 aerobic TSS per hour;
  * Run / Brick neuromuscular + axial loads re-derived from the new strain where they followed the library rule;
  * Brick ``brick_run_strain``.
Endurance aerobic TSS is untouched. The CSV is written to a temp file and replaced atomically.

Command (repo root): ``python scripts/migrations/m2026_11_19_unify_load_model.py``
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from triplan.library.build import recompute_load_columns  # noqa: E402
from triplan.library.schema import WORKOUT_COLUMNS, validate_table  # noqa: E402


def main() -> None:
    path = os.path.join(ROOT, "data", "workout_library.csv")
    df = pd.read_csv(path, keep_default_na=True)
    out = validate_table(recompute_load_columns(df)[WORKOUT_COLUMNS])
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    print(f"wrote {path}: {len(out)} rows (load columns recomputed with the unified TASK-021 model)")


if __name__ == "__main__":
    main()
