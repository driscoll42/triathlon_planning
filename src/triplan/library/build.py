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

from triplan.library.schema import WORKOUT_COLUMNS, ZONE_DISTANCE_COLUMNS, validate_table

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
SWIM_ZONE_CSS_MULT = (0.795, 0.875, 0.98, 1.04, 1.10)  # zone swim speed as a fraction of CSS
YARDS_PER_METER = 1.0936133
# 80/20 swim time trials: 400 + 200 at Zone 3, the CSS test protocol (SPEC.md). Explicit list, not a substring rule.
SWIM_TIME_TRIALS = {"STT1", "STT2"}


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


# -----------------------------------------------------------------------------
# Cap-compatible heavy lifts (TASK-033)
# -----------------------------------------------------------------------------
# The authored strength rows carry hand-set neuromuscular / axial loads (no generating formula existed). This is
# the least-squares line through the four authored barbell rows (STR_MS1_HEAVY, STR_MS2_HEAVY, STR_SM1_MAINT,
# STR_SM2_MAINT) on L = heavy axial barbell lower-body working sets (squat / deadlift / RDL):
#   axial = 35.2 + 8.85 L        neuro = 41.1 + 9.2 L      (reproduces SM2_MAINT 55/60 to within 2)
# Non-axial barbell upper-body sets (bench) add neuromuscular load only; DB accessory sets are not counted.
STRENGTH_AXIAL_BASE, STRENGTH_AXIAL_PER_SET = 35.2, 8.85
STRENGTH_NEURO_BASE, STRENGTH_NEURO_PER_SET = 41.1, 9.2
STRENGTH_NEURO_PER_BENCH_SET = 2.0


def strength_loads(lower_bb_sets: int, bench_sets: int = 0) -> Dict[str, float]:
    """(neuromuscular_load, axial_load) of a barbell strength session from its working-set counts."""
    return {"neuromuscular_load": round(STRENGTH_NEURO_BASE + STRENGTH_NEURO_PER_SET * lower_bb_sets
                                        + STRENGTH_NEURO_PER_BENCH_SET * bench_sets, 1),
            "axial_load": round(STRENGTH_AXIAL_BASE + STRENGTH_AXIAL_PER_SET * lower_bb_sets, 1)}


