"""TASK-018 (ADR-019): cross-week carry-in constants and in-model linearised ACWR / CTL ramp / run-minutes rows."""
import pytest

from triplan.control.carry import acwr_eps_floor, load_state_for_week
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.physiology.state import ALPHA_ACUTE, ALPHA_CHRONIC, ALPHA_CTL
from triplan.solver.engine import hybrid_options
from triplan.solver.engine.carry_in import DayLoad, LoadState, WeekCarryIn
from triplan.solver.engine.engine import UnifiedWeekEngine
from triplan.solver.scheduler import TriathlonMILPScheduler

from tests.test_safety_constraints import cfg, wk
from tests.test_selection_cuts import _hy, _sym_lib

DAY0_ONLY = [120.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]   # every session must land on day 0 (cap 0 elsewhere)


def _solve(lib, c, carry=None):
    s = TriathlonMILPScheduler(lib, c, carry_in=carry)
    plans = s.solve_top_k_plans(k_plans=1, time_limit=10.0)
    assert plans, s.last_diagnostics
    return plans[0]


def _codes(plan, day=None):
    days = range(7) if day is None else [day]
    return sorted(w.code for d in days for w in plan["schedule"][d])


def _day0_cfg(**kw):
    return cfg(daily_time_caps=list(DAY0_ONLY), weekday_single_workout_cap=120.0, intensity_low_min=0.0, intensity_mod_high_max=1.0, **kw)


def _state(acute, chronic, rho=1.2, eps=0.0, **kw):
    return LoadState(run_acute=acute, run_chronic=chronic, total_ctl=kw.pop("ctl", 0.0), acwr_max=rho,
                     acwr_min=kw.pop("floor", 0.0), acwr_eps=eps, **kw)


# ----------------------------------------------------------------------------- boundary constants

def test_day_load_from_workouts_uses_the_safety_classification():
    hard = wk(0, "T", "Bike", 60, z=(15.0, 25.0, 0.0, 20.0, 0.0), tss=80.0, mech=0.0, ans=30.0)
    dl = wk(1, "DL", "Strength", 45, is_heavy_strength=True, flags={"heavy_dl": 1})
    lr = wk(2, "LR", "Run", 60, flags={"long_run": 1}, mech=50.0)
    assert DayLoad.from_workouts([]).is_empty
    d = DayLoad.from_workouts([hard, dl, lr])
    assert d.hard and d.heavy_run and d.deadlift and d.heavy_strength and d.strength
    assert d.aerobic_tss == pytest.approx(120.0) and d.mech_strain == pytest.approx(60.0)
    assert d.ans_cost == pytest.approx(50.0)


def test_day0_hard_is_blocked_after_hard_day_minus_1():
    lib = [wk(0, "HARD", "Bike", 60, tss=80.0, mech=0.0, z=(15.0, 25.0, 0.0, 20.0, 0.0)), wk(1, "EASY", "Bike", 60, tss=20.0)]
    c = _day0_cfg(target_weekly_tss=80.0)
    assert _codes(_solve(lib, c), 0) == ["HARD"]                       # control: the hard day is wanted on day 0
    carry = WeekCarryIn(day_m1=DayLoad(hard=True))
    plan = _solve(lib, c, carry)
    assert "HARD" not in _codes(plan) and _codes(plan, 0) == ["EASY"]


def test_day0_heavy_run_and_deadlift_blocked_by_day_minus_1_neighbours():
    lib = [wk(0, "LONG", "Run", 60, tss=60.0, flags={"long_run": 1}), wk(1, "EASY", "Run", 60, tss=20.0),
           wk(2, "DL", "Strength", 40, tss=60.0, is_heavy_strength=True, flags={"heavy_dl": 1})]
    c = _day0_cfg(target_weekly_tss=60.0)
    assert _codes(_solve(lib, c), 0) in (["LONG"], ["DL"])
    for prev in (DayLoad(heavy_run=True), DayLoad(deadlift=True)):      # run -> no run/deadlift; deadlift -> no run
        plan = _solve(lib, c, WeekCarryIn(day_m1=prev))
        assert "LONG" not in _codes(plan)
        if prev.heavy_run:
            assert "DL" not in _codes(plan)


