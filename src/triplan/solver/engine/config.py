"""Normalized configuration and workout accessors for the unified week engine (TASK-015, ADR-017).

There is exactly one place that knows how to read

* ``MesoCycleTargetsAndConstraints`` (attribute config, scheduler facade),
* the hybrid config (dict **or** attribute object, hybrid facade),
* legacy ``optimize_weekly_schedule`` keyword arguments (DataFrame adapter),

and exactly one place that knows how to read workout attributes: :class:`WorkoutTable`.
Builders never call ``getattr`` on a workout or ``cfg[...]`` on a config.

Engine options
--------------
``EngineOptions`` selects the semantics. Each legacy engine is one preset
(:func:`meso_options`, :func:`hybrid_options`, :func:`legacy_options`); every field is an
independent switch, so a superset (e.g. the scheduler's partition model plus the barbell share) is
a different options object, not a new engine.

=====================  =========================================================================
option                 meaning
=====================  =========================================================================
session_model          ``partition_brick``: ``sum x + rest - double - brick(weekend) == 1`` with
                       brick linking (scheduler). ``per_sport_cap``: <= 2 workouts/day and <= 1
                       per sport/day, exact rest indicator (hybrid). ``single_per_day``: <= 1
                       workout/day, no auxiliary columns (legacy day scheduler).
allowed_rule           unary gating: ``meso`` (time cap, facility, Brick needs Bike+Run, weekday
                       outdoor bike forbidden), ``hybrid`` (time cap, pool/outdoor-bike day lists
                       via workout flags, optional facility access), ``none``.
cost_profile           x/aux column costs: ``meso`` (-w_durability*normalized durability +
                       w_friction*penalty, double-session cost), ``hybrid`` (w_ans*ans -
                       w_durability*durability, double/-rest/-brick), ``none``.
goal_profile           Tier-2 soft goals: ``meso`` (TSS, 5 zones, sport splits, bike/run TSS),
                       ``hybrid`` (per-sport TSS with asymmetric weights, movement-pattern sets,
                       TiZ band slacks), ``legacy`` (day scheduler goals).
rest_bounds_default    default ``(min, max)`` rest days when the config states none.
cut_mode               Top-K cut (TASK-020, ADR-018). ``selection`` (default for both facades):
                       with ``s_i = sum_d x[i,d]`` and ``S`` the chosen workouts, add
                       ``sum_{i in S} s_i <= |S| - m`` so successive plans differ in WHICH workouts are
                       chosen, not just their day layout. ``layout``: exact (i,d) no-good
                       ``sum_{(i,d) in plan} x[i,d] <= |plan| - 1`` (day permutations count as distinct).
                       ``none``: single solve (legacy adapter). Defaults: ``meso_options()`` and
                       ``hybrid_options()`` both use ``selection`` (a coach wants alternatives with
                       different sessions; the old hybrid ``z_split`` flip equality that capped Top-K at
                       2 plans is removed, the split architecture is still reported from ``z_split``).
cut_min_diff           ``m`` of the selection cut: a later plan must drop at least ``m`` of every earlier
                       plan's workouts (default 1).
symmetry_breaking      add one week-reversal ordering row when the whole week is provably invariant
                       under ``d -> 6-d`` (palindromic caps/facility/pool/outdoor/readiness, no weekend
                       brick or weekday-only cap, no legacy day rules). Optimum-preserving; off for
                       ``layout`` cuts (which treat mirrored layouts as distinct).
prune_dominated        drop provably dominated duplicate workouts before building (``solver.pruning``).
split_coherence        UL/HLM strength split architecture with ``z_split``.
barbell_share          >= ``min_barbell_set_share`` of strength sets on the barbell.
tiz_band               (superseded by ``canonical_8020``) Z1+Z2 >= p_low_min and Z3+ZY+Z4+Z5 <= p_high_max
                       with soft minute-scale slacks.
prefilter_grey_zone    drop grey-zone workouts (0 < q_i < q_hard) before the model is built (TASK-019).
quality_classification ``is_hard_endurance`` from the sport-specific quality ``q_i`` (session_quality).
one_hard_per_day       at most one hard endurance session per day (both facades).
key_sessions           ``key_sessions_per_week`` (default 2..3): upper bound hard, lower a Tier-1 slack.
hard_days_hard         heavy lower-body strength only on hard-endurance days (days with two windows).
window_gating          ``double[d] <= two_session_windows[d]`` (default all True) + deterministic AM/PM.
lift_easy_combo        DECISION-021 compound candidates (lift + <= 20 min strictly Z1/Z2 Bike/Run).
canonical_8020         weekly endurance TiZ: Z1+Z2 >= 78% (hard), Z3+ZY+Z4+Z5 <= 20% (hard), 80% soft
                       target, Zone X soft toward 0; applied by both facades (ADR-012 / DECISION-013).
meso_adjacency_rows    scheduler's own hard-endurance/day and heavy-strength vs run rows.
weekly_run_strain_cap  ``max_weekly_run_mech_strain`` row over run workouts.
safety / tier1         include the shared Tier-0 layer / Tier-1 named slacks.
empty_row_policy       ``raise`` or ``keep`` for an emptied row with unsatisfiable bounds.
plan_format            ``meso`` / ``hybrid`` / ``raw`` plan dictionaries.
=====================  =========================================================================
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from triplan.solver.engine import session_quality as sq
from triplan.solver.safety_constraints import cfg_get

DAYS = list(range(7))
WEEKDAYS = [0, 1, 2, 3, 4]
WEEKENDS = [5, 6]
SPORTS = ["Swim", "Bike", "Run", "Strength"]
MUSCLES = ["Posterior_Chain", "Anterior_Chain", "Push", "Pull"]
ZONES = [1, 2, 3, 4, 5]

DEFAULT_DAILY_CAPS = [120, 120, 75, 120, 120, 220, 180]
DEFAULT_HYBRID_WEIGHTS = {
    "w_tss_neg": 150.0, "w_tss_pos": 100.0,
    "w_sets_neg": 150.0, "w_sets_pos": 100.0,
    "w_tiz": 1.0, "w_ans": 0.20, "w_durability": 0.35,
    "w_double": 5.0, "w_rest": 18.0, "w_brick": 4.0,
}


# =============================================================================
# Workout accessor
# =============================================================================

class WorkoutTable:
    """Array view of a workout library. The only reader of workout attributes."""

    def __init__(self, workouts: Sequence[Any]) -> None:
        self.items = list(workouts)
        self.n = len(self.items)
        w = self.items

        self.sport: List[str] = [getattr(x, "sport", "") for x in w]
        self.sport_arr = np.array(self.sport, dtype=object)
        self.duration = np.array([float(getattr(x, "duration", 60.0)) for x in w], dtype=np.float64)
        self.aerobic_tss = np.array([float(getattr(x, "aerobic_tss", 0.0)) for x in w], dtype=np.float64)
        self.mechanical_strain = np.array([float(getattr(x, "mechanical_strain", 0.0)) for x in w], dtype=np.float64)
        self.ans_cost = np.array([float(getattr(x, "ans_cost", 20.0)) for x in w], dtype=np.float64)
        self.durability = np.array([float(getattr(x, "durability_score", 2.0)) for x in w], dtype=np.float64)
        self.preference_penalty = np.array([float(getattr(x, "preference_penalty", 0.1)) for x in w],
                                           dtype=np.float64)
        self.bb_sets = np.array([float(int(getattr(x, "bb_sets", 0))) for x in w], dtype=np.float64)
        self.db_sets = np.array([float(int(getattr(x, "db_sets", 0))) for x in w], dtype=np.float64)
        self.axial_load = np.array([float(getattr(x, "axial_load", 0.0)) for x in w], dtype=np.float64)
        self.flags: List[Dict[str, int]] = [getattr(x, "flags", None) or {} for x in w]
        self.sets_by_muscle: List[Dict[str, int]] = [getattr(x, "sets_by_muscle", None) or {} for x in w]
        self.split_group: List[str] = [getattr(x, "split_group", "ANY") for x in w]
        self.is_hard_endurance = np.array([bool(getattr(x, "is_hard_endurance", False)) for x in w], dtype=bool)
        self.is_indoor_bike = np.array([bool(getattr(x, "is_indoor_bike", False)) for x in w], dtype=bool)
        self.zone = np.array([self._zone_minutes(x) for x in w], dtype=np.float64).reshape(self.n, 5)
        # 7-zone minutes (Z1, Z2, ZX, Z3, ZY, Z4, Z5) and the part that counts toward endurance time-in-zone
        self.zone7 = np.array([sq.zone7(x) for x in w], dtype=np.float64).reshape(self.n, 7)
        self.endur_zone7 = np.array([sq.endurance_zone7(x) for x in w], dtype=np.float64).reshape(self.n, 7)
        # DECISION-021 lift + easy cardio combo: the cardio component of a compound ("" for ordinary workouts)
        self.cardio_sport: List[str] = [str(getattr(x, "cardio_sport", "") or "") for x in w]
        self.cardio_minutes = np.array([float(getattr(x, "cardio_minutes", 0.0)) for x in w], dtype=np.float64)
        self.cardio_mech = np.array([float(getattr(x, "cardio_mech_strain", 0.0)) for x in w], dtype=np.float64)
        is_run = np.array([s == "Run" for s in self.sport], dtype=bool)
        # run component: the whole workout for a run, only the cardio part of a lift+run combo
        self.run_minutes = np.where(is_run, self.duration,
                                    np.where(np.array([c == "Run" for c in self.cardio_sport]), self.cardio_minutes, 0.0))
        self.run_strain = np.where(is_run, self.mechanical_strain,
                                   np.where(np.array([c == "Run" for c in self.cardio_sport]), self.cardio_mech, 0.0))

    @staticmethod
    def _zone_minutes(x: Any) -> List[float]:
        zm = getattr(x, "zone_minutes", None)
        if zm:
            return [float(zm.get(z, 0.0)) for z in ZONES]
        zd = getattr(x, "zone_durations", None)
        if zd is not None and len(zd) >= 5:
            return [float(zd[z - 1]) for z in ZONES]
        dur = float(getattr(x, "duration", 60.0))
        return [dur * 0.35, dur * 0.45, dur * 0.15, dur * 0.05, 0.0]

    def ids(self, sport: str) -> List[int]:
        return [i for i, s in enumerate(self.sport) if s == sport]

    def combo_ids(self, sport: str) -> List[int]:
        """Lift + easy ``sport`` combos (sport stays "Strength"; they also count toward ``sport``)."""
        return [i for i, c in enumerate(self.cardio_sport) if c == sport]

    def members(self, sport: str) -> List[int]:
        """Workouts that put ``sport`` work on the day: its own workouts plus combos with that cardio."""
        return self.ids(sport) + self.combo_ids(sport)

    def mask(self, *sports: str) -> np.ndarray:
        return np.isin(self.sport_arr, list(sports))

    def muscle_sets(self, muscle: str) -> np.ndarray:
        return np.array([float(m.get(muscle, 0) or 0) for m in self.sets_by_muscle], dtype=np.float64)


# =============================================================================
# Config accessor
# =============================================================================

class WeekConfig:
    """Uniform read access to a dict or attribute config."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def get(self, *keys: str, default: Any = None) -> Any:
        return cfg_get(self.raw, *keys, default=default)

    @property
    def daily_caps(self) -> List[float]:
        return list(self.get("daily_duration_caps", "daily_time_caps", default=DEFAULT_DAILY_CAPS))

    @property
    def facility_access(self) -> Optional[Dict[str, List[bool]]]:
        return self.get("facility_access", default=None)

    @property
    def hybrid_weights(self) -> Dict[str, float]:
        return self.get("weights", default=DEFAULT_HYBRID_WEIGHTS)

    @property
    def two_windows(self) -> List[bool]:
        """Days with an AM and a PM window (>= ``am_pm_min_gap_h`` apart). Default: every day (no gating)."""
        v = self.get("two_session_windows", "two_windows", default=None)
        return [bool(x) for x in v] if v is not None else [True] * 7

    @property
    def am_pm_min_gap_h(self) -> float:
        return float(self.get("am_pm_min_gap_h", default=6.0))

    @property
    def key_range(self) -> Tuple[int, int]:
        lo, hi = self.get("key_sessions_per_week", default=(2, 3))
        return int(lo), int(hi)

    def weight(self, key: str, default: float) -> float:
        return float(self.hybrid_weights.get(key, default))


