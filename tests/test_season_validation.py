"""TASK-027: independent Tier-0 season checker, infeasibility diagnosis, and multi-distance season runs."""

import contextlib
import io
from dataclasses import replace

import pandas as pd
import pytest

from triplan.control.feasibility import RACE_DEMANDS, season_infeasibility_report, time_budget_diagnosis
from triplan.control.season import WeekRecord
from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.perf import season_validation as sv
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.solver.engine.carry_in import LoadState, WeekCarryIn
from triplan.solver.safety_constraints import SafetyLimits


def wk(sport="Bike", dur=60.0, tss=40.0, mech=5.0, ans=50.0, axial=0.0, neuro=0.0, z=None, **kw):
    z7 = z or (dur, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return CandidateWorkout(
        id=kw.pop("id", 1), code=kw.pop("code", f"{sport}_X"), name="x", sport=sport, duration=dur, aerobic_tss=tss,
        mechanical_strain=mech, ans_cost=ans, durability_score=1.0, zone_durations=(dur, 0, 0, 0, 0),
        axial_load=axial, neuromuscular_load=neuro, zone7=z7, **kw)


def rec(schedule, week=1, carry=None, solved=True, status="OPTIMAL", cfg=None, limits=None):
    full = {d: list(schedule.get(d, [])) for d in range(7)}
    return WeekRecord(week_num=week, phase="Base", step_type="Base", role="build", block="race", target_tss=100.0,
                      status=status, solved=solved, schedule=full, limits=limits or SafetyLimits(),
                      cfg=cfg or MesoCycleTargetsAndConstraints(daily_time_caps=[300.0] * 7, weekday_single_workout_cap=300.0),
                      carry=carry)


def rules(viol):
    return {v.rule for v in viol}


def test_clean_week_has_no_violations():
    r = rec({0: [wk("Bike")], 2: [wk("Run", 45, 30, 20, id=2)], 4: [wk("Run", 45, 30, 20, id=3)],
             5: [wk("Run", 45, 30, 20, id=4)], 6: [wk("Run", 45, 30, 20, id=5)]})
    assert sv.check_week(r) == []


@pytest.mark.parametrize("attr,field,val,rule", [
    ("max_48h_tss", "tss", 200.0, "48h_tss"),
    ("max_48h_ans", "ans", 500.0, "48h_ans"),
    ("max_48h_mech", "mech", 150.0, "48h_strain"),
])
def test_checker_catches_48h_caps(attr, field, val, rule):
    lim = SafetyLimits(**{attr: val * 1.5})
    a, b = wk("Bike", **{field: val, "id": 1}), wk("Bike", **{field: val, "id": 2})
    assert rule in rules(sv.check_week(rec({1: [a], 2: [b]}, limits=lim)))
    assert rule not in rules(sv.check_week(rec({1: [a], 3: [b]}, limits=lim)))   # 72h apart: fine


def test_checker_catches_48h_across_week_boundary():
    lim = SafetyLimits(max_48h_tss=100.0)
    prev = rec({6: [wk("Bike", tss=70.0)]}, week=1)
    cur = rec({0: [wk("Bike", tss=70.0, id=2)]}, week=2)
    assert "48h_tss" in rules(sv.check_week(replace(cur, limits=lim), prev))
    assert "48h_tss" not in rules(sv.check_week(replace(cur, limits=lim)))


def test_checker_catches_daily_and_weekly_axial_neuro():
    lim = SafetyLimits(max_daily_axial=60.0, max_weekly_axial=100.0, max_daily_neuro=70.0, max_weekly_neuro=100.0)
    over = wk("Strength", 45, axial=80.0, neuro=10.0, id=1)
    assert "daily_axial" in rules(sv.check_week(rec({0: [over]}, limits=lim)))
    over = wk("Strength", 45, axial=10.0, neuro=90.0, id=1)
    assert "daily_neuro" in rules(sv.check_week(rec({0: [over]}, limits=lim)))
    parts = [wk("Strength", 45, axial=55.0, neuro=55.0, id=i) for i in (1, 2)]
    r = rules(sv.check_week(rec({0: [parts[0]], 3: [parts[1]]}, limits=lim)))
    assert {"weekly_axial", "weekly_neuro"} <= r and not ({"daily_axial", "daily_neuro"} & r)


def test_checker_catches_single_run_share():
    runs = {d: [wk("Run", 30 if d else 90, 20, 10, id=d + 1)] for d in (0, 2, 4, 5)}   # 90 / 180 = 50%
    assert "run_share" in rules(sv.check_week(rec(runs)))
    even = {d: [wk("Run", 40, 20, 10, id=d + 1)] for d in (0, 2, 4, 5)}                    # 25%
    assert "run_share" not in rules(sv.check_week(rec(even)))
    three = {d: [wk("Run", 40, 20, 10, id=d + 1)] for d in (0, 2, 4)}                       # 33% <= pigeonhole 40%
    assert "run_share" not in rules(sv.check_week(rec(three)))


def test_checker_catches_deadlift_run_lockout_and_hard_days():
    dl = wk("Strength", 40, is_heavy_strength=True, flags={"heavy_dl": 1}, id=1, is_hard_endurance=False)
    hr = wk("Run", 50, is_heavy_run=True, id=2, is_hard_endurance=True, mech=40.0)
    assert "deadlift_run_lockout" in rules(sv.check_week(rec({2: [dl], 3: [hr]})))      # +24h
    assert "deadlift_run_lockout" in rules(sv.check_week(rec({3: [dl, hr]})))           # same day
    assert "deadlift_run_lockout" not in rules(sv.check_week(rec({1: [dl], 4: [hr]})))
    bike_hard = wk("Bike", 60, is_hard_endurance=True, id=3)
    assert "hard_hard" in rules(sv.check_week(rec({1: [bike_hard], 2: [hr]})))
    assert "hard_hard" not in rules(sv.check_week(rec({1: [bike_hard], 3: [hr]})))


def test_checker_catches_time_caps_and_strength_per_day():
    cfg = MesoCycleTargetsAndConstraints(daily_time_caps=[60.0] * 7, weekday_single_workout_cap=45.0)
    assert "daily_time_cap" in rules(sv.check_week(rec({5: [wk("Bike", 45), wk("Run", 30, id=2)]}, cfg=cfg)))
    assert "weekday_single_cap" in rules(sv.check_week(rec({1: [wk("Bike", 60)]}, cfg=cfg)))
    s1, s2 = wk("Strength", 30, id=1), wk("Strength", 30, id=2)
    assert "strength_per_day" in rules(sv.check_week(rec({1: [s1, s2]})))


def test_checker_catches_8020_rows():
    cfg = MesoCycleTargetsAndConstraints(daily_time_caps=[300.0] * 7, weekday_single_workout_cap=300.0)
    hard = wk("Bike", 60, z=(30.0, 0.0, 0.0, 0.0, 0.0, 30.0, 0.0), id=1)     # 50% high
    assert "8020_high" in rules(sv.check_week(rec({1: [hard]}, cfg=cfg)))
    assert "8020_low" in rules(sv.check_week(rec({1: [hard]}, cfg=cfg)))
    ok = wk("Bike", 60, z=(30.0, 20.0, 0.0, 5.0, 0.0, 5.0, 0.0), id=2)       # 83% low, 17% high
    r = rules(sv.check_week(rec({1: [ok]}, cfg=cfg)))
    assert not {"8020_low", "8020_high"} & r


def test_checker_catches_load_dynamics_rows():
    ls = LoadState(run_acute=0.0, run_chronic=0.0, total_ctl=0.0, run_ctl=0.0, acwr_max=1.2, acwr_min=0.8,
                   acwr_eps=0.0, ctl_ramp_max=7.0, run_minutes_cap=60.0)
    carry = WeekCarryIn(load=ls)
    big = wk("Run", 90, 400, 100.0, id=1)
    r = rules(sv.check_week(rec({1: [big]}, carry=carry)))
    assert {"run_acwr", "ctl_ramp", "run_minutes_guard"} <= r
    small = wk("Run", 30, 10, 0.0, id=2)
    assert not ({"run_acwr", "ctl_ramp", "run_minutes_guard"} & rules(sv.check_week(rec({1: [small]}, carry=carry))))


def test_checker_flags_fallback_weeks():
    assert rules(sv.check_week(rec({}, solved=False, status="INFEASIBLE"))) == {"fallback"}
    assert rules(sv.check_week(rec({}))) == {"fallback"}          # empty "solved" week is a silent degradation
    assert sv.check_season([rec({}, solved=False, status="INFEASIBLE")])[0].rule == "fallback"


# --------------------------------------------------------------------------- infeasibility diagnosis
def test_time_budget_diagnosis_names_long_ride_for_current_athlete():
    msgs = time_budget_diagnosis(CURRENT_ATHLETE, "ironman")
    assert any("IM long ride 180+ min cannot fit" in m and "min/day caps" in m for m in msgs), msgs
    assert any("peak week" in m for m in msgs)
    sprint = time_budget_diagnosis(CURRENT_ATHLETE, "sprint")
    assert not any("long ride" in m for m in sprint)               # a 60 min ride fits 75 min days


def test_time_budget_diagnosis_quiet_for_reference_profile():
    for dist in sv.DISTANCES:
        assert time_budget_diagnosis(sv.reference_profile(), dist) == [], dist
    assert RACE_DEMANDS["ironman"]["long_ride"] == 180.0


def test_season_report_rolls_up_relaxations_and_shortfalls():
    summary = pd.DataFrame([
        {"Week": w, "Target_TSS": 300.0, "Solved_TSS": 200.0 if w < 4 else 300.0,
         "Relaxations": "Dropped anchor BIKE_DUR_OUT_DEF (Bike, 180 min).; Running ACWR floor waived (deload; ok)" if w < 4 else "",
         "Diagnostics": ""}
        for w in range(1, 7)])
    summary.loc[:, "Relaxations"] = summary.Week.map(
        lambda w: "Dropped anchor BIKE_DUR_OUT_DEF (Bike, 180 min). ; Key sessions: 1 fewer" if w < 4 else "")
    msgs = season_infeasibility_report(summary, CURRENT_ATHLETE, "ironman")
    text = "\n".join(msgs)
    assert "long ride 180+ min cannot fit" in text
    assert "Relaxed in 3 week(s) (1-3)" in text and "Dropped anchor BIKE_DUR_OUT_DEF" in text
    assert "Target TSS missed by >10% in 3 of 6" in text


# --------------------------------------------------------------------------- real (short) seasons
def _short_season(profile_name, distance, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return sv.run_one(profile_name, distance, time_limit_per_week=2.0, **kw)


def test_short_reference_season_clean():
    res = _short_season("reference", "sprint", weeks_to_race=4)
    assert res["weeks"] == 4 and res["fallback"] == 0
    assert res["violations"] == [], res["violations"]
    assert res["max48_tss"] <= 295.0 + 1e-6 and res["high_pct_max"] <= 20.0 + 1e-6 and res["low_pct_min"] >= 78.0 - 1e-6
    assert res["infeasibility"] is not None                       # attached (possibly only relaxation roll-up)


def test_short_current_athlete_season_clean_and_diagnosed():
    res = _short_season("current", "ironman", weeks_to_race=4, reentry_weeks=4)
    assert res["weeks"] == 8 and res["fallback"] == 0
    assert res["violations"] == [], res["violations"]
    assert any("long ride 180+ min cannot fit" in m for m in res["infeasibility"]), res["infeasibility"]


@pytest.mark.slow
@pytest.mark.parametrize("distance", sv.DISTANCES)
@pytest.mark.parametrize("profile_name", ["current", "reference"])
def test_full_matrix_season(profile_name, distance):
    res = sv.run_one(profile_name, distance)
    assert res["fallback"] == 0
    assert res["violations"] == [], res["violations"][:5]
    # TASK-038: recovery / taper / race weeks no longer overshoot their targets by more than 25 TSS
    for kind in ("recovery", "taper/race"):
        assert res["tss_by_type"][kind]["max_over"] <= 25.0 + 1e-6, (kind, res["tss_by_type"][kind])
    if profile_name == "current" and distance == "ironman":
        assert any("long ride 180+ min cannot fit" in m for m in res["infeasibility"])
