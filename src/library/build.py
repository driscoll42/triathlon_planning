"""
Build-time derivation for the canonical workout library (TASK-011).

``data/workout_library.csv`` is the single source of truth for every workout, and it is
hand-editable. Behavior flags are explicit columns. They are derived here once, at build or
refresh time, and are never inferred from code substrings at solve time (the old
``enrich_workouts_from_df`` did that).

* ``derive_8020_rows``: deterministic, documented rules that turn scraped 80/20 rows
  (``data/Run.xlsx``, ``Bike.xlsx``, ``Swim.xlsx`` = scrape cache) into canonical rows.
* ``refresh_from_8020_scrape``: re-derives the 80/20 rows after a re-scrape and keeps every
  non-80/20 row (agent-authored, hand-coded) exactly as authored.

Rules (fixing the audit defects in TASK-011):
* hard (key) session: >= 15 min in Z3+ or >= 5 min in Z4+. Minutes, not IF, so swims are no
  longer "hard" just because the swim IF scale starts at 0.795.
* strides: only when the description says "stride". 80/20 hill reps are 30 s Zone 5, which is
  not alactic (<= 20 s, SPEC.md). Code substrings are not used: 'SS' in code used to mark
  every SSP*/SSI*/SW_CSS_* swim as strides.
* indoor bike: every 80/20 ride is trainer-executable (no duration guess).
* run: heavy if hard or >= 70 min; long if >= 60 min.
* swim: pool always; CSS-threshold if hard.
* bike: high-torque if hard, or if the description prescribes cadence <= 70 rpm.
"""

from __future__ import annotations

import math
import os
import re
from typing import Dict, List, Optional

import pandas as pd

from library.schema import WORKOUT_COLUMNS, validate_table

# Explicit, reviewed corrections to scraped rows whose zone minutes did not sum to their duration.
ZONE_CORRECTIONS: Dict[str, Dict[str, float]] = {
    # "5 minutes Zone 1, 6 x (20" Zone 4/40" rest)": the 6 x 40 s rests (4 min) were omitted.
    "RTa1": {"duration_min": 11.0, "z1_min": 9.0, "z2_min": 0.0, "z3_min": 0.0, "z4_min": 2.0, "z5_min": 0.0},
    # "10 Z1, 10 Z2, 3 x (12 Z3 / 4 Z1), 13 Z1" = 81 min (Z1 35, Z2 10, Z3 36); the row said 75.
    "BK_FR_TQ2": {"duration_min": 81.0, "z1_min": 35.0, "z2_min": 10.0, "z3_min": 36.0, "z4_min": 0.0, "z5_min": 0.0},
}

_LOW_CADENCE = re.compile(r"(\d{2,3})\s*(?:-\s*(\d{2,3}))?\s*rpm", re.IGNORECASE)

# Zone intensity multipliers (fraction of CP) used for TSS estimation from zone minutes.
BIKE_ZONE_IF = (0.60, 0.765, 0.955, 1.06, 1.30)    # classes.triVariables.bike_zone_power_multiplier
RUN_ZONE_IF = (0.63, 0.82, 0.97, 1.115, 1.35)      # classes.triVariables.run_zone_power_multiplier
BRICK_ZONE_IF = (0.615, 0.79, 0.96, 1.09, 1.325)   # legacy util.load_workouts brick multipliers


def ans_cost(z: List[float], has_strides: bool) -> float:
    """C_ans = sum_z T_z * e^(0.65 (z-1)); alactic strides exempt Z5 (weight 1.0). Mirrors SPEC/ADR-009."""
    w = [math.exp(0.65 * k) for k in range(5)]
    return float(sum(z[k] * (1.0 if (k == 4 and has_strides) else w[k]) for k in range(5)))


