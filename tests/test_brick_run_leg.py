"""Unit tests for Brick run-leg accounting across safety, load-dynamics, goals, and schema (TASK-059)."""

import pandas as pd
import pytest
from pydantic import ValidationError

from triplan.library.build import brick_run_loads
from triplan.library.loader import load_library
from triplan.library.schema import Workout
from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.perf import season_validation as sv
from triplan.physiology.state import ALPHA_ACUTE, ALPHA_CHRONIC, ALPHA_CTL
from triplan.solver.engine.carry_in import DayLoad, LoadState, WeekCarryIn
from triplan.solver.engine.config import WorkoutTable
from triplan.solver.safety_constraints import SafetyLimits, classify_workouts, run_minutes, run_strain
from triplan.solver.scheduler import TriathlonMILPScheduler

ALL = [True] * 7


def _wk(
    idx: int,
    code: str,
    sport: str,
    dur: float,
    tss: float = 40.0,
    mech: float = 20.0,
    ans: float = 20.0,
    z=None,
    **kw,
) -> CandidateWorkout:
    z5 = z or (dur * 0.4, dur * 0.6, 0.0, 0.0, 0.0)
    kw.setdefault("is_indoor_bike", sport == "Bike")
    return CandidateWorkout(
        id=idx,
        code=code,
        name=code,
        sport=sport,
        duration=float(dur),
        aerobic_tss=float(tss),
        mechanical_strain=float(mech),
        ans_cost=float(ans),
        durability_score=2.0,
        zone_durations=z5,
        zone7=(z5[0], z5[1], 0.0, z5[2], 0.0, z5[3], z5[4]),
        **kw,
    )


def _cfg(**kw) -> MesoCycleTargetsAndConstraints:
    base = dict(
        daily_time_caps=[120.0] * 7,
        weekday_single_workout_cap=120.0,
        facility_access={"Swim": ALL, "Bike": ALL, "Run": ALL, "Strength": ALL},
        sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (0, 7)},
        target_weekly_tss=100.0,
        nominal_weekly_duration=200.0,
        required_heavy_strength_sessions=0,
        min_stride_sessions=0,
        key_sessions_per_week=(0, 7),
        min_rest_days=1,
        max_rest_days=7,
    )
    base.update(kw)
    return MesoCycleTargetsAndConstraints(**base)


def _solve(lib, cfg, carry_in=None):
    s = TriathlonMILPScheduler(lib, cfg, carry_in=carry_in)
    return s, s.solve_top_k_plans(k_plans=1, time_limit=10.0)


def _day_of(plan, code: str) -> int:
    return next(d for d in range(7) for w in plan["schedule"][d] if w.code == code)


def _rec(schedule, week=1, carry=None, cfg=None, limits=None):
    full = {d: list(schedule.get(d, [])) for d in range(7)}
    return sv.WeekRecord(
        week_num=week,
        phase="Base",
        step_type="Base",
        role="build",
        block="race",
        target_tss=100.0,
        status="OPTIMAL",
        solved=True,
        schedule=full,
        limits=limits or SafetyLimits(),
        cfg=cfg or MesoCycleTargetsAndConstraints(daily_time_caps=[300.0] * 7, weekday_single_workout_cap=300.0),
        carry=carry,
    )


# ----------------------------------------------------------------------------- helpers & table accessors

