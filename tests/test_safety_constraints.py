"""Unit tests for the shared Tier-0 safety layer and Tier-1 lexicographic slacks (TASK-016 / TASK-017).

Small synthetic libraries isolate one rule at a time.
"""
import pytest

from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.solver.hybrid import HybridAthleteMILPOptimizer
from triplan.solver.scheduler import TriathlonMILPScheduler
from triplan.solver.safety_constraints import classify_workouts

ALL = [True] * 7


def wk(i, code, sport, dur, tss=20.0, mech=10.0, ans=10.0, z=None, **kw):
    z = z or (dur * 0.4, dur * 0.6, 0.0, 0.0, 0.0)
    kw.setdefault("is_indoor_bike", True)
    return CandidateWorkout(i, code, code, sport, float(dur), float(tss), float(mech), float(ans), 1.0, z, **kw)


def cfg(**kw):
    base = dict(
        daily_time_caps=[120.0] * 7,
        weekday_single_workout_cap=120.0,
        facility_access={"Swim": ALL, "Bike": ALL, "Run": ALL, "Strength": ALL},
        sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (0, 7)},
        target_weekly_tss=100.0,
        nominal_weekly_duration=200.0,
        required_heavy_strength_sessions=0,
        min_stride_sessions=0,
        key_sessions_per_week=(0, 7),   # session-structure rules (TASK-019) have their own tests
        min_rest_days=1,
        max_rest_days=7,
    )
    base.update(kw)
    return MesoCycleTargetsAndConstraints(**base)


def solve(lib, c, k=1):
    s = TriathlonMILPScheduler(lib, c)
    return s, s.solve_top_k_plans(k_plans=k, time_limit=10.0)


def runs_of(plan):
    return [w for d in range(7) for w in plan["schedule"][d] if w.sport == "Run"]


def day_of(plan, code):
    return next(d for d in range(7) for w in plan["schedule"][d] if w.code == code)


# ----------------------------------------------------------------------------- run share (ADR-003)

def test_single_run_cap_is_hard_with_four_runs():
    # A 100 min run is 53% of 100 + 3 x 30: it must not be scheduled even though it scores well on TSS.
    lib = [wk(0, "BIG", "Run", 100, tss=120)] + [wk(i, f"R{i}", "Run", 30) for i in range(1, 6)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (4, 4), "Strength": (0, 0)}, target_weekly_tss=220.0)
    _, plans = solve(lib, c)
    runs = runs_of(plans[0])
    assert len(runs) == 4
    assert max(w.duration for w in runs) / sum(w.duration for w in runs) <= 0.32 + 1e-9


def test_anchor_that_would_break_run_cap_is_dropped_and_reported_not_violated():
    lib = [wk(0, "BIG", "Run", 100, is_anchor=True)] + [wk(i, f"R{i}", "Run", 30) for i in range(1, 6)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (4, 4), "Strength": (0, 0)})
    _, plans = solve(lib, c)
    p = plans[0]
    assert "BIG" not in [w.code for w in runs_of(p)]
    assert [r for r in p["relaxations"] if r["target"] == "anchor BIG"]


def test_pigeonhole_three_runs_uses_explicit_reported_cap():
    # Only {40, 35, 30} keeps the largest run <= 40% with 3 runs (40/105 = 38%).
    lib = [wk(0, "L", "Run", 40), wk(1, "M", "Run", 35), wk(2, "S", "Run", 30), wk(3, "XS", "Run", 20)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (3, 3), "Strength": (0, 0)}, target_weekly_tss=200.0)
    s, plans = solve(lib, c)
    p = plans[0]
    runs = runs_of(p)
    assert len(runs) == 3
    assert max(w.duration for w in runs) / sum(w.duration for w in runs) <= 0.40 + 1e-9
    assert p["run_share"]["applied_cap"] == pytest.approx(0.40) and "Pigeonhole" in p["run_share"]["note"]
    assert any("3-run cap of 40%" in m for m in s.last_diagnostics)


# ----------------------------------------------------------------------------- HEUR-004 & adjacency

def _dl_and_long_run_lib():
    return [
        wk(0, "DL", "Strength", 45, is_heavy_strength=True, is_anchor=True, flags={"heavy_dl": 1, "heavy_sq": 0}),
        # Long run flagged ONLY via flags (is_heavy_run False), the case the hybrid engine used to miss.
        wk(1, "LONG", "Run", 60, is_anchor=True, flags={"long_run": 1}),
        wk(2, "EASY", "Bike", 30),
    ]


def test_deadlift_never_within_24h_of_long_run_triathlon_engine():
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (1, 7), "Strength": (1, 7)}, target_weekly_tss=60.0,
            required_heavy_strength_sessions=1, two_session_windows=[False] * 7)  # no hard day to stack on here
    _, plans = solve(_dl_and_long_run_lib(), c, k=3)
    for p in plans:
        assert not p["relaxations"]
        assert abs(day_of(p, "DL") - day_of(p, "LONG")) >= 2


