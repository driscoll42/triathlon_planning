"""Carry-in constants and in-model load dynamics (TASK-018, ADR-019).

Everything here is a linear row over the ``x[i, d]`` columns with the previous weeks folded into
constants (``engine.carry_in.WeekCarryIn``). Nothing is added when the carry-in is inactive, so the
isolated-week model is unchanged.

1. Boundary rows (Tier-0, same rules and workout sets as ``safety_constraints`` / ``orthopedic``):
   day -1 hard -> no hard day 0; day -1 heavy/long run -> no heavy/long run or deadlift on day 0
   (48h separation, HEUR-004 +-24h); day -1 deadlift -> no heavy/long run on day 0; day -1 strength ->
   no strength on day 0; rolling 48h TSS/ANS/strain over the window (-1, 0):
   ``sum_i v_i x[i,0] <= max(0, cap - v_{-1})``.
2. Running ACWR (Tier-0 ceiling, Tier-1 floor). With ``m_i`` the run mechanical strain, ``a = ALPHA_ACUTE``,
   ``c = ALPHA_CHRONIC`` and carried states ``A0, C0`` (after day -1), the EWMA recursion unrolls to
   ``A_d = (1-a)^(d+1) A0 + a sum_{k<=d} (1-a)^(d-k) sum_i m_i x[i,k]`` (same for ``C_d`` with ``c``), linear in
   ``x``. Ceiling per day: ``A_d - rho_max C_d <= eps`` (rhs clamped at 0 so an already-overshooting start state
   is not charged to the week: the plan merely may not worsen it). Floor per day:
   ``A_d - rho_min C_d + M_d s >= 0`` with one binary waiver ``s`` per week (a named Tier-1 slack, weight 1,
   the cheapest relaxation).
3. Weekly CTL ramp (Tier-0): ``CTL_7 - CTL_0 <= ramp`` with ``CTL_7 = (1-b)^7 CTL_0 + b sum_d (1-b)^(6-d) T_d``,
   ``T_d`` the all-sport TSS incl. strength (what ``AthletePhysiologicalState.total_ctl`` integrates, TASK-021).
4. Absolute run minutes (Tier-0, re-entry guard): ``sum run duration <= cap``.
"""

from __future__ import annotations

import numpy as np

from triplan.physiology.state import ALPHA_ACUTE, ALPHA_CHRONIC, ALPHA_CTL
from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF

ACWR_FLOOR_CATEGORY = "relax_acwr_floor"


def _ban(ctx: BuildContext, ids, d: int = 0) -> None:
    """Forbid every workout in ``ids`` on day ``d`` (row ``sum x <= 0``)."""
    ids = list(ids)
    if ids:
        ctx.model.add_row((ctx.X[ids, d], np.ones(len(ids))), 0.0, 0.0)


def add_carry_in(ctx: BuildContext) -> None:
    ci = ctx.carry_in
    if ci is None or not ci.is_active or not ctx.opt.safety:
        return
    _boundary_rows(ctx)
    if ci.load is not None:
        _acwr_rows(ctx)
        _ctl_ramp_row(ctx)
        _run_minutes_row(ctx)


def _boundary_rows(ctx: BuildContext) -> None:
    S, tab, p = ctx.sets, ctx.table, ctx.carry_in.day_m1
    if p.hard:
        _ban(ctx, S.hard)                                     # no two consecutive hard days
    if p.heavy_run:
        _ban(ctx, S.heavy_or_long_runs)                       # >= 48h between heavy/long runs
        _ban(ctx, S.deadlifts)                                # HEUR-004: run on d vs deadlift on d+1
    if p.deadlift:
        _ban(ctx, S.heavy_or_long_runs)                       # HEUR-004: deadlift on d vs run on d+1
    if p.strength:
        _ban(ctx, S.strength)                                 # non-consecutive strength days
    if ctx.opt.session_model == "partition_brick" and ctx.opt.meso_adjacency_rows:
        if p.heavy_strength:
            _ban(ctx, S.heavy_or_long_runs)                   # scheduler's +-24h heavy lift vs heavy/long run
        if p.heavy_run:
            _ban(ctx, S.heavy_strength)
    for vals, prev, cap in ((tab.aerobic_tss, p.aerobic_tss, ctx.limits.max_48h_tss),
                            (tab.ans_cost, p.ans_cost, ctx.limits.max_48h_ans),
                            (tab.mechanical_strain, p.mech_strain, ctx.limits.max_48h_mech)):
        if prev > 0 and np.any(vals > 0):
            ctx.model.add_row((ctx.X[:, 0], vals), 0.0, max(0.0, cap - prev))   # window (-1, 0)


