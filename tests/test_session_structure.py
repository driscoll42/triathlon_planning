"""Session structure, DECISION-021 combos and canonical 80/20 (TASK-019, ADR-019).

Small synthetic libraries isolate one rule at a time.
"""
import numpy as np
import pytest

from triplan.library.compound import make_lift_easy_combos
from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.solver.engine import hybrid_options, meso_options
from triplan.solver.engine import session_quality as sq
from triplan.solver.engine.builders import session_structure as ss
from triplan.solver.engine.config import WorkoutTable
from triplan.solver.engine.engine import UnifiedWeekEngine
from triplan.solver.hybrid import HybridAthleteMILPOptimizer
from triplan.solver.safety_constraints import classify_workouts, run_share_report, SafetyLimits
from triplan.solver.scheduler import TriathlonMILPScheduler

ALL = [True] * 7
P = sq.SessionQualityParams()


def wk(i, code, sport, dur, z=None, tss=20.0, mech=10.0, ans=10.0, **kw):
    """z = 7-zone minutes (Z1, Z2, ZX, Z3, ZY, Z4, Z5); default all Z1/Z2."""
    z = z or (dur * 0.4, dur * 0.6, 0, 0, 0, 0, 0)
    z5 = (z[0], z[1], z[2] + z[3], z[4] + z[5], z[6])
    kw.setdefault("is_indoor_bike", True)
    return CandidateWorkout(i, code, code, sport, float(dur), float(tss), float(mech), float(ans), 1.0, z5,
                            zone7=tuple(float(v) for v in z), **kw)


def hard_z(dur):  # 20 min Z4, rest Z1/Z2
    return (dur * 0.3, dur - 20 - dur * 0.3, 0, 0, 0, 20, 0)


def cfg(**kw):
    base = dict(
        daily_time_caps=[240.0] * 7, weekday_single_workout_cap=240.0,
        facility_access={"Swim": ALL, "Bike": ALL, "Run": ALL, "Strength": ALL},
        sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (0, 7)},
        target_weekly_tss=100.0, nominal_weekly_duration=300.0, required_heavy_strength_sessions=0,
        min_stride_sessions=0, key_sessions_per_week=(0, 7), min_rest_days=1, max_rest_days=7,
    )
    base.update(kw)
    return MesoCycleTargetsAndConstraints(**base)


def solve(lib, c, k=1):
    s = TriathlonMILPScheduler(lib, c)
    return s, s.solve_top_k_plans(k_plans=k, time_limit=10.0)


def day_of(plan, code):
    return next(d for d in range(7) for w in plan["schedule"][d] if w.code == code)


def workouts_of(plan):
    return [w for d in range(7) for w in plan["schedule"][d]]


# ----------------------------------------------------------------------------- q_i, hard, grey zone

def test_q_reproduces_the_library_hard_rule_on_pure_sessions():
    assert sq.quality(wk(0, "z3", "Run", 60, z=(15, 30, 0, 15, 0, 0, 0)), P) == pytest.approx(5.0)   # 15 min Z3+
    assert sq.quality(wk(1, "z4", "Run", 60, z=(15, 40, 0, 0, 0, 5, 0)), P) == pytest.approx(5.0)    # 5 min Z4+
    assert sq.is_hard(wk(0, "z3", "Run", 60, z=(15, 30, 0, 15, 0, 0, 0)), P)
    assert sq.is_hard(wk(1, "z4", "Run", 60, z=(15, 40, 0, 0, 0, 5, 0)), P)


def test_easy_swim_is_not_hard_even_when_flagged_or_with_high_swim_if():
    # a Z2-only swim (swim IF ~0.86) mislabelled hard by an IF rule: q_i is minutes-based, so it is easy
    swim = wk(0, "SWIM_Z2", "Swim", 60, z=(10, 50, 0, 0, 0, 0, 0), is_hard_endurance=True)
    assert sq.quality(swim, P) == 0.0 and not sq.is_hard(swim, P)
    fixed = sq.reclassify_hard([swim], P)[0]
    assert fixed.is_hard_endurance is False and swim.is_hard_endurance is True   # original untouched
    hard = wk(1, "SWIM_CSS", "Swim", 55, z=hard_z(55))
    assert sq.reclassify_hard([hard], P)[0].is_hard_endurance is True