def test_deadlift_never_within_24h_of_long_run_hybrid_engine():
    lib = _dl_and_long_run_lib()
    hy = {"daily_duration_caps": [120] * 7, "pool_open_days": list(range(7)), "outdoor_bike_days": list(range(7)),
          "target_tss": {"Swim": 0.0, "Bike": 20.0, "Run": 20.0, "Strength": 20.0},
          "sport_freq_bounds": {"Run": (1, 7), "Strength": (1, 7)}, "min_rest_days": 1, "max_rest_days": 7}
    for p in HybridAthleteMILPOptimizer(lib, hy).solve_top_k_plans(k_solutions=1, time_limit=10.0):
        assert abs(day_of(p, "DL") - day_of(p, "LONG")) >= 2


def test_no_consecutive_hard_days():
    # hard by q_i (20 min Z4), not by a stale flag (TASK-019)
    lib = [wk(i, f"H{i}", "Bike", 45, z=(10.0, 15.0, 0.0, 20.0, 0.0)) for i in range(4)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (4, 4), "Run": (0, 0), "Strength": (0, 0)},
            intensity_low_min=0.0, intensity_mod_high_max=1.0)  # isolate adjacency from the 80/20 rule
    _, plans = solve(lib, c)
    days = sorted(day_of(plans[0], w.code) for w in lib)
    assert all(b - a >= 2 for a, b in zip(days, days[1:]))


def test_rolling_48h_tss_cap_is_hard():
    lib = [wk(i, f"B{i}", "Bike", 60, tss=200.0) for i in range(4)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 0), "Strength": (0, 0)},
            target_weekly_tss=800.0, max_48h_aerobic_tss=295.0)
    _, plans = solve(lib, c)
    days = sorted(day_of(plans[0], w.code) for d in range(7) for w in plans[0]["schedule"][d])
    assert all(b - a >= 2 for a, b in zip(days, days[1:])), days


# ----------------------------------------------------------------------------- Tier 1 lexicographic order

def _crowded_week(order=None):
    # 30 min/day cap => 1 session/day; >= 1 rest day => 6 sessions, but minimums ask for Swim 3 + Run 4 = 7.
    lib = [wk(i, f"S{i}", "Swim", 30) for i in range(4)] + [wk(10 + i, f"R{i}", "Run", 30) for i in range(5)]
    kw = dict(daily_time_caps=[30.0] * 7, weekday_single_workout_cap=30.0,
              sport_freq_bounds={"Swim": (3, 3), "Bike": (0, 0), "Run": (4, 4), "Strength": (0, 0)}, target_weekly_tss=140.0)
    if order is not None:
        kw["tier1_relaxation_order"] = order
    return lib, cfg(**kw)


def test_relaxation_order_drops_swim_before_run():
    lib, c = _crowded_week()
    _, plans = solve(lib, c)
    rel = {r["category"]: r["amount"] for r in plans[0]["relaxations"]}
    assert rel == {"reduce_frequency:Swim": 1}


def test_relaxation_order_is_configurable():
    lib, c = _crowded_week(order=["reduce_frequency:Run", "reduce_frequency:Swim"])
    _, plans = solve(lib, c)
    rel = {r["category"]: r["amount"] for r in plans[0]["relaxations"]}
    assert rel == {"reduce_frequency:Run": 1}


def test_rest_day_floor_of_one_is_never_relaxed():
    lib, c = _crowded_week()
    _, plans = solve(lib, c)
    rest = sum(1 for d in range(7) if not plans[0]["schedule"][d])
    assert rest >= 1


# ----------------------------------------------------------------------------- explainer & immutability

def test_infeasible_tier0_returns_plain_english_reason():
    lib = [wk(0, "SQ", "Strength", 45, is_heavy_strength=True, axial_load=85.0, flags={"heavy_sq": 1})]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (0, 0), "Strength": (0, 7)},
            required_heavy_strength_sessions=1, max_daily_axial_load=65.0)
    s, plans = solve(lib, c)
    assert plans == []
    assert any("axial" in m and "SQ" in m for m in s.last_diagnostics)


def test_scheduler_does_not_mutate_config():
    lib, c = _crowded_week()
    before = repr(c)
    solve(lib, c)
    assert repr(c) == before


def test_classification_treats_unflagged_heavy_strength_as_deadlift():
    lib = [wk(0, "MYSTERY", "Strength", 45, is_heavy_strength=True), wk(1, "SQ", "Strength", 45, flags={"heavy_sq": 1})]
    sets = classify_workouts(lib)
    assert sets.deadlifts == [0] and sets.squats == [1] and set(sets.heavy_strength) == {0, 1}


def test_classification_excludes_non_anchor_60m_easy_run_from_heavy_or_long_runs():
    easy_60 = wk(0, "RRe9", "Run", 60, is_heavy_run=False, is_anchor=False, flags={"long_run": 1})
    heavy_70 = wk(1, "RF13", "Run", 70, is_heavy_run=True, is_anchor=False, flags={"long_run": 1})
    anchor_60 = wk(2, "LONG_ANCHOR", "Run", 60, is_heavy_run=False, is_anchor=True, flags={"long_run": 1})
    sets = classify_workouts([easy_60, heavy_70, anchor_60])
    assert sets.heavy_or_long_runs == [1, 2]
