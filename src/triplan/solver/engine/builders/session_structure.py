"""Session-structure builder (TASK-019, ADR-019): hard/key structure, Hard-Days-Hard, AM/PM windows.

Every rule is its own function with an ``EngineOptions`` switch; ``add_session_structure`` runs the
enabled ones. Hard / key classification itself (``q_i``) lives in ``engine.session_quality`` and is applied
to the workout list *before* the model is built (pre-filtering invariant), so these rows only see
``is_hard_endurance`` and ``ctx.key``.

* ``add_one_hard_per_day``: ``sum_{i in HardEndurance} x[i,d] <= 1`` (the meso facade already carries this
  row in ``orthopedic.add_meso_adjacency``; the hybrid facade gets it here).
* ``add_key_sessions``: ``key_lo <= sum_{i,d} key_i x[i,d] <= key_hi``. The upper bound is hard. The lower
  bound is a named Tier-1 slack (``reduce_key_sessions``) like every other minimum, so an unmeetable week is
  reported, not infeasible. (Stride sessions: ``min_stride_sessions`` defaults to 2 and stays the Tier-1
  ``drop_stride_count`` slack in ``safety_constraints.add_tier1_targets``; there is one stride rule.)
* ``add_hard_days_hard``: on every day with two windows, ``sum_{i in HeavyLower} x[i,d] <= k_d`` with
  ``k_d = sum_{j in HardEndurance} x[j,d]``: heavy lower-body lifting is stacked on a hard endurance day
  (AM endurance + PM lift) so easy days stay easy. On single-window days nothing can be stacked, so the
  heavy lift stands alone and the existing hard/easy adjacency rows apply. The stacking row carries a
  named Tier-1 slack (``unstack_heavy_strength``) so a week that cannot stack still lifts and reports it. Non-consecutive strength days
  already live in the shared safety layer and are not repeated.
* ``add_window_gating``: ``double[d] <= two_session_windows[d]`` (default: every day has two windows).
* ``add_combo_exclusivity``: a day holds at most one workout that puts Bike (or Run) work on it, counting a
  lift + easy cardio combo as a Bike (Run) session, so a combo never doubles a sport on one day.

``order_day`` gives the deterministic AM/PM order used in plan output.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np

from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF
from triplan.solver.engine.session_quality import ENDURANCE_SPORTS
from triplan.solver.safety_constraints import DAY_NAMES

_SPORT_ORDER = {"Swim": 0, "Bike": 1, "Brick": 2, "Run": 3, "Strength": 4}


def _hard_endurance_ids(ctx: BuildContext) -> np.ndarray:
    tab = ctx.table
    return np.flatnonzero(tab.is_hard_endurance & tab.mask(*ENDURANCE_SPORTS))


def add_one_hard_per_day(ctx: BuildContext) -> None:
    hard = _hard_endurance_ids(ctx)
    if len(hard) < 2:
        return
    for d in ctx.days:
        ctx.model.add_row((ctx.X[hard, d], np.ones(len(hard))), 0.0, 1.0, name=f"one_hard_{d}")


def add_key_sessions(ctx: BuildContext) -> None:
    lo, hi = ctx.cfg.key_range
    ids = np.flatnonzero(ctx.key) if ctx.key is not None else np.array([], dtype=np.int64)
    cols, vals = ctx.per_week(np.ones(ctx.n), ids)
    m = ctx.model
    if len(cols):
        m.add_row((cols, vals), 0.0, float(hi), name="key_sessions_max")
    if lo <= 0:
        return
    if ctx.reg is not None:
        slack = ctx.reg.add("reduce_key_sessions", f"key sessions >= {lo}", lo,
                            f"Key sessions: {{n}} fewer than the minimum of {lo}.")
        m.add_row((np.append(cols, slack), np.append(vals, 1.0)), float(lo), INF, name="key_sessions_min")
    else:
        m.add_row((cols, vals), float(lo), INF, name="key_sessions_min")


def add_hard_days_hard(ctx: BuildContext) -> None:
    heavy = np.asarray(ctx.sets.heavy_strength, dtype=np.int64)
    if len(heavy) == 0:
        return
    hard = _hard_endurance_ids(ctx)
    two = ctx.cfg.two_windows
    caps = ctx.cfg.daily_caps
    wk_cap = ctx.limits.weekday_single_workout_cap
    tab = ctx.table
    for d in ctx.days:
        if not two[d]:
            continue
        # Stacking must be physically possible on this day (both sessions allowed, together within the day's
        # time cap); otherwise the lift stands alone and nothing is reported.
        if not any(ctx.allowed(int(i), d) and ctx.allowed(int(j), d)
                   and tab.duration[i] + tab.duration[j] <= float(caps[d]) + 1e-9
                   for i in heavy for j in hard):
            continue
        cols = np.concatenate([ctx.X[heavy, d], ctx.X[hard, d]])
        vals = np.concatenate([np.ones(len(heavy)), -np.ones(len(hard))])
        if ctx.reg is not None:
            # A week with no hard endurance session to stack on (or none that the other Tier-0 rules allow on
            # a lifting day, e.g. squat vs high-torque bike) must still lift: the unstacked day is a named,
            # reported Tier-1 slack, never a silent drop and never an infeasible week.
            slack = ctx.reg.add("unstack_heavy_strength", f"heavy strength stacked on a hard day ({DAY_NAMES[d]})", 1,
                                f"Heavy lower-body strength on {DAY_NAMES[d]} is not stacked on a hard endurance "
                                f"day ({{n}} unstacked).")
            if slack is not None:
                cols, vals = np.append(cols, slack), np.append(vals, -1.0)
        ctx.model.add_row((cols, vals), -INF, 0.0, name=f"hdh_{d}")


def add_window_gating(ctx: BuildContext) -> None:
    two = ctx.cfg.two_windows
    for d in ctx.days:
        col = ctx.double.get(d)
        if col is None:
            continue
        if not two[d]:
            ctx.model.add_row({col: 1.0}, -INF, 0.0, name=f"w_double_le_two_windows_{d}")


def add_combo_exclusivity(ctx: BuildContext) -> None:
    tab = ctx.table
    for sport in ("Bike", "Run"):
        if not tab.extra_members(sport):
            continue
        ids = tab.members(sport)
        for d in ctx.days:
            ctx.model.add_row((ctx.X[ids, d], np.ones(len(ids))), 0.0, 1.0, name=f"combo_{sport}_{d}")


def add_session_structure(ctx: BuildContext) -> None:
    opt = ctx.opt
    if opt.one_hard_per_day and not opt.meso_adjacency_rows:
        add_one_hard_per_day(ctx)
    if opt.key_sessions:
        add_key_sessions(ctx)
    if opt.hard_days_hard:
        add_hard_days_hard(ctx)
    if opt.window_gating:
        add_window_gating(ctx)
    if (opt.lift_easy_combo or any(ctx.table.brick_ids(s) for s in ("Bike", "Run"))) and opt.session_model != "single_per_day":
        add_combo_exclusivity(ctx)


# ----------------------------------------------------------------------------- AM/PM ordering

def order_day(workouts: Sequence[Any]) -> List[Any]:
    """Deterministic AM -> PM order of the workouts of one day.

    * A lift (strength) paired with endurance: a HARD endurance session goes AM and the lift PM (Keep
      Hard Days Hard: AM endurance + PM strength); with an EASY endurance session the lift goes AM,
      fresh, and the easy session PM (>= 6 h later, so the AMPK/mTOR signals do not collide).
    * Two endurance sessions: the harder first, then sport order Swim, Bike, Brick, Run.
    * A lift + easy cardio combo is one window (lift first by construction) and never re-ordered.
    """
    ws = list(workouts)
    if len(ws) < 2:
        return ws

    def hard(w):
        return bool(getattr(w, "is_hard_endurance", False))

    lifts = [w for w in ws if w.sport == "Strength"]
    rest = [w for w in ws if w.sport != "Strength"]
    rest.sort(key=lambda w: (not hard(w), _SPORT_ORDER.get(w.sport, 9), str(getattr(w, "code", ""))))
    if not lifts:
        return rest
    lifts.sort(key=lambda w: str(getattr(w, "code", "")))
    if any(hard(w) for w in rest):
        return rest + lifts
    return lifts + rest


def day_slots(ordered: Sequence[Any], min_gap_h: float, brick: bool = False) -> List[Dict[str, Any]]:
    """AM/PM slot labels for an ordered day. One window -> ``WINDOW``; two -> ``AM`` / ``PM`` with the
    required minimum gap. On a brick day the Bike and Run legs (back to back) share one window."""
    windows: List[List[Any]] = []
    for w in ordered:
        if brick and w.sport in ("Bike", "Run") and windows and windows[-1][-1].sport in ("Bike", "Run"):
            windows[-1].append(w)
        else:
            windows.append([w])
    out = []
    for k, grp in enumerate(windows):
        slot = "WINDOW" if len(windows) == 1 else ("AM" if k == 0 else "PM")
        for w in grp:
            e = {"slot": slot, "code": w.code, "sport": w.sport}
            if slot == "PM":
                e["min_gap_h"] = float(min_gap_h)
            out.append(e)
    return out
