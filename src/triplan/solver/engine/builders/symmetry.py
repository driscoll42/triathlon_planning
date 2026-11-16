"""Symmetry breaking for interchangeable calendar structure (TASK-020, ADR-018).

Which day symmetries exist?  Arbitrary day permutations are NOT symmetries of the week model: the
rolling 48h windows, hard/easy adjacency and +-24h lockouts all couple consecutive days, so swapping
two non-mirror days changes feasibility. The one non-trivial automorphism of a path of 7 days is the
reversal ``d -> 6 - d``. Every adjacency row in the shared safety layer, the meso adjacency rows and
the legacy-free hybrid rows is symmetric under it (each ``(d, d+1)`` row has a matching mirrored
row), and no cost or goal depends on the day. So when the *day-indexed inputs* are palindromic, a
plan and its mirror are both feasible and equally good, and we may keep only one of each pair.

We test the inputs directly (never assume): daily caps, readiness alpha, the unary gating
``allowed(i, d) == allowed(i, 6 - d)`` for every workout (covers facility access, pool days,
outdoor-bike days), no weekday-only single-workout cap, no weekend-only brick (partition model), no
legacy day rules. If any check fails the week is simply asymmetric and nothing is added.

The ordering row is ``sum_d (d - 3) * sum_i dur_i x[i, d] <= 0``: mirroring a plan negates the
left-hand side, so every plan or its mirror satisfies it (both when it equals 0).
"""

from __future__ import annotations

import numpy as np

from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF


def is_week_reversal_symmetric(ctx: BuildContext) -> bool:
    opt = ctx.opt
    if opt.legacy is not None or opt.session_model == "partition_brick":
        return False
    if ctx.limits.weekday_single_workout_cap is not None:
        return False
    caps = [float(c) for c in ctx.cfg.daily_caps]
    if any(abs(caps[d] - caps[6 - d]) > 1e-9 for d in range(3)):
        return False
    # TASK-019: window gating and Hard-Days-Hard are day-asymmetric when the two-window pattern is not a
    # palindrome (a day-specific rule). With the default (all days True) they are symmetric.
    if (ctx.opt.window_gating or ctx.opt.hard_days_hard) and any(
            ctx.cfg.two_windows[d] != ctx.cfg.two_windows[6 - d] for d in range(3)):
        return False
    alpha = [float(a) for a in ctx.limits.readiness_alpha]
    if len(alpha) == 7 and any(abs(alpha[d] - alpha[6 - d]) > 1e-9 for d in range(3)):
        return False
    return all(bool(ctx.allowed(i, d)) == bool(ctx.allowed(i, 6 - d)) for i in range(ctx.n) for d in range(3))


def add_reversal_symmetry(ctx: BuildContext) -> bool:
    """Add the week-reversal ordering row when it is sound. Returns whether a row was added."""
    if not is_week_reversal_symmetric(ctx) or ctx.n == 0:
        return False
    cols, vals = [], []
    for d in ctx.days:
        if d == 3:
            continue
        cols.append(ctx.X[:, d])
        vals.append((d - 3.0) * ctx.table.duration)
    ctx.model.add_row((np.concatenate(cols), np.concatenate(vals)), -INF, 0.0)
    return True