# Heavy = main lift >= 85% 1RM. Every variant keeps 3 or fewer heavy lower-body barbell sets so the session fits the
# CURRENT_ATHLETE daily caps (axial <= 65, neuro <= 75) with headroom for a lift + easy-bike combo. Barbell 60% share
# holds per session (bb/(bb+db) >= 0.6). Equipment per ADR-008 / DECISION-022 (barbell, DB <= 75 lb, no trap bar/bands).
HEAVY_VARIANTS = {
    "STR_HV1_SQUAT": dict(
        category="Maximum Strength (cap-compatible)", sq=1, dl=0, lower=3, bench=2, bb=5, db=2, sets=(3, 0, 2, 2),
        description="10 min ramp warm-up, Back Squat (3 x 3 @ 87.5% 1RM, heavy lift), Bench Press (2 x 5 @ 85% 1RM), "
                    "DB Rows (2 x 8 w/ 60lb DBs), 3 min rest between main sets, 5 min cooldown.",
        adaptations="Maximal strength with 3 heavy squat sets: low axial and neuromuscular cost for in-season weeks"),
    "STR_HV2_DEADLIFT": dict(
        category="Maximum Strength (cap-compatible)", sq=0, dl=1, lower=3, bench=2, bb=5, db=2, sets=(0, 3, 2, 2),
        description="10 min ramp warm-up, Conventional Deadlift (3 x 3 @ 87.5% 1RM, heavy lift), Bench Press (2 x 5 @ 85% "
                    "1RM), Pull-Ups (2 x 6), 3 min rest between main sets, 5 min cooldown.",
        adaptations="Posterior chain maximal force with 3 heavy pull sets and upper-body pressing"),
    "STR_HV3_SQUAT_SL": dict(
        category="Maximum Strength (cap-compatible)", sq=1, dl=0, lower=2, bench=3, bb=5, db=3, sets=(2, 3, 3, 0),
        description="10 min ramp warm-up, Back Squat (2 x 4 @ 87.5% 1RM, heavy lift), Bench Press (3 x 5 @ 85% 1RM), "
                    "Single-Leg DB Romanian Deadlift (3 x 8/leg w/ 60lb DBs), 3 min rest between main sets, 5 min cooldown.",
        adaptations="Heavy bilateral squat plus single-leg posterior chain at reduced axial load"),
    "STR_HV4_DEADLIFT_SL": dict(
        category="Maximum Strength (cap-compatible)", sq=0, dl=1, lower=2, bench=3, bb=5, db=3, sets=(3, 2, 3, 0),
        description="10 min ramp warm-up, Conventional Deadlift (2 x 4 @ 87.5% 1RM, heavy lift), Bench Press (3 x 5 @ 85% "
                    "1RM), DB Bulgarian Split Squats (3 x 6/leg w/ 50lb DBs), 3 min rest between main sets, 5 min cooldown.",
        adaptations="Heavy deadlift plus unilateral quad strength at reduced axial load"),
}
HEAVY_VARIANT_ZONES = (20.0, 20.0, 0.0, 5.0, 0.0, 0.0, 0.0)  # 45 min, same envelope as STR_SM1_MAINT (zone minutes)


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
    for c in ("zx_min", "zy_min", *ZONE_DISTANCE_COLUMNS, "normalized_power", "intensity_factor", "bb_sets", "db_sets",
              "sets_anterior_chain", "sets_posterior_chain", "sets_push", "sets_pull"):
        row[c] = 0
    for c in ("is_hard_endurance", "is_heavy_run", "is_long_run", "is_heavy_strength", "is_anchor", "has_strides",
              "is_indoor_bike", "is_outdoor_bike", "is_test", "pool", "swim_threshold", "hi_torque_bike",
              "heavy_sq", "heavy_dl", "heavy_press", "lib_default", "lib_hybrid", "needs_review"):
        row[c] = False
    row.update(split_group="ANY", preference_penalty=0.1, category="", distance_unit="", prescription="time",
               target_adaptations="", notes="")
    return row


# -----------------------------------------------------------------------------
# Distance-prescribed workouts (TASK-012 runs in miles; TASK-013 swims)
# -----------------------------------------------------------------------------
_MILE_SEG = re.compile(r"([\d.]+)\s*miles?\s*(?:Zone\s*(\d)|(at race pace))", re.IGNORECASE)
_MILE_REP = re.compile(r"(\d+)\s*x\s*\(([^)]*)\)", re.IGNORECASE)
RACE_PACE_ZONE = 3  # "at race pace" (marathon pace, RLMS only) is treated as Zone 3
_SWIM_SEG = re.compile(r"(\d+(?:\.\d+)?)\s*Zone\s*(\d)", re.IGNORECASE)


def parse_swim_prescription(desc: str) -> List[float]:
    """'250 Zone 1, 4 x (500 Zone 2, 60" rest) 250 Zone 1' -> pool units in (Z1..Z5); rests ignored."""
    zd = [0.0] * 5

    def add(text: str, mult: float) -> None:
        for m in _SWIM_SEG.finditer(text):
            zd[int(m.group(2)) - 1] += mult * float(m.group(1))

    for rep in _MILE_REP.finditer(desc):
        add(rep.group(2), float(rep.group(1)))
    add(_MILE_REP.sub(" ", desc), 1.0)
    if sum(zd) <= 0:
        raise ValueError(f"no swim segments parsed from {desc!r}")
    return zd


def css_units_per_min(swim_css_m_per_min: float, pool_length: str) -> float:
    """Athlete CSS (m/min) expressed in the pool's distance unit per minute."""
    return swim_css_m_per_min * (YARDS_PER_METER if pool_length.endswith("y") else 1.0)