def test_brick_run_accessors_and_sw_t1_exclusion():
    brick = _wk(
        0, "BR_45_15", "Brick", 60.0, tss=55.0, mech=25.0,
        brick_run_minutes=15.0, brick_run_strain=12.0, brick_run_hard=False,
    )
    sw_brick = _wk(
        1, "SW_T1_BRICK", "Brick", 75.0, tss=80.0, mech=35.2,
        brick_run_minutes=0.0, brick_run_strain=0.0, brick_run_hard=False,
        is_hard_endurance=True,
    )
    assert run_minutes(brick) == 15.0 and run_strain(brick) == 12.0
    assert run_minutes(sw_brick) == 0.0 and run_strain(sw_brick) == 0.0

    tab = WorkoutTable([brick, sw_brick])
    assert tab.run_minutes[0] == 15.0 and tab.run_strain[0] == 12.0
    assert tab.run_minutes[1] == 0.0 and tab.run_strain[1] == 0.0
    assert tab.brick_ids("Bike") == [0] and tab.brick_ids("Run") == [0]
    assert tab.brick_ids("Swim") == []
    assert tab.members("Run") == [0] and tab.members("Bike") == [0]

    S = classify_workouts([brick, sw_brick])
    assert S.runs == [0]
    assert S.heavy_or_long_runs == []


# ----------------------------------------------------------------------------- Tier-0 & load-dynamics bounds on Brick run leg

def test_brick_run_leg_bounded_by_run_minutes_cap():
    # Carry-in run_minutes_cap=10 allows R10 (10m) but forbids Brick with 15m run leg.
    brick = _wk(
        0, "BR_15", "Brick", 60.0, tss=80.0, mech=25.0,
        brick_run_minutes=15.0, brick_run_strain=12.0, is_anchor=True,
    )
    r10 = _wk(1, "R10", "Run", 10.0, tss=10.0, mech=8.0)
    b60 = _wk(2, "B60", "Bike", 60.0, tss=50.0, mech=5.0)
    ls = LoadState(run_acute=0.0, run_chronic=20.0, total_ctl=30.0, run_ctl=10.0,
                   acwr_max=1.2, acwr_min=0.0, acwr_eps=0.0, run_minutes_cap=10.0, reentry=True)
    c = _cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (1, 7), "Run": (1, 7), "Strength": (0, 0)})
    _, plans = _solve([brick, r10, b60], c, carry_in=WeekCarryIn(load=ls))
    chosen = [w.code for d in range(7) for w in plans[0]["schedule"][d]]
    assert "BR_15" not in chosen and "R10" in chosen


def test_brick_run_leg_bounded_by_weekly_run_mech_strain_cap():
    brick = _wk(
        0, "BR_STRAIN", "Brick", 60.0, tss=80.0, mech=40.0,
        brick_run_minutes=15.0, brick_run_strain=30.0, is_anchor=True,
    )
    r15 = _wk(1, "R15", "Run", 15.0, tss=15.0, mech=10.0)
    b60 = _wk(2, "B60", "Bike", 60.0, tss=50.0, mech=5.0)
    c = _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (1, 7), "Run": (1, 7), "Strength": (0, 0)},
        max_weekly_run_mech_strain=20.0,
    )
    _, plans = _solve([brick, r15, b60], c)
    chosen = [w.code for d in range(7) for w in plans[0]["schedule"][d]]
    assert "BR_STRAIN" not in chosen and "R15" in chosen


def test_brick_run_leg_bounded_by_in_model_acwr_ceiling():
    # Acute is already right under 1.2 * chronic; adding brick_run_strain=80 breaches ACWR ceiling on any day.
    brick = _wk(
        0, "BR_ACWR", "Brick", 60.0, tss=80.0, mech=90.0,
        brick_run_minutes=15.0, brick_run_strain=80.0, is_anchor=True,
    )
    r10 = _wk(1, "R10", "Run", 10.0, tss=8.0, mech=2.0)
    ls = LoadState(run_acute=23.9, run_chronic=20.0, total_ctl=30.0, run_ctl=10.0,
                   acwr_max=1.20, acwr_min=0.0, acwr_eps=0.0)
    c = _cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (1, 7), "Strength": (0, 0)})
    _, plans = _solve([brick, r10], c, carry_in=WeekCarryIn(load=ls))
    chosen = [w.code for d in range(7) for w in plans[0]["schedule"][d]]
    assert "BR_ACWR" not in chosen and "R10" in chosen