def mechanical_strain(sport: str, tss: float, intensity_factor: float, duration: float) -> float:
    if sport == "Swim":
        return round(0.05 * tss, 1)
    if sport == "Bike":
        return round(0.10 * tss * (intensity_factor ** 0.2), 1)
    if sport == "Run":
        return round(1.00 * tss * intensity_factor, 1)
    if sport == "Strength":
        return round(1.50 * max(tss, 15.0) * (duration / 45.0) * 2.5, 1)
    if sport == "Brick":
        return round(0.55 * tss * intensity_factor, 1)
    return round(0.50 * tss, 1)


def zone_tss(z5: List[float], zone_if=BRICK_ZONE_IF) -> float:
    """TSS from zone minutes via an NP-style 4th-power mean (same method as legacy util.load_workouts):
    IF = (sum_z m_z^4 t_z / T)^(1/4); TSS = 100 * IF^2 * T/60."""
    tot = sum(z5)
    if tot <= 0:
        return 0.0
    if_ = (sum((zone_if[k] ** 4) * t for k, t in enumerate(z5)) / tot) ** 0.25
    return round(100.0 * if_ ** 2 * tot / 60.0, 2)


def if_from_tss(tss: float, duration_min: float) -> float:
    return round(math.sqrt(tss / (duration_min / 60.0 * 100.0)), 3) if tss > 0 and duration_min > 0 else 0.0


def durability(sport: str, duration: float, high: float, code: str) -> float:
    if duration >= 150:
        return 8.5
    if duration >= 120:
        return 7.0
    if sport == "Run" and duration >= 70:
        return 7.5
    if sport == "Brick":
        return 8.5
    if high >= 25.0:
        return 6.5
    return 2.0


def _blank_row() -> Dict[str, object]:
    row: Dict[str, object] = {c: None for c in WORKOUT_COLUMNS}
    for c in ("zx_min", "zy_min", "normalized_power", "intensity_factor", "bb_sets", "db_sets",
              "sets_anterior_chain", "sets_posterior_chain", "sets_push", "sets_pull"):
        row[c] = 0
    for c in ("is_hard_endurance", "is_heavy_run", "is_long_run", "is_heavy_strength", "is_anchor", "has_strides",
              "is_indoor_bike", "is_outdoor_bike", "is_test", "pool", "swim_threshold", "hi_torque_bike",
              "heavy_sq", "heavy_dl", "heavy_press", "lib_default", "lib_hybrid", "needs_review"):
        row[c] = False
    row.update(split_group="ANY", preference_penalty=0.1, category="", distance_unit="", target_adaptations="", notes="")
    return row


def endurance_flags(sport: str, desc: str, duration: float, z: List[float]) -> Dict[str, object]:
    """Deterministic endurance flag rules (module docstring). z = (Z1, Z2, Z3, Z4, Z5) minutes."""
    high = z[2] + z[3] + z[4]
    hard = high >= 15.0 or (z[3] + z[4]) >= 5.0
    d = desc.lower()
    f: Dict[str, object] = {"is_hard_endurance": hard}
    if sport == "Run":
        f.update(is_heavy_run=hard or duration >= 70.0, is_long_run=duration >= 60.0, has_strides="stride" in d)
    elif sport == "Bike":
        low_cad = any(int(m.group(2) or m.group(1)) <= 70 for m in _LOW_CADENCE.finditer(desc))
        f.update(is_indoor_bike=True, is_outdoor_bike=False, has_strides="stride" in d or "cadence burst" in d,
                 hi_torque_bike=hard or low_cad or "torque" in d)
    elif sport == "Swim":
        f.update(pool=True, swim_threshold=hard)
    return f


