"""Goals builder: x/aux column costs and Tier-2 dimensionless soft goals.

Every goal is ``coeffs . x`` versus a target with deviation columns costed ``weight / scale``, via
``WeekModel.declare_soft_goal(s)`` / ``goal_row`` (columns are declared up front so the legacy column
order is preserved; ``WeekModel.add_soft_goal`` does both in one call).
"""

from __future__ import annotations

from typing import Dict

import numpy as np

from triplan.solver.engine.config import SPORTS, ZONES
from triplan.solver.engine.context import BuildContext

HYBRID_DEV_UB = 1e4  # deviation upper bound the hybrid engine shipped with


# ----------------------------------------------------------------------------- column costs

def cost_terms(ctx: BuildContext) -> Dict[str, object]:
    """Objective coefficients of the x columns and the double/rest/brick indicators."""
    tab, cfg, profile = ctx.table, ctx.cfg, ctx.opt.cost_profile
    zeros = np.zeros(ctx.n)
    if profile == "meso":
        max_durab = float(tab.durability.max()) if ctx.n else 1.0
        w_dur = float(cfg.get("w_durability", default=25.0))
        w_fric = float(cfg.get("w_friction", default=5.0))
        x = (-w_dur * (tab.durability / max(max_durab, 1e-9)) + w_fric * tab.preference_penalty
             + float(cfg.get("w_variety", default=4.0)) * _variety(ctx))
        return {"x": x, "double": float(cfg.get("w_double_session", default=0.5)), "rest": 0.0, "brick": 0.0}
    if profile == "hybrid":
        x = (cfg.weight("w_ans", 0.20) * tab.ans_cost - cfg.weight("w_durability", 0.35) * tab.durability
             + cfg.weight("w_variety", 4.0) * _variety(ctx))
        return {"x": x, "double": cfg.weight("w_double", 5.0), "rest": -cfg.weight("w_rest", 18.0),
                "brick": -cfg.weight("w_brick", 4.0)}
    return {"x": zeros, "double": 0.0, "rest": 0.0, "brick": 0.0}


def _variety(ctx: BuildContext) -> np.ndarray:
    """Per-workout recency weight of the multi-week variety penalty (``variety.py``); zeros without carry-in memory."""
    return ctx.variety if ctx.variety is not None else np.zeros(ctx.n)


def _tte_reference(ctx: BuildContext):
    """``(mode, reference minutes, target minutes)`` of the week's TTE goal, or ``None`` when it is off (TASK-023).

    Build weeks (``progress``) aim at ``T^thr_w >= (1 + g) T^thr_ref`` with ``T_ref`` the last non-deload solved week;
    a recovery week (``deload``) caps ``T^thr_w <= ratio * T^thr_{w-1}``. No carry-in memory (an isolated week, the first
    week, or a reference of 0 threshold minutes) means no goal."""
    cfg, ci = ctx.cfg, ctx.carry_in
    mode = str(cfg.get("tte_mode", default="off"))
    if ci is None or mode == "off":
        return None
    if mode == "progress":
        ref = float(ci.tte_ref_min)
        target = (1.0 + float(cfg.get("tte_growth", default=0.05))) * ref
    elif mode == "deload":
        ref = float(ci.prev_tte_min)
        target = float(cfg.get("tte_deload_ratio", default=0.60)) * ref
    else:
        raise ValueError(f"unknown tte_mode {mode!r}")
    return (mode, ref, target) if ref > 0 else None


# ----------------------------------------------------------------------------- declare

