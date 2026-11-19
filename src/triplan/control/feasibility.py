"""Season-level infeasibility diagnosis (TASK-027).

A week that cannot meet its Tier-1 targets already reports named relaxations (``plan["relaxations"]``) and
plain-English Tier-0 reasons (``last_diagnostics``). This module rolls those up for a whole season and adds
the *race-demand* question the per-week model cannot ask: can the athlete's time budget host the long
sessions and weekly volume the race distance needs at all? The answer is explicit text, never a silent
shortfall.

Race demands are coarse planning norms (long ride/run minutes, peak weekly hours) used only to *explain*
shortfalls; they never change a constraint.
"""

from __future__ import annotations

import copy
import re
from collections import OrderedDict
from dataclasses import replace
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from triplan.control.periodization import normalize_distance

# distance -> (long ride min, long run min, peak weekly training hours)
RACE_DEMANDS: Dict[str, Dict[str, float]] = {
    "sprint": {"long_ride": 60.0, "long_run": 40.0, "peak_hours": 5.0},
    "olympic": {"long_ride": 90.0, "long_run": 60.0, "peak_hours": 7.0},
    "70.3": {"long_ride": 150.0, "long_run": 90.0, "peak_hours": 10.0},
    "ironman": {"long_ride": 180.0, "long_run": 105.0, "peak_hours": 13.0},
}
_LABEL = {"sprint": "Sprint", "olympic": "Olympic", "70.3": "70.3", "ironman": "IM"}


def peak_48h(schedule: Dict[int, Sequence[Any]]) -> Dict[str, float]:
    """Largest rolling 48h (two adjacent days inside the week) TSS / ANS / mechanical strain of a solved schedule."""
    out = {}
    for key, attr in (("TSS", "aerobic_tss"), ("ANS", "ans_cost"), ("Strain", "mechanical_strain")):
        day = [sum(float(getattr(w, attr, 0.0) or 0.0) for w in schedule.get(d, [])) for d in range(7)]
        out[key] = max(day[d] + day[d + 1] for d in range(6))
    return out


def _day_budgets(daily_caps: Sequence[float], weekday_single_cap: Optional[float]) -> List[float]:
    """Largest single session each day can host (weekday sessions are also bounded by the single-session cap)."""
    out = []
    for d, cap in enumerate(daily_caps):
        c = float(cap)
        if d < 5 and weekday_single_cap is not None:
            c = min(c, float(weekday_single_cap))
        out.append(c)
    return out


def time_budget_diagnosis(profile: Any, distance: str, library: Optional[Sequence[Any]] = None) -> List[str]:
    """Pre-solve messages: which race demands the profile's time budget cannot host."""
    dist = normalize_distance(distance)
    need = RACE_DEMANDS[dist]
    label = _LABEL[dist]
    caps = list(profile.daily_time_caps)
    budgets = _day_budgets(caps, profile.weekday_single_workout_cap)
    windows = list(getattr(profile, "two_session_windows", [False] * 7))
    msgs: List[str] = []
    biggest = max(budgets) if budgets else 0.0
    cap_txt = f"{min(caps):.0f}-{max(caps):.0f} min/day caps"

    ride_pool = [w for w in (library or []) if w.sport == "Bike"]
    run_pool = [w for w in (library or []) if w.sport == "Run"]
    if library is not None:
        longest_ride = max((w.duration for w in ride_pool), default=0.0)
        longest_run = max((w.duration for w in run_pool), default=0.0)
    else:
        longest_ride = longest_run = float("inf")

    if biggest + 1e-9 < need["long_ride"]:
        msgs.append(f"{label} long ride {need['long_ride']:.0f}+ min cannot fit {cap_txt} "
                    f"(largest single-day budget {biggest:.0f} min).")
    elif library is not None and longest_ride + 1e-9 < need["long_ride"]:
        msgs.append(f"{label} long ride {need['long_ride']:.0f}+ min is not in the workout library "
                    f"(longest ride {longest_ride:.0f} min).")
    if biggest + 1e-9 < need["long_run"]:
        msgs.append(f"{label} long run {need['long_run']:.0f}+ min cannot fit {cap_txt} "
                    f"(largest single-day budget {biggest:.0f} min).")
    elif library is not None and longest_run + 1e-9 < need["long_run"]:
        msgs.append(f"{label} long run {need['long_run']:.0f}+ min is not in the workout library "
                    f"(longest run {longest_run:.0f} min).")

    # Weekly time: every day may hold sessions up to its cap (a second window adds a session, not minutes).
    weekly_minutes = float(sum(caps))
    # One rest day is mandatory (min_rest_days): the smallest cap is the cheapest day to give up.
    rest = int(getattr(profile, "min_rest_days", 1) or 0)
    usable = sorted(caps)[rest:] if rest else sorted(caps)
    weekly_usable = float(sum(usable))
    if weekly_usable / 60.0 + 1e-9 < need["peak_hours"]:
        msgs.append(f"{label} peak week needs ~{need['peak_hours']:.0f} h but the time budget allows at most "
                    f"{weekly_usable / 60.0:.1f} h/week ({weekly_minutes / 60.0:.1f} h before the {rest} mandatory "
                    f"rest day(s); two-session windows on {sum(windows)} day(s) add sessions, not minutes).")
    return msgs