def test_reclassification_never_touches_strength():
    s = wk(0, "S", "Strength", 45, is_heavy_strength=True)
    assert sq.reclassify_hard([s], P)[0] is s and not sq.is_hard(s, P)


def test_strides_z5_is_alactic_and_carries_no_quality():
    strides = wk(0, "RS", "Run", 45, z=(10, 33, 0, 0, 0, 0, 2), has_strides=True)
    assert sq.raw_quality(strides, P) == 0.0
    assert sq.raw_quality(wk(1, "R5", "Run", 45, z=(10, 33, 0, 0, 0, 0, 2)), P) == pytest.approx(2.0)


def test_incidental_allowance_scales_with_duration():
    long_ride = wk(0, "LONG", "Bike", 165, z=(35, 120, 0, 10, 0, 0, 0))     # q 3.33 <= 2 + 2.75
    short_ride = wk(1, "SHORT", "Bike", 60, z=(15, 33, 0, 12, 0, 0, 0))     # q 4.0  >  2 + 1
    assert sq.quality(long_ride, P) == 0.0 and not sq.is_grey_zone(long_ride, P)
    assert sq.quality(short_ride, P) == pytest.approx(4.0) and sq.is_grey_zone(short_ride, P)


def test_grey_zone_prefilter_drops_medium_but_keeps_anchors_tests_easy_and_hard():
    grey = wk(0, "GREY", "Bike", 60, z=(15, 33, 0, 12, 0, 0, 0))
    anchor = wk(1, "ANCHOR", "Bike", 60, z=(15, 33, 0, 12, 0, 0, 0), is_anchor=True)
    test = wk(2, "TEST", "Bike", 60, z=(15, 33, 0, 12, 0, 0, 0), flags={"test": 1})
    easy = wk(3, "EASY", "Bike", 60)
    hard = wk(4, "HARD", "Bike", 60, z=hard_z(60))
    lift = wk(5, "LIFT", "Strength", 45, z=(20, 20, 0, 5, 0, 0, 0))
    kept, dropped = sq.prefilter_grey_zone([grey, anchor, test, easy, hard, lift], P)
    assert dropped == ["GREY"] and [w.code for w in kept] == ["ANCHOR", "TEST", "EASY", "HARD", "LIFT"]


def test_engine_prefilters_before_the_model_is_built_and_is_switchable():
    grey = wk(0, "GREY", "Run", 60, z=(15, 33, 0, 12, 0, 0, 0))
    easy = wk(1, "EASY", "Run", 45)
    eng = UnifiedWeekEngine([grey, easy], cfg(), meso_options())
    assert eng.grey_zone_dropped == ["GREY"] and [w.code for w in eng.workouts] == ["EASY"]
    off = meso_options()
    off.prefilter_grey_zone = False
    assert [w.code for w in UnifiedWeekEngine([grey, easy], cfg(), off).workouts] == ["GREY", "EASY"]


def test_thresholds_are_config_overridable_and_validated():
    c = cfg(session_quality={"q_hard": {"Run": 20.0}, "zone_weights": [0, 0, 0, 0, 0, 2, 2]})
    p = sq.SessionQualityParams.from_config(UnifiedWeekEngine([], c, meso_options()).cfg)
    assert p.q_hard["Run"] == 20.0 and p.q_hard["Bike"] == 5.0 and p.zone_weights[5] == 2
    with pytest.raises(ValueError):
        sq.SessionQualityParams.from_config(UnifiedWeekEngine([], cfg(session_quality={"bogus": 1}), meso_options()).cfg)