def test_brick_run_leg_bounded_by_single_run_share():
    # 4 run sessions total: Brick (run leg 50m) + 3 x 20m runs => 50 / 110 = 45.5% > 32%.
    # With Run min=4 (and BR_BIG attractive on TSS), Tier 0 must reject BR_BIG and pick R1..R4.
    big_brick = _wk(
        0, "BR_BIG", "Brick", 90.0, tss=120.0, mech=50.0,
        brick_run_minutes=50.0, brick_run_strain=20.0,
    )
    runs = [_wk(i, f"R{i}", "Run", 20.0, tss=15.0, mech=5.0) for i in range(1, 5)]
    c = _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (4, 4), "Strength": (0, 0)},
        target_weekly_tss=160.0,
    )
    _, plans = _solve([big_brick] + runs, c)
    chosen = [w.code for d in range(7) for w in plans[0]["schedule"][d]]
    assert "BR_BIG" not in chosen and len(chosen) == 4


def test_brick_counts_toward_bike_and_run_freq_and_cannot_share_day_with_bike_or_run():
    # Only Brick + Bike + Run: Bike and Run min = 1, max = 1 => choosing Brick satisfies both and forbids Bike/Run.
    brick = _wk(
        0, "BR_45_15", "Brick", 60.0, tss=55.0, mech=25.0,
        brick_run_minutes=15.0, brick_run_strain=12.0, is_anchor=True,
    )
    bike = _wk(1, "B45", "Bike", 45.0, tss=35.0, mech=4.0)
    run = _wk(2, "R20", "Run", 20.0, tss=18.0, mech=10.0)
    c = _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (1, 1), "Run": (1, 1), "Strength": (0, 0)},
        target_weekly_tss=60.0,
    )
    _, plans = _solve([brick, bike, run], c)
    p = plans[0]
    chosen = [w.code for d in range(7) for w in p["schedule"][d]]
    assert chosen == ["BR_45_15"] and p["relaxations"] == []

    # When max=2 and Brick is scheduled, it still cannot share the same day as B45 or R20.
    c2 = _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (1, 2), "Run": (1, 2), "Strength": (0, 0)},
        target_weekly_tss=120.0,
    )
    _, plans2 = _solve([brick, bike, run], c2)
    b_day = _day_of(plans2[0], "BR_45_15")
    assert all(w.sport not in ("Bike", "Run") for w in plans2[0]["schedule"][b_day] if w.code != "BR_45_15")


# ----------------------------------------------------------------------------- heavy-run Brick separation & HEUR-004