def parse_mile_prescription(desc: str) -> List[float]:
    """'1 mile Zone 1, 8 x (0.25 mile Zone 3/0.75 mile Zone 2), ...' -> miles in (Z1..Z5)."""
    zd = [0.0] * 5

    def add(text: str, mult: float) -> None:
        for m in _MILE_SEG.finditer(text):
            zone = int(m.group(2)) if m.group(2) else RACE_PACE_ZONE
            zd[zone - 1] += mult * float(m.group(1))

    for rep in _MILE_REP.finditer(desc):
        add(rep.group(2), float(rep.group(1)))
    add(_MILE_REP.sub(" ", desc), 1.0)
    if sum(zd) <= 0:
        raise ValueError(f"no mile segments parsed from {desc!r}")
    return zd


def run_zone_paces(easy_pace_min_per_mile: float) -> List[float]:
    """Pace (min/mile) per zone: the athlete's easy pace is Zone 2; other zones scale by the
    80/20 run power ratios (speed ~ power on flat ground)."""
    return [easy_pace_min_per_mile * RUN_ZONE_IF[1] / m for m in RUN_ZONE_IF]


def power_tss(zone_minutes: List[float], zone_if, cp: float):
    """(NP, IF, TSS) from zone minutes, the same as clean_file.calculate_TSS."""
    tot = sum(zone_minutes)
    if tot <= 0:
        return 0.0, 0.0, 0.0
    np_ = (sum(((cp * m) ** 4) * t for m, t in zip(zone_if, zone_minutes)) / tot) ** 0.25
    if_ = np_ / cp
    return round(np_, 2), round(if_, 4), round(100.0 * if_ ** 2 * tot / 60.0, 2)


def materialize_distance_row(row: Dict[str, object], *, easy_pace_min_per_mile: float, run_cp: float,
                             swim_css: float = 40.0, pool_length: str = "25y") -> Dict[str, object]:
    """Recompute minutes, TSS, flags and loads of a distance-prescribed row from athlete thresholds.
    Runs: minutes from zone paces (easy pace = Z2). Swims (TASK-013): minutes = distance / (CSS x zone
    multiplier) in the pool's unit; sTSS = IF^3 x hours x 100 with IF = normalized speed / CSS."""
    if row.get("prescription") != "distance":
        return row
    sport = row["sport"]
    zd = [float(row[c] or 0.0) for c in ("z1_dist", "z2_dist", "z3_dist", "z4_dist", "z5_dist")]
    if sport == "Run" and row.get("distance_unit") == "mile":
        paces = run_zone_paces(easy_pace_min_per_mile)
        z = [round(d * p, 3) for d, p in zip(zd, paces)]
        np_, if_, tss = power_tss(z, RUN_ZONE_IF, run_cp)
    elif sport == "Swim" and row.get("distance_unit") == "pool_units":
        css = css_units_per_min(swim_css, pool_length)
        z = [round(d / (css * m), 3) for d, m in zip(zd, SWIM_ZONE_CSS_MULT)]
        tot = sum(z)
        np_ = round(sum(zd) / tot, 4)          # normalized swim speed (pool units / min)
        if_ = round(np_ / css, 4)
        tss = round((if_ ** 3) * tot / 60.0 * 100.0, 2)
    else:
        raise ValueError(f"{row['code']}: no materializer for {sport} in {row.get('distance_unit')!r}")
    out = dict(row)
    out.update(z1_min=z[0], z2_min=z[1], zx_min=0.0, z3_min=z[2], zy_min=0.0, z4_min=z[3], z5_min=z[4],
               duration_min=round(sum(z), 3), tss=tss, intensity_factor=if_, normalized_power=np_)
    flags = endurance_flags(sport, str(out["description"]), out["duration_min"], z)
    out.update(flags)
    out.update(endurance_loads(sport, tss, if_, out["duration_min"], z, bool(flags.get("has_strides"))))
    return out


def _profile_kwargs(profile) -> Dict[str, object]:
    return dict(easy_pace_min_per_mile=profile.easy_run_pace_min_per_mile, run_cp=profile.run_cp,
                swim_css=profile.swim_css, pool_length=profile.swim_pool_length)


