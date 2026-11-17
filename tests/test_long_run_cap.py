"""TASK-039 / ADR-022: absolute long-run cap (minutes) for weeks with < 4 runs; 32% ratio kept for >= 4 runs."""
from dataclasses import replace

import pytest

from triplan.control.carry import next_week_carry_in
from triplan.perf import season_validation as sv
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.solver.engine.carry_in import LoadState, WeekCarryIn
from triplan.solver.hybrid import HybridAthleteMILPOptimizer
from triplan.solver.safety_constraints import SafetyLimits, resolve_long_run_cap, run_share_report
from triplan.solver.scheduler import TriathlonMILPScheduler

from tests.test_safety_constraints import cfg, wk
from tests.test_season_validation import rec, rules, wk as vwk


def _load(**kw):
    return LoadState(run_acute=5.0, run_chronic=5.0, total_ctl=10.0, acwr_max=2.0, acwr_min=0.0, acwr_eps=50.0, **kw)


def _carry(prev_longest, reentry=False):
    return WeekCarryIn(load=_load(reentry=reentry), prev_longest_run_min=prev_longest)


def _lib():
    return [wk(0, "R75", "Run", 75, tss=90.0), wk(1, "R45", "Run", 45, tss=50.0), wk(2, "R30", "Run", 30, tss=30.0)]


def _c(**kw):
    return cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (1, 1), "Strength": (0, 0)},
               target_weekly_tss=90.0, long_run_step_min=10.0, **kw)


def _runs(plan):
    return [w.duration for d in range(7) for w in plan["schedule"][d] if w.sport == "Run"]


def _solve1(lib, c, carry=None):
    s = TriathlonMILPScheduler(lib, c, carry_in=carry)
    return s, s.solve_top_k_plans(k_plans=1, time_limit=10.0)[0]


def test_resolve_cap_progression_floor_and_reentry_ceiling():
    lim = SafetyLimits(long_run_step_min=10.0, long_run_floor_min=30.0, reentry_long_run_max_min=45.0)
    assert resolve_long_run_cap(lim, _carry(45.0)) == 55.0
    assert resolve_long_run_cap(lim, _carry(45.0, reentry=True)) == 45.0
    assert resolve_long_run_cap(lim, _carry(60.0)) == 70.0                    # no re-entry: step only
    assert resolve_long_run_cap(lim, _carry(60.0, reentry=True)) == 45.0      # ceiling tighter
    assert resolve_long_run_cap(lim, _carry(0.0)) == 30.0                     # first week: floor
    assert resolve_long_run_cap(lim, WeekCarryIn()) == 45.0                   # empty carry-in: ceiling only
    assert resolve_long_run_cap(SafetyLimits(), _carry(45.0)) is None         # rule off by default


def test_one_run_week_blocks_75_when_previous_longest_was_45():
    s, p = _solve1(_lib(), _c(), _carry(45.0))
    assert s.limits.long_run_abs_cap_min == 55.0
    assert _runs(p) == [45.0]                       # 75 is blocked; 45 beats 30 on TSS
    assert p["run_share"]["regime"] == "absolute" and "ADR-022" in p["run_share"]["note"]
    _, free = _solve1(_lib(), _c(), _carry(75.0))
    assert _runs(free) == [75.0]                    # allowed after a 75


def test_first_week_floor_and_shortest_run_always_schedulable():
    _, p = _solve1(_lib(), _c(), _carry(0.0))
    assert _runs(p) == [30.0]
    _, p = _solve1([wk(0, "R40", "Run", 40, tss=40.0)], _c(), _carry(0.0))   # shortest run above the floor
    assert _runs(p) == [40.0]


def test_reentry_ceiling_applies_without_carry_in():
    s, p = _solve1(_lib(), _c(reentry_long_run_max_min=45.0))
    assert s.limits.long_run_abs_cap_min == 45.0 and _runs(p) == [45.0]


def test_four_runs_still_use_the_32_percent_ratio_not_the_absolute_cap():
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (4, 4), "Strength": (0, 0)},
            target_weekly_tss=300.0, long_run_step_min=10.0)
    # abs cap = max(30, 30 + 10) = 40 would exclude BIG (60) if it applied; 60 / (60 + 3 * 45) = 31% <= 32%.
    lib = [wk(0, "BIG", "Run", 60, tss=80.0)] + [wk(i, f"R{i}", "Run", 45) for i in range(1, 6)]
    s, p = _solve1(lib, c, _carry(30.0))
    assert s.limits.long_run_abs_cap_min == 40.0
    runs = _runs(p)
    assert len(runs) == 4 and 60.0 in runs and max(runs) / sum(runs) <= 0.32 + 1e-9
    assert p["run_share"]["regime"] == "ratio"


def test_hybrid_facade_applies_the_absolute_cap():
    hy = {"daily_duration_caps": [120] * 7, "pool_open_days": list(range(7)), "outdoor_bike_days": list(range(7)),
          "target_tss": {"Swim": 0.0, "Bike": 0.0, "Run": 90.0, "Strength": 0.0},
          "sport_freq_bounds": {"Run": (1, 1)}, "min_rest_days": 1, "max_rest_days": 7, "long_run_step_min": 10.0}
    opt = HybridAthleteMILPOptimizer(_lib(), hy, carry_in=_carry(45.0))
    plans = opt.solve_top_k_plans(k_solutions=1, time_limit=10.0)
    assert opt.limits.long_run_abs_cap_min == 55.0
    assert _runs(plans[0]) == [45.0]