# =============================================================================
# Options
# =============================================================================

@dataclass
class LegacySpec:
    """Inputs of the legacy day scheduler (``optimize_weekly_schedule``) that are not workout data."""
    num_workouts: int
    target_tss: float
    tss_tolerance: float
    max_high_ratio: float
    bike_dur_ratio: float
    run_dur_ratio: float
    swim_dur_ratio: float
    high_ratio_per_sport: float
    z3_high_ratio: float
    z4_high_ratio: float
    z5_high_ratio: float
    day_max: List[float]
    hard_bike_run: np.ndarray        # bool per workout: forbids consecutive hard days
    long_run: np.ndarray             # bool per workout: forbids consecutive long runs
    forbid_consecutive_hard: bool = True
    forbid_consecutive_long_runs: bool = True
    threads: Optional[int] = 2
    random_seed: Optional[int] = None


@dataclass
class EngineOptions:
    session_model: str = "partition_brick"
    allowed_rule: str = "meso"
    cost_profile: str = "meso"
    goal_profile: str = "meso"
    cut_mode: str = "selection"
    cut_min_diff: int = 1
    symmetry_breaking: bool = True
    prune_dominated: bool = True
    rest_bounds_default: Tuple[int, int] = (1, 7)
    empty_row_policy: str = "raise"
    safety: bool = True
    tier1: bool = True
    meso_adjacency_rows: bool = True
    weekly_run_strain_cap: bool = True
    split_coherence: bool = False
    barbell_share: bool = False
    tiz_band: bool = False
    # TASK-019 (ADR-019): session structure and canonical 80/20. All default on for the meso/hybrid
    # presets and off for the legacy day scheduler.
    prefilter_grey_zone: bool = True   # drop 0 < q_i < q_hard workouts before building
    quality_classification: bool = True  # is_hard_endurance from sport-specific q_i, not IF / stale flags
    one_hard_per_day: bool = True      # <= 1 hard endurance session per day (both facades)
    key_sessions: bool = True          # key_lo <= sum key_i x <= key_hi (lower bound is a Tier-1 slack)
    hard_days_hard: bool = True        # heavy lower strength only on hard-endurance days (when 2 windows)
    window_gating: bool = True         # double[d] <= two_windows[d]
    lift_easy_combo: bool = True       # DECISION-021 compound candidates
    canonical_8020: bool = True        # Z1+Z2 >= 78% and Z3+ZY+Z4+Z5 <= 20% hard, 80% soft, Zone X soft
    plan_format: str = "meso"
    infeasible_hint: bool = True
    # solver options
    quiet_option: str = "output_flag"
    mip_rel_gap: Optional[float] = 0.01
    mip_abs_gap: Optional[float] = None
    mip_heuristic_effort: Optional[float] = 0.20
    legacy: Optional[LegacySpec] = None