def test_key_session_definition():
    assert sq.is_key(wk(0, "H", "Run", 60, z=hard_z(60)), P)
    assert sq.is_key(wk(1, "A", "Swim", 30, is_anchor=True), P)
    assert sq.is_key(wk(2, "LONGRUN", "Run", 75), P) and sq.is_key(wk(3, "LONGRIDE", "Bike", 150), P)
    assert not sq.is_key(wk(4, "E", "Run", 45), P) and not sq.is_key(wk(5, "S", "Strength", 120), P)
    assert not sq.is_key(wk(6, "LONGSWIM", "Swim", 150), P)


# ----------------------------------------------------------------------------- hard classification in the rows

def test_hard_by_quality_drives_one_hard_per_day_in_the_hybrid_facade():
    """The hybrid facade had no <= 1 hard/day row; two q-hard sessions can no longer share a day."""
    lib = [wk(0, "H1", "Bike", 45, z=hard_z(45)), wk(1, "H2", "Run", 45, z=hard_z(45))]
    hy = {"daily_duration_caps": [240] * 7, "pool_open_days": list(range(7)), "outdoor_bike_days": list(range(7)),
          "target_tss": {"Swim": 0.0, "Bike": 20.0, "Run": 20.0, "Strength": 0.0},
          "sport_freq_bounds": {"Bike": (1, 1), "Run": (1, 1)}, "min_rest_days": 1, "max_rest_days": 7,
          "p_low_min": 0.0, "p_high_max": 1.0, "key_sessions_per_week": (0, 7)}
    for p in HybridAthleteMILPOptimizer(lib, hy).solve_top_k_plans(k_solutions=1, time_limit=10.0):
        assert abs(day_of(p, "H1") - day_of(p, "H2")) >= 2          # not same day, not consecutive days


def test_stale_hard_flag_does_not_create_a_hard_day():
    """Two sessions flagged hard but with no quality minutes may sit on consecutive days."""
    lib = [wk(i, f"F{i}", "Bike", 45, is_hard_endurance=True) for i in range(4)]
    _, plans = solve(lib, cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (4, 4), "Run": (0, 0), "Strength": (0, 0)},
                              target_weekly_tss=80.0))
    assert plans and plans[0]["relaxations"] == []


# ----------------------------------------------------------------------------- key sessions & strides

def _keys(plan):
    return sum(1 for w in workouts_of(plan) if sq.is_key(w, P))


def test_key_sessions_upper_bound_is_hard():
    lib = [wk(i, f"H{i}", "Bike", 40, z=hard_z(40), tss=60.0) for i in range(5)] + [wk(10, "E", "Run", 40)]
    c = cfg(key_sessions_per_week=(2, 3), target_weekly_tss=400.0, intensity_low_min=0.0, intensity_mod_high_max=1.0,
            sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (0, 0)})
    _, plans = solve(lib, c, k=2)
    for p in plans:
        assert 2 <= _keys(p) <= 3


def test_key_sessions_lower_bound_is_a_named_tier1_slack():
    lib = [wk(0, "H", "Bike", 40, z=hard_z(40)), wk(1, "E1", "Run", 40), wk(2, "E2", "Run", 45)]
    c = cfg(key_sessions_per_week=(2, 3), sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7),
                                                              "Strength": (0, 0)}, intensity_low_min=0.0, intensity_mod_high_max=1.0)
    _, plans = solve(lib, c)
    rel = {r["category"]: r["amount"] for r in plans[0]["relaxations"]}
    assert rel == {"reduce_key_sessions": 1}


def test_key_range_is_read_from_the_profile_name_and_defaults_to_2_3():
    assert MesoCycleTargetsAndConstraints().key_sessions_per_week == (2, 3)
    assert UnifiedWeekEngine([], {"sport_freq_bounds": {}}, hybrid_options()).cfg.key_range == (2, 3)
    assert UnifiedWeekEngine([], {"key_sessions_per_week": [0, 1]}, hybrid_options()).cfg.key_range == (0, 1)


