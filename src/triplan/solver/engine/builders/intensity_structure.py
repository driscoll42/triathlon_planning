"""Intensity-structure builder: time-in-zone band, hard/easy adjacency, weekly count and TSS guardrails.

TASK-019 (session structure, 80/20 TiZ, key-session counts) will extend this module; today it holds the
two rule sets the legacy engines already had:

* the hybrid TiZ band (``Z1+Z2 >= p_low_min`` and ``Z4+Z5 <= p_high_max`` with soft slacks), and
* the legacy day scheduler's structure (exact workout count, hard/easy and long-run adjacency, sport
  count bounds, weekly TSS guardrails and the weekly high-intensity ceiling).
"""

from __future__ import annotations

import numpy as np

from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF


def add_tiz_band(ctx: BuildContext) -> None:
    """Polarized/pyramidal band over endurance sessions. The two slacks are minutes-valued with unit
    scale, exactly as the hybrid engine shipped them (recalibration is EXP-004)."""
    tab, m = ctx.table, ctx.model
    endur = tab.mask("Swim", "Bike", "Run")
    p_low = float(ctx.cfg.get("p_low_min", default=0.78))
    p_high = float(ctx.cfg.get("p_high_max", default=0.20))
    ids = np.flatnonzero(endur)
    z12 = tab.zone[:, 0] + tab.zone[:, 1]
    z45 = tab.zone[:, 3] + tab.zone[:, 4]
    tot = tab.zone.sum(axis=1)
    cols, v_low = ctx.per_week(z12 - p_low * tot, ids)
    _, v_hi = ctx.per_week(z45 - p_high * tot, ids)
    low, hi = ctx.goals["tiz_low"], ctx.goals["tiz_high"]
    m.add_row((np.append(cols, low), np.append(v_low, 1.0)), 0.0, INF, name="tiz_low")
    m.add_row((np.append(cols, hi), np.append(v_hi, -1.0)), -INF, 0.0, name="tiz_high")


def add_legacy_structure(ctx: BuildContext) -> None:
    """Rules of the legacy day scheduler (``optimize_weekly_schedule``)."""
    spec, tab, m = ctx.opt.legacy, ctx.table, ctx.model
    n_wk = spec.num_workouts
    is_bike = (tab.sport_arr == "Bike").astype(float)
    is_run = (tab.sport_arr == "Run").astype(float)
    is_swim = (tab.sport_arr == "Swim").astype(float)
    is_brick = (tab.sport_arr == "Brick").astype(float)
    high = tab.zone[:, 2] + tab.zone[:, 3] + tab.zone[:, 4]

    # Hard/Easy Rule: no consecutive hard days on Bike, Run, Brick (Swim is an active-recovery exception)
    if spec.forbid_consecutive_hard:
        hard = spec.hard_bike_run.astype(float)
        for d in range(6):
            cols = np.concatenate([ctx.X[:, d], ctx.X[:, d + 1]])
            m.add_row((cols, np.concatenate([hard, hard])), -INF, 1.0)
    # Injury prevention: no consecutive long runs
    if spec.forbid_consecutive_long_runs:
        lr = spec.long_run.astype(float)
        for d in range(6):
            cols = np.concatenate([ctx.X[:, d], ctx.X[:, d + 1]])
            m.add_row((cols, np.concatenate([lr, lr])), -INF, 1.0)

    # Tightened sport count bounds (swim-aware; bikes include bricks)
    bike_brick = is_bike + is_brick
    has_swim = is_swim.sum() > 0 and spec.swim_dur_ratio > 0.05
    if has_swim:
        exp_s = int(round(n_wk * spec.swim_dur_ratio))
        lo_s, hi_s = max(1, exp_s - 1), min(n_wk - 2, exp_s + 1)
        exp_b = int(round(n_wk * spec.bike_dur_ratio))
        lo_b, hi_b = max(1, exp_b - 1), min(n_wk - 2, exp_b + 1)
        exp_r = int(round(n_wk * spec.run_dur_ratio))
        lo_r, hi_r = max(1, exp_r - 1), min(n_wk - 2, exp_r + 1)
        _count_bounds(ctx, is_swim, lo_s, hi_s)
        _count_bounds(ctx, bike_brick, lo_b, hi_b)
        _count_bounds(ctx, is_run, lo_r, hi_r)
    else:
        exp_b = int(round(n_wk * spec.bike_dur_ratio))
        lo_b, hi_b = max(1, exp_b - 1), min(n_wk - 1, exp_b + 1)
        lo_r, hi_r = max(1, n_wk - hi_b), min(n_wk - 1, n_wk - lo_b)
        _count_bounds(ctx, bike_brick, lo_b, hi_b)
        _count_bounds(ctx, is_run, lo_r, hi_r)

    # Weekly TSS guardrails and the weekly high-intensity ceiling
    cols, tss = ctx.per_week(tab.aerobic_tss)
    m.add_row((cols, tss), spec.target_tss * (1.0 - spec.tss_tolerance), spec.target_tss * (1.0 + spec.tss_tolerance))
    _, hv = ctx.per_week(high - spec.max_high_ratio * tab.duration)
    m.add_row((cols, hv), -INF, 0.0)


def _count_bounds(ctx: BuildContext, indicator: np.ndarray, lo: int, hi: int) -> None:
    cols, vals = ctx.per_week(indicator)
    ctx.model.add_row((cols, vals), float(lo), INF)
    ctx.model.add_row((cols, vals), -INF, float(hi))
