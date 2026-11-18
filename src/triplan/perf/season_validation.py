"""Multi-distance season validation (TASK-027).

``check_season`` independently re-computes every Tier-0 quantity from the solved schedules (it never reads the
solver's own report): rolling 48h TSS / ANS / mechanical strain (incl. the Sunday -> Monday boundary), daily and
weekly axial / neuromuscular load, single-run share, deadlift <-> heavy-run lockout (+-24h), no two hard days in a
row, one strength session a day, daily time caps, canonical 80/20 hard rows, and the carried running-ACWR /
CTL-ramp / run-minutes rows. ``run_matrix`` runs sprint / olympic / 70.3 / Ironman on CURRENT_ATHLETE and an
unconstrained reference profile in parallel processes and renders the summary table + markdown report.

CLI:  python -m triplan.perf.season_validation [--distances sprint olympic ...] [--profiles current reference]
                                               [--report docs/experiments/EXP-007_multi_distance_validation.md]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from triplan.control.periodization import DEFAULT_RACE_WEEKS, normalize_distance
from triplan.control.season import WeekRecord, generate_closed_loop_season_schedule
from triplan.physiology.state import ALPHA_ACUTE, ALPHA_CHRONIC, ALPHA_CTL
from triplan.solver.engine.session_quality import endurance_zone7
from triplan.solver.safety_constraints import classify_workouts, run_minutes, run_strain

DISTANCES = ("sprint", "olympic", "70.3", "ironman")
OK_STATUSES = ("OPTIMAL", "TIME_LIMIT_FEASIBLE")
TOL = 1e-6


@dataclass
class Violation:
    week: int
    rule: str
    message: str
    value: float = 0.0
    limit: float = 0.0

    def __str__(self) -> str:
        return f"week {self.week} [{self.rule}] {self.message}"


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
def reference_profile(base: Optional[Any] = None) -> Any:
    """Unconstrained reference athlete: realistic long-course day caps (up to 5 h Sat / 4 h Sun), two session
    windows every day, pool open every day, no re-entry block, trained starting fitness. Tier-0 safety ceilings
    (axial / neuro / run share / 48h) are NOT relaxed: only the time budget and starting state differ."""
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE
    base = base or CURRENT_ATHLETE
    return replace(
        base,
        name="reference_unconstrained",
        daily_time_caps=[90.0, 120.0, 90.0, 120.0, 90.0, 300.0, 240.0],
        weekday_single_workout_cap=120.0,
        two_session_windows=[True] * 7,
        facility_access={"Swim": [True] * 7, "Bike": [True] * 7, "Run": [True] * 7, "Strength": [True] * 7},
        reentry_weeks=0, reentry_phase=False,
        starting_ctl=45.0, starting_atl=40.0, starting_run_ctl=22.0, starting_run_atl=20.0,
        starting_longest_run_min=90.0,   # TASK-049: trained runner, not in re-entry (reentry_phase=False is honoured by carry)
        initial_metabolic_state=45.0, initial_connective_tissue_state=45.0,
        initial_running_acute_mech=35.0, initial_running_chronic_mech=35.0,
        base_weekly_tss=300.0, peak_weekly_tss=560.0,
        max_48h_ans_cost=700.0, max_weekly_run_mech_strain=250.0,
        sport_freq_bounds={"Swim": (2, 3), "Bike": (3, 4), "Run": (4, 5), "Strength": (2, 3)},
        target_sport_splits={"Swim": 0.15, "Bike": 0.45, "Run": 0.30, "Strength": 0.10},
        reentry_heavy_strength_sessions=2,
    )


# Reference base weekly TSS per distance (peak = base x the matrix peak factor ~1.3-1.6): a realistic volume ladder.
REFERENCE_BASE_TSS = {"sprint": 250.0, "olympic": 330.0, "70.3": 400.0, "ironman": 450.0}


def build_profile(name: str, distance: str) -> Any:
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE
    if name == "current":
        return replace(CURRENT_ATHLETE, target_race_distance=distance)
    base = reference_profile()
    return replace(base, target_race_distance=distance, base_weekly_tss=REFERENCE_BASE_TSS[normalize_distance(distance)])


# ---------------------------------------------------------------------------
# Independent checker
# ---------------------------------------------------------------------------
def _day(rec: WeekRecord, d: int) -> List[Any]:
    return rec.schedule.get(d, [])


def _sum(ws: Sequence[Any], attr: str) -> float:
    return float(sum(float(getattr(w, attr, 0.0) or 0.0) for w in ws))


def independent_long_run_cap(rec: WeekRecord, prev: Optional[WeekRecord]) -> Optional[float]:
    """The ADR-022 absolute long-run cap for ``rec`` recomputed from first principles: progression cap
    ``max(floor, prev longest run + step)`` in a season, re-entry ceiling while ``carry.load.reentry``."""
    lim, carry = rec.limits, rec.carry
    if lim is None:
        return None
    load = getattr(carry, "load", None)
    caps: List[float] = []
    step, floor, ceil = lim.long_run_step_min, lim.long_run_floor_min, lim.reentry_long_run_max_min
    if step is not None and load is not None:
        if prev is not None and prev.solved:
            prev_runs = [run_minutes(w) for d in range(7) for w in _day(prev, d) if run_minutes(w) > 0]
        else:
            prev_runs = []
        prev_longest = max(prev_runs) if prev_runs else float(getattr(carry, "prev_longest_run_min", 0.0))
        caps.append(max(floor, prev_longest + step))
    if ceil is not None and (load is None or getattr(load, "reentry", False)):
        caps.append(max(floor, ceil))
    return min(caps) if caps else None


def check_week(rec: WeekRecord, prev: Optional[WeekRecord] = None) -> List[Violation]:
    v: List[Violation] = []
    n = rec.week_num
    lim, cfg = rec.limits, rec.cfg

    def add(rule: str, msg: str, val: float = 0.0, cap: float = 0.0) -> None:
        v.append(Violation(n, rule, msg, val, cap))

    if not rec.solved or rec.status not in OK_STATUSES:
        add("fallback", f"week did not solve (status {rec.status}); diagnostics: {'; '.join(rec.diagnostics)}")
        return v
    if sum(len(_day(rec, d)) for d in range(7)) == 0:
        add("fallback", "solved week is empty")
        return v

    # Rolling 48h TSS / ANS / mech strain (+ Sunday -> Monday boundary from the previous week's record)
    for attr, cap, rule in (("aerobic_tss", lim.max_48h_tss, "48h_tss"), ("ans_cost", lim.max_48h_ans, "48h_ans"),
                            ("mechanical_strain", lim.max_48h_mech, "48h_strain")):
        loads = [_sum(_day(rec, d), attr) for d in range(7)]
        pre = _sum(_day(prev, 6), attr) if prev is not None and prev.solved else 0.0
        pairs = [(pre + loads[0], "Sun->Mon")] if prev is not None else []
        pairs += [(loads[d] + loads[d + 1], f"day {d}-{d + 1}") for d in range(6)]
        for val, where in pairs:
            if val > cap + TOL:
                add(rule, f"48h {attr} {val:.1f} > {cap:.1f} ({where})", val, cap)

    # Daily / weekly axial & neuromuscular
    for attr, dcap, wcap, tag in (("axial_load", lim.max_daily_axial, lim.max_weekly_axial, "axial"),
                                  ("neuromuscular_load", lim.max_daily_neuro, lim.max_weekly_neuro, "neuro")):
        loads = [_sum(_day(rec, d), attr) for d in range(7)]
        for d, val in enumerate(loads):
            c = dcap * (lim.readiness_alpha[d] if tag == "neuro" else 1.0)
            if val > c + TOL:
                add(f"daily_{tag}", f"daily {tag} {val:.1f} > {c:.1f} (day {d})", val, c)
        if sum(loads) > wcap + TOL:
            add(f"weekly_{tag}", f"weekly {tag} {sum(loads):.1f} > {wcap:.1f}", sum(loads), wcap)

    # Single-run share (running minutes, incl. lift + easy-run combo legs)
    runs = [run_minutes(w) for d in range(7) for w in _day(rec, d) if run_minutes(w) > 0]
    if runs:
        share = max(runs) / sum(runs)
        cap = lim.run_share_cap(len(runs))
        if share > cap + TOL:
            add("run_share", f"single run {share:.3f} of run time > {cap:.2f} ({len(runs)} runs)", share, cap)
        # ADR-022: weeks with < 4 runs are held to an ABSOLUTE long-run cap, re-derived here from the previous
        # week's actual schedule (not from the solver's resolved value) and the profile parameters.
        abs_cap = independent_long_run_cap(rec, prev)
        if len(runs) < 4 and abs_cap is not None and max(runs) > abs_cap + TOL:
            add("long_run_abs", f"longest run {max(runs):.0f} min > absolute cap {abs_cap:.0f} min ({len(runs)} runs)",
                max(runs), abs_cap)

    # Hard/easy adjacency, one strength a day, deadlift <-> heavy/long run lockout (same day and +-24h)
    flat = [(d, w) for d in range(7) for w in _day(rec, d)]
    ws = [w for _, w in flat]
    S = classify_workouts(ws)
    idx_day = {i: flat[i][0] for i in range(len(flat))}
    hard_days = {idx_day[i] for i in S.hard}
    for d in range(6):
        if d in hard_days and d + 1 in hard_days:
            add("hard_hard", f"hard days back to back (day {d}, {d + 1})")
    if prev is not None and prev.solved and any(classify_workouts(_day(prev, 6)).hard) and 0 in hard_days:
        add("hard_hard", "hard Sunday followed by hard Monday (week boundary)")
    for d in range(7):
        if sum(1 for i in S.strength if idx_day[i] == d) > 1:
            add("strength_per_day", f"{sum(1 for i in S.strength if idx_day[i] == d)} strength sessions on day {d}")
    dl_days = [idx_day[i] for i in S.deadlifts]
    hr_days = [idx_day[i] for i in S.heavy_or_long_runs]
    for a in dl_days:
        for b in hr_days:
            if abs(a - b) <= 1:
                add("deadlift_run_lockout", f"deadlift day {a} within 24h of heavy/long run day {b}")
    for a in hr_days:
        for b in hr_days:
            if a < b <= a + 1:
                add("heavy_run_gap", f"heavy/long runs on consecutive days {a},{b}")
    if prev is not None and prev.solved and 0 in set(dl_days + hr_days):
        p = classify_workouts(_day(prev, 6))
        if (p.heavy_or_long_runs and (0 in hr_days or 0 in dl_days)) or (p.deadlifts and 0 in hr_days):
            add("deadlift_run_lockout", "heavy/long run vs deadlift/heavy run across the Sunday -> Monday boundary")

    # Daily time caps and weekday single-session cap
    caps = list(cfg.daily_time_caps)
    for d in range(7):
        mins = _sum(_day(rec, d), "duration")
        if mins > caps[d] + TOL:
            add("daily_time_cap", f"day {d} has {mins:.0f} min > cap {caps[d]:.0f}", mins, caps[d])
        if d < 5 and cfg.weekday_single_workout_cap is not None:
            for w in _day(rec, d):
                if w.duration > cfg.weekday_single_workout_cap + TOL:
                    add("weekday_single_cap", f"{w.code} {w.duration:.0f} min on weekday {d} > {cfg.weekday_single_workout_cap:.0f}")

    # Canonical 80/20 hard rows over endurance minutes
    z = [endurance_zone7(w) for w in ws]
    tot = sum(sum(x) for x in z)
    if tot > 0:
        low = sum(x[0] + x[1] for x in z) / tot
        high = sum(x[3] + x[4] + x[5] + x[6] for x in z) / tot
        if low < cfg.intensity_low_min - TOL:
            add("8020_low", f"Z1+Z2 share {low:.3f} < {cfg.intensity_low_min:.2f}", low, cfg.intensity_low_min)
        if high > cfg.intensity_mod_high_max + TOL:
            add("8020_high", f"Z3+ZY+Z4+Z5 share {high:.3f} > {cfg.intensity_mod_high_max:.2f}", high,
                cfg.intensity_mod_high_max)

    # Carried load dynamics: running ACWR ceiling row, CTL ramp, re-entry run-minutes guard
    ls = rec.carry.load if rec.carry is not None else None
    if ls is not None:
        a_, c_ = ALPHA_ACUTE, ALPHA_CHRONIC
        A, C = ls.run_acute, ls.run_chronic
        for d in range(7):
            m = sum(run_strain(w) for w in _day(rec, d))
            A, C = (1 - a_) * A + a_ * m, (1 - c_) * C + c_ * m
            const = (1 - a_) ** (d + 1) * ls.run_acute - ls.acwr_max * (1 - c_) ** (d + 1) * ls.run_chronic
            allowed = max(ls.acwr_eps, const)       # the row clamps its right-hand side at 0 (ADR-019)
            if A - ls.acwr_max * C > allowed + 1e-6:
                add("run_acwr", f"day {d}: A-{ls.acwr_max:.2f}*C = {A - ls.acwr_max * C:.3f} > {allowed:.3f}",
                    A - ls.acwr_max * C, allowed)
        if ls.ctl_ramp_max is not None:
            b = ALPHA_CTL
            ctl = ls.total_ctl
            for d in range(7):
                t = sum(w.aerobic_tss for w in _day(rec, d) if w.sport in ("Bike", "Run", "Swim"))
                ctl = (1 - b) * ctl + b * t
            if ctl - ls.total_ctl > ls.ctl_ramp_max + 1e-6:
                add("ctl_ramp", f"CTL ramp {ctl - ls.total_ctl:.2f} > {ls.ctl_ramp_max:.1f}", ctl - ls.total_ctl,
                    ls.ctl_ramp_max)
        if ls.run_minutes_cap is not None:
            rm = sum(run_minutes(w) for w_ in range(7) for w in _day(rec, w_))
            if rm > ls.run_minutes_cap + TOL:
                add("run_minutes_guard", f"run minutes {rm:.0f} > guard {ls.run_minutes_cap:.0f}", rm, ls.run_minutes_cap)
    return v


def check_season(result: Sequence[WeekRecord], profile: Any = None) -> List[Violation]:
    """All Tier-0 violations of a solved season (``[]`` = clean). ``profile`` is accepted for API symmetry and
    future profile-level checks; each week is checked against the limits it was actually solved under (and the
    profile's tightened daily axial / neuro ceilings when given)."""
    out: List[Violation] = []
    prev: Optional[WeekRecord] = None
    for rec in result:
        out += check_week(rec, prev)
        if profile is not None and rec.limits is not None and rec.solved:
            for attr, plim in (("max_daily_axial", "max_daily_axial_load"), ("max_daily_neuro", "max_daily_neuro_load")):
                if getattr(rec.limits, attr) > getattr(profile, plim) + TOL:
                    out.append(Violation(rec.week_num, "limit_loosened",
                                         f"{attr} {getattr(rec.limits, attr):.0f} looser than profile {getattr(profile, plim):.0f}"))
        prev = rec
    return out


# ---------------------------------------------------------------------------
# Metrics, runner, report
# ---------------------------------------------------------------------------
MAX_CANDIDATES_BUDGET = 120


def season_metrics(records: Sequence[WeekRecord]) -> Dict[str, Any]:
    ok = [r for r in records if r.solved and r.status in OK_STATUSES]
    max48 = {"tss": 0.0, "ans": 0.0, "strain": 0.0}
    max_ax = max_nu = max_share = 0.0
    pigeon = single = 0
    longest_by_week: List[float] = []
    low_all, hi_all = [], []
    prev: Optional[WeekRecord] = None
    for r in ok:
        for key, attr in (("tss", "aerobic_tss"), ("ans", "ans_cost"), ("strain", "mechanical_strain")):
            loads = [_sum(_day(r, d), attr) for d in range(7)]
            max48[key] = max(max48[key], max(loads[d] + loads[d + 1] for d in range(6)))
        max_ax = max(max_ax, max(_sum(_day(r, d), "axial_load") for d in range(7)))
        max_nu = max(max_nu, max(_sum(_day(r, d), "neuromuscular_load") for d in range(7)))
        runs = [run_minutes(w) for d in range(7) for w in _day(r, d) if run_minutes(w) > 0]
        if runs:
            max_share = max(max_share, max(runs) / sum(runs))
            if len(runs) < 4:
                pigeon += 1
        if len(runs) == 1:
            single += 1
        longest_by_week.append(round(max(runs), 1) if runs else 0.0)
        z = [endurance_zone7(w) for d in range(7) for w in _day(r, d)]
        tot = sum(sum(x) for x in z)
        if tot > 0:
            low_all.append(sum(x[0] + x[1] for x in z) / tot)
            hi_all.append(sum(x[3] + x[4] + x[5] + x[6] for x in z) / tot)
        prev = r
    return {
        "weeks": len(records), "solved": len(ok), "fallback": len(records) - len(ok),
        "relaxations": int(sum(len(r.relaxations) for r in records)),
        "max48_tss": max48["tss"], "max48_ans": max48["ans"], "max48_strain": max48["strain"],
        "longest_run_by_week": longest_by_week, "pigeonhole_weeks": pigeon, "single_run_weeks": single, "max_axial": max_ax, "max_neuro": max_nu, "max_run_share": max_share,
        "low_pct_mean": 100 * (sum(low_all) / len(low_all)) if low_all else 0.0,
        "low_pct_min": 100 * min(low_all) if low_all else 0.0,
        "high_pct_max": 100 * max(hi_all) if hi_all else 0.0,
        "max_candidates": max((r.n_candidates for r in ok), default=0),
        "mean_candidates": round(sum(r.n_candidates for r in ok) / len(ok), 1) if ok else 0.0,
    }


def _tss_by_type(summary: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    """Solved vs target TSS by week role: where does the solver overshoot / undershoot its targets?"""
    out: Dict[str, Dict[str, float]] = {}
    if summary is None or summary.empty:
        return out
    kind = summary.Step_Type.astype(str).map(
        lambda t: "recovery" if t.startswith("Recovery") else "taper/race" if t.startswith(("Taper", "Race")) else "build")
    for k in ("build", "recovery", "taper/race"):
        sub = summary[kind == k]
        if len(sub):
            diff = sub.Solved_TSS - sub.Target_TSS
            out[k] = {"weeks": float(len(sub)), "median_ratio": float((sub.Solved_TSS / sub.Target_TSS.clip(lower=1)).median()),
                      "max_over": float(diff.max()), "max_under": float(-diff.min()),
                      "mean_over": float(diff.mean())}
    return out


def run_one(profile_name: str, distance: str, time_limit_per_week: float = 3.5,
            weeks_to_race: Optional[int] = None, reentry_weeks: Optional[int] = None) -> Dict[str, Any]:
    """One season (profile x distance): the unit of work for the process pool. Returns plain picklable data."""
    t0 = time.time()
    profile = build_profile(profile_name, distance)
    if weeks_to_race is None:
        weeks_to_race = DEFAULT_RACE_WEEKS[normalize_distance(distance)]
    records: List[WeekRecord] = []
    with contextlib.redirect_stdout(io.StringIO()):
        daily, summary = generate_closed_loop_season_schedule(
            profile=profile, distance=distance, time_limit_per_week=time_limit_per_week,
            weeks_to_race=weeks_to_race, reentry_weeks=reentry_weeks, collect=records)
    viol = check_season(records, profile)
    m = season_metrics(records)
    m.update({
        "profile": profile_name, "distance": distance, "wall_s": time.time() - t0,
        "violations": [str(x) for x in viol],
        "infeasibility": list(summary.attrs.get("infeasibility", [])),
        "max_odi": float(summary.Ending_ODI.max()) if len(summary) else 0.0,
        "max_raw_acwr": float(summary.Max_Run_ACWR.max()) if len(summary) else 0.0,
        "tss_ratio": float((summary.Solved_TSS / summary.Target_TSS.clip(lower=1)).median()) if len(summary) else 0.0,
        "tss_by_type": _tss_by_type(summary),
        "summary_records": summary[["Week", "Phase", "Step_Type", "Target_TSS", "Solved_TSS", "Relaxations"]].to_dict("records"),
    })
    return m


def run_matrix(distances: Sequence[str] = DISTANCES, profiles: Sequence[str] = ("current", "reference"),
               time_limit_per_week: float = 3.5, max_workers: Optional[int] = None) -> List[Dict[str, Any]]:
    jobs = [(p, d) for p in profiles for d in distances]
    with ProcessPoolExecutor(max_workers=max_workers or min(len(jobs), 8)) as ex:
        futs = [ex.submit(run_one, p, d, time_limit_per_week) for p, d in jobs]
        return [f.result() for f in futs]


def summary_table(results: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for r in results:
        rows.append({
            "profile": r["profile"], "distance": r["distance"], "weeks": r["weeks"], "solved": r["solved"],
            "fallback": r["fallback"], "relax": r["relaxations"], "viol": len(r["violations"]),
            "48h TSS": round(r["max48_tss"], 1), "48h ANS": round(r["max48_ans"], 1),
            "48h strain": round(r["max48_strain"], 1), "axial": round(r["max_axial"], 1),
            "neuro": round(r["max_neuro"], 1), "run share": round(r["max_run_share"], 3), "<4 runs wk": r["pigeonhole_weeks"],
            "low%": round(r["low_pct_min"], 1), "high%": round(r["high_pct_max"], 1),
            "cands": r.get("max_candidates", 0),
            "wall s": round(r["wall_s"], 0),
        })
    return pd.DataFrame(rows)


def render_markdown(results: Sequence[Dict[str, Any]]) -> str:
    df = summary_table(results)
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    out = ["## Summary table", "", *lines, "",
           "`48h *`, `axial`, `neuro` are season maxima; `run share` the largest single-run fraction of run time; "
           "`low%` the minimum weekly Z1+Z2 share of endurance time, `high%` the maximum weekly Z3+ZY+Z4+Z5 share.", ""]
    for r in results:
        out.append(f"### {r['profile']} / {r['distance']}")
        for k, t in r["tss_by_type"].items():
            out.append(f"- TSS vs target, {k} weeks (n={int(t['weeks'])}): median solved/target {t['median_ratio']:.2f}, "
                       f"mean {t['mean_over']:+.0f}, max over {t['max_over']:+.0f}, max under {-t['max_under']:+.0f}")
        out.append(f"- weeks with a single run: {r['single_run_weeks']}; weeks with <4 runs (pigeonhole cap): "
                   f"{r['pigeonhole_weeks']}")
        out.append(f"- violations: {len(r['violations'])}; max ODI {r['max_odi']:.2f}; max raw running ACWR "
                   f"{r['max_raw_acwr']:.2f}; median solved/target TSS {r['tss_ratio']:.2f}")
        for x in r["violations"][:10]:
            out.append(f"  - VIOLATION {x}")
        for x in r["infeasibility"]:
            out.append(f"  - {x}")
        out.append("")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--distances", nargs="+", default=list(DISTANCES))
    ap.add_argument("--profiles", nargs="+", default=["current", "reference"], choices=["current", "reference"])
    ap.add_argument("--time-limit", type=float, default=3.5)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--report", default=None, help="write the markdown report here")
    args = ap.parse_args(argv)
    res = run_matrix(args.distances, args.profiles, args.time_limit, args.workers)
    md = render_markdown(res)
    print(md)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(md)
    return 0 if all(r["fallback"] == 0 and not r["violations"] for r in res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
