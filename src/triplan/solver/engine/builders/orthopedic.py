"""Orthopedic builder: the shared Tier-0 safety layer and Tier-1 named slacks (delegated).

The rules themselves live in ``triplan.solver.safety_constraints`` (ADR-013, ADR-008, HEUR-004). This
module only wires that layer to the ``WeekModel`` buffer so both facades share one implementation.
"""

from __future__ import annotations

import numpy as np

from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF
from triplan.solver.safety_constraints import Tier1Registry, add_tier0_safety, add_tier1_targets


def make_hooks(ctx: BuildContext) -> None:
    ctx.hooks = ctx.model.hooks(ctx.x, ctx.allowed)


def add_meso_adjacency(ctx: BuildContext) -> None:
    """Scheduler-specific Tier-0 rows (a superset of the shared layer for these cases): at most one
    hard endurance session per day, heavy strength never same-day with a heavy/long run, and a +-24h
    buffer between ANY heavy strength and threshold/long runs."""
    m, S, tab = ctx.model, ctx.sets, ctx.table
    hard_end = np.flatnonzero(tab.is_hard_endurance)
    heavy = S.heavy_strength + S.heavy_or_long_runs
    for d in ctx.days:
        m.add_row((ctx.X[hard_end, d], np.ones(len(hard_end))), 0.0, 1.0)
        m.add_row((ctx.X[heavy, d], np.ones(len(heavy))), 0.0, 1.0)
    for d in range(6):
        r = {ctx.x(i, d): 1.0 for i in S.heavy_strength}
        r.update({ctx.x(j, d + 1): 1.0 for j in S.heavy_or_long_runs})
        m.add_row(r, 0.0, 1.0)
        r = {ctx.x(i, d): 1.0 for i in S.heavy_or_long_runs}
        r.update({ctx.x(j, d + 1): 1.0 for j in S.heavy_strength})
        m.add_row(r, 0.0, 1.0)


def add_safety(ctx: BuildContext) -> None:
    """Gating, daily/weekly axial and neuromuscular caps, rolling 48h TSS/ANS/strain, hard/easy
    adjacency, 48h heavy-run separation, deadlift +-24h lockout, antagonism matrix, single-run share."""
    add_tier0_safety(ctx.hooks, ctx.table.items, ctx.limits, ctx.sets)


def add_weekly_run_strain_cap(ctx: BuildContext) -> None:
    """Weekly running mechanical strain ceiling (ACWR & ODI guardrail)."""
    cols, vals = ctx.per_week(ctx.table.run_strain, ctx.sets.runs)  # run component only (combos: cardio part)
    ctx.model.add_row((cols, vals), 0.0, float(ctx.cfg.get("max_weekly_run_mech_strain", default=350.0)))


def add_tier1(ctx: BuildContext) -> None:
    """Anchors, minimum sport frequencies, strides, heavy-strength count, rest days: named slacks."""
    ctx.reg = Tier1Registry(ctx.hooks, ctx.cfg.get("tier1_relaxation_order", default=None))
    add_tier1_targets(
        ctx.reg, ctx.table.items, ctx.cfg.raw, ctx.sets,
        x=ctx.x,
        sport_day=lambda s, d: ctx.y.get((s, d)),
        rest_day=lambda d: ctx.rest[d],
        rest_bounds_default=ctx.opt.rest_bounds_default,
        extra_members=ctx.table.extra_members,
    )