def test_stride_minimum_defaults_to_two_and_is_one_tier1_rule():
    assert MesoCycleTargetsAndConstraints().min_stride_sessions == 2
    lib = [wk(0, "S1", "Run", 40, has_strides=True), wk(1, "E", "Run", 45), wk(2, "E2", "Bike", 45)]
    c = cfg(min_stride_sessions=2, sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (0, 0)})
    _, plans = solve(lib, c)
    rel = {r["category"]: r["amount"] for r in plans[0]["relaxations"]}
    assert rel == {"drop_stride_count": 1}      # only one stride workout exists: reported, not silent


# ----------------------------------------------------------------------------- Hard-Days-Hard & windows

def _hdh_lib():
    return [wk(0, "HEAVY", "Strength", 45, is_heavy_strength=True, flags={"heavy_sq": 1, "heavy_dl": 0}),
            wk(1, "HARDB", "Bike", 45, z=hard_z(45)), wk(2, "EASYB", "Bike", 45)]


def _hdh_cfg(**kw):
    return cfg(required_heavy_strength_sessions=1, key_sessions_per_week=(0, 7), allow_lift_cardio_combo=False,
               sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (1, 1)},
               target_weekly_tss=120.0, intensity_low_min=0.0, intensity_mod_high_max=1.0, **kw)


def test_heavy_lower_strength_is_stacked_on_a_hard_endurance_day_when_two_windows_exist():
    _, plans = solve(_hdh_lib(), _hdh_cfg(), k=2)
    for p in plans:
        assert p["relaxations"] == []
        assert day_of(p, "HEAVY") == day_of(p, "HARDB")
        order = [w.code for w in p["schedule"][day_of(p, "HEAVY")]]
        assert order == ["HARDB", "HEAVY"]                     # AM hard endurance, PM lift
        slots = p["am_pm"][day_of(p, "HEAVY")]
        assert [s["slot"] for s in slots] == ["AM", "PM"] and slots[1]["min_gap_h"] == 6.0 and "min_gap_h" not in slots[0]


def test_without_two_windows_the_heavy_lift_stands_alone_and_nothing_is_reported():
    _, plans = solve(_hdh_lib(), _hdh_cfg(two_session_windows=[False] * 7))
    p = plans[0]
    assert p["relaxations"] == []
    assert all(len(p["schedule"][d]) <= 1 for d in range(7))   # w_double[d] <= two_windows[d] = 0
    assert day_of(p, "HEAVY") != day_of(p, "HARDB")


def test_unstackable_week_reports_a_named_relaxation_instead_of_failing():
    # the only hard ride is a high-torque ride: squat vs high-torque bike is a same-day Tier-0 exclusion
    lib = [wk(0, "HEAVY", "Strength", 45, is_heavy_strength=True, flags={"heavy_sq": 1}),
           wk(1, "TORQUE", "Bike", 45, z=hard_z(45), flags={"hi_torque_bike": 1})]
    _, plans = solve(lib, _hdh_cfg())
    cats = {r["category"] for r in plans[0]["relaxations"]}
    assert "unstack_heavy_strength" in cats and any(w.code == "HEAVY" for w in workouts_of(plans[0]))


def test_window_gating_limits_double_days_in_both_facades():
    two = [True, False, False, False, False, False, False]
    lib = [wk(i, f"B{i}", "Bike", 40) for i in range(4)] + [wk(10 + i, f"R{i}", "Run", 40) for i in range(4)]
    _, plans = solve(lib, cfg(two_session_windows=two, target_weekly_tss=300.0, max_rest_days=7))
    assert all(len(plans[0]["schedule"][d]) <= 1 for d in range(1, 5))     # weekend: a Bike+Run brick is ONE window
    hy = {"daily_duration_caps": [240] * 7, "pool_open_days": list(range(7)), "outdoor_bike_days": list(range(7)),
          "target_tss": {"Swim": 0.0, "Bike": 80.0, "Run": 80.0, "Strength": 0.0}, "two_session_windows": two,
          "min_rest_days": 1, "max_rest_days": 7, "key_sessions_per_week": (0, 7)}
    for p in HybridAthleteMILPOptimizer(lib, hy).solve_top_k_plans(k_solutions=1, time_limit=10.0):
        assert all(len(p["schedule"][d]) <= 1 for d in range(1, 7))