def test_hard_run_brick_enforces_48h_separation_and_deadlift_lockout_and_boundary():
    hard_brick = _wk(
        0, "BR_HARD", "Brick", 60.0, tss=68.0, mech=31.0,
        z=(10.0, 35.0, 15.0, 0.0, 0.0),
        is_hard_endurance=True, is_anchor=True,
        brick_run_minutes=20.0, brick_run_strain=27.6, brick_run_hard=True,
    )
    heavy_run = _wk(
        1, "R_HARD", "Run", 22.0, tss=32.0, mech=28.0,
        z=(2.0, 5.0, 15.0, 0.0, 0.0),
        is_hard_endurance=True, is_heavy_run=True, is_anchor=True, flags={"heavy_run": 1},
    )
    dl = _wk(
        2, "STR_HV2_DEADLIFT", "Strength", 45.0, tss=38.0, mech=45.0,
        is_heavy_strength=True, is_anchor=True, flags={"heavy_dl": 1, "heavy_sq": 0},
    )
    S = classify_workouts([hard_brick, heavy_run, dl])
    assert 0 in S.heavy_or_long_runs and 0 in S.runs

    c = _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (1, 7)},
        required_heavy_strength_sessions=1,
        two_session_windows=[False] * 7,
        intensity_low_min=0.0, intensity_mod_high_max=1.0,
    )
    _, plans = _solve([hard_brick, heavy_run, dl], c)
    d_br = _day_of(plans[0], "BR_HARD")
    d_hr = _day_of(plans[0], "R_HARD")
    d_dl = _day_of(plans[0], "STR_HV2_DEADLIFT")
    assert abs(d_br - d_hr) >= 2
    assert abs(d_br - d_dl) >= 2

    # Sunday -> Monday boundary carry-in: deadlift on Sunday bans hard-run Brick on Monday (day 0).
    ci_dl_sun = WeekCarryIn(day_m1=DayLoad.from_workouts([dl]))
    assert ci_dl_sun.day_m1.deadlift
    _, plans_b = _solve([hard_brick], _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (0, 7)},
        intensity_low_min=0.0, intensity_mod_high_max=1.0,
    ), carry_in=ci_dl_sun)
    assert _day_of(plans_b[0], "BR_HARD") >= 1

    # Hard-run Brick on Sunday carries heavy_run=True into Monday and bans deadlift on Monday (day 0).
    ci_br_sun = WeekCarryIn(day_m1=DayLoad.from_workouts([hard_brick]))
    assert ci_br_sun.day_m1.heavy_run
    _, plans_c = _solve([dl], _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (1, 7)},
        required_heavy_strength_sessions=1,
        two_session_windows=[False] * 7,
    ), carry_in=ci_br_sun)
    assert _day_of(plans_c[0], "STR_HV2_DEADLIFT") >= 1


# ----------------------------------------------------------------------------- independent checker (season_validation)

def test_season_validation_catches_brick_run_leg_violations():
    big_brick = _wk(
        0, "BR_BIG", "Brick", 120.0, tss=140.0, mech=55.0,
        brick_run_minutes=70.0, brick_run_strain=45.0, brick_run_hard=True,
    )
    small_runs = [_wk(i, f"R{i}", "Run", 20.0, tss=15.0, mech=8.0) for i in range(1, 4)]

    # 1. run_share (70 / 130 = 53.8% > 32%)
    r_share = _rec({0: [big_brick], 2: [small_runs[0]], 4: [small_runs[1]], 6: [small_runs[2]]})
    assert "run_share" in {x.rule for x in sv.check_week(r_share)}

    # 2. long_run_abs (< 4 runs, cap 45m, Brick run leg 70m)
    lim_abs = SafetyLimits(reentry_long_run_max_min=45.0)
    r_abs = _rec({0: [big_brick], 2: [small_runs[0]]}, limits=lim_abs)
    assert "long_run_abs" in {x.rule for x in sv.check_week(r_abs)}

    # 3. run_acwr, run_minutes_guard, ctl_ramp (Brick TSS counts in CTL ramp)
    ls = LoadState(
        run_acute=0.0, run_chronic=0.0, total_ctl=0.0, run_ctl=0.0,
        acwr_max=1.2, acwr_min=0.8, acwr_eps=0.0, ctl_ramp_max=1.0, run_minutes_cap=50.0,
    )
    r_load = _rec({1: [big_brick]}, carry=WeekCarryIn(load=ls))
    got = {x.rule for x in sv.check_week(r_load)}
    assert {"run_acwr", "run_minutes_guard", "ctl_ramp"} <= got

    # 4. deadlift_run_lockout
    dl = _wk(9, "STR_HV2_DEADLIFT", "Strength", 45.0, is_heavy_strength=True, flags={"heavy_dl": 1})
    r_dl = _rec({1: [dl], 2: [big_brick]})
    assert "deadlift_run_lockout" in {x.rule for x in sv.check_week(r_dl)}


# ----------------------------------------------------------------------------- schema & canonical library backfill