def test_non_consecutive_strength_includes_the_boundary():
    lib = [wk(0, "ST", "Strength", 40, tss=60.0), wk(1, "EASY", "Bike", 40, tss=20.0)]
    c = _day0_cfg(target_weekly_tss=60.0)
    assert _codes(_solve(lib, c), 0) == ["ST"]
    assert "ST" not in _codes(_solve(lib, c, WeekCarryIn(day_m1=DayLoad(strength=True))))


@pytest.mark.parametrize("metric,attr,cap", [("aerobic_tss", "tss", 295.0), ("ans_cost", "ans", 700.0),
                                              ("mech_strain", "mech", 260.0)])
def test_rolling_48h_cap_counts_day_minus_1_load(metric, attr, cap):
    big = {"tss": 150.0, "mech": 10.0, "ans": 10.0}
    big[attr] = {"tss": 150.0, "mech": 150.0, "ans": 400.0}[attr]
    lib = [wk(0, "BIG", "Bike", 60, tss=big["tss"], mech=big["mech"], ans=big["ans"]),
           wk(1, "SMALL", "Bike", 60, tss=20.0, mech=5.0, ans=5.0)]
    c = _day0_cfg(target_weekly_tss=150.0)
    assert _codes(_solve(lib, c), 0) == ["BIG"]
    prev = DayLoad(**{metric: cap - big[attr] + 10.0})                  # leaves 10 less than BIG needs
    assert _codes(_solve(lib, c, WeekCarryIn(day_m1=prev)), 0) == ["SMALL"]


# ----------------------------------------------------------------------------- empty carry-in == isolated week

def test_empty_carry_in_is_exactly_the_isolated_model(default_library):
    from triplan.model.candidates import MesoCycleTargetsAndConstraints
    c = MesoCycleTargetsAndConstraints()
    ref = TriathlonMILPScheduler(default_library, c)
    ref_plan = ref.solve_top_k_plans(1, 10.0)[0]
    for carry in (WeekCarryIn(), WeekCarryIn(day_m1=DayLoad(), day_m2=DayLoad(), load=None)):
        assert not carry.is_active
        s = TriathlonMILPScheduler(default_library, c, carry_in=carry)
        plan = s.solve_top_k_plans(1, 10.0)[0]
        assert plan["status"] == ref_plan["status"]
        assert plan["objective"] == pytest.approx(ref_plan["objective"], abs=1e-9)
        assert _codes(plan) == _codes(ref_plan)
        assert s.engine.build_model().model.num_rows == ref.engine.build_model().model.num_rows


def test_carry_in_adds_rows_only_when_active():
    lib = [wk(0, "R", "Run", 40, mech=30.0), wk(1, "B", "Bike", 40)]
    c = cfg()
    base = TriathlonMILPScheduler(lib, c).engine.build_model().model.num_rows
    boundary = TriathlonMILPScheduler(lib, c, WeekCarryIn(day_m1=DayLoad(hard=True))).engine.build_model().model.num_rows
    dyn = TriathlonMILPScheduler(lib, c, WeekCarryIn(load=_state(5, 5, ctl_ramp_max=7.0,
                                                                  run_minutes_cap=60.0))).engine.build_model().model.num_rows
    assert boundary == base                    # no hard workout in this library: nothing to ban, no row
    assert dyn > base


# ----------------------------------------------------------------------------- in-model dynamics

def _acwr_series(plan, a0, c0):
    out, a, ch = [], a0, c0
    for d in range(7):
        m = sum(w.mechanical_strain for w in plan["schedule"][d] if w.sport == "Run")
        a = ALPHA_ACUTE * m + (1 - ALPHA_ACUTE) * a
        ch = ALPHA_CHRONIC * m + (1 - ALPHA_CHRONIC) * ch
        out.append((a, ch))
    return out