def test_default_windows_are_all_true_so_behaviour_is_preserved():
    assert MesoCycleTargetsAndConstraints().two_session_windows == ALL
    assert UnifiedWeekEngine([], {}, hybrid_options()).cfg.two_windows == ALL


def test_am_pm_ordering_rules_are_deterministic():
    lift = wk(0, "LIFT", "Strength", 45)
    hard = wk(1, "HARD", "Run", 45, z=hard_z(45), is_hard_endurance=True)
    easy = wk(2, "EASY", "Bike", 45)
    swim = wk(3, "SWIM", "Swim", 45)
    assert [w.code for w in ss.order_day([lift, hard])] == ["HARD", "LIFT"]      # hard AM, lift PM
    assert [w.code for w in ss.order_day([hard, lift])] == ["HARD", "LIFT"]
    assert [w.code for w in ss.order_day([easy, lift])] == ["LIFT", "EASY"]      # lift fresh AM, easy PM
    assert [w.code for w in ss.order_day([easy, hard])] == ["HARD", "EASY"]
    assert [w.code for w in ss.order_day([easy, swim])] == ["SWIM", "EASY"]
    assert [w.code for w in ss.order_day([lift])] == ["LIFT"]
    assert ss.day_slots([lift], 6.0) == [{"slot": "WINDOW", "code": "LIFT", "sport": "Strength"}]
    brick_day = ss.day_slots(ss.order_day([easy, wk(4, "RUN", "Run", 30), lift]), 6.0, brick=True)
    assert [(s["code"], s["slot"]) for s in brick_day] == [("LIFT", "AM"), ("EASY", "PM"), ("RUN", "PM")]
    assert [s["slot"] for s in ss.day_slots([easy, wk(4, "RUN", "Run", 30)], 6.0, brick=True)] == ["WINDOW", "WINDOW"]
    assert [s["slot"] for s in ss.day_slots([easy, wk(4, "RUN", "Run", 30)], 6.0, brick=False)] == ["AM", "PM"]


def test_window_rules_make_the_week_reversal_symmetry_conditional():
    lib = [wk(0, "A", "Run", 40), wk(1, "B", "Run", 45)]
    base = {"daily_duration_caps": [240] * 7}
    from triplan.solver.engine.builders.symmetry import is_week_reversal_symmetric
    ctx = UnifiedWeekEngine(lib, base, hybrid_options()).build_model()
    assert is_week_reversal_symmetric(ctx) is True                          # all-True windows are palindromic
    asym_cfg = {"daily_duration_caps": [240] * 7, "two_session_windows": [True, False, False, False, False, False, False]}
    ctx2 = UnifiedWeekEngine(lib, asym_cfg, hybrid_options()).build_model()
    assert is_week_reversal_symmetric(ctx2) is False
    sym_cfg = {"daily_duration_caps": [240] * 7, "two_session_windows": [True, False, False, False, False, False, True]}
    ctx3 = UnifiedWeekEngine(lib, sym_cfg, hybrid_options()).build_model()
    assert is_week_reversal_symmetric(ctx3) is True                         # palindromic pattern stays symmetric
    off = hybrid_options()
    off.window_gating = off.hard_days_hard = False
    assert is_week_reversal_symmetric(UnifiedWeekEngine(lib, asym_cfg, off).build_model()) is True


# ----------------------------------------------------------------------------- DECISION-021 combos