def meso_options() -> EngineOptions:
    """``TriathlonMILPScheduler`` semantics."""
    return EngineOptions()


def hybrid_options() -> EngineOptions:
    """``HybridAthleteMILPOptimizer`` semantics (rest window 1..2, <=2/day, <=1 per sport/day)."""
    return EngineOptions(
        session_model="per_sport_cap", allowed_rule="hybrid", cost_profile="hybrid", goal_profile="hybrid",
        rest_bounds_default=(1, 2), empty_row_policy="keep",
        meso_adjacency_rows=False, weekly_run_strain_cap=False, split_coherence=True, barbell_share=True,
        tiz_band=False, plan_format="hybrid", infeasible_hint=False, mip_heuristic_effort=None,
    )


def legacy_options(spec: LegacySpec) -> EngineOptions:
    """``optimize_weekly_schedule`` semantics: one workout/day, exact count, no Tier-0/1 layers."""
    return EngineOptions(
        session_model="single_per_day", allowed_rule="none", cost_profile="none", goal_profile="legacy",
        cut_mode="none", symmetry_breaking=False, prune_dominated=False, safety=False, tier1=False, meso_adjacency_rows=False, weekly_run_strain_cap=False,
        prefilter_grey_zone=False, quality_classification=False, one_hard_per_day=False, key_sessions=False,
        hard_days_hard=False, window_gating=False, lift_easy_combo=False, canonical_8020=False,
        plan_format="raw", infeasible_hint=False, quiet_option="log_to_console", mip_rel_gap=None,
        mip_abs_gap=0.01, mip_heuristic_effort=0.20, legacy=spec,
    )