def materialize(df: pd.DataFrame, profile) -> pd.DataFrame:
    """Athlete-specific values at load time (TASK-012/013): distance-prescribed rows are re-derived,
    and normalized power of time-prescribed runs/rides is IF x the athlete's current CP."""
    out = df.copy()
    for sport, cp in (("Run", profile.run_cp), ("Bike", profile.bike_cp)):
        m = (out["prescription"] == "time") & (out["sport"] == sport)
        out.loc[m, "normalized_power"] = (out.loc[m, "intensity_factor"] * cp).round(2)
    mask = out["prescription"] == "distance"
    if mask.any():
        rows = [materialize_distance_row(r, **_profile_kwargs(profile)) for r in out[mask].to_dict(orient="records")]
        out.loc[mask, WORKOUT_COLUMNS] = pd.DataFrame(rows, index=out.index[mask])[WORKOUT_COLUMNS]
    return out


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


def derive_8020_rows(frames: Dict[str, pd.DataFrame], source_files: Dict[str, str], profile=None) -> pd.DataFrame:
    """Canonical rows for scraped 80/20 workouts (codes without '_'). Mile-prescribed long runs
    (scraped with TSS 0) become distance-prescribed rows materialized from the athlete's easy
    pace (TASK-012)."""
    if profile is None:
        from triplan.physiology.athlete_profile import CURRENT_ATHLETE as profile
    rows = []
    for sport, df in frames.items():
        for r in df.to_dict(orient="records"):
            code = str(r["Code"])
            if "_" in code:
                continue  # custom rows are authored separately
            tss = float(r.get("TSS") or 0.0)
            desc = str(r.get("Description") or "")
            if tss <= 0 and sport == "Run" and "mile" in desc:
                zd = parse_mile_prescription(desc)
                row = _blank_row()
                row.update(code=code, name=code, sport=sport, provenance="8020_library", source=source_files[sport],
                           description=desc, prescription="distance", distance=round(sum(zd), 3), distance_unit="mile",
                           z1_dist=zd[0], z2_dist=zd[1], z3_dist=zd[2], z4_dist=zd[3], z5_dist=zd[4], duration_min=1.0, tss=1.0,
                           notes=("Mile-prescribed; minutes/TSS recomputed at load from easy_run_pace_min_per_mile (TASK-012)."
                                  + (" 'Race pace' treated as Zone 3." if "race pace" in desc.lower() else "")))
                rows.append(materialize_distance_row(row, **_profile_kwargs(profile)))
                continue
            if sport == "Swim":
                zd = parse_swim_prescription(desc)
                listed = float(r.get("Distance") or 0.0)
                if abs(sum(zd) - listed) > 0.5:
                    raise ValueError(f"{code}: parsed swim distance {sum(zd)} != listed {listed}")
                row = _blank_row()
                row.update(code=code, name=code, sport=sport, provenance="8020_library", source=source_files[sport],
                           description=desc, prescription="distance", distance=listed, distance_unit="pool_units",
                           z1_dist=zd[0], z2_dist=zd[1], z3_dist=zd[2], z4_dist=zd[3], z5_dist=zd[4], duration_min=1.0,
                           tss=1.0, is_test=code in SWIM_TIME_TRIALS,
                           notes=("Distance-prescribed; minutes/sTSS recomputed at load from swim_css + pool length (TASK-013)."
                                  + (" CSS time trial (400 + 200 Zone 3); scraper had dropped its Zone 3 segments."
                                     if code in SWIM_TIME_TRIALS else "")))
                rows.append(materialize_distance_row(row, **_profile_kwargs(profile)))
                continue
            if tss <= 0:
                raise ValueError(f"{code}: TSS 0 and not a mile-prescribed run; cannot derive")
            row = _blank_row()
            z = [float(r.get(f"Zone {k}") or 0.0) for k in range(1, 6)]
            duration = float(r.get("Duration") or 0.0)
            row.update(code=code, name=code, sport=sport, provenance="8020_library", source=source_files[sport],
                       description=str(r.get("Description") or ""), duration_min=duration, tss=tss,
                       intensity_factor=float(r.get("Intensity Factor") or 0.0))
            # NP = IF x the athlete's CP (the scrape cache used fixed reference CPs; TASK-013)
            cp = profile.run_cp if sport == "Run" else profile.bike_cp
            row["normalized_power"] = round(row["intensity_factor"] * cp, 2)
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
        files[sport] = os.path.relpath(path, start=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))).replace("\\", "/")
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