def _strength_lib():
    return [wk(0, "HEAVY", "Strength", 45, is_heavy_strength=True, flags={"heavy_sq": 1}),
            wk(1, "LIGHT", "Strength", 40)]


def test_combo_generation_follows_decision_021():
    combos = make_lift_easy_combos(_strength_lib())
    by = {(c.code.split("+")[0], c.cardio_sport) for c in combos}
    assert by == {("HEAVY", "Bike"), ("LIGHT", "Bike"), ("LIGHT", "Run")}      # Bike only after heavy lower
    for c in combos:
        base = next(s for s in _strength_lib() if c.code.startswith(s.code + "+"))
        assert c.sport == "Strength" and c.cardio_minutes <= 20.0 and c.duration == base.duration + c.cardio_minutes
        assert sum(c.cardio_zone_durations) == pytest.approx(c.cardio_minutes)   # strictly Z1/Z2
        assert c.zone7[2:] == base.zone7[2:] and not c.is_anchor
        assert c.aerobic_tss > base.aerobic_tss and c.mechanical_strain > base.mechanical_strain
        assert c.is_heavy_strength == base.is_heavy_strength


def test_combo_options_are_configurable_and_bounded():
    only_bike = make_lift_easy_combos(_strength_lib(), sports_after_other_lift=("Bike",))
    assert {c.cardio_sport for c in only_bike} == {"Bike"}
    assert len(make_lift_easy_combos(_strength_lib(), minutes=(10.0, 20.0))) == 4
    assert len(make_lift_easy_combos(_strength_lib(), minutes=(10.0, 20.0), max_combos_per_lift=None)) == 6
    with pytest.raises(ValueError):
        make_lift_easy_combos(_strength_lib(), minutes=(30.0,))


def test_combo_run_component_counts_in_run_sets_run_share_and_run_strain():
    combos = make_lift_easy_combos([wk(1, "LIGHT", "Strength", 40)], sports_after_other_lift=("Run",))
    c = combos[0]
    S = classify_workouts([c])
    assert S.runs == [0] and S.strength == [0] and S.heavy_or_long_runs == []
    tab = WorkoutTable([c])
    assert tab.run_minutes[0] == c.cardio_minutes and tab.run_strain[0] == pytest.approx(c.cardio_mech_strain)
    assert tab.run_strain[0] < tab.mechanical_strain[0]                      # strength strain is not run strain
    assert tab.members("Run") == [0] and tab.ids("Run") == [] and tab.combo_ids("Bike") == []
    rep = run_share_report({0: [c]}, SafetyLimits())
    assert rep["n_runs"] == 1 and rep["max_single_run_share"] == 1.0


def test_combo_is_one_session_toward_the_day_cap_and_the_cardio_sports_frequency():
    # One strength workout and no run workout: the Run minimum of 1 can only be met by the lift + easy-run combo.
    lib = [wk(0, "LIGHT", "Strength", 40), wk(1, "B", "Bike", 40)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (1, 1), "Strength": (1, 1)},
            daily_time_caps=[60.0] * 7, weekday_single_workout_cap=60.0, target_weekly_tss=60.0)
    s, plans = solve(lib, c)
    p = plans[0]
    combo = [w for w in workouts_of(p) if w.cardio_sport == "Run"]
    assert len(combo) == 1 and p["relaxations"] == []
    d = day_of(p, combo[0].code)
    assert len(p["schedule"][d]) == 1 and combo[0].duration <= 60.0           # ONE session, within the day cap
    assert p["run_share"]["n_runs"] == 1


def test_combo_respects_the_day_time_cap():
    lib = [wk(0, "LIGHT", "Strength", 45)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 7), "Run": (1, 1), "Strength": (1, 1)},
            daily_time_caps=[60.0] * 7, weekday_single_workout_cap=60.0)
    _, plans = solve(lib, c)
    assert not any(w.cardio_sport for w in workouts_of(plans[0]))             # 45 + 20 > 60: combo never allowed
    assert any(r["category"] == "reduce_frequency:Run" for r in plans[0]["relaxations"])


