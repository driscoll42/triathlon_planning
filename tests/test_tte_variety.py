"""TASK-023 (ADR-036): TTE progression goal, multi-week variety penalty and their carry-in hand-off."""
import pytest

from triplan.control.carry import VARIETY_MEMORY_WEEKS, next_week_carry_in, week_key_codes, week_tte_min
from triplan.model.candidates import MesoCycleTargetsAndConstraints
from triplan.physiology.state import AthletePhysiologicalState
from triplan.solver.engine.carry_in import WeekCarryIn
from triplan.solver.engine.variety import recency_weights, variety_vector
from triplan.solver.engine import session_quality as sq
from triplan.solver.pruning import prune_dominated_candidates
from triplan.solver.scheduler import TriathlonMILPScheduler

from tests.test_safety_constraints import cfg, wk

ONE_DAY = [120.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def _cfg(**kw):
    return cfg(daily_time_caps=list(ONE_DAY), weekday_single_workout_cap=120.0, intensity_low_min=0.0,
               intensity_mod_high_max=1.0, intensity_low_target=0.0, target_weekly_tss=40.0, w_zone=0.0, w_sport_dur=0.0, w_sport_tss=0.0, **kw)


def _hard(i, code, t_thr=20.0, pen=0.1, tss=40.0, **kw):
    """A 60 min Z4-heavy bike (a hard / key session) with ``t_thr`` threshold minutes."""
    return wk(i, code, "Bike", 60, tss=tss, mech=0.0, z=(20.0, 20.0, 0.0, 20.0, 0.0), preference_penalty=pen,
              t_thr_min=t_thr, t_cont_min=t_thr, **kw)


def _easy(i, code, pen=0.1, tss=40.0):
    return wk(i, code, "Bike", 60, tss=tss, mech=0.0, preference_penalty=pen)


def _codes(lib, c, carry=None):
    s = TriathlonMILPScheduler(lib, c, carry_in=carry)
    plans = s.solve_top_k_plans(k_plans=1, time_limit=10.0)
    assert plans, s.last_diagnostics
    return sorted(w.code for d in range(7) for w in plans[0]["schedule"][d]), s


# ----------------------------------------------------------------------------- TTE progression goal

def test_progress_mode_pulls_in_the_session_that_meets_the_growth_target():
    lib = [_easy(0, "EASY", pen=0.1), _hard(1, "THR", t_thr=20.0, pen=0.5)]
    assert _codes(lib, _cfg())[0] == ["EASY"]                                          # friction alone prefers EASY
    c = _cfg(tte_mode="progress")
    assert _codes(lib, c, WeekCarryIn())[0] == ["EASY"]                                # no reference -> no goal
    codes, s = _codes(lib, c, WeekCarryIn(tte_ref_min=20.0, prev_tte_min=20.0))
    assert codes == ["THR"]                                                            # T >= 1.05 * 20 -> shortfall costs more
    assert "tte" in s.engine.last_context.goals


def test_deload_mode_caps_threshold_minutes_against_the_previous_week():
    lib = [_hard(0, "THR", t_thr=40.0, pen=0.1), _easy(1, "EASY", pen=0.5)]
    assert _codes(lib, _cfg())[0] == ["THR"]
    codes, _ = _codes(lib, _cfg(tte_mode="deload"), WeekCarryIn(prev_tte_min=50.0, tte_ref_min=50.0))
    assert codes == ["EASY"]                                                           # 40 > 0.6 * 50 = 30 -> overshoot costs
    codes, _ = _codes(lib, _cfg(tte_mode="deload"), WeekCarryIn(prev_tte_min=100.0))
    assert codes == ["THR"]                                                            # 40 <= 60 -> no pressure


def test_the_goal_is_soft_not_a_hard_row():
    lib = [_easy(0, "EASY")]                       # no session can meet the target; the week must still solve
    codes, s = _codes(lib, _cfg(tte_mode="progress"), WeekCarryIn(tte_ref_min=30.0))
    assert codes == ["EASY"]                       # solved (a plan exists) with the shortfall paid in the objective


def test_isolated_week_has_no_tte_or_variety_columns():
    lib = [_easy(0, "EASY"), _hard(1, "THR")]
    _, s = _codes(lib, _cfg(tte_mode="progress"))
    assert "tte" not in s.engine.last_context.goals and not s.engine.last_context.variety.any()


# ----------------------------------------------------------------------------- variety penalty

def test_recently_used_key_session_is_penalised_in_favour_of_an_equal_alternative():
    lib = [_hard(0, "A"), _hard(1, "B")]
    for used, other in (("A", "B"), ("B", "A")):                                       # whichever was used last week loses
        assert _codes(lib, _cfg(), WeekCarryIn(recent_key_codes=((used,),)))[0] == [other]
    tied = WeekCarryIn(recent_key_codes=(("A",),))
    assert _codes(lib, _cfg(w_variety=0.0), tied)[0] == _codes(lib, _cfg())[0]        # weight 0 = no memory


def test_penalty_fades_with_age_and_ignores_easy_sessions_and_anchors():
    params = sq.SessionQualityParams()
    rec = recency_weights(WeekCarryIn(recent_key_codes=(("A",), ("B",), ("A",))), window=3)
    assert rec == {"A": pytest.approx(1.0 + 1 / 3), "B": pytest.approx(2 / 3)}
    assert recency_weights(WeekCarryIn(recent_key_codes=(("A",), ("B",))), window=1) == {"A": 1.0}
    lib = [_hard(0, "A"), _easy(1, "E"), _hard(2, "ANCH", is_anchor=True)]
    v = variety_vector(lib, {"A": 1.0, "E": 1.0, "ANCH": 1.0}, params)
    assert list(v) == [1.0, 0.0, 0.0]                                                  # only the non-anchor key session


def test_dominance_pruning_keeps_the_candidate_with_the_lower_variety_cost():
    twins = [_hard(i, f"T{i}") for i in range(4)]                                      # identical but for the code
    bounds = {"Bike": (0, 2)}                                                          # cap = 2 kept copies
    kept, _ = prune_dominated_candidates(twins, bounds)
    assert [w.code for w in kept] == ["T0", "T1"]
    penal = {"T0": 1.0, "T1": 1.0}
    kept, _ = prune_dominated_candidates(twins, bounds, extra_cost=lambda w: penal.get(w.code, 0.0))
    assert sorted(w.code for w in kept) == ["T2", "T3"]


# ----------------------------------------------------------------------------- carry-in hand-off

def _schedule(*workouts_by_day):
    return {d: list(ws) for d, ws in enumerate(workouts_by_day)} | {d: [] for d in range(len(workouts_by_day), 7)}


def _carry_after(prev, schedule, deload=False):
    return next_week_carry_in(prev, schedule, AthletePhysiologicalState(), None, [], 100.0, deload=deload)


def test_next_week_carry_in_records_threshold_minutes_and_key_codes():
    sched = _schedule([_hard(0, "A", t_thr=20.0)], [_easy(1, "E")], [_hard(2, "B", t_thr=12.0)])
    assert week_tte_min(sched) == 32.0 and week_key_codes(sched) == ("A", "B")
    c1 = _carry_after(WeekCarryIn(), sched)
    assert (c1.prev_tte_min, c1.tte_ref_min, c1.recent_key_codes) == (32.0, 32.0, (("A", "B"),))
    # a deload week updates the previous-week value but keeps the progression reference of the last build week
    c2 = _carry_after(c1, _schedule([_hard(0, "A", t_thr=6.0)]), deload=True)
    assert (c2.prev_tte_min, c2.tte_ref_min) == (6.0, 32.0) and c2.recent_key_codes[0] == ("A",)
    # an empty (failed) week does not reset the reference
    assert _carry_after(c2, _schedule()).tte_ref_min == 32.0


def test_key_memory_is_capped_and_newest_first():
    c = WeekCarryIn()
    for k in range(VARIETY_MEMORY_WEEKS + 3):
        c = _carry_after(c, _schedule([_hard(0, f"K{k}")]))
    assert len(c.recent_key_codes) == VARIETY_MEMORY_WEEKS
    assert c.recent_key_codes[0] == (f"K{VARIETY_MEMORY_WEEKS + 2}",)


def test_meso_config_carries_the_new_knobs_with_documented_defaults():
    c = MesoCycleTargetsAndConstraints()
    assert (c.tte_mode, c.tte_growth, c.tte_deload_ratio, c.variety_window, c.walkjog_stage) == ("off", 0.05, 0.60, 3, 0)
    assert c.variety_window <= VARIETY_MEMORY_WEEKS