def declare_goals(ctx: BuildContext) -> None:
    """Create the deviation columns of the selected goal profile (before any rows, legacy order)."""
    m, cfg, profile = ctx.model, ctx.cfg, ctx.opt.goal_profile
    G = ctx.goals
    if profile == "meso":
        target = float(cfg.get("target_weekly_tss", default=540.0))
        nominal = float(cfg.get("nominal_weekly_duration", default=640.0))
        G["tss"] = m.declare_soft_goal("tss", max(target, 1.0), float(cfg.get("w_tss", default=100.0)))
        zs = m.declare_soft_goals([f"zone{z}" for z in ZONES], [max(nominal, 1.0)] * 5,
                                  float(cfg.get("w_zone", default=80.0)))
        G["zone"] = dict(zip(ZONES, zs))
        ss = m.declare_soft_goals([f"sdur_{s}" for s in ctx.sports], [max(nominal, 1.0)] * len(ctx.sports),
                                  float(cfg.get("w_sport_dur", default=50.0)))
        G["sdur"] = dict(zip(ctx.sports, ss))
        bs = m.declare_soft_goals([f"stss_{s}" for s in ("Bike", "Run")], [max(target, 1.0)] * 2,
                                  float(cfg.get("w_sport_tss", default=45.0)))
        G["stss"] = dict(zip(("Bike", "Run"), bs))
        tte = _tte_reference(ctx)
        if tte is not None:
            mode, ref, target = tte
            w_tte = float(cfg.get("w_tte", default=30.0))
            # one-sided: progress penalizes only the SHORTFALL (neg), deload only the OVERSHOOT (pos)
            G["tte"] = m.declare_soft_goal("tte", max(ref, 1.0), w_tte, pos_weight=0.0 if mode == "progress" else w_tte,
                                           neg_weight=w_tte if mode == "progress" else 0.0)
            G["tte_target"] = target
    elif profile == "hybrid":
        t_tss = cfg.get("target_tss", "target_sport_tss", default={"Swim": 100.0, "Bike": 260.0, "Run": 198.0,
                                                                  "Strength": 52.0})
        t_set = cfg.get("target_sets", "target_movement_sets", default={
            "Anterior_Chain": 10.0, "Posterior_Chain": 10.0, "Push": 10.0, "Pull": 10.0})
        G["t_tss"], G["t_sets"] = t_tss, t_set
        sc = [max(1.0, float(t_tss.get(s, 100.0))) for s in ctx.sports]
        gs = m.declare_soft_goals([f"tss_{s}" for s in ctx.sports], sc, 1.0,
                                  pos_weight=cfg.weight("w_tss_pos", 100.0), neg_weight=cfg.weight("w_tss_neg", 150.0),
                                  ub=HYBRID_DEV_UB, layout="blocked")
        G["tss_sport"] = dict(zip(ctx.sports, gs))
        sc = [max(1.0, float(t_set.get(mu, 10.0))) for mu in ctx.muscles]
        gm = m.declare_soft_goals([f"sets_{mu}" for mu in ctx.muscles], sc, 1.0,
                                  pos_weight=cfg.weight("w_sets_pos", 100.0), neg_weight=cfg.weight("w_sets_neg", 150.0),
                                  ub=HYBRID_DEV_UB, layout="blocked")
        G["sets"] = dict(zip(ctx.muscles, gm))
        if ctx.opt.tiz_band:
            G["tiz_low"] = m.add_var("tiz_low_neg", 0.0, HYBRID_DEV_UB, cfg.weight("w_tiz", 1.0))
            G["tiz_high"] = m.add_var("tiz_high_pos", 0.0, HYBRID_DEV_UB, cfg.weight("w_tiz", 1.0))
    elif profile == "legacy":
        spec, tab = ctx.opt.legacy, ctx.table
        est = _legacy_est_total(ctx)
        bike_scale, run_scale = est * spec.bike_dur_ratio, est * spec.run_dur_ratio
        has_swim = (tab.sport_arr == "Swim").any() and spec.swim_dur_ratio > 0.05
        plan = [("tss", spec.target_tss), ("bike_dur_split", est), ("run_dur_split", est)]
        if has_swim:
            plan.append(("swim_dur_split", est))
        plan += [("overall_high", est), ("bike_high", bike_scale), ("run_high", run_scale),
                 ("bike_z3", bike_scale), ("bike_z4", bike_scale), ("bike_z5", bike_scale),
                 ("run_z3", run_scale), ("run_z4", run_scale), ("run_z5", run_scale)]
        for name, scale in plan:
            G[name] = m.declare_soft_goal(name, scale, 1.0)
    else:
        raise ValueError(f"unknown goal_profile {profile!r}")


def _legacy_est_total(ctx: BuildContext) -> float:
    spec, tab = ctx.opt.legacy, ctx.table
    return float(spec.target_tss * (tab.duration.sum() / tab.aerobic_tss.sum()))


# ----------------------------------------------------------------------------- rows

def add_goal_rows(ctx: BuildContext) -> None:
    {"meso": _meso_rows, "hybrid": _hybrid_rows, "legacy": _legacy_rows}[ctx.opt.goal_profile](ctx)


