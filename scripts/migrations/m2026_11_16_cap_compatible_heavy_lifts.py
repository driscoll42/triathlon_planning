"""
MIGRATION (TASK-033, 2026-11-16). Run AFTER m2026_11_15_build_canonical_library.py (idempotent).

Adds four heavy-labelled lifts (main lift >= 85% 1RM, ``is_heavy_strength``) whose neuromuscular / axial loads fit
CURRENT_ATHLETE's daily Tier-0 caps (axial <= 65, neuro <= 75; ADR-012 DECISION-023 keeps those caps). The existing
STR_MS1_HEAVY / STR_MS2_HEAVY / STR_MS1_HEAVY_DEF rows (axial 80-95) are untouched. Specs and the load model live in
``triplan.library.build`` (HEAVY_VARIANTS, strength_loads) so a rebuild reproduces the same rows. The CSV is written to
a temp file and replaced atomically.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from triplan.library.build import (  # noqa: E402
    HEAVY_VARIANT_ZONES, HEAVY_VARIANTS, _blank_row, ans_cost, mechanical_strain, strength_loads,
)
from triplan.library.schema import WORKOUT_COLUMNS, validate_table  # noqa: E402

SOURCE = "scripts/migrations/m2026_11_16_cap_compatible_heavy_lifts.py"
DURATION, TSS, IF = 45.0, 38.88, 0.72   # same envelope as STR_SM1_MAINT (zones 20/20/0/5)


def variant_rows():
    rows = []
    z = HEAVY_VARIANT_ZONES
    for code, v in HEAVY_VARIANTS.items():
        row = _blank_row()
        loads = strength_loads(v["lower"], v["bench"])
        row.update(
            code=code, name=code, sport="Strength", category=v["category"], provenance="agent_authored", source=SOURCE,
            description=v["description"], duration_min=DURATION, tss=TSS, intensity_factor=IF,
            z1_min=z[0], z2_min=z[1], zx_min=z[2], z3_min=z[3], zy_min=z[4], z4_min=z[5], z5_min=z[6],
            mechanical_strain=mechanical_strain("Strength", TSS, IF, DURATION),
            ans_cost=round(ans_cost([z[0], z[1], z[3], z[5], z[6]], False), 1), durability_score=2.0,
            neuromuscular_load=loads["neuromuscular_load"], axial_load=loads["axial_load"],
            is_heavy_strength=True, heavy_sq=bool(v["sq"]), heavy_dl=bool(v["dl"]), heavy_press=True,
            lib_default=True, lib_hybrid=True, needs_review=True, split_group="HLM", bb_sets=v["bb"], db_sets=v["db"],
            sets_anterior_chain=v["sets"][0], sets_posterior_chain=v["sets"][1], sets_push=v["sets"][2],
            sets_pull=v["sets"][3], target_adaptations=v["adaptations"],
            notes="Cap-compatible heavy lift (TASK-033): loads from library.build.strength_loads; TSS awaits TASK-021.")
        rows.append(row)
    return rows


def main():
    path = os.path.join(ROOT, "data", "workout_library.csv")
    df = pd.read_csv(path, keep_default_na=True)
    df = df[~df["code"].isin(HEAVY_VARIANTS)]
    new = pd.DataFrame(variant_rows(), columns=WORKOUT_COLUMNS)
    out = validate_table(pd.concat([df[WORKOUT_COLUMNS], new], ignore_index=True))
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    print(f"wrote {path}: {len(out)} rows (+{len(new)} heavy variants)")


if __name__ == "__main__":
    main()
