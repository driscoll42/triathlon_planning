"""
MILP-Optimized 20-Week Level 0 Hybrid Athlete Training Plan Generator (Duathlon + Strength).

Drives ``generate_closed_loop_season_schedule`` with ``CURRENT_ATHLETE`` (no pool access,
20 weeks = 4-week re-entry + 16-week Olympic progression) and the default candidate library
(including ``8020_library`` endurance workouts from ``Run.xlsx``/``Bike.xlsx``/``Swim.xlsx``
alongside strength and gap-fill sessions).
"""

from __future__ import annotations

import contextlib
import io
from collections import Counter
from typing import Dict, List

import pandas as pd

from triplan.control.season import WeekRecord, generate_closed_loop_season_schedule
from triplan.library.candidates import build_default_candidate_library
from triplan.library.compound import COMBO_SEPARATOR
from triplan.library.loader import load_library
from triplan.paths import training_plan_path
from triplan.physiology.athlete_profile import CURRENT_ATHLETE

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _description_map() -> Dict[str, str]:
    lib_df = load_library(profile=CURRENT_ATHLETE)
    return dict(zip(lib_df["code"], lib_df["description"]))


def _describe(code: str, name: str, desc_map: Dict[str, str]) -> str:
    if code in desc_map and pd.notna(desc_map[code]):
        return str(desc_map[code])
    if COMBO_SEPARATOR in code:
        base, suffix = code.split(COMBO_SEPARATOR, 1)
        base_desc = desc_map.get(base, name)
        return f"{base_desc} + {suffix}"
    return name


def build_20_week_plan(time_limit_per_week: float = 3.5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate the 20-week MILP-optimized hybrid plan and write CSV outputs."""
    lib = build_default_candidate_library()
    records: List[WeekRecord] = []
    with contextlib.redirect_stdout(io.StringIO()):
        _daily_raw, summary_df = generate_closed_loop_season_schedule(
            profile=CURRENT_ATHLETE,
            distance="olympic",
            library=lib,
            time_limit_per_week=time_limit_per_week,
            weeks_to_race=16,
            reentry_weeks=4,
            collect=records,
        )

    desc_map = _description_map()
    daily_rows = []
    for rec in records:
        for d, day_name in enumerate(DAY_NAMES):
            wkts = rec.schedule.get(d, [])
            if not wkts:
                daily_rows.append({
                    "Week": rec.week_num,
                    "Phase": rec.phase,
                    "Step_Type": rec.step_type,
                    "Day": day_name,
                    "Sport": "Rest",
                    "Code": "REST",
                    "Provenance": "",
                    "Description": "Scheduled Complete Rest Day",
                    "Duration_min": 0.0,
                    "TSS": 0.0,
                    "Mechanical_Strain": 0.0,
                    "Axial_Load": 0.0,
                    "Neuromuscular_Load": 0.0,
                    "BB_Sets": 0,
                    "DB_Sets": 0,
                    "Intensity": "REST",
                })
                continue
            for w in wkts:
                daily_rows.append({
                    "Week": rec.week_num,
                    "Phase": rec.phase,
                    "Step_Type": rec.step_type,
                    "Day": day_name,
                    "Sport": w.sport,
                    "Code": w.code,
                    "Provenance": getattr(w, "provenance", "") or "",
                    "Description": _describe(w.code, w.name, desc_map),
                    "Duration_min": round(float(w.duration), 1),
                    "TSS": round(float(w.aerobic_tss), 1),
                    "Mechanical_Strain": round(float(w.mechanical_strain), 1),
                    "Axial_Load": round(float(w.axial_load), 1),
                    "Neuromuscular_Load": round(float(w.neuromuscular_load), 1),
                    "BB_Sets": int(getattr(w, "bb_sets", 0)),
                    "DB_Sets": int(getattr(w, "db_sets", 0)),
                    "Intensity": "HARD" if w.is_hard_endurance or w.is_heavy_strength else "EASY",
                })

    daily_df = pd.DataFrame(daily_rows)
    daily_path = training_plan_path("level0_hybrid_20w_daily.csv")
    summary_path = training_plan_path("level0_hybrid_20w_summary.csv")
    daily_df.to_csv(daily_path, index=False)
    summary_df.to_csv(summary_path, index=False)

    prov_counts = Counter(daily_df[daily_df["Code"] != "REST"]["Provenance"])
    print("Successfully generated and saved:")
    print(f" - {daily_path} (rows: {len(daily_df)})")
    print(f" - {summary_path} (rows: {len(summary_df)})")
    print(" - Selected workout provenances:", dict(prov_counts))
    cols = [
        "Week", "Phase", "Step_Type", "Target_TSS", "Solved_TSS",
        "Ending_CTL", "TSB", "Ending_ACWR", "Long_Run_Ratio", "Solve_Status",
    ]
    print("\nSummary Preview:")
    print(summary_df[cols].to_string(index=False))
    return daily_df, summary_df


if __name__ == "__main__":
    build_20_week_plan()