def test_acwr_row_binds_in_a_constructed_ramp_case():
    # Steady state A = C = 10; a 40-strain run on day 0 would give A - 1.2 C = 3.2 > 0, a 20-strain run -0.3.
    lib = [wk(0, "BIGRUN", "Run", 60, tss=60.0, mech=40.0), wk(1, "SMALLRUN", "Run", 30, tss=30.0, mech=20.0)]
    c = _day0_cfg(target_weekly_tss=60.0)
    free = _solve(lib, c)
    assert _codes(free, 0) == ["BIGRUN"]
    assert max(a - 1.2 * ch for a, ch in _acwr_series(free, 10.0, 10.0)) > 0.0
    plan = _solve(lib, c, WeekCarryIn(load=_state(10.0, 10.0, rho=1.2, eps=0.0)))
    assert _codes(plan) == ["SMALLRUN"]
    assert all(a - 1.2 * ch <= 1e-6 for a, ch in _acwr_series(plan, 10.0, 10.0))


def test_acwr_eps_floor_admits_a_single_floor_session_from_a_detrained_state():
    lib = [wk(0, "RUN30", "Run", 30, tss=30.0, mech=20.0)]
    c = _day0_cfg(target_weekly_tss=30.0)
    rho = 1.2
    eps = acwr_eps_floor(lib, rho)
    assert eps == pytest.approx((ALPHA_ACUTE - rho * ALPHA_CHRONIC) * 20.0)
    assert _codes(_solve(lib, c, WeekCarryIn(load=_state(0.0, 0.0, rho=rho, eps=eps)))) == ["RUN30"]
    plan = _solve(lib, c, WeekCarryIn(load=_state(0.0, 0.0, rho=rho, eps=eps * 0.9)))
    assert _codes(plan) == []                                          # eps too small: the run is not admissible


def test_acwr_floor_is_a_named_tier1_waiver_not_infeasibility():
    # Chronic load far above anything schedulable: A can never reach 0.8 C within the week.
    lib = [wk(0, "RUN30", "Run", 30, tss=30.0, mech=20.0)]
    plan = _solve(lib, _day0_cfg(target_weekly_tss=30.0), WeekCarryIn(load=_state(0.0, 60.0, rho=1.2, eps=5.0,
                                                                                  floor=0.8)))
    assert plan["status"] in ("OPTIMAL", "TIME_LIMIT_FEASIBLE")
    assert [r for r in plan["relaxations"] if r["category"] == "relax_acwr_floor"]


def test_acwr_floor_not_waived_when_the_week_can_meet_it():
    lib = [wk(i, f"R{i}", "Run", 20, tss=20.0, mech=6.0) for i in range(6)]    # a run on every training day
    c = cfg(target_weekly_tss=120.0, sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (6, 7), "Strength": (0, 0)})
    plan = _solve(lib, c, WeekCarryIn(load=_state(5.0, 5.0, rho=1.2, eps=5.0, floor=0.8)))
    assert len(_codes(plan)) == 6
    assert not [r for r in plan["relaxations"] if r["category"] == "relax_acwr_floor"]


def test_ctl_ramp_row_caps_the_weekly_ctl_gain():
    lib = [wk(0, "B1", "Bike", 60, tss=100.0), wk(1, "B2", "Bike", 60, tss=100.0), wk(2, "B3", "Bike", 60, tss=100.0)]
    c = cfg(target_weekly_tss=300.0, daily_time_caps=[120.0] * 7, min_rest_days=1)

    def gain(plan):
        ctl = 10.0
        for d in range(7):
            ctl = ALPHA_CTL * sum(w.aerobic_tss for w in plan["schedule"][d]) + (1 - ALPHA_CTL) * ctl
        return ctl - 10.0

    free = _solve(lib, c)
    assert gain(free) > 4.0
    plan = _solve(lib, c, WeekCarryIn(load=_state(0.0, 0.0, ctl=10.0, ctl_ramp_max=4.0)))
    assert gain(plan) <= 4.0 + 1e-6 and gain(plan) < gain(free)