def endurance_loads(sport: str, tss: float, if_: float, duration: float, z: List[float], strides: bool) -> Dict[str, float]:
    mech = mechanical_strain(sport, tss, if_, duration)
    high = z[2] + z[3] + z[4]
    hard = high >= 15.0 or (z[3] + z[4]) >= 5.0
    if sport == "Swim":
        neuro, axial = (20.0 if hard else 10.0), 0.0
    elif sport == "Bike":
        neuro, axial = (round(0.55 * tss, 1), 10.0) if hard else (round(0.25 * tss, 1), 5.0)
    elif sport == "Run":
        heavy = hard or duration >= 70.0
        neuro, axial = (round(0.65 * mech, 1), round(0.38 * mech, 1)) if heavy else (round(0.40 * mech, 1), round(0.30 * mech, 1))
    else:  # Brick
        neuro, axial = round(0.55 * mech, 1), round(0.35 * mech, 1)
    return {"mechanical_strain": mech, "ans_cost": round(ans_cost(z, strides), 1),
            "durability_score": durability(sport, duration, high, ""), "neuromuscular_load": neuro, "axial_load": axial}


def derive_8020_rows(frames: Dict[str, pd.DataFrame], source_files: Dict[str, str]) -> pd.DataFrame:
    """Canonical rows for scraped 80/20 workouts (codes without '_'). Mile-prescribed long runs
    (TSS 0) are skipped here; TASK-012 converts them via the athlete's easy pace."""
    rows = []
    for sport, df in frames.items():
        for r in df.to_dict(orient="records"):
            code = str(r["Code"])
            if "_" in code:
                continue  # custom rows are authored separately
            tss = float(r.get("TSS") or 0.0)
            if tss <= 0:
                continue
            row = _blank_row()
            z = [float(r.get(f"Zone {k}") or 0.0) for k in range(1, 6)]
            duration = float(r.get("Duration") or 0.0)
            row.update(code=code, name=code, sport=sport, provenance="8020_library", source=source_files[sport],
                       description=str(r.get("Description") or ""), duration_min=duration, tss=tss,
                       intensity_factor=float(r.get("Intensity Factor") or 0.0),
                       normalized_power=float(r.get("Normalized Power") or 0.0))
            if sport == "Swim":
                row.update(distance=float(r.get("Distance") or 0.0), distance_unit="pool_units")
            corr = ZONE_CORRECTIONS.get(code)
            if corr:
                row["duration_min"] = corr["duration_min"]
                duration = corr["duration_min"]
                z = [corr["z1_min"], corr["z2_min"], corr["z3_min"], corr["z4_min"], corr["z5_min"]]
                row["notes"] = "Zone minutes corrected from description (TASK-011)."
            row.update(z1_min=z[0], z2_min=z[1], z3_min=z[2], z4_min=z[3], z5_min=z[4])
            flags = endurance_flags(sport, row["description"], duration, z)
            row.update(flags)
            row.update(endurance_loads(sport, tss, row["intensity_factor"], duration, z, bool(flags.get("has_strides"))))
            rows.append(row)
    return pd.DataFrame(rows, columns=WORKOUT_COLUMNS)


def read_8020_scrape(run_xlsx: str, bike_xlsx: str, swim_xlsx: str):
    frames, files = {}, {}
    for sport, path in (("Run", run_xlsx), ("Bike", bike_xlsx), ("Swim", swim_xlsx)):
        frames[sport] = pd.read_excel(path, engine="openpyxl")
        files[sport] = os.path.relpath(path, start=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))).replace("\\", "/")
    return frames, files


def refresh_from_8020_scrape(canonical: pd.DataFrame, run_xlsx: str, bike_xlsx: str, swim_xlsx: str) -> pd.DataFrame:
    """Replace the 80/20-provenance rows with freshly derived ones; keep every other row untouched."""
    frames, files = read_8020_scrape(run_xlsx, bike_xlsx, swim_xlsx)
    fresh = derive_8020_rows(frames, files)
    kept = canonical[canonical["provenance"] != "8020_library"]
    clash = set(fresh["code"]) & set(kept["code"])
    if clash:
        raise ValueError(f"Scraped codes collide with authored rows: {sorted(clash)}")
    out = pd.concat([fresh, kept], ignore_index=True)[WORKOUT_COLUMNS]
    return validate_table(out)