def season_infeasibility_report(summary: pd.DataFrame, profile: Any, distance: str,
                                library: Optional[Sequence[Any]] = None,
                                shortfall_frac: float = 0.10) -> List[str]:
    """Season-level diagnosis: pre-solve time-budget messages + which weeks relaxed what + target shortfalls.

    Never empty-handed silently: it returns ``[]`` only when every week met its targets without relaxation
    and the time budget hosts the race demands.
    """
    msgs = time_budget_diagnosis(profile, distance, library) if profile is not None else []
    if summary is None or summary.empty:
        return msgs

    by_msg: "OrderedDict[str, List[int]]" = OrderedDict()
    for _, r in summary.iterrows():
        for part in re.split(r"; (?=[A-Z])", str(r.get("Relaxations") or "")):
            if part.strip():
                by_msg.setdefault(part.strip(), []).append(int(r["Week"]))
    for text, weeks in by_msg.items():
        msgs.append(f"Relaxed in {len(weeks)} week(s) ({_week_ranges(weeks)}): {text}")

    msgs.extend(_target_raised_message(summary))
    msgs.extend(single_run_message(summary))
    short = summary[(summary.Target_TSS > 0) & (summary.Solved_TSS < summary.Target_TSS * (1.0 - shortfall_frac))]
    if len(short):
        worst = short.assign(gap=short.Target_TSS - short.Solved_TSS).sort_values("gap", ascending=False).iloc[0]
        msgs.append(f"Target TSS missed by >{shortfall_frac:.0%} in {len(short)} of {len(summary)} week(s) "
                    f"({_week_ranges(short.Week.tolist())}); worst week {int(worst.Week)}: target "
                    f"{worst.Target_TSS:.0f}, solved {worst.Solved_TSS:.0f}. Tier-0 caps and the time budget bind "
                    f"before volume does; this is reported, not hidden.")
    msgs.extend(_binding_48h_caps(short, profile))
    over = summary[(summary.Target_TSS > 0) & (summary.Solved_TSS > summary.Target_TSS * (1.0 + 0.25))]
    if len(over):
        msgs.append(f"Solved TSS exceeds target by >25% in {len(over)} week(s) ({_week_ranges(over.Week.tolist())}): "
                    f"structure minimums (frequency, strength, anchors) set a floor above the target.")
    diag = summary[summary.Diagnostics.astype(str).str.len() > 0] if "Diagnostics" in summary else summary.iloc[0:0]
    if len(diag):
        first = str(diag.iloc[0].Diagnostics).split("; ")[0]
        msgs.append(f"Solver diagnostics in {len(diag)} week(s) ({_week_ranges(diag.Week.tolist())}), e.g. {first}")
    return msgs


def _target_raised_message(summary: pd.DataFrame) -> List[str]:
    """TASK-041 / ADR-025: race-block weeks whose TSS target was raised from the matrix value to the structural floor."""
    if "Recalibration" not in summary or "Matrix_Target_TSS" not in summary:
        return []
    hit = summary[summary.Recalibration.astype(str).str.contains("TARGET_RAISED_TO_STRUCTURE")]
    if hit.empty:
        return []
    return [f"TARGET_RAISED_TO_STRUCTURE in {len(hit)} week(s) ({_week_ranges(hit.Week.tolist())}): the committed weekly "
            f"structure (key / stride / Bike-Run minimums, strength, 1 session per day) costs more TSS than the matrix "
            f"target (matrix {hit.Matrix_Target_TSS.min():.0f}-{hit.Matrix_Target_TSS.max():.0f} -> "
            f"{hit.Target_TSS.min():.0f}-{hit.Target_TSS.max():.0f}); owner decision: the structure is the plan, so the "
            f"target follows it (ADR-025). The CTL ramp, ACWR and 48h rows stay hard."]


