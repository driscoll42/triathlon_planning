"""Calendar builder: day grid columns, uniqueness, daily caps, session model, rest/brick linking."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence

import numpy as np

from triplan.solver.engine.config import EngineOptions, WEEKDAYS, WEEKENDS, WeekConfig, WorkoutTable
from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF


# ----------------------------------------------------------------------------- unary gating

def make_allowed(table: WorkoutTable, cfg: WeekConfig, opt: EngineOptions) -> Callable[[int, int], bool]:
    """Unary Tier-0 gating ``allowed(i, d)`` for the selected ``allowed_rule`` (precomputed matrix)."""
    n = table.n
    rule = opt.allowed_rule
    if rule == "none" or n == 0:
        mat = np.ones((n, 7), dtype=bool)
    else:
        caps = np.asarray(cfg.daily_caps, dtype=np.float64)
        access = cfg.facility_access or {}
        mat = table.duration[:, None] <= caps[None, :] + 1e-9
        acc_masks = {sp: np.asarray(access.get(sp, [True] * 7), dtype=bool)
                     for sp in ("Swim", "Bike", "Run", "Strength", "Brick")}
        if rule == "meso":
            wd_idx = np.asarray(WEEKDAYS, dtype=np.int64)
            for i in range(n):
                sport = table.sport[i]
                if sport == "Brick":
                    sports = (["Bike", "Run"] if (table.brick_run_minutes[i] > 0 or not table.flags[i].get("pool", 0))
                              else ["Swim", "Bike"])
                else:
                    sports = [sport]
                if table.cardio_sport[i]:
                    sports = sports + [table.cardio_sport[i]]
                for sp in sports:
                    m_sp = acc_masks.get(sp)
                    if m_sp is None:
                        m_sp = np.asarray(access.get(sp, [True] * 7), dtype=bool)
                        acc_masks[sp] = m_sp
                    mat[i] &= m_sp
                if sport == "Bike" and not table.is_indoor_bike[i]:
                    mat[i, wd_idx] = False
        else:
            pool_days = set(cfg.get("pool_open_days", default=[0, 2, 4]))
            bike_days = set(cfg.get("outdoor_bike_days", default=[5, 6]))
            pool_mask = np.asarray([d in pool_days for d in range(7)], dtype=bool)
            bike_mask = np.asarray([d in bike_days for d in range(7)], dtype=bool)
            for i in range(n):
                flg = table.flags[i]
                if flg.get("pool", 0) == 1:
                    mat[i] &= pool_mask
                if flg.get("outdoor_bike", 0) == 1:
                    mat[i] &= bike_mask
                if access:
                    sport = table.sport[i]
                    sports = (sport, table.cardio_sport[i]) if table.cardio_sport[i] else (sport,)
                    for sp in sports:
                        m_sp = acc_masks.get(sp)
                        if m_sp is None:
                            m_sp = np.asarray(access.get(sp, [True] * 7), dtype=bool)
                            acc_masks[sp] = m_sp
                        mat[i] &= m_sp

    def allowed(i: int, d: int) -> bool:
        return bool(mat[i, d])

    allowed.matrix = mat  # type: ignore[attr-defined]
    return allowed


# ----------------------------------------------------------------------------- columns

def add_variables(ctx: BuildContext, costs: dict) -> None:
    """Create the decision/auxiliary columns for the selected session model, plus weekly indicators ``s``."""
    m, opt = ctx.model, ctx.opt
    n = ctx.n
    ctx.X = m.add_vars(n * 7, 0.0, 1.0, cost=0.0, integer=True).reshape(n, 7)
    ctx.s = m.add_vars(n, 0.0, 1.0, cost=costs["x"], integer=True)
    if opt.session_model == "single_per_day":
        return
    for s in ctx.sports:
        for d in ctx.days:
            ctx.y[(s, d)] = m.add_var(f"y_{s}_{d}", 0.0, 1.0, 0.0, True)
    if opt.session_model == "partition_brick":
        for d in ctx.days:
            ctx.double[d] = m.add_var(f"double_{d}", 0.0, 1.0, costs["double"], True)
            ctx.rest[d] = m.add_var(f"rest_{d}", 0.0, 1.0, 0.0, True)
            ctx.brick[d] = m.add_var(f"brick_{d}", 0.0, 1.0, 0.0, True)
        return
    # per_sport_cap (hybrid column order: y, u, w, r, b, z_split)
    for mu in ctx.muscles:
        for d in ctx.days:
            ctx.u[(mu, d)] = m.add_var(f"u_{mu}_{d}", 0.0, 1.0, 0.0, True)
    for d in ctx.days:
        ctx.double[d] = m.add_var(f"double_{d}", 0.0, 1.0, costs["double"], True)
    for d in ctx.days:
        ctx.rest[d] = m.add_var(f"rest_{d}", 0.0, 1.0, costs["rest"], True)
    for d in ctx.days:
        ctx.brick[d] = m.add_var(f"brick_{d}", 0.0, 1.0, costs["brick"], True)
    if opt.split_coherence:
        ctx.z_split = m.add_var("z_split", 0.0, 1.0, 0.0, True)


# ----------------------------------------------------------------------------- rows

def add_uniqueness(ctx: BuildContext) -> None:
    """Link weekly indicator ``s[i] = sum_d x[i, d]`` (enforces ``<= 1`` use per week since ``s[i] in {0, 1}``)."""
    ones_neg = np.array([1.0] * 7 + [-1.0], dtype=np.float64)
    for i in range(ctx.n):
        ctx.model.add_row((np.append(ctx.X[i], ctx.s[i]), ones_neg), 0.0, 0.0)


def add_fixed_days(
    ctx: BuildContext,
    fixed_days: Optional[Dict[int, Sequence[Any]]] = None,
    disallowed_hard_days: Optional[Sequence[int]] = None,
) -> None:
    """Fix completed days d < today and optionally block hard/heavy sessions on today (TASK-025)."""
    if fixed_days:
        code_to_idx: Dict[str, int] = {}
        for idx, item in enumerate(ctx.table.items):
            code_to_idx.setdefault(str(getattr(item, "code", "")), idx)
        for d, items in fixed_days.items():
            if d not in ctx.days:
                continue
            fixed_indices = set()
            for item in items:
                code = str(item) if isinstance(item, str) else str(getattr(item, "code", item))
                if code in code_to_idx:
                    fixed_indices.add(code_to_idx[code])
                else:
                    raise ValueError(f"Fixed workout {code!r} for day {d} not found in solver candidate pool")
            for i in range(ctx.n):
                val = 1.0 if i in fixed_indices else 0.0
                ctx.model.add_row({ctx.x(i, d): 1.0}, val, val)

    if disallowed_hard_days:
        for d in disallowed_hard_days:
            if d not in ctx.days:
                continue
            for i, item in enumerate(ctx.table.items):
                if ctx.table.is_hard_endurance[i] or bool(getattr(item, "is_heavy_strength", False)):
                    ctx.model.add_row({ctx.x(i, d): 1.0}, 0.0, 0.0)


def add_exact_count(ctx: BuildContext, count: int) -> None:
    ctx.model.add_row((ctx.X.ravel(), np.ones(ctx.X.size)), float(count), float(count))


def add_duration_caps(ctx: BuildContext, skip_zero_cap: bool = False) -> None:
    """Daily duration ceilings: ``sum_i dur_i x[i, d] <= cap_d``."""
    caps = ctx.cfg.daily_caps
    for d in ctx.days:
        if skip_zero_cap and caps[d] <= 0:
            continue
        ctx.model.add_row((ctx.X[:, d], ctx.table.duration), 0.0, float(caps[d]))


def add_day_counts(ctx: BuildContext) -> None:
    """Session-count limits per day for the non-partition session models."""
    m, tab = ctx.model, ctx.table
    caps = ctx.cfg.daily_caps
    ones = np.ones(ctx.n)
    if ctx.opt.session_model == "single_per_day":
        for d in ctx.days:
            m.add_row((ctx.X[:, d], ones), 0.0, 1.0 if caps[d] > 0 else 0.0)
        return
    # per_sport_cap: duration cap, <= 2 workouts/day, <= 1 workout per sport per day
    for d in ctx.days:
        m.add_row((ctx.X[:, d], tab.duration), 0.0, float(caps[d]))
        m.add_row((ctx.X[:, d], ones), 0.0, 2.0)
        for s in ctx.sports:
            ids = tab.ids(s)
            m.add_row((ctx.X[ids, d], np.ones(len(ids))), 0.0, 1.0)


def add_session_partition(ctx: BuildContext) -> None:
    """Scheduler session model: per-sport linking, ``sum x + rest - double - brick(weekend) == 1``
    (at most 2 sessions/day; a weekend brick may add a third leg), and brick linking."""
    m, tab = ctx.model, ctx.table
    ones = np.ones(ctx.n)
    for d in ctx.days:
        for s in ctx.sports:
            ids = tab.ids(s)
            cols = np.concatenate([ctx.X[ids, d], [ctx.y[(s, d)]]])
            vals = np.concatenate([np.ones(len(ids)), [-1.0]])
            m.add_row((cols, vals), 0.0, 0.0)

        cols = [ctx.X[:, d], [ctx.rest[d], ctx.double[d]]]
        vals = [ones, [1.0, -1.0]]
        if d in WEEKENDS:
            cols.append([ctx.brick[d]])
            vals.append([-1.0])
        m.add_row((np.concatenate(cols), np.concatenate(vals)), 1.0, 1.0)
        m.add_row({ctx.rest[d]: 1.0, ctx.double[d]: 1.0}, 0.0, 1.0)

        m.add_row({ctx.y[("Bike", d)]: 1.0, ctx.y[("Run", d)]: 1.0, ctx.brick[d]: -1.0}, -INF, 1.0)
        m.add_row({ctx.brick[d]: 1.0, ctx.y[("Bike", d)]: -1.0}, -INF, 0.0)
        m.add_row({ctx.brick[d]: 1.0, ctx.y[("Run", d)]: -1.0}, -INF, 0.0)


def add_session_links(ctx: BuildContext) -> None:
    """Hybrid session model linking: double/rest/sport/brick indicators and the exact rest day."""
    m, tab = ctx.model, ctx.table
    ones = np.ones(ctx.n)
    for d in ctx.days:
        m.add_row((np.append(ctx.X[:, d], ctx.double[d]), np.append(ones, -1.0)), -INF, 1.0)
        for i in range(ctx.n):
            m.add_row({ctx.rest[d]: 1.0, ctx.x(i, d): 1.0}, 0.0, 1.0)
        for s in ctx.sports:
            ids = tab.ids(s)
            for i in ids:
                m.add_row({ctx.x(i, d): 1.0, ctx.y[(s, d)]: -1.0}, -INF, 0.0)
            m.add_row((np.append(ctx.X[ids, d], ctx.y[(s, d)]), np.append(-np.ones(len(ids)), 1.0)), -INF, 0.0)
        m.add_row({ctx.brick[d]: 1.0, ctx.y[("Bike", d)]: -1.0}, -INF, 0.0)
        m.add_row({ctx.brick[d]: 1.0, ctx.y[("Run", d)]: -1.0}, -INF, 0.0)
    # rest day is exact: rest_d = 1 iff no workout on day d (rest_d + x_id <= 1 above; here >= 1)
    for d in ctx.days:
        m.add_row((np.append(ctx.X[:, d], ctx.rest[d]), np.ones(ctx.n + 1)), 1.0, INF)
