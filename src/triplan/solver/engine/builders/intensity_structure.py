"""Intensity-structure builder: time-in-zone band, hard/easy adjacency, weekly count and TSS guardrails.

TASK-019 added the canonical 80/20 rule (``add_canonical_8020``) for both facades; session structure
(key sessions, Hard-Days-Hard, windows) is in ``builders.session_structure``. This module also holds the
two rule sets the legacy engines already had:

* the superseded hybrid TiZ band (``add_tiz_band``, soft minute-scale slacks; the old ``Z4+Z5`` ceiling
  ignored Z3 and ZY, which is the defect ``canonical_8020`` fixes), and
* the legacy day scheduler's structure (exact workout count, hard/easy and long-run adjacency, sport
  count bounds, weekly TSS guardrails and the weekly high-intensity ceiling).
"""

from __future__ import annotations

import numpy as np

from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF


def add_canonical_8020(ctx: BuildContext) -> None:
    """Canonical 80/20 time-in-zone over the week's ENDURANCE minutes (ADR-012, DECISION-013).

    With ``T = sum_i x_i * endurance_minutes_i`` (strength excluded; the cardio part of a lift + easy
    cardio combo included), ``Low = Z1 + Z2``, ``High = Z3 + ZY + Z4 + Z5`` and ``ZX`` the grey zone:

    * hard:  ``Low >= p_low_min * T``   (default 0.78)
    * hard:  ``High <= p_high_max * T`` (default 0.20)
    * soft:  ``Low >= p_low_target * T`` (default 0.80), shortfall penalised (``w_8020_low``)
    * soft:  ``ZX`` penalised toward 0 (``w_zone_x``); omitted when no workout has ZX minutes.

    Both facades apply it; the meso 5-zone ``target_zone_pcts`` goal stays as a Tier-2 goal. Both ratio
    rows are exact linearizations (``sum x (zone - p * total) {>=,<=} 0``), no big-M.
    """
    tab, m, cfg = ctx.table, ctx.model, ctx.cfg
    p_low = float(cfg.get("p_low_min", "intensity_low_min", default=0.78))
    p_high = float(cfg.get("p_high_max", "intensity_mod_high_max", default=0.20))
    p_tgt = float(cfg.get("intensity_low_target", default=0.80))
    z = tab.endur_zone7                     # (N, 7): Z1, Z2, ZX, Z3, ZY, Z4, Z5
    ids = np.flatnonzero(z.sum(axis=1) > 0)
    if len(ids) == 0:
        return
    tot = z.sum(axis=1)
    low = z[:, 0] + z[:, 1]
    high = z[:, 3] + z[:, 4] + z[:, 5] + z[:, 6]
    zx = z[:, 2]
    cols, v_low = ctx.per_week(low - p_low * tot, ids)
    m.add_row((cols, v_low), 0.0, INF, name="8020_low_min")
    _, v_hi = ctx.per_week(high - p_high * tot, ids)
    m.add_row((cols, v_hi), -INF, 0.0, name="8020_high_max")
    scale = max(1.0, float(cfg.get("nominal_weekly_duration", default=480.0)))
    _, v_tgt = ctx.per_week(low - p_tgt * tot, ids)
    m.add_soft_goal((cols, v_tgt), 0.0, scale, float(cfg.get("w_8020_low", default=80.0)), "8020_low_target",
                    pos_weight=0.0)       # only a shortfall below the 80% target costs
    if zx[ids].sum() > 0:
        _, v_zx = ctx.per_week(zx, ids)
        m.add_soft_goal((cols, v_zx), 0.0, scale, float(cfg.get("w_zone_x", default=80.0)), "8020_zone_x",
                        neg_weight=0.0)   # ZX minutes >= 0, so only an excess can occur


def add_tiz_band(ctx: BuildContext) -> None:
    """Superseded by :func:`add_canonical_8020` (kept as an option for comparisons). Band over endurance
    sessions with minute-scale soft slacks (recalibration was EXP-004); the ceiling now counts
    Z3 + ZY + Z4 + Z5 instead of only Z4 + Z5."""
    tab, m = ctx.table, ctx.model
    endur = tab.mask("Swim", "Bike", "Run")
    p_low = float(ctx.cfg.get("p_low_min", default=0.78))
    p_high = float(ctx.cfg.get("p_high_max", default=0.20))
    ids = np.flatnonzero(endur)
    z12 = tab.zone[:, 0] + tab.zone[:, 1]
    z45 = tab.zone[:, 2] + tab.zone[:, 3] + tab.zone[:, 4]   # 5-zone view folds ZX into Z3 and ZY into Z4
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