def _binding_48h_caps(short: pd.DataFrame, profile: Any, tol: float = 0.03) -> List[str]:
    """TASK-040: name the rolling 48h cap(s) that sit at their ceiling in the weeks that missed their TSS target.

    The caps are the GEMINI.md section 2 global Tier-0 ceilings (a profile may only tighten them), so a target that
    needs more than they allow is reported as unreachable under the caps, not as a library gap."""
    if profile is None or short is None or short.empty:
        return []
    out = []
    for col, attr, label in (("Max_48h_TSS", "max_48h_aerobic_tss", "TSS"), ("Max_48h_ANS", "max_48h_ans_cost", "ANS cost"),
                             ("Max_48h_Strain", "max_48h_mech_strain", "mechanical strain")):
        cap = getattr(profile, attr, None)
        if col not in short or not cap:
            continue
        hit = short[short[col] >= cap * (1.0 - tol)]
        if len(hit):
            out.append(f"Rolling 48h {label} cap {cap:.0f} binds in {len(hit)} of the {len(short)} short week(s) "
                       f"({_week_ranges(hit.Week.tolist())}; peak {hit[col].max():.0f}): the target is not reachable under "
                       f"the profile's 48h caps (global Tier-0 ceilings, never relaxed), not because of the library.")
    return out


# ---------------------------------------------------------------------------
# TASK-046: which Tier-0 / structure row keeps a week at a single run?
# ---------------------------------------------------------------------------
SINGLE_RUN_ROWS = ("run-minutes guard", "running ACWR ceiling", "weekly run strain cap", "long-run cap",
                   "1 session/day", "structure minimums")
SINGLE_RUN_COMBINED = "combination of rows"
SINGLE_RUN_UNBOUND = "no run row (goal trade-off)"
SINGLE_RUN_MIN_ONE = "run-frequency minimum of 1 (no row prevents more runs)"
SINGLE_RUN_BUDGET_S = 1.0     # time limit of each scratch re-solve
KEYSTONE_RUN_ROWS = (
    "weekly run strain cap",
    "running ACWR ceiling",
    "long-run cap",
    "run-share ceiling",
    "run frequency cap",
    "heavy-lift +-24h lockout",
    "48h caps",
    "1 session/day",
)
KEYSTONE_RUN_UNBOUND = "not hostable under daily time caps / library / structure"


def run_session_count(schedule: Dict[int, Sequence[Any]]) -> int:
    """Number of sessions that contain running (a lift + easy-run combo counts through its run leg)."""
    from triplan.solver.safety_constraints import run_minutes
    return sum(1 for d in schedule for w in schedule[d] if run_minutes(w) > 0)


def _relax_row(name: str, cfg: Any, carry: Any):
    """Scratch copy of (cfg, carry) with ONE candidate binding row relaxed. Diagnostic only: never fed back to a plan."""
    cfg, load = copy.deepcopy(cfg), getattr(carry, "load", None)
    if name == "weekly run strain cap":
        cfg.max_weekly_run_mech_strain = 1e6
    elif name == "1 session/day":
        cfg.two_session_windows = [True] * 7
    elif name == "structure minimums":
        from triplan.control.periodization import apply_reentry_relief
        apply_reentry_relief(cfg, 2)
    elif name == "run-share ceiling":
        cfg.max_single_run_vol_ratio = 1.0
        if hasattr(cfg, "long_run_cap_ratio"):
            cfg.long_run_cap_ratio = 1.0
        cfg.pigeonhole_run_share = {3: 1.0, 2: 1.0}
    elif name == "run frequency cap":
        bounds = dict(getattr(cfg, "sport_freq_bounds", {}) or {})
        lo, _hi = bounds.get("Run", (0, 7))
        bounds["Run"] = (lo, 7)
        cfg.sport_freq_bounds = bounds
    elif name == "heavy-lift +-24h lockout":
        cfg.required_heavy_strength_sessions = 0
    elif name == "48h caps":
        cfg.max_48h_aerobic_tss = 1e6
        cfg.max_48h_ans_cost = 1e6
        cfg.max_48h_mech_strain = 1e6
    elif name == "long-run cap":
        cfg.long_run_step_min = None
        cfg.reentry_long_run_max_min = None
        if carry is not None:
            carry = replace(carry, prev_longest_run_min=1e4,
                            load=replace(load, reentry=False) if load is not None else None)
    elif load is not None:
        if name == "run-minutes guard":
            carry = replace(carry, load=replace(load, run_minutes_cap=None))
        elif name == "running ACWR ceiling":
            carry = replace(carry, load=replace(load, acwr_max=1e3, acwr_min=0.0))
    return cfg, carry


