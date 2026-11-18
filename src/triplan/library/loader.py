"""
Load the canonical workout library (TASK-011).

``data/workout_library.csv`` is validated with Pandera and Pydantic on load, then turned into
``CandidateWorkout`` objects for the MILP engines, or into the legacy Title-Case frame used by
``util.load_workouts`` / ``highs_optimization``. Behavior flags come only from explicit
columns; nothing is inferred from codes or descriptions here.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable, List, Optional

import pandas as pd

from triplan.library.schema import WORKOUT_COLUMNS, validate_table

from triplan.paths import REPO_ROOT as _REPO_ROOT

ROOT = str(_REPO_ROOT)
LIBRARY_PATH = os.path.join(ROOT, "data", "workout_library.csv")


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime: float) -> pd.DataFrame:
    df = pd.read_csv(path, keep_default_na=True)
    missing = [c for c in WORKOUT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    return validate_table(df[WORKOUT_COLUMNS])


_CURRENT = object()


def load_library(path: Optional[str] = None, profile=_CURRENT) -> pd.DataFrame:
    """Validated canonical table (cached until the file changes). Returns a copy.

    Distance-prescribed rows (mile long runs, TASK-012) are re-materialized for ``profile``
    (default: CURRENT_ATHLETE) at load time, so durations and TSS follow the athlete's
    current easy pace / thresholds. Pass ``profile=None`` for the stored values.
    """
    from triplan.library.build import materialize
    from triplan.library.schema import Workout

    p = os.path.abspath(path or LIBRARY_PATH)
    df = _load_cached(p, os.path.getmtime(p)).copy()
    if profile is _CURRENT:
        from triplan.physiology.athlete_profile import CURRENT_ATHLETE as profile
    if profile is not None:
        df = materialize(df, profile)
        for rec in df[df["prescription"] == "distance"].to_dict(orient="records"):
            Workout(**{k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in rec.items()})
    return df


def select(df: pd.DataFrame, *, library: Optional[str] = None, provenance: Optional[Iterable[str]] = None,
           sports: Optional[Iterable[str]] = None, include_tests: bool = True) -> pd.DataFrame:
    out = df
    if library == "default":
        out = out[out["lib_default"]]
    elif library == "hybrid":
        out = out[out["lib_hybrid"]]
    elif library is not None:
        raise ValueError(f"unknown library set {library!r}")
    if provenance is not None:
        out = out[out["provenance"].isin(list(provenance))]
    if sports is not None:
        out = out[out["sport"].isin(list(sports))]
    if not include_tests:
        out = out[~out["is_test"]]
    return out


def _flags(r: dict) -> dict:
    f = {}
    if r["sport"] == "Strength":  # always explicit for strength: (0,0) means "flagged, not a deadlift"
        f.update(heavy_sq=int(r["heavy_sq"]), heavy_dl=int(r["heavy_dl"]), heavy_press=int(r["heavy_press"]))
    for col, key in (("is_heavy_run", "heavy_run"), ("is_long_run", "long_run"), ("pool", "pool"),
                     ("swim_threshold", "swim_th"), ("hi_torque_bike", "hi_torque_bike"),
                     ("is_outdoor_bike", "outdoor_bike"), ("is_test", "test")):
        if r[col]:
            f[key] = 1
    if r["sport"] == "Brick":
        f["brick"] = 1
    return f


def to_candidates(df: pd.DataFrame, base_id: int = 0) -> list:
    """Canonical rows -> CandidateWorkout objects (5-zone engine view: ZX->Z3, ZY->Z4)."""
    from triplan.model.candidates import CandidateWorkout  # local import avoids a module cycle

    out: List = []
    for k, r in enumerate(df.to_dict(orient="records")):
        muscles = {m: v for m, v in (("Anterior_Chain", r["sets_anterior_chain"]), ("Posterior_Chain", r["sets_posterior_chain"]),
                                     ("Push", r["sets_push"]), ("Pull", r["sets_pull"])) if v > 0}
        out.append(CandidateWorkout(
            id=base_id + k, code=r["code"], name=r["name"], sport=r["sport"], duration=float(r["duration_min"]),
            aerobic_tss=float(r["tss"]), mechanical_strain=float(r["mechanical_strain"]), ans_cost=float(r["ans_cost"]),
            durability_score=float(r["durability_score"]),
            zone_durations=(float(r["z1_min"]), float(r["z2_min"]), float(r["z3_min"] + r["zx_min"]),
                            float(r["z4_min"] + r["zy_min"]), float(r["z5_min"])),
            zone7=(float(r["z1_min"]), float(r["z2_min"]), float(r["zx_min"]), float(r["z3_min"]),
                   float(r["zy_min"]), float(r["z4_min"]), float(r["z5_min"])),
            is_hard_endurance=bool(r["is_hard_endurance"]), is_heavy_run=bool(r["is_heavy_run"]),
            is_heavy_strength=bool(r["is_heavy_strength"]), is_anchor=bool(r["is_anchor"]),
            has_strides=bool(r["has_strides"]), is_indoor_bike=bool(r["is_indoor_bike"]),
            preference_penalty=float(r["preference_penalty"]), split_group=r["split_group"],
            bb_sets=int(r["bb_sets"]), db_sets=int(r["db_sets"]),
            neuromuscular_load=float(r["neuromuscular_load"]), axial_load=float(r["axial_load"]),
            sets_by_muscle=muscles, flags=_flags(r), provenance=str(r.get("provenance") or ""),
        ))
    return out


def load_candidates(*, library: Optional[str] = None, provenance: Optional[Iterable[str]] = None,
                    sports: Optional[Iterable[str]] = None, include_tests: bool = True,
                    include_8020: bool = True, path: Optional[str] = None) -> list:
    """Load CandidateWorkout objects from the canonical library.

    When ``library`` is ``"default"`` or ``"hybrid"`` and ``provenance`` is not given,
    ``include_8020=True`` (default) also includes ``provenance == "8020_library"`` options
    alongside the curated ``lib_default`` / ``lib_hybrid`` core.
    """
    lib_df = load_library(path)
    if library in ("default", "hybrid") and provenance is None and include_8020:
        core = select(lib_df, library=library, sports=sports, include_tests=include_tests)
        extra = select(lib_df, provenance=["8020_library"], sports=sports, include_tests=include_tests)
        combined = pd.concat([core, extra], ignore_index=True).drop_duplicates(subset=["code"], keep="first")
        return to_candidates(combined)
    return to_candidates(select(lib_df, library=library, provenance=provenance,
                                sports=sports, include_tests=include_tests))


def legacy_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Title-Case frame consumed by util.load_workouts / highs_optimization (5-zone, ZX->Z3, ZY->Z4)."""
    out = pd.DataFrame({
        "Code": df["code"].values,
        "Type": df["sport"].values,
        "Duration": df["duration_min"].astype(float).values,
        "Description": df["description"].values,
        "Zone 1": df["z1_min"].values,
        "Zone 2": df["z2_min"].values,
        "Zone 3": (df["z3_min"] + df["zx_min"]).values,
        "Zone 4": (df["z4_min"] + df["zy_min"]).values,
        "Zone 5": df["z5_min"].values,
        "TSS": df["tss"].values,
        "Intensity Factor": df["intensity_factor"].values,
        "Normalized Power": df["normalized_power"].values,
    })
    out["High Duration"] = out["Zone 3"] + out["Zone 4"] + out["Zone 5"]
    out["Low Duration"] = out["Zone 1"] + out["Zone 2"]
    return out
