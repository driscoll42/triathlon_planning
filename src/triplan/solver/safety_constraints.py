"""
Shared Tier-0 safety constraints, Tier-1 named slacks, and lexicographic solving (TASK-016 / TASK-017).

This is the orthopedic layer of the unified week engine (``triplan.solver.engine``, TASK-015 /
ADR-017); ``engine.builders.orthopedic`` delegates here. Hierarchy (ADR-013):

* Tier 0, hard, never relaxed: time and facility gating, rolling 48h TSS/ANS/strain caps,
  daily/weekly axial and neuromuscular caps, single-run share cap, hard/easy adjacency, 48h
  heavy-run separation, deadlift +-24h lockout against threshold/long runs, the same-day
  biomechanical antagonism matrix, and non-consecutive strength days.
* Tier 1, relaxable only through named integer slacks: anchors, minimum sport frequencies,
  stride count, rest-day count (floor 1), heavy-strength count (floor 1). Each slack is
  weighted lexicographically by the athlete's relaxation order (DECISION-006) and reported
  in plain English.
* Tier 2: soft goals (TSS, splits, zones, durability) in each engine's objective.

``lexicographic_solve`` minimizes the weighted Tier-1 slack first, then locks it and
optimizes Tier 2. A Tier-2 goal therefore never buys a Tier-1 shortfall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import highspy

from triplan.solver.highs_runtime import SolveResult, run_and_check

logger = logging.getLogger(__name__)

DAYS = list(range(7))
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS = [0, 1, 2, 3, 4]

# Default relaxation order (DECISION-006 / ADR-012), cheapest first. "reduce_weekly_tss" is a Tier-2 goal.
DEFAULT_RELAXATION_ORDER = [
    "reduce_weekly_tss",
    "drop_stride_count",
    "shorten_or_drop_anchor:Swim",
    "reduce_frequency:Swim",
    "shorten_or_drop_anchor:Run",
    "reduce_frequency:Run",
    "shorten_or_drop_anchor:Bike",
    "reduce_frequency:Bike",
    "reduce_rest_days_to_min_1",
    "reduce_heavy_strength_count_to_min_1",
]
# Categories not named in the athlete's order. Extra rest is the safest relaxation, so it is cheapest.
# Strength frequency sits with heavy strength (Strength is the top sport priority).
# Structure preferences of TASK-019 (Hard-Days-Hard stacking, key-session count) are cheap named relaxations.
_PREPEND = ["extend_rest_days", "unstack_heavy_strength", "reduce_key_sessions"]
_APPEND = ["shorten_or_drop_anchor:Strength", "reduce_frequency:Strength", "shorten_or_drop_anchor:Brick",
           "reduce_frequency:Brick"]
LEX_BASE = 8  # > max slack per category (<= 7 sessions), so weights are exactly lexicographic


def cfg_get(cfg: Any, *keys: str, default: Any = None) -> Any:
    """Read the first present key from a dict config or attribute config."""
    for k in keys:
        if isinstance(cfg, dict):
            if k in cfg and cfg[k] is not None:
                return cfg[k]
        elif getattr(cfg, k, None) is not None:
            return getattr(cfg, k)
    return default


# =============================================================================
# Limits & workout classification
# =============================================================================

@dataclass
class SafetyLimits:
    weekday_single_workout_cap: Optional[float] = None
    max_48h_tss: float = 295.0
    max_48h_ans: float = 120.0
    max_48h_mech: float = 260.0
    max_daily_axial: float = 120.0
    max_weekly_axial: float = 320.0
    max_daily_neuro: float = 130.0
    max_weekly_neuro: float = 360.0
    max_single_run_share: float = 0.32
    # Pigeonhole caps for weeks with fewer than 4 runs (smallest possible share is 1/N). ADR-013.
    pigeonhole_run_share: Dict[int, float] = field(default_factory=lambda: {3: 0.40, 2: 0.60})
    readiness_alpha: List[float] = field(default_factory=lambda: [1.0] * 7)
    # ADR-022 (TASK-039): absolute long-run cap in MINUTES for weeks with < 4 runs, where the 32% ratio is vacuous.
    # long_run_step_min None = progression rule disabled (library/test default); the profile enables it.
    long_run_step_min: Optional[float] = None            # longest run <= previous week's longest + step
    long_run_floor_min: float = 30.0                     # cap never below this (first week / shortest library run)
    reentry_long_run_max_min: Optional[float] = None     # re-entry (run CTL < exit) ceiling; None = none
    long_run_abs_cap_min: Optional[float] = None         # RESOLVED per-week cap (resolve_long_run_cap); None = off

    @classmethod
    def from_config(cls, cfg: Any) -> "SafetyLimits":
        d = cls()
        return cls(
            weekday_single_workout_cap=cfg_get(cfg, "weekday_single_workout_cap", default=None),
            max_48h_tss=float(cfg_get(cfg, "max_48h_aerobic_tss", "max_48h_tss", default=d.max_48h_tss)),
            max_48h_ans=float(cfg_get(cfg, "max_48h_ans_cost", "max_48h_ans", default=d.max_48h_ans)),
            max_48h_mech=float(cfg_get(cfg, "max_48h_mech_strain", "max_48h_mech", default=d.max_48h_mech)),
            max_daily_axial=float(cfg_get(cfg, "max_daily_axial", "max_daily_axial_load", default=d.max_daily_axial)),
            max_weekly_axial=float(cfg_get(cfg, "max_weekly_axial", "max_weekly_axial_load", default=d.max_weekly_axial)),
            max_daily_neuro=float(cfg_get(cfg, "max_daily_neuro", "max_daily_neuro_load", default=d.max_daily_neuro)),
            max_weekly_neuro=float(cfg_get(cfg, "max_weekly_neuro", "max_weekly_neuro_load", default=d.max_weekly_neuro)),
            max_single_run_share=float(cfg_get(cfg, "long_run_cap_ratio", "max_single_run_vol_ratio",
                                               default=d.max_single_run_share)),
            pigeonhole_run_share=dict(cfg_get(cfg, "pigeonhole_run_share", default=d.pigeonhole_run_share)),
            readiness_alpha=list(cfg_get(cfg, "readiness_alpha", default=d.readiness_alpha)),
            long_run_step_min=_opt_float(cfg_get(cfg, "long_run_step_min", default=None)),
            long_run_floor_min=float(cfg_get(cfg, "long_run_floor_min", default=d.long_run_floor_min)),
            reentry_long_run_max_min=_opt_float(cfg_get(cfg, "reentry_long_run_max_min", default=None)),
        )

    def run_share_cap(self, n_runs: int) -> float:
        if n_runs >= 4:
            return self.max_single_run_share
        return float(self.pigeonhole_run_share.get(n_runs, 1.0))


def _opt_float(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def resolve_long_run_cap(limits: SafetyLimits, carry_in: Any = None, prev_longest_min: Optional[float] = None
                         ) -> Optional[float]:
    """This week's absolute long-run cap in minutes (ADR-022), or None when the rule is off.

    * progression cap ``max(floor, previous week's longest run + step)`` -- only in a season (a carry-in with
      load state exists; week 1 has previous longest 0, so it is the floor);
    * re-entry ceiling ``max(floor, reentry_long_run_max_min)`` while run CTL is below the re-entry exit
      (``carry_in.load.reentry``), and also for an isolated week with no carry-in;
    the tighter applies. Applied only to weeks with fewer than 4 runs (see ``_add_run_share_cap``)."""
    caps: List[float] = []
    load = getattr(carry_in, "load", None)
    if limits.long_run_step_min is not None and load is not None:
        prev = prev_longest_min if prev_longest_min is not None else float(getattr(carry_in, "prev_longest_run_min", 0.0))
        caps.append(max(limits.long_run_floor_min, prev + limits.long_run_step_min))
    if limits.reentry_long_run_max_min is not None and (load is None or getattr(load, "reentry", False)):
        caps.append(max(limits.long_run_floor_min, limits.reentry_long_run_max_min))
    return min(caps) if caps else None


def run_strain(w: Any) -> float:
    """Running mechanical strain of ``w`` (whole workout for a run, cardio part of a lift+run combo, or Brick run leg)."""
    if getattr(w, "sport", "") == "Run":
        return float(getattr(w, "mechanical_strain", 0.0))
    if getattr(w, "cardio_sport", "") == "Run":
        return float(getattr(w, "cardio_mech_strain", 0.0))
    if getattr(w, "sport", "") == "Brick":
        return float(getattr(w, "brick_run_strain", 0.0))
    return 0.0


def _flags(w: Any) -> Dict[str, int]:
    return getattr(w, "flags", None) or {}


def run_minutes(w: Any) -> float:
    """Running minutes of ``w``: whole duration for a run, cardio part of a lift+easy-run combo (DECISION-021),
    or the run leg of a Brick (TASK-059); 0 otherwise."""
    if getattr(w, "sport", "") == "Run":
        return float(getattr(w, "duration", 0.0))
    if getattr(w, "cardio_sport", "") == "Run":
        return float(getattr(w, "cardio_minutes", 0.0))
    if getattr(w, "sport", "") == "Brick":
        return float(getattr(w, "brick_run_minutes", 0.0))
    return 0.0


@dataclass
class WorkoutSets:
    runs: List[int]
    heavy_or_long_runs: List[int]   # threshold/interval/long runs: 48h separation + deadlift lockout
    deadlifts: List[int]            # heavy DL, or heavy strength without biomechanical flags (conservative)
    squats: List[int]
    presses: List[int]
    hi_torque_bikes: List[int]
    css_swims: List[int]
    strength: List[int]
    heavy_strength: List[int]
    hard: List[int]                 # hard endurance or heavy strength (hard/easy adjacency)
    strides: List[int]


def classify_workouts(workouts: Sequence[Any]) -> WorkoutSets:
    def ids(pred):
        return [i for i, w in enumerate(workouts) if pred(w)]

    def is_heavy_strength(w):
        f = _flags(w)
        return bool(getattr(w, "is_heavy_strength", False)) or f.get("heavy_dl", 0) == 1 or f.get("heavy_sq", 0) == 1

    return WorkoutSets(
        runs=ids(lambda w: w.sport == "Run" or getattr(w, "cardio_sport", "") == "Run"
                 or (w.sport == "Brick" and run_minutes(w) > 0)),  # incl. lift+run combos & Bike->Run bricks
        heavy_or_long_runs=ids(lambda w: (
            w.sport == "Run" and (
                getattr(w, "is_heavy_run", False)
                or _flags(w).get("heavy_run", 0) == 1
                or (
                    _flags(w).get("long_run", 0) == 1
                    and (float(getattr(w, "duration", 0.0) or 0.0) >= 70.0 - 1e-9
                         or bool(getattr(w, "is_anchor", False)))
                )
            )
        ) or (
            w.sport == "Brick" and (
                getattr(w, "brick_run_hard", False)
                or run_minutes(w) >= 70.0 - 1e-9
                or _flags(w).get("heavy_run", 0) == 1
                or (
                    _flags(w).get("long_run", 0) == 1
                    and (run_minutes(w) >= 70.0 - 1e-9 or bool(getattr(w, "is_anchor", False)))
                )
            )
        )),
        deadlifts=ids(lambda w: w.sport == "Strength" and (
            _flags(w).get("heavy_dl", 0) == 1
            or (getattr(w, "is_heavy_strength", False) and not any(k in _flags(w) for k in ("heavy_dl", "heavy_sq"))))),
        squats=ids(lambda w: _flags(w).get("heavy_sq", 0) == 1),
        presses=ids(lambda w: _flags(w).get("heavy_press", 0) == 1),
        hi_torque_bikes=ids(lambda w: _flags(w).get("hi_torque_bike", 0) == 1),
        css_swims=ids(lambda w: _flags(w).get("swim_th", 0) == 1),
        strength=ids(lambda w: w.sport == "Strength"),
        heavy_strength=ids(lambda w: w.sport == "Strength" and is_heavy_strength(w)),
        hard=ids(lambda w: getattr(w, "is_hard_endurance", False) or (w.sport == "Strength" and is_heavy_strength(w))),
        strides=ids(lambda w: getattr(w, "has_strides", False)),
    )


# =============================================================================
# Model hooks
# =============================================================================

@dataclass
class ModelHooks:
    """Engine-agnostic access to the HiGHS model under construction."""
    x: Callable[[int, int], int]                                # column of workout i on day d
    add_row: Callable[[Dict[int, float], float, float], None]   # (coeff map, lb, ub)
    add_var: Callable[[float, float, bool], int]                # (lb, ub, is_integer) -> column, cost 0
    allowed: Callable[[int, int], bool]                         # unary Tier-0 gating (facility, caps)
    s_col: Optional[Callable[[int], int]] = None                # weekly selection col s[i] = sum_d x[i, d]
    fix_var: Optional[Callable[[int, float, float], None]] = None  # direct col bound tightening (0 rows)


@dataclass
class RunShareModel:
    count_cols: Dict[int, int]       # {1: z1, 2: z2, 3: z3, 4: z_ge4}
    run_pairs: List[Tuple[int, int]]


def add_tier0_safety(hooks: ModelHooks, workouts: Sequence[Any], limits: SafetyLimits,
                     sets: Optional[WorkoutSets] = None) -> RunShareModel:
    """Add every Tier-0 orthopedic / physiological rule as a hard constraint."""
    S = sets or classify_workouts(workouts)
    N = len(workouts)
    inf = highspy.kHighsInf
    x, row = hooks.x, hooks.add_row
    s_col, fix_var = hooks.s_col, hooks.fix_var

    def dur(i):
        return float(getattr(workouts[i], "duration", 0.0))

    # Unary gating (pre-filter): weekday single-session cap, facility/time (engine-supplied).
    for i in range(N):
        ok_days = 0
        for d in DAYS:
            too_long = (d in WEEKDAYS and limits.weekday_single_workout_cap is not None
                        and dur(i) > limits.weekday_single_workout_cap + 1e-9)
            if too_long or not hooks.allowed(i, d):
                if fix_var is not None:
                    fix_var(x(i, d), 0.0, 0.0)
                else:
                    row({x(i, d): 1.0}, 0.0, 0.0)
            else:
                ok_days += 1
        if ok_days == 0 and fix_var is not None and s_col is not None:
            fix_var(s_col(i), 0.0, 0.0)

    # Daily / weekly axial and neuromuscular ceilings (ADR-008).
    for attr, day_cap, wk_cap in (("axial_load", limits.max_daily_axial, limits.max_weekly_axial),
                                  ("neuromuscular_load", limits.max_daily_neuro, limits.max_weekly_neuro)):
        loads = [float(getattr(w, attr, 0.0) or 0.0) for w in workouts]
        if not any(loads):
            continue
        for d in DAYS:
            cap = day_cap * (float(limits.readiness_alpha[d]) if attr == "neuromuscular_load" else 1.0)
            row({x(i, d): loads[i] for i in range(N) if loads[i]}, 0.0, cap)
        if s_col is not None:
            row({s_col(i): loads[i] for i in range(N) if loads[i]}, 0.0, wk_cap)
        else:
            row({x(i, d): loads[i] for i in range(N) if loads[i] for d in DAYS}, 0.0, wk_cap)

    # Rolling 48h TSS / ANS / mechanical strain caps.
    for attr, cap in (("aerobic_tss", limits.max_48h_tss), ("ans_cost", limits.max_48h_ans),
                      ("mechanical_strain", limits.max_48h_mech)):
        vals = [float(getattr(w, attr, 0.0) or 0.0) for w in workouts]
        for d in range(6):
            r = {x(i, d): vals[i] for i in range(N) if vals[i]}
            r.update({x(i, d + 1): vals[i] for i in range(N) if vals[i]})
            row(r, 0.0, cap)

    # Hard/Easy rule: no two consecutive hard days (hard endurance or heavy strength).
    hard_day = {d: hooks.add_var(0.0, 1.0, True) for d in DAYS}
    for d in DAYS:
        for i in S.hard:
            row({x(i, d): 1.0, hard_day[d]: -1.0}, -inf, 0.0)
    for d in range(6):
        row({hard_day[d]: 1.0, hard_day[d + 1]: 1.0}, 0.0, 1.0)

    for d in DAYS:
        # Heavy/long runs: at most one per day.
        row({x(i, d): 1.0 for i in S.heavy_or_long_runs}, 0.0, 1.0)
        # HEUR-004 same day: deadlift vs threshold/long run.
        row({x(i, d): 1.0 for i in S.deadlifts + S.heavy_or_long_runs}, 0.0, 1.0)
        # Antagonism matrix (same day): squat vs high-torque bike / threshold-long run; press vs CSS swim.
        row({x(i, d): 1.0 for i in S.squats + S.hi_torque_bikes}, 0.0, 1.0)
        row({x(i, d): 1.0 for i in S.squats + S.heavy_or_long_runs}, 0.0, 1.0)
        row({x(i, d): 1.0 for i in S.presses + S.css_swims}, 0.0, 1.0)
        # At most one strength session per day.
        row({x(i, d): 1.0 for i in S.strength}, 0.0, 1.0)

    for d in range(6):
        # >= 48h between heavy/long runs.
        r = {x(i, d): 1.0 for i in S.heavy_or_long_runs}
        r.update({x(j, d + 1): 1.0 for j in S.heavy_or_long_runs})
        row(r, 0.0, 1.0)
        # HEUR-004 +-24h: deadlift on d vs heavy/long run on d+1, and run on d vs deadlift on d+1.
        r = {x(i, d): 1.0 for i in S.deadlifts}
        r.update({x(j, d + 1): 1.0 for j in S.heavy_or_long_runs})
        row(r, 0.0, 1.0)
        r = {x(i, d): 1.0 for i in S.heavy_or_long_runs}
        r.update({x(j, d + 1): 1.0 for j in S.deadlifts})
        row(r, 0.0, 1.0)
        # Non-consecutive strength days.
        r = {x(i, d): 1.0 for i in S.strength}
        r.update({x(j, d + 1): 1.0 for j in S.strength})
        row(r, 0.0, 1.0)

    return _add_run_share_cap(hooks, workouts, limits, S)


def _add_run_share_cap(hooks: ModelHooks, workouts, limits: SafetyLimits, S: WorkoutSets) -> RunShareModel:
    """Single-run share cap applied to ALL runs, conditioned on the realized run count.

    Uses weekly selection indicators ``s_col(i)`` (when provided) and Balas-lifted coefficients
    across run-count levels ``z[1..4]`` for an exact, much tighter LP relaxation.
    """
    inf = highspy.kHighsInf
    pairs = [(i, d) for i in S.runs for d in DAYS if hooks.allowed(i, d)]
    if not pairs:
        return RunShareModel({}, [])
    s_col = hooks.s_col
    run_ids = sorted({i for i, _ in pairs})
    durs = {i: run_minutes(workouts[i]) for i in run_ids}
    z = {n: hooks.add_var(0.0, 1.0, True) for n in (1, 2, 3, 4)}
    n_upper = len(run_ids) if s_col is not None else len(pairs)
    v = hooks.add_var(0.0, float(n_upper), True)
    hooks.add_row({z[n]: 1.0 for n in z}, 0.0, 1.0)
    if s_col is not None:
        count = {s_col(i): 1.0 for i in run_ids}
    else:
        count = {hooks.x(i, d): 1.0 for i, d in pairs}
    count.update({z[1]: -1.0, z[2]: -2.0, z[3]: -3.0, v: -1.0})
    hooks.add_row(count, 0.0, 0.0)
    hooks.add_row({v: 1.0, z[4]: -float(n_upper)}, -inf, 0.0)
    hooks.add_row({v: 1.0, z[4]: -4.0}, 0.0, inf)

    r_tot = hooks.add_var(0.0, float(sum(durs.values())), False)
    if s_col is not None:
        r_row = {s_col(i): durs[i] for i in run_ids}
    else:
        r_row = {hooks.x(j, dp): durs[j] for j, dp in pairs}
    r_row[r_tot] = -1.0
    hooks.add_row(r_row, 0.0, 0.0)

    rhos = {1: 1.0, 2: limits.run_share_cap(2), 3: limits.run_share_cap(3), 4: limits.run_share_cap(4)}
    for n in (4, 3, 2):
        rho = rhos[n]
        if rho >= 1.0:
            continue
        if s_col is not None:
            for i in run_ids:
                di = durs[i]
                row_l: Dict[int, float] = {s_col(i): di, r_tot: -rho}
                for m in (1, 2, 3, 4):
                    if m == n:
                        continue
                    lift = max(0.0, 1.0 - rho / max(rhos[m], 1e-9))
                    if lift > 1e-9:
                        row_l[z[m]] = -di * lift
                hooks.add_row(row_l, -inf, 0.0)
        else:
            for i, d in pairs:
                di = durs[i]
                row_l = {hooks.x(i, d): di, r_tot: -rho}
                for m in (1, 2, 3, 4):
                    if m == n:
                        continue
                    lift = max(0.0, 1.0 - rho / max(rhos[m], 1e-9))
                    if lift > 1e-9:
                        row_l[z[m]] = -di * lift
                hooks.add_row(row_l, -inf, 0.0)

    L = limits.long_run_abs_cap_min
    if L is not None:
        L = max(L, min(durs.values()))
        if s_col is not None:
            for i in run_ids:
                if durs[i] > L + 1e-9:
                    hooks.add_row({s_col(i): 1.0, z[4]: -1.0}, -inf, 0.0)
        else:
            for i, d in pairs:
                if durs[i] > L + 1e-9:
                    hooks.add_row({hooks.x(i, d): 1.0, z[4]: -1.0}, -inf, 0.0)
    return RunShareModel(z, pairs)


def run_share_report(schedule_by_day: Dict[int, List[Any]], limits: SafetyLimits) -> Dict[str, Any]:
    runs = [w for d in DAYS for w in schedule_by_day.get(d, []) if run_minutes(w) > 0]
    n = len(runs)
    total = sum(run_minutes(w) for w in runs)
    share = (max(run_minutes(w) for w in runs) / total) if total > 0 else 0.0
    cap = limits.run_share_cap(n)
    note = None
    abs_cap = limits.long_run_abs_cap_min
    longest = max((run_minutes(w) for w in runs), default=0.0)
    if 0 < n < 4 and abs_cap is not None:
        note = (f"Absolute long-run cap (ADR-022): with {n} run(s) the {limits.max_single_run_share:.0%} ratio "
                f"(ADR-003) is unattainable, so the longest run is capped at {abs_cap:.0f} min instead "
                f"(longest scheduled {longest:.0f} min).")
        return {"n_runs": n, "max_single_run_share": share, "applied_cap": cap, "regime": "absolute",
                "abs_cap_min": abs_cap, "longest_run_min": longest, "note": note}
    if 0 < n < 4:
        note = (f"Pigeonhole: with {n} run(s) the largest run is at least {100.0 / n:.0f}% of running time, so the "
                f"{limits.max_single_run_share:.0%} cap (ADR-003) is unattainable; the explicit {n}-run cap "
                f"of {cap:.0%} was enforced instead.")
    return {"n_runs": n, "max_single_run_share": share, "applied_cap": cap,
            "regime": "pigeonhole" if 0 < n < 4 else "ratio", "abs_cap_min": abs_cap,
            "longest_run_min": longest, "note": note}


# =============================================================================
# Tier-1 named slacks
# =============================================================================

@dataclass
class _Slack:
    col: int
    category: str
    label: str
    weight: float
    message: str  # format string with {n}


class Tier1Registry:
    """Integer slacks for relaxable Tier-1 targets, weighted lexicographically by relaxation order."""

    def __init__(self, hooks: ModelHooks, relaxation_order: Optional[Sequence[str]] = None):
        self.hooks = hooks
        order = [c for c in (relaxation_order or DEFAULT_RELAXATION_ORDER) if c != "reduce_weekly_tss"]
        full = [c for c in _PREPEND if c not in order] + order + [c for c in _APPEND if c not in order]
        self.rank = {c: k for k, c in enumerate(full)}
        self.slacks: List[_Slack] = []

    def weight(self, category: str) -> float:
        k = self.rank.get(category, len(self.rank))
        return float(LEX_BASE ** k)

    def add(self, category: str, label: str, ub: float, message: str) -> Optional[int]:
        if ub <= 0:
            return None
        col = self.hooks.add_var(0.0, float(ub), True)
        self.slacks.append(_Slack(col, category, label, self.weight(category), message))
        return col

    def add_named(self, category: str, label: str, ub: float, weight: float, message: str) -> Optional[int]:
        """Register a slack with an explicit weight (outside the relaxation-order ranks), e.g. the TASK-018
        running-ACWR floor waiver, which is cheaper than every ranked relaxation."""
        if ub <= 0:
            return None
        col = self.hooks.add_var(0.0, float(ub), True)
        self.slacks.append(_Slack(col, category, label, float(weight), message))
        return col

    def costs(self) -> Dict[int, float]:
        return {s.col: s.weight for s in self.slacks}

    def report(self, col_values: Sequence[float]) -> List[Dict[str, Any]]:
        out = []
        for s in self.slacks:
            n = int(round(col_values[s.col]))
            if n > 0:
                out.append({"category": s.category, "target": s.label, "amount": n,
                            "message": s.message.format(n=n)})
        return out


def add_tier1_targets(reg: Tier1Registry, workouts: Sequence[Any], cfg: Any, sets: WorkoutSets,
                      x: Callable[[int, int], int], sport_day: Optional[Callable[[str, int], int]] = None,
                      rest_day: Optional[Callable[[int], int]] = None, include_anchors: bool = True,
                      include_strides: bool = True, include_heavy_strength: bool = True,
                      rest_bounds_default: Tuple[int, int] = (1, 7),
                      extra_members: Optional[Callable[[str], Sequence[int]]] = None,
                      s: Optional[Callable[[int], int]] = None) -> None:
    """Add anchors, sport minimum frequencies, strides, heavy-strength count and rest days as Tier-1 rows.

    Upper bounds (max frequency, max strides, max heavy strength) stay hard. Only the minimums
    are relaxable, and only through a named slack.
    """
    row = reg.hooks.add_row
    inf = highspy.kHighsInf
    s_fn = s or reg.hooks.s_col

    def at_least(real: Dict[int, float], lo: float, slack: Optional[int]) -> None:
        r = dict(real)
        if slack is not None:
            r[slack] = 1.0
        if r:
            row(r, float(lo), inf)

    if include_anchors:
        for i, w in enumerate(workouts):
            if getattr(w, "is_anchor", False):
                sl = reg.add(f"shorten_or_drop_anchor:{w.sport}", f"anchor {w.code}", 1,
                             f"Dropped anchor {w.code} ({w.sport}, {w.duration:.0f} min).")
                at_least({s_fn(i): 1.0} if s_fn is not None else {x(i, d): 1.0 for d in DAYS}, 1.0, sl)

    bounds = cfg_get(cfg, "sport_freq_bounds", default=None) or {}
    for sport, (f_min, f_max) in bounds.items():
        sd_cols = [sport_day(sport, d) for d in DAYS] if sport_day is not None else [None]
        if all(c is not None for c in sd_cols):
            real = {c: 1.0 for c in sd_cols}
        elif s_fn is not None:
            real = {s_fn(i): 1.0 for i, w in enumerate(workouts) if w.sport == sport}
        else:
            real = {x(i, d): 1.0 for i, w in enumerate(workouts) if w.sport == sport for d in DAYS}
        if extra_members is not None:  # lift + easy cardio combos count toward the cardio sport (DECISION-021)
            if s_fn is not None:
                real.update({s_fn(i): 1.0 for i in extra_members(sport)})
            else:
                real.update({x(i, d): 1.0 for i in extra_members(sport) for d in DAYS})
        if real:
            row(real, 0.0, float(f_max))
        if f_min > 0:
            sl = reg.add(f"reduce_frequency:{sport}", f"{sport} sessions >= {f_min}", f_min,
                         f"{sport}: {{n}} fewer session(s) than the minimum of {f_min}.")
            at_least(real, f_min, sl)

    if include_strides:
        lo = int(cfg_get(cfg, "min_stride_sessions", default=0) or 0)
        hi = int(cfg_get(cfg, "max_stride_sessions", default=7))
        real = ({s_fn(i): 1.0 for i in sets.strides} if s_fn is not None
                else {x(i, d): 1.0 for i in sets.strides for d in DAYS})
        if real:
            row(real, 0.0, float(hi))
        if lo > 0:
            sl = reg.add("drop_stride_count", f"stride sessions >= {lo}", lo,
                         f"Strides: {{n}} fewer stride session(s) than the minimum of {lo}.")
            at_least(real, lo, sl)

    req_raw = cfg_get(cfg, "required_heavy_strength_sessions", default=None)
    if include_heavy_strength and req_raw is not None:  # engines without a heavy-strength count skip this rule
        req = int(req_raw)
        real = ({s_fn(i): 1.0 for i in sets.heavy_strength} if s_fn is not None
                else {x(i, d): 1.0 for i in sets.heavy_strength for d in DAYS})
        if real:
            row(real, 0.0, float(req))
        if req > 0:
            # Floor of 1 heavy session (DECISION-006): slack may cover at most req - 1.
            sl = reg.add("reduce_heavy_strength_count_to_min_1", f"heavy strength == {req}", req - 1,
                         f"Heavy strength: {{n}} fewer heavy session(s) than the {req} required (floor 1).")
            if real:
                at_least(real, req, sl)
            else:
                # No heavy strength in the library: the floor of 1 is unattainable. Make that an explicit
                # Tier-0 infeasibility (reported by explain_feasibility) instead of silently dropping it.
                zero = reg.hooks.add_var(0.0, 0.0, False)
                at_least({zero: 1.0}, req, sl)

    if rest_day is not None:
        lo = int(cfg_get(cfg, "min_rest_days", default=rest_bounds_default[0]))
        hi = int(cfg_get(cfg, "max_rest_days", default=rest_bounds_default[1]))
        real = {rest_day(d): 1.0 for d in DAYS}
        s_lo = reg.add("reduce_rest_days_to_min_1", f"rest days >= {lo}", max(lo - 1, 0),
                       f"Rest days: {{n}} fewer than the minimum of {lo} (floor 1).")
        at_least(real, lo, s_lo)
        s_hi = reg.add("extend_rest_days", f"rest days <= {hi}", 7 - hi,
                       f"Rest days: {{n}} more than the maximum of {hi} (not enough schedulable sessions).")
        r = dict(real)
        if s_hi is not None:
            r[s_hi] = -1.0
        row(r, -inf, float(hi))


def _max_nonconsecutive(days: Sequence[int]) -> int:
    count, last = 0, -10
    for d in sorted(days):
        if d - last >= 2:
            count, last = count + 1, d
    return count


# =============================================================================
# Pre-solve feasibility explainer
# =============================================================================

def explain_feasibility(workouts: Sequence[Any], cfg: Any, limits: SafetyLimits,
                        allowed: Callable[[int, int], bool], sets: Optional[WorkoutSets] = None) -> List[str]:
    """Plain-English reasons a week cannot meet its Tier-1 targets under Tier-0 rules (pre-solve)."""
    S = sets or classify_workouts(workouts)
    msgs: List[str] = []

    def ok(i, d):
        cap = limits.weekday_single_workout_cap
        if d in WEEKDAYS and cap is not None and workouts[i].duration > cap + 1e-9:
            return False
        w = workouts[i]
        if float(getattr(w, "axial_load", 0.0) or 0.0) > limits.max_daily_axial + 1e-9:
            return False
        if float(getattr(w, "neuromuscular_load", 0.0) or 0.0) > limits.max_daily_neuro * float(limits.readiness_alpha[d]) + 1e-9:
            return False
        return allowed(i, d)

    def why_not(i):
        w = workouts[i]
        reasons = []
        if float(getattr(w, "axial_load", 0.0) or 0.0) > limits.max_daily_axial + 1e-9:
            reasons.append(f"its spinal (axial) load {w.axial_load:.0f} exceeds the daily cap {limits.max_daily_axial:.0f}")
        if float(getattr(w, "neuromuscular_load", 0.0) or 0.0) > limits.max_daily_neuro + 1e-9:
            reasons.append(f"its neuromuscular load {w.neuromuscular_load:.0f} exceeds the daily cap {limits.max_daily_neuro:.0f}")
        if not reasons:
            reasons.append("no day has enough time, facility access, or weekday-session allowance for it")
        return "; ".join(reasons)

    for i, w in enumerate(workouts):
        if getattr(w, "is_anchor", False) and not any(ok(i, d) for d in DAYS):
            msgs.append(f"Anchor {w.code} ({w.sport}, {w.duration:.0f} min) cannot be scheduled: {why_not(i)}.")

    # Workouts that the daily axial / neuromuscular caps rule out entirely.
    for attr, cap, label in (("axial_load", limits.max_daily_axial, "spinal (axial) load"),
                             ("neuromuscular_load", limits.max_daily_neuro, "neuromuscular load")):
        blocked = [w for w in workouts if float(getattr(w, attr, 0.0) or 0.0) > cap + 1e-9]
        if blocked:
            listing = ", ".join(f"{w.code} ({float(getattr(w, attr)):.0f})" for w in blocked[:6])
            more = f" and {len(blocked) - 6} more" if len(blocked) > 6 else ""
            msgs.append(f"Daily {label} cap {cap:.0f} excludes {len(blocked)} workout(s): {listing}{more}.")

    bounds = cfg_get(cfg, "sport_freq_bounds", default=None) or {}
    for sport, (f_min, _f_max) in bounds.items():
        if f_min <= 0:
            continue
        days_ok = [d for d in DAYS if any(ok(i, d) for i, w in enumerate(workouts) if w.sport == sport)]
        n_distinct = sum(1 for i, w in enumerate(workouts) if w.sport == sport and any(ok(i, d) for d in DAYS))
        if len(days_ok) < f_min:
            where = ", ".join(DAY_NAMES[d] for d in days_ok) or "no day"
            msgs.append(f"{sport} needs at least {f_min} session(s) but only {len(days_ok)} day(s) can host one ({where}).")
        elif n_distinct < f_min:
            msgs.append(f"{sport} needs at least {f_min} session(s) but the library has only {n_distinct} schedulable "
                        f"{sport} workout(s) (each workout is used at most once per week).")

    req = int(cfg_get(cfg, "required_heavy_strength_sessions", default=0) or 0)
    if req > 0:
        hs_days = [d for d in DAYS if any(ok(i, d) for i in S.heavy_strength)]
        max_nonconsec = _max_nonconsecutive(hs_days)
        if not S.heavy_strength:
            msgs.append(f"{req} heavy strength session(s) required but the library has none.")
        elif not hs_days:
            reasons = sorted({why_not(i) for i in S.heavy_strength})
            msgs.append(f"{req} heavy strength session(s) required but none can be scheduled: {reasons[0]}.")
        elif len([i for i in S.heavy_strength if any(ok(i, d) for d in DAYS)]) < req:
            n_hs = len([i for i in S.heavy_strength if any(ok(i, d) for d in DAYS)])
            msgs.append(f"{req} heavy strength session(s) required but the library has only {n_hs} schedulable heavy "
                        f"strength workout(s) (each used at most once per week).")
        elif max_nonconsec < req:
            msgs.append(f"{req} heavy strength sessions required but strength days must be non-consecutive and only "
                        f"{len(hs_days)} day(s) can host one.")

    run_bounds = bounds.get("Run")
    if run_bounds and 0 < run_bounds[1] < 4:
        if limits.long_run_abs_cap_min is not None:
            msgs.append(f"Run frequency is capped at {run_bounds[1]}; with fewer than 4 runs the 32% single-run cap is "
                        f"unattainable, so an absolute long-run cap of {limits.long_run_abs_cap_min:.0f} min "
                        f"applies (ADR-022).")
        else:
            msgs.append(f"Run frequency is capped at {run_bounds[1]}; with fewer than 4 runs the 32% single-run cap is "
                        f"mathematically unattainable, so the explicit {run_bounds[1]}-run cap of "
                        f"{limits.run_share_cap(run_bounds[1]):.0%} applies.")

    min_rest = int(cfg_get(cfg, "min_rest_days", default=0) or 0)
    min_sessions = sum(lo for lo, _ in bounds.values())
    train_days = 7 - min_rest
    if min_sessions > 2 * train_days:
        msgs.append(f"Sport minimums add up to {min_sessions} sessions but {train_days} training day(s) hold at most "
                    f"{2 * train_days} (max 2 sessions/day).")
    return msgs


# =============================================================================
# Lexicographic solve
# =============================================================================

@dataclass
class LexState:
    locked: bool = False
    tier1_penalty: float = 0.0


def lexicographic_solve(h: "highspy.Highs", tier1_costs: Dict[int, float], state: LexState,
                        tier2_gap: Optional[float] = None, *,
                        phase_a_factory: Optional[Callable[[], "highspy.Highs"]] = None,
                        run_bounds: Optional[Tuple[int, int]] = None,
                        run_share_model: Optional[RunShareModel] = None,
                        run_slack_cols: Optional[Sequence[int]] = None,
                        phase_b_no_sub_mip: bool = True) -> SolveResult:
    """Phase A minimizes Tier-1 slack only. Its per-weight slacks are then bound-locked without
    huge coefficients, and Phase B optimizes Tier-2. Later calls (Top-K) run only Phase B."""
    if not tier1_costs or state.locked:
        return run_and_check(h)

    import numpy as np  # local: keep module import-light

    st_g, orig_rel_gap = h.getOptionValue("mip_rel_gap")
    target_gap = float(orig_rel_gap) if (tier2_gap is None and st_g == highspy.HighsStatus.kOk) else (0.01 if tier2_gap is None else tier2_gap)

    solver_a = phase_a_factory() if phase_a_factory is not None else h
    n = solver_a.getNumCol()
    tier2 = list(h.getLp().col_cost_) if solver_a is h else None
    idx = np.arange(n, dtype=np.int32)

    phase_a = np.zeros(n, dtype=np.float64)
    for c, wgt in tier1_costs.items():
        phase_a[c] = wgt
    solver_a.changeColsCost(n, idx, phase_a)
    solver_a.setOptionValue("mip_rel_gap", 0.0)
    solver_a.setOptionValue("mip_abs_gap", 0.5)  # integer-valued objective
    res_a = run_and_check(solver_a)
    if not res_a.has_solution:
        if solver_a is h and tier2 is not None:
            solver_a.changeColsCost(n, idx, np.asarray(tier2, dtype=np.float64))
        return res_a

    best = float(res_a.objective or 0.0)
    # Lock each weight level cleanly via column bounds or unit-coefficient rows (eliminates 10^11 row scaling).
    by_w: Dict[float, List[int]] = {}
    for c, wgt in tier1_costs.items():
        by_w.setdefault(float(wgt), []).append(c)
    for _, cols_w in sorted(by_w.items()):
        v_sum = sum(int(round(res_a.col_value[c])) for c in cols_w)
        if v_sum == 0:
            for c in cols_w:
                h.changeColBounds(c, 0.0, 0.0)
        elif len(cols_w) == 1:
            h.changeColBounds(cols_w[0], float(v_sum), float(v_sum))
        else:
            h.addRow(-highspy.kHighsInf, float(v_sum) + 0.5, len(cols_w),
                     np.array(sorted(cols_w), dtype=np.int32), np.ones(len(cols_w), dtype=np.float64))

    if run_bounds is not None and run_share_model is not None and run_share_model.count_cols:
        r_min, r_max = int(run_bounds[0]), int(run_bounds[1])
        s_val = sum(int(round(res_a.col_value[c])) for c in (run_slack_cols or ()))
        eff_min = max(0, r_min - s_val)
        if eff_min <= r_max:
            for n_lvl in (1, 2, 3, 4):
                if n_lvl in run_share_model.count_cols and (n_lvl < eff_min or n_lvl > r_max):
                    h.changeColBounds(run_share_model.count_cols[n_lvl], 0.0, 0.0)
            if eff_min >= 4 and 4 in run_share_model.count_cols:
                h.changeColBounds(run_share_model.count_cols[4], 1.0, 1.0)

    if solver_a is h and tier2 is not None:
        for c in tier1_costs:
            tier2[c] = 0.0
        h.changeColsCost(n, np.arange(h.getNumCol(), dtype=np.int32), np.asarray(tier2, dtype=np.float64))
    else:
        for c in tier1_costs:
            h.changeColCost(c, 0.0)
    h.setOptionValue("mip_abs_gap", 1e-6)
    h.setOptionValue("mip_rel_gap", target_gap)
    if phase_b_no_sub_mip:
        for opt_name in ("mip_heuristic_run_rins", "mip_heuristic_run_rens", "mip_heuristic_run_feasibility_jump"):
            h.setOptionValue(opt_name, False)
    state.locked = True
    state.tier1_penalty = best
    res_b = run_and_check(h)
    if not res_b.has_solution and res_a.has_solution:
        # Phase B found no incumbent within budget: fall back to the Phase-A solution (already Tier-1 optimal).
        logger.warning("Lexicographic Phase B found no incumbent (%s); returning Phase-A solution.", res_b.status)
        return res_a
    return res_b