def acwr_coefficients(d: int, a: float, c: float, rho: float):
    """``(k, f_k)``: coefficient of the day-``k`` run strain in ``A_d - rho * C_d`` (per unit strain)."""
    return [(k, a * (1 - a) ** (d - k) - rho * c * (1 - c) ** (d - k)) for k in range(d + 1)]


def _acwr_rows(ctx: BuildContext) -> None:
    ls, tab = ctx.carry_in.load, ctx.table
    runs = np.asarray(ctx.sets.runs, dtype=np.int64)
    if len(runs) == 0:
        return
    a, c = ALPHA_ACUTE, ALPHA_CHRONIC
    m = tab.run_strain[runs]          # run component only (a lift + easy-run combo counts its run leg)

    def row_for(d: int, rho: float):
        cols, vals = [], []
        for k, f in acwr_coefficients(d, a, c, rho):
            cols.append(ctx.X[runs, k])
            vals.append(f * m)
        const = (1 - a) ** (d + 1) * ls.run_acute - rho * (1 - c) ** (d + 1) * ls.run_chronic
        return np.concatenate(cols), np.concatenate(vals), const

    slack = _floor_slack(ctx)
    for d in ctx.days:
        cols, vals, const = row_for(d, ls.acwr_max)                      # Tier-0 ceiling
        ctx.model.add_row((cols, vals), -INF, max(ls.acwr_eps - const, 0.0), name=f"acwr_ceiling_d{d}")
        if slack is not None:                                            # Tier-1 floor (waivable)
            cols, vals, const = row_for(d, ls.acwr_min)
            big_m = max(0.0, -const - float(np.minimum(vals, 0.0).sum()))
            ctx.model.add_row((np.append(cols, slack), np.append(vals, big_m)), -const, INF,
                              name=f"acwr_floor_d{d}")


def _floor_slack(ctx: BuildContext):
    """One binary waiver per week for the undertraining floor, registered as the cheapest Tier-1 slack."""
    ls = ctx.carry_in.load
    if ctx.reg is None or ls.acwr_min <= 0:
        return None
    return ctx.reg.add_named(
        ACWR_FLOOR_CATEGORY, f"running ACWR >= {ls.acwr_min:.2f}", 1, 1.0,
        f"Running ACWR floor {ls.acwr_min:.2f} waived this week (deload / re-entry; undertraining is not an "
        f"injury risk).")


def _ctl_ramp_row(ctx: BuildContext) -> None:
    ls, tab = ctx.carry_in.load, ctx.table
    if ls.ctl_ramp_max is None:
        return
    b = ALPHA_CTL
    ids = np.flatnonzero(tab.mask("Bike", "Run", "Swim", "Brick", "Strength"))
    if len(ids) == 0:
        return
    cols = np.concatenate([ctx.X[ids, d] for d in ctx.days])
    vals = np.concatenate([b * (1 - b) ** (6 - d) * tab.aerobic_tss[ids] for d in ctx.days])
    rhs = ls.ctl_ramp_max + (1 - (1 - b) ** 7) * ls.total_ctl
    ctx.model.add_row((cols, vals), -INF, rhs, name="ctl_ramp")


def _run_minutes_row(ctx: BuildContext) -> None:
    ls = ctx.carry_in.load
    if ls.run_minutes_cap is None or not ctx.sets.runs:
        return
    cols, vals = ctx.per_week(ctx.table.run_minutes, ctx.sets.runs)
    ctx.model.add_row((cols, vals), -INF, float(ls.run_minutes_cap), name="run_minutes_cap")
