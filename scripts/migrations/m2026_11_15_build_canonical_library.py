"""
ONE-TIME MIGRATION (TASK-011, 2026-11-15). Historical lineage record, not part of the runtime.

Merged every workout source into data/workout_library.csv:
  data/Run.xlsx, Bike.xlsx, Swim.xlsx (80/20 scrape + 14 custom rows), recommended_workout_legos.csv,
  data/strength_workouts.csv (subset of legos), data/brick_workouts.csv, and the hand-coded
  build_default_candidate_library() / build_hybrid_candidate_library() lists in closed_loop_optimizer.py.

It runs only against commit a3bfabc, where the hand-coded lists still exist. Afterwards the CSV
is the hand-editable source of truth (later versioned steps: m2026_11_16_cap_compatible_heavy_lifts.py); refresh 80/20 rows with library.build.refresh_from_8020_scrape.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from triplan.physiology import load_model as lm  # noqa: E402
from triplan.library.build import (  # noqa: E402
    BIKE_ZONE_IF, RUN_ZONE_IF, ZONE_CORRECTIONS, _blank_row, derive_8020_rows, endurance_flags, endurance_loads, if_from_tss,
    mechanical_strain, ans_cost, read_8020_scrape, zone_tss,
)
from triplan.library.schema import WORKOUT_COLUMNS, validate_table  # noqa: E402

COLLISIONS = {"BIKE_DUR_OUT", "BIKE_TE1_PYR", "RUN_SS1_STR", "RUN_TB1_BRICK", "STR_MS1_HEAVY"}

# Formerly KNOWN_STRENGTH_CONFIGS inside enrich_workouts_from_df's row loop.
STRENGTH = {
    "STR_AA1":       dict(split_group="HLM", bb=0,  db=12, neuro=45.0,  axial=30.0, sets=(3, 3, 3, 3), sq=0, dl=0, pr=0),
    "STR_MS1_HEAVY": dict(split_group="HLM", bb=8,  db=0,  neuro=105.0, axial=90.0, sets=(3, 2, 3, 0), sq=1, dl=1, pr=1),
    "STR_MS2_HEAVY": dict(split_group="HLM", bb=10, db=0,  neuro=95.0,  axial=80.0, sets=(0, 7, 3, 0), sq=0, dl=1, pr=1),
    "STR_SM1_MAINT": dict(split_group="HLM", bb=6,  db=0,  neuro=65.0,  axial=60.0, sets=(2, 2, 2, 0), sq=1, dl=1, pr=1),
    "STR_SM2_MAINT": dict(split_group="HLM", bb=2,  db=4,  neuro=55.0,  axial=45.0, sets=(2, 2, 2, 0), sq=0, dl=1, pr=1),
    "STR_PLYO1":     dict(split_group="UL",  bb=0,  db=0,  neuro=80.0,  axial=15.0, sets=(4, 4, 0, 0), sq=0, dl=0, pr=0),
    "STR_UPPER_HYP": dict(split_group="UL",  bb=0,  db=12, neuro=50.0,  axial=10.0, sets=(0, 0, 6, 6), sq=0, dl=0, pr=1),
    "STR_LOWER_UNI": dict(split_group="UL",  bb=0,  db=11, neuro=60.0,  axial=25.0, sets=(5, 6, 0, 0), sq=0, dl=0, pr=0),
    "STR_POST_CORE": dict(split_group="UL",  bb=3,  db=3,  neuro=55.0,  axial=15.0, sets=(0, 6, 0, 0), sq=0, dl=0, pr=0),
    "STR_DELOAD":    dict(split_group="HLM", bb=0,  db=0,  neuro=15.0,  axial=5.0,  sets=(2, 2, 2, 2), sq=0, dl=0, pr=0),
}


def endurance_row(code, sport, desc, duration, z, tss, if_, np_, provenance, source, **extra):
    row = _blank_row()
    row.update(code=code, name=code, sport=sport, description=desc, duration_min=float(duration),
               tss=float(tss), intensity_factor=float(if_), normalized_power=float(np_ or 0.0),
               provenance=provenance, source=source,
               z1_min=z[0], z2_min=z[1], z3_min=z[2], z4_min=z[3], z5_min=z[4])
    flags = endurance_flags(sport, desc, duration, z)
    row.update(flags)
    row.update(endurance_loads(sport, tss, if_, duration, z, bool(flags.get("has_strides"))))
    row.update(extra)
    return row


def main():
    frames, files = read_8020_scrape(*(os.path.join(ROOT, "data", f) for f in ("Run.xlsx", "Bike.xlsx", "Swim.xlsx")))
    rows = derive_8020_rows(frames, files).to_dict(orient="records")

    legos = pd.read_csv(os.path.join(ROOT, "recommended_workout_legos.csv")).set_index("Code")
    bricks = pd.read_csv(os.path.join(ROOT, "data", "brick_workouts.csv")).set_index("Code")

    # 1) 14 custom (underscore) xlsx rows -> agent_authored, enriched with legos category / adaptations
    for sport, df in frames.items():
        for r in df.to_dict(orient="records"):
            code = str(r["Code"])
            if "_" not in code:
                continue
            z = [float(r.get(f"Zone {k}") or 0.0) for k in range(1, 6)]
            duration = float(r["Duration"])
            notes = ""
            if code in ZONE_CORRECTIONS:
                c = ZONE_CORRECTIONS[code]
                duration = c["duration_min"]
                z = [c["z1_min"], c["z2_min"], c["z3_min"], c["z4_min"], c["z5_min"]]
                notes = "Zone minutes corrected from description (TASK-011)."
            lego = legos.loc[code] if code in legos.index else None
            extra = dict(category=str(lego["Category"]) if lego is not None else "",
                         target_adaptations=str(lego["Target Adaptations"]) if lego is not None else "",
                         notes=notes, needs_review=True)
            if sport == "Swim":
                extra.update(distance=float(r.get("Distance") or 0.0), distance_unit="pool_units")
            rows.append(endurance_row(code, sport, str(r["Description"]), duration, z, float(r["TSS"]),
                                      float(r.get("Intensity Factor") or 0.0), r.get("Normalized Power"),
                                      "agent_authored", files[sport], **extra))

    # 2) legos rows not in the xlsx: strength templates and bricks
    seen = {r["code"] for r in rows}
    for code, r in legos.iterrows():
        if code in seen:
            continue
        z = [float(r[f"Zone {k}"]) for k in range(1, 6)]
        if r["Type"] == "Strength":
            k = STRENGTH[code]
            row = _blank_row()
            row.update(code=code, name=code, sport="Strength", category=str(r["Category"]), provenance="agent_authored",
                       source="recommended_workout_legos.csv", description=str(r["Description"]),
                       duration_min=float(r["Duration"]), tss=float(r["TSS"]), intensity_factor=float(r["Intensity Factor"]),
                       z1_min=z[0], z2_min=z[1], z3_min=z[2], z4_min=z[3], z5_min=z[4],
                       mechanical_strain=lm.strength_strain(code) if code in lm.STRENGTH_SET_SPECS else 0.0,  # TASK-021 model
                       ans_cost=round(ans_cost(z, False, float(r["Duration"])), 1), durability_score=2.0,
                       neuromuscular_load=k["neuro"], axial_load=k["axial"], is_heavy_strength=("MS" in code or "HEAVY" in code),
                       heavy_sq=bool(k["sq"]), heavy_dl=bool(k["dl"]), heavy_press=bool(k["pr"]), split_group=k["split_group"],
                       bb_sets=k["bb"], db_sets=k["db"], sets_anterior_chain=k["sets"][0], sets_posterior_chain=k["sets"][1],
                       sets_push=k["sets"][2], sets_pull=k["sets"][3], target_adaptations=str(r["Target Adaptations"]),
                       needs_review=True,
                       notes="Strength TSS / strain recalibrated by m2026_11_19_unify_load_model.py (TASK-021).")
            rows.append(row)
        else:
            rows.append(endurance_row(code, "Brick", str(r["Description"]), float(r["Duration"]), z, float(r["TSS"]),
                                      float(r["Intensity Factor"]), 0.0, "agent_authored", "recommended_workout_legos.csv",
                                      category=str(r["Category"]), target_adaptations=str(r["Target Adaptations"]),
                                      durability_score=8.5, needs_review=True))
        seen.add(code)

    # 3) data/brick_workouts.csv rows without TSS (BR1-BR6): TSS estimated from zone minutes (bike IF midpoints)
    for code, r in bricks.iterrows():
        if code in seen:
            continue
        z = [float(r[f"Zone {k}"]) for k in range(1, 6)]
        tss = zone_tss(z)
        rows.append(endurance_row(code, "Brick", str(r["Description"]), float(r["Duration"]), z, tss,
                                  if_from_tss(tss, float(r["Duration"])), 0.0, "agent_authored", "data/brick_workouts.csv",
                                  durability_score=8.5, needs_review=True,
                                  notes="TSS estimated from zone minutes (NP-style method, legacy brick multipliers; no source TSS)."))
        seen.add(code)

    # 4) hand-coded libraries (exact authored values; colliding default codes get a _DEF suffix)
    from triplan.library.candidates import build_default_candidate_library, build_hybrid_candidate_library
    for lib_name, lib in (("default", build_default_candidate_library()), ("hybrid", build_hybrid_candidate_library())):
        for w in lib:
            code = w.code + "_DEF" if (lib_name == "default" and w.code in COLLISIONS) else w.code
            f = w.flags or {}
            row = _blank_row()
            z = list(w.zone_durations)
            row.update(code=code, name=w.name, sport=w.sport, provenance=f"hand_coded_{lib_name}",
                       source=f"closed_loop_optimizer.build_{lib_name}_candidate_library (a3bfabc)",
                       description=w.name, duration_min=float(w.duration), tss=float(w.aerobic_tss),
                       intensity_factor=if_from_tss(w.aerobic_tss, w.duration),
                       z1_min=z[0], z2_min=z[1], z3_min=z[2], z4_min=z[3], z5_min=z[4],
                       mechanical_strain=w.mechanical_strain, ans_cost=w.ans_cost, durability_score=w.durability_score,
                       neuromuscular_load=w.neuromuscular_load, axial_load=w.axial_load,
                       is_hard_endurance=bool(w.is_hard_endurance), is_heavy_run=bool(w.is_heavy_run or f.get("heavy_run")),
                       is_long_run=bool(f.get("long_run")), is_heavy_strength=bool(w.is_heavy_strength),
                       is_anchor=bool(w.is_anchor), has_strides=bool(w.has_strides),
                       is_test=bool(f.get("test")), heavy_sq=bool(f.get("heavy_sq")), heavy_dl=bool(f.get("heavy_dl")),
                       heavy_press=bool(f.get("heavy_press")), split_group=w.split_group, bb_sets=w.bb_sets, db_sets=w.db_sets,
                       sets_anterior_chain=w.sets_by_muscle.get("Anterior_Chain", 0), sets_posterior_chain=w.sets_by_muscle.get("Posterior_Chain", 0),
                       sets_push=w.sets_by_muscle.get("Push", 0), sets_pull=w.sets_by_muscle.get("Pull", 0),
                       preference_penalty=w.preference_penalty, lib_default=(lib_name == "default"), lib_hybrid=(lib_name == "hybrid"))
            if w.sport == "Bike":
                if lib_name == "hybrid":   # hybrid engine gated rides only by the outdoor flag
                    outdoor = bool(f.get("outdoor_bike"))
                else:                      # triathlon engine gated rides by is_indoor_bike
                    outdoor = not w.is_indoor_bike
                row.update(is_indoor_bike=not outdoor, is_outdoor_bike=outdoor,
                           hi_torque_bike=bool(f.get("hi_torque_bike")))
            if w.sport == "Swim":
                row.update(pool=True, swim_threshold=bool(f.get("swim_th")) or bool(w.is_hard_endurance))
            if code == "RUN_TB1_BRICK_DEF":
                row.update(is_hard_endurance=True, notes="Audit fix: 15 of 30 min in Z3 => hard (TASK-011).")
            if code != w.code:
                row["notes"] = (row["notes"] + " " if row["notes"] else "") + \
                    f"Renamed from {w.code}: a different authored workout owns that code."
            rows.append(row)

    # 5) new stride variants (audit: only two stride workouts existed, both runs)
    def stride_row(code, sport, desc, z, zone_if, default=True):
        dur = sum(z)
        tss = zone_tss(z, zone_if)
        return endurance_row(code, sport, desc, dur, z, tss, if_from_tss(tss, dur), 0.0, "agent_authored",
                             "TASK-011 stride variants", lib_default=default, needs_review=True,
                             notes="Added for stride-session coverage (audit). Alactic strides <= 20 s.")
    rows.append(stride_row("RUN_AE1_STR30", "Run",
                           "5 min Z1, 18 min Z2, 6 x (20 sec Z5 strides / 60 sec Z1 walk-jog), 2 min Z1",
                           [13.0, 18.0, 0.0, 0.0, 2.0], RUN_ZONE_IF))
    rows.append(stride_row("BIKE_AE2_CAD_STR", "Bike",
                           "10 min Z1, 25 min Z2, 8 x (15 sec high-cadence Z5 strides / 45 sec Z1), 2 min Z1",
                           [18.0, 25.0, 0.0, 0.0, 2.0], BIKE_ZONE_IF))

    df = pd.DataFrame(rows, columns=WORKOUT_COLUMNS)
    df = validate_table(df)
    out = os.path.join(ROOT, "data", "workout_library.csv")
    df.to_csv(out, index=False)
    print(f"wrote {out}: {len(df)} rows")
    print(df.groupby(["provenance", "sport"]).size().to_string())


if __name__ == "__main__":
    main()