def test_combo_needs_the_cardio_sports_facility_and_is_switchable():
    lib = [wk(0, "LIGHT", "Strength", 40)]
    no_run = {"Swim": ALL, "Bike": ALL, "Run": [False] * 7, "Strength": ALL}
    eng = UnifiedWeekEngine(lib, cfg(facility_access=no_run), meso_options())
    assert not any(w.cardio_sport == "Run" for w in eng.workouts)
    mon_run = {"Swim": ALL, "Bike": ALL, "Run": [True] + [False] * 6, "Strength": ALL}
    eng_mon = UnifiedWeekEngine(lib, cfg(facility_access=mon_run), meso_options())
    run_combo = next(i for i, w in enumerate(eng_mon.workouts) if w.cardio_sport == "Run")
    assert [eng_mon.allowed(run_combo, d) for d in range(7)] == [True] + [False] * 6
    off = meso_options()
    off.lift_easy_combo = False
    assert len(UnifiedWeekEngine(lib, cfg(), off).workouts) == 1
    assert len(UnifiedWeekEngine(lib, cfg(allow_lift_cardio_combo=False), meso_options()).workouts) == 1


def test_combo_cardio_minutes_count_as_endurance_in_the_8020_denominator():
    c = make_lift_easy_combos([wk(1, "LIGHT", "Strength", 40, z=(10, 20, 0, 10, 0, 0, 0))])[0]
    tab = WorkoutTable([c])
    assert tab.endur_zone7[0].tolist() == [c.cardio_zone_durations[0], c.cardio_zone_durations[1], 0, 0, 0, 0, 0]
    assert WorkoutTable([wk(1, "L", "Strength", 40)]).endur_zone7.sum() == 0   # plain strength excluded


# ----------------------------------------------------------------------------- canonical 80/20

def _shares(plan):
    z = np.sum([sq.endurance_zone7(w) for w in workouts_of(plan)], axis=0)
    tot = z.sum()
    return (z[0] + z[1]) / tot, z[3:].sum() / tot, z[2] / tot


def _moderate_lib():
    # every workout is 60 min with 15 min Z3 (25% high): only mixing in easy sessions can reach 80/20
    return ([wk(i, f"M{i}", "Run", 60, z=(15, 30, 0, 15, 0, 0, 0), tss=60.0) for i in range(4)]
            + [wk(10 + i, f"E{i}", "Bike", 60, tss=40.0) for i in range(4)])


@pytest.mark.parametrize("facade", ["meso", "hybrid"])
def test_canonical_8020_is_hard_in_both_facades_and_counts_z3(facade):
    lib = _moderate_lib()
    if facade == "meso":
        c = cfg(target_weekly_tss=600.0, key_sessions_per_week=(0, 7), sport_freq_bounds={
            "Swim": (0, 0), "Bike": (0, 7), "Run": (0, 7), "Strength": (0, 0)})
        _, plans = solve(lib, c)
    else:
        hy = {"daily_duration_caps": [240] * 7, "pool_open_days": list(range(7)), "outdoor_bike_days": list(range(7)),
              "target_tss": {"Swim": 0.0, "Bike": 150.0, "Run": 300.0, "Strength": 0.0},
              "min_rest_days": 1, "max_rest_days": 7, "key_sessions_per_week": (0, 7)}
        plans = HybridAthleteMILPOptimizer(lib, hy).solve_top_k_plans(k_solutions=1, time_limit=10.0)
    low, high, _ = _shares(plans[0])
    assert low >= 0.78 - 1e-6 and high <= 0.20 + 1e-6   # Z3 (not only Z4/Z5) counts toward the high ceiling
    assert any(w.sport == "Bike" for w in workouts_of(plans[0]))


