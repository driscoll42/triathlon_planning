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
_PREPEND = ["extend_rest_days"]
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
    max_48h_ans: float = 700.0
    max_48h_mech: float = 260.0
    max_daily_axial: float = 120.0
    max_weekly_axial: float = 320.0
    max_daily_neuro: float = 130.0
    max_weekly_neuro: float = 360.0
    max_single_run_share: float = 0.32
    # Pigeonhole caps for weeks with fewer than 4 runs (smallest possible share is 1/N). ADR-013.
    pigeonhole_run_share: Dict[int, float] = field(default_factory=lambda: {3: 0.40, 2: 0.60})
    readiness_alpha: List[float] = field(default_factory=lambda: [1.0] * 7)

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
        )

    def run_share_cap(self, n_runs: int) -> float:
        if n_runs >= 4:
            return self.max_single_run_share
        return float(self.pigeonhole_run_share.get(n_runs, 1.0))


def _flags(w: Any) -> Dict[str, int]:
    return getattr(w, "flags", None) or {}


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
        runs=ids(lambda w: w.sport == "Run"),
        heavy_or_long_runs=ids(lambda w: w.sport == "Run" and (
            getattr(w, "is_heavy_run", False) or _flags(w).get("heavy_run", 0) == 1 or _flags(w).get("long_run", 0) == 1)),
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

    def dur(i):
        return float(getattr(workouts[i], "duration", 0.0))

    # Unary gating (pre-filter): weekday single-session cap, facility/time (engine-supplied).
    for i in range(N):
        for d in DAYS:
            too_long = (d in WEEKDAYS and limits.weekday_single_workout_cap is not None
                        and dur(i) > limits.weekday_single_workout_cap + 1e-9)
            if too_long or not hooks.allowed(i, d):
                row({x(i, d): 1.0}, 0.0, 0.0)

    # Daily / weekly axial and neuromuscular ceilings (ADR-008).
    for attr, day_cap, wk_cap in (("axial_load", limits.max_daily_axial, limits.max_weekly_axial),
                                  ("neuromuscular_load", limits.max_daily_neuro, limits.max_weekly_neuro)):
        loads = [float(getattr(w, attr, 0.0) or 0.0) for w in workouts]
        if not any(loads):
            continue
        for d in DAYS:
            cap = day_cap * (float(limits.readiness_alpha[d]) if attr == "neuromuscular_load" else 1.0)
            row({x(i, d): loads[i] for i in range(N) if loads[i]}, 0.0, cap)
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

    R = number of scheduled runs, encoded with z1..z3 (R == n) and z_ge4 (R >= 4).
    For each level n with cap rho_n < 1 and each run (i, d):
        dur_i * x_id - rho_n * sum_j dur_j x_j + dur_i * z_n <= dur_i
    The row binds only when z_n = 1. There is no silent relaxation: weeks with < 4 runs get the
    explicit pigeonhole cap from SafetyLimits.pigeonhole_run_share, reported via run_share_report().
    """
    inf = highspy.kHighsInf
    pairs = [(i, d) for i in S.runs for d in DAYS if hooks.allowed(i, d)]
    if not pairs:
        return RunShareModel({}, [])
    z = {n: hooks.add_var(0.0, 1.0, True) for n in (1, 2, 3, 4)}
    v = hooks.add_var(0.0, float(len(pairs)), True)
    hooks.add_row({z[n]: 1.0 for n in z}, 0.0, 1.0)
    count = {hooks.x(i, d): 1.0 for i, d in pairs}
    count.update({z[1]: -1.0, z[2]: -2.0, z[3]: -3.0, v: -1.0})
    hooks.add_row(count, 0.0, 0.0)
    hooks.add_row({v: 1.0, z[4]: -float(len(pairs))}, -inf, 0.0)
    hooks.add_row({v: 1.0, z[4]: -4.0}, 0.0, inf)

    durs = {i: float(workouts[i].duration) for i in S.runs}
    for n in (4, 3, 2):
        rho = limits.run_share_cap(n)
        if rho >= 1.0:
            continue
        for i, d in pairs:
            r = {hooks.x(j, dp): -rho * durs[j] for j, dp in pairs}
            r[hooks.x(i, d)] = r[hooks.x(i, d)] + durs[i]
            r[z[n]] = durs[i]
            hooks.add_row(r, -inf, durs[i])
    return RunShareModel(z, pairs)


def run_share_report(schedule_by_day: Dict[int, List[Any]], limits: SafetyLimits) -> Dict[str, Any]:
    runs = [w for d in DAYS for w in schedule_by_day.get(d, []) if w.sport == "Run"]
    n = len(runs)
    total = sum(w.duration for w in runs)
    share = (max(w.duration for w in runs) / total) if total > 0 else 0.0
    cap = limits.run_share_cap(n)
    note = None
    if 0 < n < 4:
        note = (f"Pigeonhole: with {n} run(s) the largest run is at least {100.0 / n:.0f}% of running time, so the "
                f"{limits.max_single_run_share:.0%} cap (ADR-003) is unattainable; the explicit {n}-run cap "
                f"of {cap:.0%} was enforced instead.")
    return {"n_runs": n, "max_single_run_share": share, "applied_cap": cap, "note": note}


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
                      rest_bounds_default: Tuple[int, int] = (1, 7)) -> None:
    """Add anchors, sport minimum frequencies, strides, heavy-strength count and rest days as Tier-1 rows.

    Upper bounds (max frequency, max strides, max heavy strength) stay hard. Only the minimums
    are relaxable, and only through a named slack.
    """
    row = reg.hooks.add_row
    inf = highspy.kHighsInf

    def at_least(real: Dict[int, float], lo: float, slack: Optional[int]) -> None:
        r = dict(real)
        if slack is not None:
            r[slack] = 1.0
        if r:
            row(r, float(lo), inf)

    if include_anchors:
        for i, w in enumerate(workouts):
            if getattr(w, "is_anchor", False):
                s = reg.add(f"shorten_or_drop_anchor:{w.sport}", f"anchor {w.code}", 1,
                            f"Dropped anchor {w.code} ({w.sport}, {w.duration:.0f} min).")
                at_least({x(i, d): 1.0 for d in DAYS}, 1.0, s)

    bounds = cfg_get(cfg, "sport_freq_bounds", default=None) or {}
    for sport, (f_min, f_max) in bounds.items():
        sd_cols = [sport_day(sport, d) for d in DAYS] if sport_day is not None else [None]
        if all(c is not None for c in sd_cols):
            real = {c: 1.0 for c in sd_cols}
        else:
            real = {x(i, d): 1.0 for i, w in enumerate(workouts) if w.sport == sport for d in DAYS}
        if real:
            row(real, 0.0, float(f_max))
        if f_min > 0:
            s = reg.add(f"reduce_frequency:{sport}", f"{sport} sessions >= {f_min}", f_min,
                        f"{sport}: {{n}} fewer session(s) than the minimum of {f_min}.")
            at_least(real, f_min, s)

    if include_strides:
        lo = int(cfg_get(cfg, "min_stride_sessions", default=0) or 0)
        hi = int(cfg_get(cfg, "max_stride_sessions", default=7))
        real = {x(i, d): 1.0 for i in sets.strides for d in DAYS}
        if real:
            row(real, 0.0, float(hi))
        if lo > 0:
            s = reg.add("drop_stride_count", f"stride sessions >= {lo}", lo,
                        f"Strides: {{n}} fewer stride session(s) than the minimum of {lo}.")
            at_least(real, lo, s)

    req_raw = cfg_get(cfg, "required_heavy_strength_sessions", default=None)
    if include_heavy_strength and req_raw is not None:  # engines without a heavy-strength count skip this rule
        req = int(req_raw)
        real = {x(i, d): 1.0 for i in sets.heavy_strength for d in DAYS}
        if real:
            row(real, 0.0, float(req))
        if req > 0:
            # Floor of 1 heavy session (DECISION-006): slack may cover at most req - 1.
            s = reg.add("reduce_heavy_strength_count_to_min_1", f"heavy strength == {req}", req - 1,
                        f"Heavy strength: {{n}} fewer heavy session(s) than the {req} required (floor 1).")
            if real:
                at_least(real, req, s)
            else:
                # No heavy strength in the library: the floor of 1 is unattainable. Make that an explicit
                # Tier-0 infeasibility (reported by explain_feasibility) instead of silently dropping it.
                zero = reg.hooks.add_var(0.0, 0.0, False)
                at_least({zero: 1.0}, req, s)

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
                        tier2_gap: Optional[float] = None) -> SolveResult:
    """Phase A minimizes Tier-1 slack only. Its optimum is then locked with a row, and
    Phase B optimizes the engine's Tier-2 objective. Later calls (Top-K) run only Phase B."""
    if not tier1_costs or state.locked:
        return run_and_check(h)

    n = h.getNumCol()
    tier2 = list(h.getLp().col_cost_)
    idx = list(range(n))
    import numpy as np  # local: keep module import-light

    phase_a = [0.0] * n
    for c, wgt in tier1_costs.items():
        phase_a[c] = wgt
    h.changeColsCost(n, np.array(idx, dtype=np.int32), np.array(phase_a, dtype=np.float64))
    h.setOptionValue("mip_rel_gap", 0.0)
    h.setOptionValue("mip_abs_gap", 0.5)  # integer-valued objective
    res_a = run_and_check(h)
    if not res_a.has_solution:
        h.changeColsCost(n, np.array(idx, dtype=np.int32), np.array(tier2, dtype=np.float64))
        return res_a

    best = float(res_a.objective or 0.0)
    cols = sorted(tier1_costs)
    h.addRow(-highspy.kHighsInf, best + 0.5, len(cols), np.array(cols, dtype=np.int32),
             np.array([tier1_costs[c] for c in cols], dtype=np.float64))
    for c in cols:  # Tier-1 slacks carry no Tier-2 cost
        tier2[c] = 0.0
    h.changeColsCost(n, np.array(idx, dtype=np.int32), np.array(tier2, dtype=np.float64))
    h.setOptionValue("mip_abs_gap", 1e-6)
    h.setOptionValue("mip_rel_gap", 0.01 if tier2_gap is None else tier2_gap)
    state.locked = True
    state.tier1_penalty = best
    res_b = run_and_check(h)
    if not res_b.has_solution and res_a.has_solution:
        # Phase B timed out without an incumbent: fall back to the Phase-A solution (already Tier-1 optimal).
        logger.warning("Lexicographic Phase B found no incumbent (%s); returning Phase-A solution.", res_b.status)
        return res_a
    return res_b