def test_carry_in_tracks_longest_run_and_holds_through_run_free_weeks():
    st = CURRENT_ATHLETE.to_athlete_state()
    sched = {0: [wk(0, "A", "Run", 40)], 2: [wk(1, "B", "Run", 55)], 4: [wk(2, "C", "Bike", 60)]}
    c1 = next_week_carry_in(WeekCarryIn(), sched, st, CURRENT_ATHLETE, [], 100.0)
    assert c1.prev_longest_run_min == 55.0 and c1.load.reentry
    c2 = next_week_carry_in(c1, {4: [wk(2, "C", "Bike", 60)]}, st, CURRENT_ATHLETE, [], 50.0)
    assert c2.prev_longest_run_min == 55.0


def test_report_regimes():
    lim = SafetyLimits(long_run_abs_cap_min=55.0)
    assert run_share_report({0: [wk(0, "A", "Run", 45)]}, lim)["regime"] == "absolute"
    assert run_share_report({0: [wk(0, "A", "Run", 45)]}, SafetyLimits())["regime"] == "pigeonhole"
    four = {d: [wk(d, f"R{d}", "Run", 30)] for d in (0, 2, 4, 5)}
    assert run_share_report(four, lim)["regime"] == "ratio"


def test_profile_fields_validated_and_current_athlete_values():
    assert CURRENT_ATHLETE.long_run_step_min == 10.0 and CURRENT_ATHLETE.reentry_long_run_max_min == 45.0
    with pytest.raises(ValueError, match="reentry_long_run_max_min"):
        replace(CURRENT_ATHLETE, reentry_long_run_max_min=20.0)
    with pytest.raises(ValueError, match="long_run_step_min"):
        replace(CURRENT_ATHLETE, long_run_step_min=0.0)
    c = CURRENT_ATHLETE.to_mesocycle_config()
    assert c.long_run_step_min == 10.0 and c.reentry_long_run_max_min == 45.0
    assert CURRENT_ATHLETE.to_hybrid_config()["long_run_floor_min"] == 30.0


# ----------------------------------------------------------------------------- independent checker

def _season_rec(sched, prev_sched, ceil=None):
    lim = SafetyLimits(long_run_step_min=10.0, reentry_long_run_max_min=ceil)
    carry = _carry(0.0, reentry=ceil is not None)
    prev = rec(prev_sched, week=1, carry=carry, limits=lim)
    return rec(sched, week=2, carry=carry, limits=lim), prev


def test_checker_catches_absolute_long_run_violation_and_passes_valid_week():
    prev_sched = {0: [vwk("Run", 45, 30, 20, id=1)], 3: [vwk("Run", 30, 20, 10, id=2)]}
    bad, prev = _season_rec({1: [vwk("Run", 75, 60, 30, id=3)]}, prev_sched)
    assert "long_run_abs" in rules(sv.check_week(bad, prev))
    ok, prev = _season_rec({1: [vwk("Run", 55, 40, 20, id=3)]}, prev_sched)
    assert "long_run_abs" not in rules(sv.check_week(ok, prev))
    ceil_bad, prev = _season_rec({1: [vwk("Run", 50, 40, 20, id=3)]}, prev_sched, ceil=45.0)
    assert "long_run_abs" in rules(sv.check_week(ceil_bad, prev))
    four = {d: [vwk("Run", 75 if d == 0 else 60, 40, 20, id=d + 10)] for d in (0, 1, 3, 5)}   # 75/255 = 29%: ratio governs
    r4, prev = _season_rec(four, prev_sched)
    assert "long_run_abs" not in rules(sv.check_week(r4, prev))


# ---- TASK-045: starting_longest_run_min seeds week 1 -------------------------------------------------------------
def _week1_cap(profile):
    from triplan.control.carry import initial_carry_in
    lib = []
    carry = initial_carry_in(profile.to_athlete_state(), profile, lib)
    lim = SafetyLimits.from_config(profile.to_mesocycle_config())
    return carry, resolve_long_run_cap(lim, carry)


def test_starting_longest_run_default_keeps_week1_cap_at_the_floor():
    assert CURRENT_ATHLETE.starting_longest_run_min == 30.0
    carry, cap = _week1_cap(CURRENT_ATHLETE)
    assert carry.prev_longest_run_min == 0.0 and cap == 30.0


def test_starting_longest_run_seed_is_used_but_reentry_ceiling_still_binds():
    carry, cap = _week1_cap(replace(CURRENT_ATHLETE, starting_longest_run_min=45.0))
    assert carry.prev_longest_run_min == 45.0 and carry.load.reentry
    assert cap == 45.0                      # progression allows 55, ADR-022 re-entry ceiling (owner-kept) caps at 45


def test_starting_longest_run_seed_allows_progression_when_not_in_reentry():
    prof = replace(CURRENT_ATHLETE, starting_longest_run_min=90.0, reentry_phase=False,
                   starting_ctl=45.0, starting_atl=40.0, starting_run_ctl=16.0, starting_run_atl=14.0)
    carry, cap = _week1_cap(prof)
    assert not carry.load.reentry and cap == 100.0


def test_starting_longest_run_must_be_non_negative():
    with pytest.raises(ValueError):
        replace(CURRENT_ATHLETE, starting_longest_run_min=-1.0)


def test_reentry_phase_false_is_never_reentry_even_with_low_run_ctl():
    from triplan.control.carry import load_state_for_week
    prof = replace(CURRENT_ATHLETE, reentry_phase=False)           # run CTL 1.0 < exit 15
    st = load_state_for_week(prof.to_athlete_state(), prof, 0.0, [])
    assert not st.reentry and st.acwr_max == prof.max_running_acwr and st.run_minutes_cap is None
