"""
Canonical workout schema (TASK-010).

* ``WORKOUT_COLUMNS``: column contract for ``data/workout_library.csv``, the single validated library.
* ``Workout``: Pydantic row model (7-zone minutes, explicit behavior flags, closed vocabularies).
* ``WORKOUT_TABLE_SCHEMA``: Pandera DataFrame schema (unique code, enums, zone sum within
  1 min of duration, TSS > 0, flag dtypes).

Zones follow the canonical 80/20 7-zone scale (ADR-009): Z1, Z2, ZX, Z3, ZY, Z4, Z5.
The 80/20 library never prescribes the ZX/ZY grey zones, so they are 0 for every prescribed
workout. They exist so that ingested *actual* sessions (TASK-024) can record grey-zone drift.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Tuple

import pandas as pd
import pandera.pandas as pa
from pydantic import BaseModel, ConfigDict, Field, model_validator

ZONE_COLUMNS = ["z1_min", "z2_min", "zx_min", "z3_min", "zy_min", "z4_min", "z5_min"]
# Per-zone prescribed distance (in distance_unit) for distance-prescribed workouts (TASK-012/013).
ZONE_DISTANCE_COLUMNS = ["z1_dist", "z2_dist", "zx_dist", "z3_dist", "zy_dist", "z4_dist", "z5_dist"]
PRESCRIPTIONS = ("time", "distance")
DISTANCE_UNITS = ("", "mile", "pool_units")
ZONE_SUM_TOLERANCE_MIN = 1.0


class Sport(str, Enum):
    SWIM = "Swim"
    BIKE = "Bike"
    RUN = "Run"
    STRENGTH = "Strength"
    BRICK = "Brick"


class Provenance(str, Enum):
    LIBRARY_8020 = "8020_library"            # scraped from the 80/20 Endurance workout library
    AGENT_AUTHORED = "agent_authored"        # authored by an AI agent; provisional (ADR-012 DECISION-016)
    HAND_CODED_DEFAULT = "hand_coded_default"  # former build_default_candidate_library() rows
    HAND_CODED_HYBRID = "hand_coded_hybrid"    # former build_hybrid_candidate_library() rows


class SplitGroup(str, Enum):
    ANY = "ANY"
    UL = "UL"
    HLM = "HLM"


BOOL_FLAGS = [
    "is_hard_endurance",   # key endurance session (>= 15 min Z3+ or >= 5 min Z4+)
    "is_heavy_run",        # threshold/interval run or run >= 70 min (48h separation, HEUR-004)
    "is_long_run",         # run >= 60 min (HEUR-004 deadlift lockout, single-run share)
    "is_brick_run_hard",   # brick run leg >= 15 min Z3+, >= 5 min Z4+, or >= 70 min (48h separation, HEUR-004)
    "is_heavy_strength",   # heavy lower-body barbell session
    "is_anchor",           # mandatory keystone session (Tier-1 slack when it cannot fit)
    "has_strides",         # alactic strides <= 20 s (zero ANS penalty for Z5, SPEC.md)
    "is_indoor_bike",      # executable on the smart trainer
    "is_outdoor_bike",     # requires an outdoor ride day
    "is_test",             # CP / CSS test (ADR-011 retest lockouts)
    "pool",                # requires pool access
    "swim_threshold",      # CSS threshold swim (antagonism vs heavy press)
    "hi_torque_bike",      # high-torque / threshold bike (antagonism vs heavy squat)
    "heavy_sq",            # heavy squat
    "heavy_dl",            # heavy deadlift (HEUR-004)
    "heavy_press",         # heavy upper press (antagonism vs CSS swim)
    "lib_default",         # member of the default triathlon candidate set
    "lib_hybrid",          # member of the hybrid-athlete candidate set
    "needs_review",        # values estimated, not verified by a human
]

WORKOUT_COLUMNS = [
    "code", "name", "sport", "category", "provenance", "source",
    "duration_min", "distance", "distance_unit", "prescription", "description",
    *ZONE_COLUMNS, *ZONE_DISTANCE_COLUMNS,
    "tss", "intensity_factor", "normalized_power",
    "mechanical_strain", "ans_cost", "durability_score", "t_thr_min", "t_cont_min", "late_min",
    "neuromuscular_load", "axial_load",
    "brick_run_min", "brick_run_strain",
    *BOOL_FLAGS,
    "split_group", "bb_sets", "db_sets",
    "sets_anterior_chain", "sets_posterior_chain", "sets_push", "sets_pull",
    "preference_penalty", "target_adaptations", "notes",
]


class Workout(BaseModel):
    """One validated library row."""
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    code: str = Field(min_length=1)
    name: str
    sport: Sport
    category: str = ""
    provenance: Provenance
    source: str
    duration_min: float = Field(gt=0)
    distance: Optional[float] = Field(default=None, ge=0)
    distance_unit: str = ""
    prescription: str = "time"
    description: str = ""
    z1_min: float = Field(ge=0)
    z2_min: float = Field(ge=0)
    zx_min: float = Field(ge=0)
    z3_min: float = Field(ge=0)
    zy_min: float = Field(ge=0)
    z4_min: float = Field(ge=0)
    z5_min: float = Field(ge=0)
    z1_dist: float = Field(ge=0)
    z2_dist: float = Field(ge=0)
    zx_dist: float = Field(ge=0)
    z3_dist: float = Field(ge=0)
    zy_dist: float = Field(ge=0)
    z4_dist: float = Field(ge=0)
    z5_dist: float = Field(ge=0)
    tss: float = Field(gt=0)
    intensity_factor: float = Field(ge=0)
    normalized_power: float = Field(ge=0)
    mechanical_strain: float = Field(ge=0)
    ans_cost: float = Field(ge=0)
    durability_score: float = Field(ge=0, le=10)  # one 0..10 scale, derived from the interval features (TASK-023)
    t_thr_min: float = Field(default=0.0, ge=0)    # T^thr: Z3/ZY/Z4/Z5 minutes (alactic strides excluded)
    t_cont_min: float = Field(default=0.0, ge=0)   # T^cont: longest continuous threshold block
    late_min: float = Field(default=0.0, ge=0)     # threshold minutes after 60% of the session
    neuromuscular_load: float = Field(ge=0)
    axial_load: float = Field(ge=0)
    brick_run_min: float = Field(default=0.0, ge=0)
    brick_run_strain: float = Field(default=0.0, ge=0)
    is_hard_endurance: bool
    is_heavy_run: bool
    is_long_run: bool
    is_brick_run_hard: bool = False
    is_heavy_strength: bool
    is_anchor: bool
    has_strides: bool
    is_indoor_bike: bool
    is_outdoor_bike: bool
    is_test: bool
    pool: bool
    swim_threshold: bool
    hi_torque_bike: bool
    heavy_sq: bool
    heavy_dl: bool
    heavy_press: bool
    lib_default: bool
    lib_hybrid: bool
    needs_review: bool
    split_group: SplitGroup = SplitGroup.ANY
    bb_sets: int = Field(ge=0)
    db_sets: int = Field(ge=0)
    sets_anterior_chain: float = Field(ge=0)
    sets_posterior_chain: float = Field(ge=0)
    sets_push: float = Field(ge=0)
    sets_pull: float = Field(ge=0)
    preference_penalty: float = Field(ge=0, le=1)
    target_adaptations: str = ""
    notes: str = ""

    @property
    def zones7(self) -> Tuple[float, float, float, float, float, float, float]:
        return (self.z1_min, self.z2_min, self.zx_min, self.z3_min, self.zy_min, self.z4_min, self.z5_min)

    @property
    def zones5(self) -> Tuple[float, float, float, float, float]:
        """Legacy 5-zone view for the engines: ZX folds into Z3 and ZY into Z4 (conservative)."""
        return (self.z1_min, self.z2_min, self.z3_min + self.zx_min, self.z4_min + self.zy_min, self.z5_min)

    @property
    def sets_by_muscle(self) -> Dict[str, float]:
        return {k: v for k, v in (("Anterior_Chain", self.sets_anterior_chain),
                                  ("Posterior_Chain", self.sets_posterior_chain),
                                  ("Push", self.sets_push), ("Pull", self.sets_pull)) if v > 0}

    @model_validator(mode="after")
    def _check(self) -> "Workout":
        zsum = sum(self.zones7)
        if abs(zsum - self.duration_min) > ZONE_SUM_TOLERANCE_MIN:
            raise ValueError(f"{self.code}: zone minutes sum {zsum:.2f} != duration {self.duration_min:.2f} (+-1 min)")
        zd = (self.z1_dist + self.z2_dist + self.zx_dist + self.z3_dist + self.zy_dist + self.z4_dist + self.z5_dist)
        if self.prescription not in PRESCRIPTIONS:
            raise ValueError(f"{self.code}: prescription must be one of {PRESCRIPTIONS}")
        if self.distance_unit not in DISTANCE_UNITS:
            raise ValueError(f"{self.code}: distance_unit must be one of {DISTANCE_UNITS}")
        if self.prescription == "distance":
            if not self.distance or abs(zd - self.distance) > 0.01 * self.distance + 1e-6:
                raise ValueError(f"{self.code}: zone distances {zd:.3f} must sum to distance {self.distance}")
            if not self.distance_unit:
                raise ValueError(f"{self.code}: distance-prescribed workouts need a distance_unit")
        elif zd > 0:
            raise ValueError(f"{self.code}: time-prescribed workouts must not carry zone distances")
        tol = 1e-6
        if self.t_cont_min > self.t_thr_min + tol or self.late_min > self.t_thr_min + tol:
            raise ValueError(f"{self.code}: need late_min <= t_thr_min and t_cont_min <= t_thr_min")
        if self.t_thr_min > self.duration_min + ZONE_SUM_TOLERANCE_MIN:
            raise ValueError(f"{self.code}: t_thr_min {self.t_thr_min} exceeds duration {self.duration_min}")
        s = self.sport
        if s != Sport.RUN.value and (self.is_heavy_run or self.is_long_run):
            raise ValueError(f"{self.code}: run-only flag on a {s} workout")
        if self.has_strides and s not in (Sport.RUN.value, Sport.BIKE.value):
            raise ValueError(f"{self.code}: strides are only defined for Run and Bike")
        if s != Sport.STRENGTH.value and (self.is_heavy_strength or self.heavy_sq or self.heavy_dl or self.heavy_press):
            raise ValueError(f"{self.code}: strength flag on a {s} workout")
        if s != Sport.BIKE.value and (self.is_indoor_bike or self.is_outdoor_bike or self.hi_torque_bike):
            raise ValueError(f"{self.code}: bike flag on a {s} workout")
        if s not in (Sport.SWIM.value, Sport.BRICK.value) and self.pool:
            raise ValueError(f"{self.code}: swim flag on a {s} workout")
        if s != Sport.SWIM.value and self.swim_threshold:
            raise ValueError(f"{self.code}: swim flag on a {s} workout")
        if s != Sport.BRICK.value:
            if self.brick_run_min > 0 or self.brick_run_strain > 0 or self.is_brick_run_hard:
                raise ValueError(f"{self.code}: brick-run field on a {s} workout")
        else:
            if self.brick_run_min > self.duration_min + 1e-6:
                raise ValueError(f"{self.code}: brick_run_min {self.brick_run_min} > duration {self.duration_min}")
            if self.brick_run_strain > self.mechanical_strain + 1e-6:
                raise ValueError(
                    f"{self.code}: brick_run_strain {self.brick_run_strain} > mechanical_strain {self.mechanical_strain}"
                )
            if self.brick_run_min == 0 and (self.brick_run_strain > 0 or self.is_brick_run_hard):
                raise ValueError(f"{self.code}: zero brick_run_min cannot carry strain or hard flag")
        if s == Sport.BIKE.value and self.is_indoor_bike == self.is_outdoor_bike:
            raise ValueError(f"{self.code}: bike must be exactly one of indoor/outdoor")
        if s == Sport.SWIM.value and not self.pool:
            raise ValueError(f"{self.code}: swims require pool=True")
        return self


def _zone_sum_ok(df: pd.DataFrame) -> pd.Series:
    return (df[ZONE_COLUMNS].sum(axis=1) - df["duration_min"]).abs() <= ZONE_SUM_TOLERANCE_MIN


WORKOUT_TABLE_SCHEMA = pa.DataFrameSchema(
    columns={
        "code": pa.Column(str, unique=True, nullable=False),
        "name": pa.Column(str),
        "sport": pa.Column(str, pa.Check.isin([s.value for s in Sport])),
        "category": pa.Column(str, nullable=True),
        "provenance": pa.Column(str, pa.Check.isin([p.value for p in Provenance])),
        "source": pa.Column(str),
        "duration_min": pa.Column(float, pa.Check.gt(0)),
        "distance": pa.Column(float, pa.Check.ge(0), nullable=True),
        "distance_unit": pa.Column(str, pa.Check.isin(list(DISTANCE_UNITS)), nullable=True),
        "prescription": pa.Column(str, pa.Check.isin(list(PRESCRIPTIONS))),
        "description": pa.Column(str, nullable=True),
        **{z: pa.Column(float, pa.Check.ge(0)) for z in ZONE_COLUMNS},
        **{z: pa.Column(float, pa.Check.ge(0)) for z in ZONE_DISTANCE_COLUMNS},
        "tss": pa.Column(float, pa.Check.gt(0)),
        "intensity_factor": pa.Column(float, pa.Check.ge(0)),
        "normalized_power": pa.Column(float, pa.Check.ge(0)),
        "mechanical_strain": pa.Column(float, pa.Check.ge(0)),
        "ans_cost": pa.Column(float, pa.Check.ge(0)),
        "durability_score": pa.Column(float, pa.Check.in_range(0, 10)),  # unified scale (TASK-023)
        "t_thr_min": pa.Column(float, pa.Check.ge(0)),
        "t_cont_min": pa.Column(float, pa.Check.ge(0)),
        "late_min": pa.Column(float, pa.Check.ge(0)),
        "neuromuscular_load": pa.Column(float, pa.Check.ge(0)),
        "axial_load": pa.Column(float, pa.Check.ge(0)),
        "brick_run_min": pa.Column(float, pa.Check.ge(0)),
        "brick_run_strain": pa.Column(float, pa.Check.ge(0)),
        **{f: pa.Column(bool) for f in BOOL_FLAGS},
        "split_group": pa.Column(str, pa.Check.isin([g.value for g in SplitGroup])),
        "bb_sets": pa.Column(int, pa.Check.ge(0)),
        "db_sets": pa.Column(int, pa.Check.ge(0)),
        "sets_anterior_chain": pa.Column(float, pa.Check.ge(0)),
        "sets_posterior_chain": pa.Column(float, pa.Check.ge(0)),
        "sets_push": pa.Column(float, pa.Check.ge(0)),
        "sets_pull": pa.Column(float, pa.Check.ge(0)),
        "preference_penalty": pa.Column(float, pa.Check.in_range(0, 1)),
        "target_adaptations": pa.Column(str, nullable=True),
        "notes": pa.Column(str, nullable=True),
    },
    checks=[pa.Check(_zone_sum_ok, error="zone minutes must sum to duration_min within 1 minute")],
    strict=True,
    ordered=True,
    coerce=True,
)


def validate_table(df: pd.DataFrame) -> pd.DataFrame:
    """Validate with Pandera (table-level) then Pydantic (row-level cross-field rules)."""
    df = df.copy()
    for c in ("category", "distance_unit", "description", "target_adaptations", "notes", "name"):
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str)
    df = WORKOUT_TABLE_SCHEMA.validate(df, lazy=True)
    for rec in df.to_dict(orient="records"):
        Workout(**{k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in rec.items()})
    return df