def test_run_minutes_guard_caps_weekly_run_minutes():
    lib = [wk(i, f"R{i}", "Run", 30, tss=30.0, mech=10.0) for i in range(4)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (0, 4), "Strength": (0, 0)},
            target_weekly_tss=120.0)
    assert len(_codes(_solve(lib, c))) == 4
    plan = _solve(lib, c, WeekCarryIn(load=_state(5.0, 5.0, rho=2.0, eps=50.0, run_minutes_cap=75.0)))
    assert len(_codes(plan)) == 2                                      # 75 min fits two 30 min runs, not three


# ----------------------------------------------------------------------------- symmetry

def test_symmetry_row_is_disabled_with_active_carry_in():
    plain = UnifiedWeekEngine(_sym_lib(), _hy(), hybrid_options())
    plain.solve(1, 10.0)
    assert plain.symmetry_row is True
    empty = UnifiedWeekEngine(_sym_lib(), _hy(), hybrid_options(), carry_in=WeekCarryIn())
    empty.solve(1, 10.0)
    assert empty.symmetry_row is True                                  # an empty carry-in changes nothing
    for carry in (WeekCarryIn(day_m1=DayLoad(strength=True)), WeekCarryIn(load=_state(5.0, 5.0))):
        eng = UnifiedWeekEngine(_sym_lib(), _hy(), hybrid_options(), carry_in=carry)
        eng.solve(1, 10.0)
        assert eng.symmetry_row is False


# ----------------------------------------------------------------------------- profile seeding and the season loop

def test_profile_seeds_per_sport_ctl_and_run_ctl_gates_the_acwr_cap():
    st = CURRENT_ATHLETE.to_athlete_state()
    assert st.run_ctl == CURRENT_ATHLETE.starting_run_ctl < CURRENT_ATHLETE.reentry_run_ctl_exit
    assert st.bike_ctl + st.run_ctl + st.swim_ctl == pytest.approx(CURRENT_ATHLETE.starting_ctl)
    ls = load_state_for_week(st, CURRENT_ATHLETE, 0.0, [wk(0, "R", "Run", 30, mech=20.0)])
    assert ls.acwr_max == CURRENT_ATHLETE.max_running_acwr_reentry == 1.20
    assert ls.run_minutes_cap == max(CURRENT_ATHLETE.reentry_run_minutes_floor,
                                     CURRENT_ATHLETE.max_weekly_run_minutes_increase)
    st.run_ctl = CURRENT_ATHLETE.reentry_run_ctl_exit
    after = load_state_for_week(st, CURRENT_ATHLETE, 90.0, [wk(0, "R", "Run", 30, mech=20.0)])
    assert after.acwr_max == CURRENT_ATHLETE.max_running_acwr == 1.25 and after.run_minutes_cap is None


@pytest.fixture(scope="module")
def olympic_season():
    import contextlib
    import io
    from triplan.control.season import generate_closed_loop_season_schedule
    with contextlib.redirect_stdout(io.StringIO()):
        return generate_closed_loop_season_schedule(distance="olympic")[1]


def test_season_respects_the_in_model_acwr_cap_every_week(olympic_season):
    s = olympic_season
    assert len(s) == 40 and set(s["Solve_Status"]) <= {"OPTIMAL", "TIME_LIMIT_FEASIBLE"}
    # every day of every week: A - rho * C <= epsilon_floor (the linearised row, evaluated on the simulated state)
    assert (s["Run_ACWR_Excess"] <= s["Run_ACWR_Eps"] + 1e-6).all(), s[["Week", "Run_ACWR_Excess", "Run_ACWR_Eps"]]
    assert (s["Run_ACWR_Cap"] <= 1.25).all() and (s.loc[s["Run_CTL"] < 15, "Run_ACWR_Cap"] <= 1.20).all()


def test_season_run_minutes_guard_and_ctl_ramp(olympic_season):
    s = olympic_season
    prev = 0.0
    for _, r in s.iterrows():
        assert r["Run_Minutes"] <= max(60.0, prev + 15.0) + 1e-6, f"week {r['Week']}"
        prev = r["Run_Minutes"]
    ctl = [CURRENT_ATHLETE.starting_ctl] + list(s["Ending_CTL"])
    assert all(b - a <= CURRENT_ATHLETE.max_weekly_ctl_ramp + 0.15 for a, b in zip(ctl, ctl[1:]))
