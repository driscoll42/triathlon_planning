"""Strength builder: split-architecture coherence, movement-pattern linking, barbell share."""

from __future__ import annotations

import numpy as np

from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF


def add_split_coherence(ctx: BuildContext) -> None:
    """Coherent strength split architecture: ``z_split = 1`` -> Upper/Lower (UL), ``0`` -> Full-Body HLM.
    A UL workout requires ``z_split = 1`` and an HLM workout requires ``z_split = 0``."""
    m, tab = ctx.model, ctx.table
    for i in range(ctx.n):
        grp = tab.split_group[i]
        if grp == "UL":
            if ctx.s is not None:
                m.add_row(([ctx.s[i], ctx.z_split], [1.0, -1.0]), -INF, 0.0)
            else:
                m.add_row((np.append(ctx.X[i], ctx.z_split), np.append(np.ones(7), -1.0)), -INF, 0.0)
        elif grp == "HLM":
            if ctx.s is not None:
                m.add_row(([ctx.s[i], ctx.z_split], [1.0, 1.0]), 0.0, 1.0)
            else:
                m.add_row((np.append(ctx.X[i], ctx.z_split), np.ones(8)), 0.0, 1.0)


def add_muscle_links(ctx: BuildContext) -> None:
    """``u[muscle, d] >= x[i, d]`` for every workout that trains the muscle pattern.

    Aggregated per ``(muscle, day)`` using the daily per-sport cap (1) when all contributing
    workouts share one sport, or the daily session cap (2) otherwise.
    """
    tab = ctx.table
    for mu in ctx.muscles:
        idx = np.flatnonzero(tab.muscle_sets(mu) > 0)
        if len(idx) == 0:
            continue
        big_m = 1.0 if len({str(tab.sport[i]) for i in idx}) <= 1 else 2.0
        ones = np.ones(len(idx))
        for d in ctx.days:
            cols = np.concatenate([ctx.X[idx, d], [ctx.u[(mu, d)]]])
            vals = np.concatenate([ones, [-big_m]])
            ctx.model.add_row((cols, vals), -INF, 0.0)


def add_barbell_share(ctx: BuildContext) -> None:
    """Barbell main lifts carry >= ``min_barbell_set_share`` of the week's strength sets."""
    tab = ctx.table
    beta = float(ctx.cfg.get("min_barbell_set_share", default=0.60))
    ids = tab.ids("Strength")
    if not ids:
        return
    coeff = tab.bb_sets - beta * (tab.bb_sets + tab.db_sets)
    cols, vals = ctx.per_week(coeff, ids)
    ctx.model.add_row((cols, vals), 0.0, INF, name="barbell_share")
