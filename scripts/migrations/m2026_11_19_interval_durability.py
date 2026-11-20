"""
MIGRATION (TASK-023, ADR-036, 2026-11-19). Idempotent. Run AFTER m2026_11_19_unify_load_model (and m2026_11_15..18).

Adds the interval-parsed durability columns to ``data/workout_library.csv`` and replaces the dual-scale hand-typed
``durability_score`` (0..10 generated rows vs 0..85 hybrid hand-coded rows) with ONE 0..10 score derived from them
(``triplan.library.intervals``):
  * ``t_thr_min``  total Z3/ZY/Z4/Z5 minutes (alactic strides excluded),
  * ``t_cont_min`` longest continuous threshold block,
  * ``late_min``   threshold minutes after 60% of the session,
  * ``durability_score = f(duration, t_cont, t_thr, late, brick)``.
Every other column is untouched. The CSV is written to a temp file and replaced atomically.

Command (repo root): ``python scripts/migrations/m2026_11_19_interval_durability.py``
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from triplan.library.intervals import apply_interval_features  # noqa: E402
from triplan.library.schema import WORKOUT_COLUMNS, validate_table  # noqa: E402


def main() -> None:
    path = os.path.join(ROOT, "data", "workout_library.csv")
    df = pd.read_csv(path, keep_default_na=True)
    for c in ("t_thr_min", "t_cont_min", "late_min"):
        if c not in df.columns:
            df[c] = 0.0
    out = validate_table(apply_interval_features(df)[WORKOUT_COLUMNS])
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    print(f"wrote {path}: {len(out)} rows (interval features + unified durability_score)")


if __name__ == "__main__":
    main()
