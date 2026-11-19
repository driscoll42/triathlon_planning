"""TASK-022 (ADR-035): ODI^run = EWMA_35(run strain) / EWMA_120(run strain), right-sign feedback, in-model ceiling row."""
import math

import pytest

from triplan.control.carry import GLOBAL_ODI_MAX, MASTERS_ODI_MAX, load_state_for_week, odi_eps_floor
from triplan.control.mpc import ClosedLoopMPCController
from triplan.model.candidates import MesoCycleTargetsAndConstraints
from triplan.perf import season_validation as sv
from triplan.physiology import state as st
from triplan.solver.engine.carry_in import LoadState, WeekCarryIn
from triplan.solver.scheduler import TriathlonMILPScheduler

from tests.test_carry_in import DAY0_ONLY, _codes, _day0_cfg, _solve
from tests.test_safety_constraints import wk
from tests.test_season_validation import rec


def _state(fast, slow, **kw):
    return st.AthletePhysiologicalState(run_strain_fast=fast, run_strain_slow=slow, **kw)


def _run_days(state, run_mech, days, tss=0.0):
    for _ in range(days):
        state.step_day(tss, run_mech, 0.0, 0.0, 0.0)
    return state


# ----------------------------------------------------------------------------- dimensional consistency

def test_steady_run_strain_reads_odi_one_whatever_the_other_sports_do():
    for tss in (0.0, 80.0, 400.0):                       # bike / swim / strength load is irrelevant to run ODI
        s = _run_days(_state(10.0, 10.0), 10.0, 200, tss=tss)
        assert s.orthopedic_decoupling_index == pytest.approx(1.0, abs=1e-9)


def test_all_sport_tss_no_longer_moves_odi():
    a, b = _state(10.0, 10.0), _state(10.0, 10.0)
    for _ in range(60):
        a.step_day(50.0, 12.0, 30.0, 20.0, 0.0)
        b.step_day(500.0, 12.0, 300.0, 200.0, 0.0)
    assert a.orthopedic_decoupling_index == pytest.approx(b.orthopedic_decoupling_index, rel=1e-12)


def test_fast_and_slow_ewma_use_the_exact_time_constants():
    s = _run_days(_state(0.0, 0.0), 10.0, 35)
    assert s.run_strain_fast == pytest.approx(10.0 * (1.0 - math.exp(-1.0)), rel=1e-9)
    s = _run_days(_state(0.0, 0.0), 10.0, 120)
    assert s.run_strain_slow == pytest.approx(10.0 * (1.0 - math.exp(-1.0)), rel=1e-9)
    s = _run_days(_state(0.0, 0.0, masters_mode=True), 10.0, 210)       # masters collagen tau = 210 d
    assert s.run_strain_slow == pytest.approx(10.0 * (1.0 - math.exp(-1.0)), rel=1e-9)


def test_step_up_reads_above_one_and_masters_read_higher():
    reg = _run_days(_state(10.0, 10.0), 20.0, 30)
    mas = _run_days(_state(10.0, 10.0, masters_mode=True), 20.0, 30)
    assert reg.orthopedic_decoupling_index > 1.0
    assert mas.orthopedic_decoupling_index > reg.orthopedic_decoupling_index   # slower collagen lags more


# ----------------------------------------------------------------------------- the controller has the right sign now

def test_cutting_run_strain_lowers_odi():
    """The old M(t) was all-sport TSS over a run-strain C(t): a run-strain cut lowered C(t) only, so ODI ROSE
    (1.13 -> 2.44 drift). With like units the fast EWMA falls first and ODI falls."""
    s = _run_days(_state(10.0, 10.0), 25.0, 28)                     # ramp
    peak = s.orthopedic_decoupling_index
    assert peak > 1.1
    prev = peak
    for _ in range(14):                                             # throttle run strain back to the base level
        s.step_day(300.0, 10.0, 200.0, 0.0, 0.0)
        assert s.orthopedic_decoupling_index < prev
        prev = s.orthopedic_decoupling_index
    assert prev < peak


def test_mpc_backstop_shrinks_the_cap_and_the_shrink_lowers_odi():
    state = _state(78.0, 48.0, running_acute_mech=40.0, running_chronic_mech=40.0)   # ODI 1.625 > 1.35
    mpc = ClosedLoopMPCController(state)
    base = MesoCycleTargetsAndConstraints(max_weekly_run_mech_strain=350.0)
    cut = mpc.process_weekly_execution_and_recalibrate([{"tss": 60.0, "run_mech": 40.0}] * 2, base)
    assert cut.max_weekly_run_mech_strain < 350.0
    assert base.max_weekly_run_mech_strain == 350.0
    assert cut.target_bike_run_tss_splits == {"Bike": 0.70, "Run": 0.30}
    # executing the throttled strain level for a week reduces the index
    before = state.orthopedic_decoupling_index
    _run_days(state, cut.max_weekly_run_mech_strain / 7.0 * 0.5, 7)
    assert state.orthopedic_decoupling_index < before


# ----------------------------------------------------------------------------- carry-in constants

def test_load_state_carries_odi_state_and_limits(default_library):
    s = _state(12.0, 9.0)
    ls = load_state_for_week(s, None, 0.0, default_library)
    assert (ls.odi_fast, ls.odi_slow) == (12.0, 9.0)
    assert ls.odi_max == GLOBAL_ODI_MAX == 1.35
    assert ls.odi_slow_alpha == pytest.approx(st.alpha_odi_slow(False))
    m = load_state_for_week(_state(12.0, 9.0, masters_mode=True), None, 0.0, default_library)
    assert m.odi_max == MASTERS_ODI_MAX == 1.20 and m.odi_slow_alpha == pytest.approx(st.alpha_odi_slow(True))


