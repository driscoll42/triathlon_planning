"""Unit tests for the unified week engine foundation (TASK-015, ADR-017).

``WeekModel`` buffering/soft-goal math, and engine-level checks that each facade still returns its
documented plan keys.
"""
import highspy
import numpy as np
import pytest

from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.solver.engine import WeekModel, hybrid_options, meso_options, solve_week
from triplan.solver.engine.model import INF
from triplan.solver.highs_runtime import apply_threads
from triplan.solver.hybrid import HybridAthleteMILPOptimizer
from triplan.solver.scheduler import TriathlonMILPScheduler

ALL = [True] * 7


def _solve(model: WeekModel):
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    apply_threads(h)  # HiGHS locks one thread count per process (TASK-007)
    model.build(h)
    h.run()
    return h, np.array(h.getSolution().col_value)


# ----------------------------------------------------------------------------- WeekModel

def test_duplicate_columns_are_summed_sorted_and_zeros_dropped():
    m = WeekModel()
    a, b, c = (m.add_var(n, 0, 1, 0, True) for n in "abc")
    m.add_row({c: 1.0, a: 2.0, b: 0.0}, 0, 1)
    m.add_row(([c, a, c, b], [1.0, 2.0, 0.5, 1.0]), 0, 4)       # c appears twice -> 1.5
    m.add_row(([a, a, b], [1.0, -1.0, 3.0]), -INF, 2)            # a cancels to zero -> dropped
    starts, idx, val, rlb, rub = m.csr()
    assert list(starts) == [0, 2, 5]
    assert list(idx) == [a, c, a, b, c, b]
    ends = list(starts[1:]) + [len(idx)]
    rows = [(list(idx[s:e]), list(val[s:e])) for s, e in zip(starts, ends)]
    assert rows[0] == ([a, c], [2.0, 1.0])
    assert rows[1] == ([a, b, c], [2.0, 1.0, 1.5])
    assert rows[2] == ([b], [3.0])


def test_empty_row_policies():
    ok = WeekModel()
    assert ok.add_row({}, 0.0, 1.0) is None           # empty and satisfiable: dropped
    with pytest.raises(ValueError):
        ok.add_row({}, 1.0, 2.0)                      # empty and unsatisfiable: raise
    keep = WeekModel(empty_row_policy="keep")
    keep.add_var("x", 0.0, 1.0, 0.0, False)
    assert keep.add_row({}, 1.0, 2.0) == 0 and keep.num_rows == 1
    h, _ = _solve(keep)
    assert h.getModelStatus() == highspy.HighsModelStatus.kInfeasible


def test_bulk_build_matches_buffered_counts():
    m = WeekModel()
    cols = m.add_vars(12, 0, 1, cost=np.arange(12.0), integer=True)
    for k in range(5):
        m.add_row((cols[k:k + 4], np.ones(4)), 0, 2)
    m.add_row({int(cols[0]): 1.0}, 1, 1)
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    m.build(h)
    assert (h.getNumCol(), h.getNumRow()) == (m.num_cols, m.num_rows) == (12, 6)
    lp = h.getLp()
    assert list(lp.col_cost_) == list(np.arange(12.0))
    assert all(t == highspy.HighsVarType.kInteger for t in lp.integrality_)
    with pytest.raises(RuntimeError):
        m.add_var("late")


def test_soft_goal_deviation_math_is_dimensionless():
    # x in [0, 10]; goal: x vs target 3 with scale 4, weight 2, over-shoot 2x, under-shoot 6x.
    for forced, dev_pos, dev_neg in ((5.0, 2.0, 0.0), (1.0, 0.0, 2.0)):
        m = WeekModel()
        x = m.add_var("x", 0.0, 10.0, 0.0, False)
        g = m.add_soft_goal({x: 1.0}, target=3.0, scale=4.0, weight=2.0, name="g", pos_weight=2.0, neg_weight=6.0)
        m.add_row({x: 1.0}, forced, forced)
        h, sol = _solve(m)
        assert sol[g.pos] == pytest.approx(dev_pos) and sol[g.neg] == pytest.approx(dev_neg)
        expected = (2.0 / 4.0) * dev_pos + (6.0 / 4.0) * dev_neg
        assert h.getInfo().objective_function_value == pytest.approx(expected)
        assert g.pos_cost == pytest.approx(0.5) and g.neg_cost == pytest.approx(1.5)


def test_soft_goal_symmetric_weight_defaults_and_scale_independence():
    # Doubling the scale and the deviation together leaves the penalty unchanged.
    objs = []
    for scale, target, forced in ((10.0, 100.0, 120.0), (20.0, 200.0, 240.0)):
        m = WeekModel()
        x = m.add_var("x", 0.0, 1e4, 0.0, False)
        m.add_soft_goal({x: 1.0}, target, scale, 1.0, "g")
        m.add_row({x: 1.0}, forced, forced)
        h, _ = _solve(m)
        objs.append(h.getInfo().objective_function_value)
    assert objs[0] == pytest.approx(objs[1]) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        WeekModel().declare_soft_goal("bad", 0.0, 1.0)


def test_declared_goal_layouts_preserve_column_order():
    m = WeekModel()
    paired = m.declare_soft_goals(["a", "b"], [1.0, 1.0], 1.0)
    assert [(g.pos, g.neg) for g in paired] == [(0, 1), (2, 3)]
    m2 = WeekModel()
    blocked = m2.declare_soft_goals(["a", "b"], [1.0, 1.0], 1.0, layout="blocked")
    assert [(g.pos, g.neg) for g in blocked] == [(0, 2), (1, 3)]