def test_canonical_8020_thresholds_come_from_the_profile_names():
    lib = _moderate_lib()
    c = cfg(target_weekly_tss=600.0, intensity_low_min=0.60, intensity_mod_high_max=0.40,
            sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (4, 4), "Strength": (0, 0)})
    _, plans = solve(lib, c)
    low, high, _ = _shares(plans[0])
    assert low == pytest.approx(0.75) and high == pytest.approx(0.25)        # 4 moderate runs allowed at 40%


def test_zone_x_is_penalised_toward_zero():
    clean = wk(0, "CLEAN", "Run", 60, z=(15, 45, 0, 0, 0, 0, 0), tss=50.0)
    grey = wk(1, "GREYX", "Run", 60, z=(15, 35, 10, 0, 0, 0, 0), tss=50.0)       # 10 min Zone X, still 83% low
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (1, 1), "Strength": (0, 0)}, target_weekly_tss=50.0,
            target_zone_pcts={1: 0.25, 2: 0.75, 3: 0.0, 4: 0.0, 5: 0.0})
    _, plans = solve([grey, clean], c)
    assert [w.code for w in workouts_of(plans[0])] == ["CLEAN"]


def test_soft_low_target_prefers_80_percent_over_the_78_floor():
    # 79% low is feasible (>= 78) but below the 80% target: the soft goal picks the 100%-low alternative
    near = wk(0, "NEAR", "Run", 100, z=(30, 49, 0, 21, 0, 0, 0), tss=60.0)      # 79% low, 21% high -> violates <= 20
    ok = wk(1, "OK", "Run", 100, z=(30, 49.5, 0, 20.5, 0, 0, 0), tss=60.0)
    easy = wk(2, "EASYR", "Run", 100, z=(30, 70, 0, 0, 0, 0, 0), tss=60.0)
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (1, 1), "Strength": (0, 0)}, target_weekly_tss=60.0)
    _, plans = solve([near, ok, easy], c)
    assert [w.code for w in workouts_of(plans[0])] == ["EASYR"]


def test_legacy_options_do_not_enable_the_new_rules():
    from triplan.solver.engine.config import LegacySpec, legacy_options
    spec = LegacySpec(5, 100, .1, .3, .4, .4, .2, .2, .5, .3, .2, [100] * 7, np.zeros(1, bool), np.zeros(1, bool))
    o = legacy_options(spec)
    assert not any([o.prefilter_grey_zone, o.quality_classification, o.one_hard_per_day, o.key_sessions,
                    o.hard_days_hard, o.window_gating, o.lift_easy_combo, o.canonical_8020])
    assert hybrid_options().tiz_band is False and hybrid_options().canonical_8020 and meso_options().canonical_8020


def test_carry_in_run_minutes_and_acwr_rows_see_the_combo_run_leg():
    from triplan.solver.engine.carry_in import LoadState, WeekCarryIn
    lib = [wk(0, "LIGHT", "Strength", 40, mech=50.0)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (0, 7), "Strength": (1, 1)}, target_weekly_tss=40.0)
    only_run = {"combo_cardio_sports_after_other_lift": ["Run"]}
    eng = UnifiedWeekEngine(lib, {**c.__dict__, **only_run}, meso_options(),
                            carry_in=WeekCarryIn(load=LoadState(run_acute=0, run_chronic=0, total_ctl=0, acwr_max=1.2,
                                                                acwr_min=0.0, acwr_eps=0.0, run_minutes_cap=10.0)))
    ctx = eng.build_model()
    i = next(k for k, w in enumerate(eng.workouts) if w.cardio_sport == "Run")
    names = ctx.model.row_names
    starts, idx, val, rlb, rub = ctx.model.csr()
    r = names.index("run_minutes_cap")
    coef = dict(zip(idx[starts[r]:starts[r + 1]], val[starts[r]:starts[r + 1]]))
    assert coef[ctx.x(i, 0)] == 20.0            # the run leg (20 min), not the 60 min session