def test_canonical_brick_rows_and_schema_validation():
    lib = load_library().set_index("code")
    assert lib.loc["SW_T1_BRICK", "brick_run_min"] == 0.0
    assert lib.loc["SW_T1_BRICK", "brick_run_strain"] == 0.0
    assert not bool(lib.loc["SW_T1_BRICK", "is_brick_run_hard"])
    assert lib.loc["RUN_TB1_BRICK", "brick_run_min"] == 15.0
    assert bool(lib.loc["RUN_TB1_BRICK", "is_brick_run_hard"])
    assert lib.loc["BR4", "brick_run_min"] == 30.0
    assert bool(lib.loc["BR4", "is_brick_run_hard"])
    assert not bool(lib.loc["BR1", "is_brick_run_hard"])

    # Non-Brick workout cannot carry brick-run fields.
    rec = load_library().set_index("code").loc["CRe1"].to_dict()
    rec["code"] = "CRe1"
    rec["brick_run_min"] = 10.0
    with pytest.raises(ValidationError, match="brick-run field"):
        Workout(**{k: ((None if k == "distance" else "") if (isinstance(v, float) and pd.isna(v)) else v)
                   for k, v in rec.items()})

    # Brick with zero run minutes cannot carry strain or hard flag.
    brec = load_library().set_index("code").loc["SW_T1_BRICK"].to_dict()
    brec["code"] = "SW_T1_BRICK"
    brec["is_brick_run_hard"] = True
    with pytest.raises(ValidationError, match="zero brick_run_min"):
        Workout(**{k: ((None if k == "distance" else "") if (isinstance(v, float) and pd.isna(v)) else v)
                   for k, v in brec.items()})

    loads = brick_run_loads([0.0, 10.0, 0.0, 5.0, 0.0])
    assert loads["brick_run_min"] == 15.0 and loads["is_brick_run_hard"] is True


def test_brick_credited_in_ctl_ramp_and_meso_sport_split_goals():
    # CTL ramp includes Brick TSS: a 500-TSS Brick breaches a 1.0 CTL ramp cap.
    huge_brick = _wk(
        0, "BR_CTL", "Brick", 60.0, tss=500.0, mech=10.0, ans=10.0,
        brick_run_minutes=15.0, brick_run_strain=5.0, is_anchor=True,
    )
    small_bike = _wk(1, "B20", "Bike", 20.0, tss=10.0, mech=2.0, ans=5.0)
    ls = LoadState(run_acute=0.0, run_chronic=20.0, total_ctl=0.0, run_ctl=20.0,
                   acwr_max=1.25, acwr_min=0.0, acwr_eps=0.0, ctl_ramp_max=1.0)
    c = _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (1, 7), "Run": (0, 7), "Strength": (0, 0)},
        max_48h_aerobic_tss=1000.0,
    )
    _, plans = _solve([huge_brick, small_bike], c, carry_in=WeekCarryIn(load=ls))
    chosen = [w.code for d in range(7) for w in plans[0]["schedule"][d]]
    assert "BR_CTL" not in chosen and "B20" in chosen

    # Meso goal rows credit Brick's bike/run minutes (45m / 15m) and proportional TSS (75% / 25%) to Bike and Run.
    brick = _wk(
        0, "BR_45_15", "Brick", 60.0, tss=60.0, mech=20.0,
        brick_run_minutes=15.0, brick_run_strain=12.0, is_anchor=True,
    )
    c_split = _cfg(
        sport_freq_bounds={"Swim": (0, 0), "Bike": (1, 1), "Run": (1, 1), "Strength": (0, 0)},
        target_weekly_tss=60.0,
        target_sport_duration_splits={"Swim": 0.0, "Bike": 0.75, "Run": 0.25, "Strength": 0.0},
        target_bike_run_tss_splits={"Bike": 0.75, "Run": 0.25},
        target_zone_pcts={1: 0.4, 2: 0.6, 3: 0.0, 4: 0.0, 5: 0.0},
        w_durability=0.0, w_friction=0.0,
    )
    _, plans_split = _solve([brick], c_split)
    assert plans_split[0]["objective"] == pytest.approx(0.0, abs=1e-6)