def test_hooks_write_into_the_buffer():
    m = WeekModel()
    hooks = m.hooks(lambda i, d: 0, lambda i, d: True)
    col = hooks.add_var(0.0, 1.0, True)
    hooks.add_row({col: 1.0}, 0.0, 1.0)
    assert m.num_cols == 1 and m.num_rows == 1


# ----------------------------------------------------------------------------- facades keep their documented keys

def _wk(i, code, sport, dur, tss=20.0, **kw):
    z = (dur * 0.4, dur * 0.6, 0.0, 0.0, 0.0)
    kw.setdefault("is_indoor_bike", True)
    return CandidateWorkout(i, code, code, sport, float(dur), float(tss), 10.0, 10.0, 1.0, z, **kw)


MESO_KEYS = {"rank", "status", "mip_gap", "objective", "tier1_penalty", "relaxations", "diagnostics", "run_share",
             "total_tss", "total_mech_strain", "total_duration", "total_ans_cost", "max_single_run_ratio",
             "schedule", "soft_slack_violations"}
HYBRID_KEYS = {"option", "rank", "split_architecture", "status", "mip_gap", "objective_value", "tier1_penalty",
               "relaxations", "diagnostics", "run_share", "total_tss", "total_axial_load", "total_duration",
               "schedule", "selected_pairs"}


def _small_lib():
    return ([_wk(i, f"R{i}", "Run", 30) for i in range(5)] + [_wk(10 + i, f"B{i}", "Bike", 45) for i in range(4)])


def test_scheduler_facade_plan_keys_and_state():
    c = MesoCycleTargetsAndConstraints(
        daily_time_caps=[120.0] * 7, weekday_single_workout_cap=120.0,
        facility_access={"Swim": ALL, "Bike": ALL, "Run": ALL, "Strength": ALL},
        sport_freq_bounds={"Swim": (0, 0), "Bike": (1, 4), "Run": (2, 5), "Strength": (0, 0)},
        target_weekly_tss=150.0, nominal_weekly_duration=250.0, required_heavy_strength_sessions=0,
        min_stride_sessions=0, max_rest_days=7)
    s = TriathlonMILPScheduler(_small_lib(), c)
    plans = s.solve_top_k_plans(k_plans=2, time_limit=10.0)
    assert len(plans) == 2
    assert set(plans[0]) == MESO_KEYS and plans[0]["rank"] == 1 and plans[1]["rank"] == 2
    assert s.last_status == plans[0]["status"] and isinstance(s.last_diagnostics, list)
    assert isinstance(s.notes, list)
    sched = [(w.code, d) for d in range(7) for w in plans[0]["schedule"][d]]
    assert sched != [(w.code, d) for d in range(7) for w in plans[1]["schedule"][d]]


def test_hybrid_facade_plan_keys_and_state():
    hy = {"daily_duration_caps": [120] * 7, "pool_open_days": list(range(7)), "outdoor_bike_days": list(range(7)),
          "target_tss": {"Swim": 0.0, "Bike": 60.0, "Run": 60.0, "Strength": 0.0},
          "sport_freq_bounds": {"Run": (2, 5), "Bike": (1, 4)}, "min_rest_days": 1, "max_rest_days": 7}
    opt = HybridAthleteMILPOptimizer(_small_lib(), hy)
    plans = opt.solve_top_k_plans(k_solutions=2, time_limit=10.0)
    assert plans and set(plans[0]) == HYBRID_KEYS
    assert plans[0]["option"] == "A" and plans[0]["split_architecture"] in (
        "Upper/Lower Barbell + DB Split", "Full-Body HLM / Texas Method Split")
    assert all(isinstance(p, tuple) and len(p) == 2 for p in plans[0]["selected_pairs"])
    assert opt.last_status == plans[0]["status"] and isinstance(opt.last_diagnostics, list)


def test_engine_presets_are_distinct_and_documented():
    m, h = meso_options(), hybrid_options()
    assert (m.session_model, m.cut_mode, m.rest_bounds_default) == ("partition_brick", "selection", (1, 7))
    assert (h.session_model, h.cut_mode, h.rest_bounds_default) == ("per_sport_cap", "selection", (1, 2))


def test_engine_model_is_built_in_bulk_and_matches_highs():
    c = MesoCycleTargetsAndConstraints(
        daily_time_caps=[120.0] * 7, weekday_single_workout_cap=120.0,
        facility_access={"Swim": ALL, "Bike": ALL, "Run": ALL, "Strength": ALL},
        sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 4), "Run": (1, 5), "Strength": (0, 0)},
        target_weekly_tss=100.0, nominal_weekly_duration=200.0, required_heavy_strength_sessions=0,
        min_stride_sessions=0, max_rest_days=7)
    s = TriathlonMILPScheduler(_small_lib(), c)
    assert s.solve_top_k_plans(k_plans=1, time_limit=10.0)
    model = s.engine.last_context.model
    assert model.built and model.num_cols > 0 and model.num_rows > 0
    plans = solve_week(_small_lib(), s.cfg, meso_options(), k=1, time_limit=10.0)
    assert plans and plans[0]["objective"] == pytest.approx(s.solve_top_k_plans(1, 10.0)[0]["objective"])