# =============================================================================
# Meso config normalization (facility redistribution, TASK-013 no-mutation)
# =============================================================================

def normalize_meso_config(config: Any) -> Tuple[Any, List[str]]:
    """Deep-copy ``config`` (never mutate the caller's) and redistribute the duration-split target of a
    sport with no facility access across the other sports. Frequency minimums are NOT touched; an
    unmeetable minimum becomes a reported Tier-1 relaxation. Returns ``(config_copy, notes)``."""
    cfg = copy.deepcopy(config)
    notes: List[str] = []
    if cfg.sport_freq_bounds is None:
        cfg.sport_freq_bounds = {"Swim": (2, 2), "Bike": (3, 4), "Run": (4, 5), "Strength": (1, 2)}
    for s in SPORTS:
        if sum(cfg.facility_access.get(s, [True] * 7)) == 0 and cfg.target_sport_duration_splits.get(s, 0.0) > 0:
            zero_split = cfg.target_sport_duration_splits[s]
            cfg.target_sport_duration_splits[s] = 0.0
            rem_sum = sum(v for k, v in cfg.target_sport_duration_splits.items() if k != s)
            if rem_sum > 0:
                for k in cfg.target_sport_duration_splits:
                    if k != s:
                        cfg.target_sport_duration_splits[k] += zero_split * (
                            cfg.target_sport_duration_splits[k] / rem_sum)
            notes.append(f"No {s} access this week: its {zero_split:.0%} duration-split target was "
                         f"redistributed to the other sports.")
    return cfg, notes