def test_odi_eps_floor_is_one_smallest_run_from_detrained():
    lib = [wk(0, "R1", "Run", 30, mech=20.0), wk(1, "R2", "Run", 60, mech=45.0), wk(2, "B", "Bike", 60, mech=0.0)]
    eps = odi_eps_floor(lib, 1.35, st.alpha_odi_slow(False))
    assert eps == pytest.approx((st.ALPHA_ODI_FAST - 1.35 * st.alpha_odi_slow(False)) * 20.0)
    assert odi_eps_floor([], 1.35, 0.01) == 0.0


# ----------------------------------------------------------------------------- in-model row

def _odi_state(fast, slow, rho=1.35, eps=0.0):
    return LoadState(run_acute=0.0, run_chronic=0.0, total_ctl=0.0, acwr_max=1e3, acwr_min=0.0,
                     odi_fast=fast, odi_slow=slow, odi_slow_alpha=st.alpha_odi_slow(False), odi_max=rho, odi_eps=eps)


def test_odi_row_blocks_a_run_that_would_breach_the_ceiling():
    f, s_ = st.ALPHA_ODI_FAST, st.alpha_odi_slow(False)
    big = 250.0                                                        # under the 260 rolling-48h strain cap
    # day-0 breach test: F0' = (1-f)F0 + f m, S0' = (1-s)S0 + s m, need F0' <= 1.35 S0'
    lib = [wk(0, "BIGRUN", "Run", 60, tss=60.0, mech=big), wk(1, "EASY", "Bike", 60, tss=30.0, mech=0.0)]
    c = _day0_cfg(target_weekly_tss=60.0, max_weekly_run_mech_strain=1e6)
    free = _solve(lib, c, WeekCarryIn(load=_odi_state(10.0, 10.0, rho=1e3)))
    assert "BIGRUN" in _codes(free)                                    # control: the row is off at rho = 1000
    assert (1 - f) * 10.0 + f * big > 1.35 * ((1 - s_) * 10.0 + s_ * big)
    capped = _solve(lib, c, WeekCarryIn(load=_odi_state(10.0, 10.0)))
    assert "BIGRUN" not in _codes(capped)                              # the 35 d EWMA would jump past 1.35 x the 120 d one


def _odi_row_names(lib, c, load):
    model = TriathlonMILPScheduler(lib, c, WeekCarryIn(load=load)).engine.build_model().model
    return [str(n) for n in model.row_names if n and str(n).startswith("odi_ceiling_d")]


def test_odi_row_admits_a_small_run():
    lib = [wk(0, "SMALL", "Run", 30, tss=30.0, mech=12.0)]
    plan = _solve(lib, _day0_cfg(target_weekly_tss=30.0), WeekCarryIn(load=_odi_state(10.0, 10.0)))
    assert "SMALL" in _codes(plan)


def test_odi_rows_are_emitted_only_when_they_can_bind():
    lib = [wk(0, "R", "Run", 45, tss=40.0, mech=40.0)]
    near = _odi_row_names(lib, _day0_cfg(max_weekly_run_mech_strain=350.0), _odi_state(10.0, 10.0))
    assert 1 <= len(near) <= 7                                          # a 350 weekly strain budget could breach 1.35
    slack = _odi_row_names(lib, _day0_cfg(max_weekly_run_mech_strain=20.0), _odi_state(10.0, 10.0))
    assert slack == []                                                  # 20 strain / week cannot lift F past 1.35 S
    assert _odi_row_names(lib, _day0_cfg(max_weekly_run_mech_strain=350.0), _odi_state(10.0, 10.0, rho=1e3)) == []
    hot = _odi_row_names(lib, _day0_cfg(max_weekly_run_mech_strain=20.0), _odi_state(30.0, 10.0))
    assert len(hot) == 7                                                # a start state far over 1.35 binds every day


def test_odi_rhs_is_clamped_so_an_already_overshooting_start_is_not_charged_to_the_week():
    # F0 well above 1.35 S0 (e.g. after an actuals import): the plan may not worsen it but must stay feasible with
    # a floor-sized run.
    eps = odi_eps_floor([wk(0, "R", "Run", 30, mech=12.0)], 1.35, st.alpha_odi_slow(False))
    lib = [wk(0, "SMALL", "Run", 30, tss=30.0, mech=12.0)]
    c = _day0_cfg(target_weekly_tss=30.0)
    plans = TriathlonMILPScheduler(lib, c, WeekCarryIn(load=_odi_state(30.0, 10.0, eps=eps))).solve_top_k_plans(1, 10.0)
    assert plans is not None


# ----------------------------------------------------------------------------- independent checker

def test_checker_flags_odi_breach_and_accepts_clean_week():
    ls = _odi_state(10.0, 10.0)
    big = wk(1, "BIGRUN", "Run", 60, tss=60.0, mech=250.0)
    small = wk(2, "SMALL", "Run", 30, tss=30.0, mech=12.0)
    bad = sv.check_week(rec({0: [big]}, carry=WeekCarryIn(load=ls)))
    assert "run_odi" in {v.rule for v in bad}
    ok = sv.check_week(rec({0: [small]}, carry=WeekCarryIn(load=ls)))
    assert "run_odi" not in {v.rule for v in ok}
