"""TASK-026: 3:1 season matrices, re-entry block, maintenance strength, ADR-011 test lockouts."""
import pytest

from triplan.control.periodization import (
    DEFAULT_RACE_WEEKS, build_season_matrix, filter_test_workouts, is_cp_test_allowed,
)
from triplan.physiology.athlete_profile import CURRENT_ATHLETE

DISTANCES = ["sprint", "olympic", "70.3", "ironman"]


@pytest.mark.parametrize("dist", DISTANCES)
def test_matrix_is_3_to_1(dist):
    m = build_season_matrix(dist, build_weeks=3, recovery_weeks=1)
    assert len(m) == DEFAULT_RACE_WEEKS[dist]
    assert [w.week_num for w in m] == list(range(1, len(m) + 1))
    cycle = [w for w in m if w.role not in ("taper", "race")]
    for i, w in enumerate(cycle):
        assert w.is_recovery == ((i + 1) % 4 == 0), f"week {w.week_num}"
        if w.is_recovery:
            prev = cycle[i - 1]
            assert w.tss_factor < prev.tss_factor and w.req_strength == 0
    assert m[-1].role == "race" and m[-2].role == "taper"
    assert m[-1].tss_factor < m[-2].tss_factor < max(w.tss_factor for w in cycle)


def test_matrix_follows_mesocycle_shape():
    m = build_season_matrix("olympic", build_weeks=2, recovery_weeks=1)  # a 2:1 profile still works
    cycle = [w for w in m if w.role not in ("taper", "race")]
    assert [w.is_recovery for w in cycle[:6]] == [False, False, True] * 2


def test_reentry_block_is_24_weeks_3_to_1_and_ramps():
    p = CURRENT_ATHLETE
    assert p.reentry_weeks >= 24
    m = build_season_matrix("olympic", p.weeks_to_race, p.mesocycle_build_weeks,
                            p.mesocycle_recovery_weeks, p.reentry_weeks)
    re_entry = [w for w in m if w.block == "reentry"]
    assert len(re_entry) == p.reentry_weeks
    assert len(m) == p.reentry_weeks + 16
    assert [w.week_num for w in m] == list(range(1, len(m) + 1))
    assert all(w.is_recovery == (w.block_week % 4 == 0) for w in re_entry)
    builds = [w.tss_factor for w in re_entry if not w.is_recovery]
    assert builds == sorted(builds) and builds[0] < 1.0
    assert not any(w.block == "reentry" for w in m[p.reentry_weeks:])


def test_reentry_disabled():
    m = build_season_matrix("olympic", reentry_weeks=0)
    assert len(m) == 16 and all(w.block == "race" for w in m)


def test_profile_has_reentry_and_validates():
    assert CURRENT_ATHLETE.reentry_weeks == 24
    assert CURRENT_ATHLETE.reentry_heavy_strength_sessions == 0 and CURRENT_ATHLETE.pool_open_days == []
    from dataclasses import replace
    with pytest.raises(ValueError):
        replace(CURRENT_ATHLETE, reentry_weeks=-1)


def _lib_test_codes(lib):
    return {w.code for w in lib if w.flags.get("test")}


def test_no_cp_test_in_week_1_and_lockouts(default_library):
    codes = _lib_test_codes(default_library)
    assert {"BIKE_CP_TEST", "RUN_CP_TEST"} <= codes
    kw = dict(reentry_weeks=24)
    m = build_season_matrix("olympic", reentry_weeks=24)
    last = {}
    for w in m:
        kept = {c.code for c in filter_test_workouts(default_library, w, last, **kw) if c.flags.get("test")}
        if w.week_num == 1:
            assert not kept, "ADR-011: no test workouts in week 1"
        if w.week_num <= 4:
            assert "BIKE_CP_TEST" not in kept          # bike lockout <= 4 weeks
        if w.week_num <= 6:
            assert "RUN_CP_TEST" not in kept           # run lockout <= 6 weeks
        if w.role in ("taper", "race"):
            assert not kept
        if w.week_num == 5:
            assert "BIKE_CP_TEST" in kept              # ADR-012 DECISION-005: bike test at start of week 5
        if w.week_num == 8:
            assert "RUN_CP_TEST" in kept               # ... run test at the week-8 deload
        if w.week_num in (6, 7, 9, 10, 11):
            assert not kept                            # build weeks: tests only in deloads
    # filtering only removes test workouts, never other candidates
    others = len(default_library) - len(codes)
    assert len(filter_test_workouts(default_library, m[0], {}, **kw)) == others


def test_min_gap_between_tests():
    m = build_season_matrix("olympic", reentry_weeks=24)
    wk12 = next(w for w in m if w.week_num == 12)          # a recovery week
    assert is_cp_test_allowed("Run", wk12, {"Run": 4}, reentry_weeks=24)
    assert not is_cp_test_allowed("Run", wk12, {"Run": 8}, reentry_weeks=24)   # only 4 weeks since last


@pytest.fixture(scope="module")
def olympic_season():
    from triplan.control.season import generate_closed_loop_season_schedule

    return generate_closed_loop_season_schedule(distance="olympic", time_limit_per_week=3.5)


def test_full_olympic_season_with_reentry_solves(olympic_season):
    daily, summary = olympic_season
    p = CURRENT_ATHLETE
    assert len(summary) == p.reentry_weeks + p.weeks_to_race
    assert (summary["Solve_Status"].isin(["OPTIMAL", "TIME_LIMIT_FEASIBLE"])).all(), "infeasible/fallback week"
    assert (summary["Solved_TSS"] > 0).all()
    re_entry = daily[daily.Week <= p.reentry_weeks]
    assert not (re_entry.Sport == "Swim").any()                       # re-entry: strength + bike + run only
    assert {"Strength", "Bike", "Run"} <= set(re_entry.Sport)


def test_full_season_recovery_weeks_keep_maintenance_lift_and_no_tests(olympic_season):
    daily, summary = olympic_season
    for week in summary.loc[summary.Step_Type.str.startswith(("Recovery", "Taper", "Race Week")), "Week"]:
        lifts = daily[(daily.Week == week) & (daily.Sport == "Strength")]
        assert len(lifts) >= CURRENT_ATHLETE.recovery_week_strength_sessions, f"week {week} has no strength"
    assert not daily[daily.Week == 1].Code.str.contains("TEST").any()           # ADR-011: nothing in week 1
    for sport_prefix, lockout in (("BIKE", 4), ("RUN", 6)):                     # ADR-011 re-entry lockouts
        tested = daily[daily.Code.str.startswith(sport_prefix) & daily.Code.str.contains("CP_TEST")]
        assert (tested.Week > lockout).all()