def attribute_single_run_week(library: Sequence[Any], cfg: Any, carry: Any,
                              time_limit: float = SINGLE_RUN_BUDGET_S, min_runs: int = 2) -> Dict[str, Any]:
    """Name the row(s) that keep a week at one run, by relaxing each candidate alone in a scratch model.

    A row is *binding* when re-solving the same week (same library, goals and carry-in) with only that row relaxed
    yields >= ``min_runs`` run sessions. If no single row does but relaxing all of them does, the label is
    :data:`SINGLE_RUN_COMBINED`; if even that does not, :data:`SINGLE_RUN_UNBOUND` (the week is one run by goal
    trade-off, not by a run row). Diagnostic only: nothing here changes a constraint of the week that is reported.
    """
    from triplan.solver.scheduler import TriathlonMILPScheduler

    def runs_with(names) -> int:
        c, k = cfg, carry
        for n in names:
            c, k = _relax_row(n, c, k)
        plans = TriathlonMILPScheduler(library, c, carry_in=k).solve_top_k_plans(k_plans=1, time_limit=time_limit)
        return run_session_count(plans[0]["schedule"]) if plans else 0

    binding = [n for n in SINGLE_RUN_ROWS if runs_with([n]) >= min_runs]
    if binding:
        label = " + ".join(binding)
    else:
        if runs_with(SINGLE_RUN_ROWS) >= min_runs:
            label = SINGLE_RUN_COMBINED
        else:   # nothing blocks a second run: the Run-frequency minimum is already <= 1 (deload relief, ADR-023) or it is a goal trade-off
            run_min = (getattr(cfg, "sport_freq_bounds", {}) or {}).get("Run", (2, 7))[0]
            label = SINGLE_RUN_MIN_ONE if run_min <= 1 else SINGLE_RUN_UNBOUND
    return {"label": label, "binding": binding}


def attribute_keystone_run_week(
    library: Sequence[Any],
    cfg: Any,
    carry: Any,
    target_run_min: float,
    time_limit: float = 3.5,
    check_pairs: bool = True,
) -> Dict[str, Any]:
    """TASK-062: attribute why a week cannot host a ``target_run_min`` keystone run by relaxing candidate rows.

    Mirrors :func:`attribute_single_run_week`: applies ``apply_keystone_week(library, "run", target_run_min)`` and
    relaxes each row in :data:`KEYSTONE_RUN_ROWS` one at a time (then pairs and all rows if needed) in a scratch
    model. Diagnostic only: never mutates ``cfg``, ``carry``, or ``library``.
    """
    from triplan.control.periodization import apply_keystone_week
    from triplan.solver.safety_constraints import run_minutes
    from triplan.solver.scheduler import TriathlonMILPScheduler

    ks_lib = apply_keystone_week(library, "run", float(target_run_min))

    def hosts_with(names: Sequence[str]) -> bool:
        c, k = cfg, carry
        for n in names:
            c, k = _relax_row(n, c, k)
        plans = TriathlonMILPScheduler(ks_lib, c, carry_in=k).solve_top_k_plans(k_plans=1, time_limit=time_limit)
        if not plans:
            return False
        if any(x.get("category") == "shorten_or_drop_anchor:Run" for x in plans[0].get("relaxations", [])):
            return False
        longest = max((run_minutes(w) for d in range(7) for w in plans[0]["schedule"][d]), default=0.0)
        return longest >= float(target_run_min) - 1e-6

    binding = [n for n in KEYSTONE_RUN_ROWS if hosts_with([n])]
    pairs: List[List[str]] = []
    if binding:
        label = " + ".join(binding)
    elif hosts_with(KEYSTONE_RUN_ROWS):
        if check_pairs:
            pairs = [list(pair) for pair in combinations(KEYSTONE_RUN_ROWS, 2) if hosts_with(pair)]
        if len(pairs) == 1:
            label = " + ".join(pairs[0])
        elif pairs:
            label = f"{SINGLE_RUN_COMBINED} ({'; '.join(' + '.join(p) for p in pairs)})"
        else:
            label = SINGLE_RUN_COMBINED
    else:
        label = KEYSTONE_RUN_UNBOUND
    return {"label": label, "binding": binding, "pairs": pairs, "target_run_min": float(target_run_min)}


def single_run_message(summary: pd.DataFrame) -> List[str]:
    """TASK-046: season-level roll-up of the single-run weeks by binding row, one message per distinct attribution."""
    if summary is None or summary.empty or "Single_Run_Binding" not in summary:
        return []
    hit = summary[summary.Single_Run_Binding.astype(str).str.len() > 0]
    if hit.empty:
        return []
    out = []
    for label, grp in hit.groupby("Single_Run_Binding", sort=False):
        out.append(f"Single-run week(s) bound by {label}: {len(grp)} of {len(summary)} week(s) ({_week_ranges(grp.Week.tolist())}); "
                   f"relaxing that row in a scratch model yields >= 2 runs (diagnosis only, no constraint changed).")
    return out


def _week_ranges(weeks: Sequence[int]) -> str:
    ws = sorted(set(int(w) for w in weeks))
    if not ws:
        return ""
    out, start, prev = [], ws[0], ws[0]
    for w in ws[1:] + [None]:
        if w is not None and w == prev + 1:
            prev = w
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        if w is not None:
            start = prev = w
    return ",".join(out)