def _meso_rows(ctx: BuildContext) -> None:
    tab, cfg, m, G = ctx.table, ctx.cfg, ctx.model, ctx.goals
    target = float(cfg.get("target_weekly_tss", default=540.0))
    m.goal_row(G["tss"], ctx.per_week(tab.aerobic_tss), target)
    if "tte" in G:
        m.goal_row(G["tte"], ctx.per_week(tab.t_thr), float(G["tte_target"]))

    zpcts = cfg.get("target_zone_pcts", default={})
    for z in ZONES:
        p_z = zpcts.get(z, 0.0)
        m.goal_row(G["zone"][z], ctx.per_week(tab.zone[:, z - 1] - p_z * tab.duration), 0.0)

    is_br_brick = (tab.sport_arr == "Brick") & (tab.brick_run_minutes > 0)
    splits = cfg.get("target_sport_duration_splits", default={})
    for s in ctx.sports:
        r_s = splits.get(s, 0.0)
        if s == "Run":
            sport_dur = np.where(tab.sport_arr == s, tab.duration,
                                 np.where(is_br_brick, tab.brick_run_minutes, 0.0))
        elif s == "Bike":
            sport_dur = np.where(tab.sport_arr == s, tab.duration,
                                 np.where(is_br_brick, np.maximum(0.0, tab.duration - tab.brick_run_minutes), 0.0))
        else:
            sport_dur = np.where(tab.sport_arr == s, tab.duration, 0.0)
        m.goal_row(G["sdur"][s], ctx.per_week(sport_dur - r_s * tab.duration), 0.0)

    br = cfg.get("target_bike_run_tss_splits", default={})
    br_ids = np.flatnonzero(tab.mask("Bike", "Run") | is_br_brick)
    run_frac = np.where(is_br_brick, tab.brick_run_minutes / np.maximum(tab.duration, 1e-9), 0.0)
    for s in ("Bike", "Run"):
        r_tss = br.get(s, 0.5)
        if s == "Run":
            sport_tss = np.where(tab.sport_arr == s, tab.aerobic_tss,
                                 np.where(is_br_brick, tab.aerobic_tss * run_frac, 0.0))
        else:
            sport_tss = np.where(tab.sport_arr == s, tab.aerobic_tss,
                                 np.where(is_br_brick, tab.aerobic_tss * (1.0 - run_frac), 0.0))
        m.goal_row(G["stss"][s], ctx.per_week(sport_tss - r_tss * tab.aerobic_tss, br_ids), 0.0)


def _hybrid_rows(ctx: BuildContext) -> None:
    tab, m, G = ctx.table, ctx.model, ctx.goals
    for s in ctx.sports:
        m.goal_row(G["tss_sport"][s], ctx.per_week(tab.aerobic_tss, tab.ids(s)), float(G["t_tss"].get(s, 100.0)))
    for mu in ctx.muscles:
        sets = tab.muscle_sets(mu)
        m.goal_row(G["sets"][mu], ctx.per_week(sets, np.flatnonzero(sets > 0)), float(G["t_sets"].get(mu, 10.0)))


def _legacy_rows(ctx: BuildContext) -> None:
    spec, tab, m, G = ctx.opt.legacy, ctx.table, ctx.model, ctx.goals
    is_bike = (tab.sport_arr == "Bike").astype(float)
    is_run = (tab.sport_arr == "Run").astype(float)
    is_swim = (tab.sport_arr == "Swim").astype(float)
    is_brick = (tab.sport_arr == "Brick").astype(float)
    dur = tab.duration
    z3, z4, z5 = tab.zone[:, 2], tab.zone[:, 3], tab.zone[:, 4]
    high = z3 + z4 + z5
    hr = spec.high_ratio_per_sport
    pw = ctx.per_week

    m.goal_row(G["tss"], pw(tab.aerobic_tss), spec.target_tss)
    m.goal_row(G["bike_dur_split"], pw((is_bike + is_brick - spec.bike_dur_ratio) * dur), 0.0)
    m.goal_row(G["run_dur_split"], pw((is_run - spec.run_dur_ratio) * dur), 0.0)
    if "swim_dur_split" in G:
        m.goal_row(G["swim_dur_split"], pw((is_swim - spec.swim_dur_ratio) * dur), 0.0)
    m.goal_row(G["overall_high"], pw(high - hr * dur), 0.0)
    m.goal_row(G["bike_high"], pw(is_bike * (high - hr * dur)), 0.0)
    m.goal_row(G["run_high"], pw(is_run * (high - hr * dur)), 0.0)
    tg = {"z3": spec.z3_high_ratio * hr, "z4": spec.z4_high_ratio * hr, "z5": spec.z5_high_ratio * hr}
    zs = {"z3": z3, "z4": z4, "z5": z5}
    for z in ("z3", "z4", "z5"):
        m.goal_row(G[f"bike_{z}"], pw(is_bike * (zs[z] - tg[z] * dur)), 0.0)
    for z in ("z3", "z4", "z5"):
        m.goal_row(G[f"run_{z}"], pw(is_run * (zs[z] - tg[z] * dur)), 0.0)
