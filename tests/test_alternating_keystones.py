"""TASK-055 (ADR-028): alternating long-run / long-ride keystone weeks in long-course race-block build weeks."""
import pytest

from triplan.control.periodization import (
    KEYSTONE_RUN_LADDER, apply_keystone_week, build_season_matrix, keystone_kind, keystone_run_minutes,
)

BIG_CAPS = [90.0, 120.0, 90.0, 120.0, 90.0, 300.0, 240.0]


def _kinds(dist):
    out, n = [], 0
    for w in build_season_matrix(dist, None, 3, 1, 0):
        k = keystone_kind(dist, w.role, w.block, n)
        if k is not None:
            n += 1
            out.append((w.week_num, w.role, k))
    return out


@pytest.mark.parametrize("dist", ["70.3", "ironman"])
def test_alternation_pattern_long_course(dist):
    kinds = _kinds(dist)
    assert len(kinds) >= 8
    assert [k for _, _, k in kinds] == ["run", "ride"] * (len(kinds) // 2) + (["run"] if len(kinds) % 2 else [])
    assert {r for _, r, _ in kinds} <= {"build", "overload"}      # never base / recovery / taper / race


@pytest.mark.parametrize("dist", ["sprint", "olympic"])
def test_short_course_and_reentry_untouched(dist):
    assert _kinds(dist) == []
    for w in build_season_matrix(dist, 16, 3, 1, 24):
        assert keystone_kind(dist, w.role, w.block, 0) is None
    for w in build_season_matrix("70.3", 20, 3, 1, 24):
        if w.block == "reentry":
            assert keystone_kind("70.3", w.role, w.block, 0) is None


def test_deload_roles_keep_adr023_no_keystone():
    for role in ("recovery", "taper", "race", "base"):
        assert keystone_kind("70.3", role, "race", 0) is None


def test_apply_keystone_week_moves_only_the_obligation(default_library):
    lib = default_library
    run_wk = apply_keystone_week(lib, "run", 75.0)
    ride_wk = apply_keystone_week(lib, "ride", 75.0)
    assert [w.code for w in run_wk] == [w.code for w in lib] == [w.code for w in ride_wk]
    run_anchors = {w.code for w in run_wk if w.is_anchor}
    ride_anchors = {w.code for w in ride_wk if w.is_anchor}
    assert run_anchors == {"RUN_AE2_LONG"}                       # long-ride anchor optional, long-run mandatory
    assert ride_anchors == {"BIKE_DUR_OUT_DEF"}                  # long-run anchor optional, long-ride mandatory
    assert {w.code for w in apply_keystone_week(lib, "run", 105.0) if w.is_anchor} == {"RUN_LC_Z2_105"}
    assert {w.code for w in lib if w.is_anchor} >= {"RUN_AE2_LONG", "BIKE_DUR_OUT_DEF"}   # input not mutated


def test_keystone_run_minutes_progresses_one_rung_and_respects_caps(default_library):
    lib = default_library
    ladder = KEYSTONE_RUN_LADDER["70.3"]
    assert keystone_run_minutes("70.3", 0, 0.0, lib, BIG_CAPS) == 75.0
    assert keystone_run_minutes("70.3", 1, 75.0, lib, BIG_CAPS) == 90.0
    assert keystone_run_minutes("70.3", 3, 75.0, lib, BIG_CAPS) == 90.0      # ladder wants 105, but one rung per step
    assert keystone_run_minutes("70.3", 3, 90.0, lib, BIG_CAPS) == 105.0
    assert keystone_run_minutes("70.3", 99, 1e4, lib, BIG_CAPS) == ladder[-1] == 120.0
    assert keystone_run_minutes("ironman", 99, 1e4, lib, BIG_CAPS) == 150.0
    assert keystone_run_minutes("70.3", 5, 1e4, lib, [75.0] * 7) == 75.0       # day caps host only the 75 min anchor


def test_reference_70_3_long_run_week_then_long_ride_week(monkeypatch):
    """Weeks 2 / 3 of the reference 70.3 race block (first build / overload): week 2 holds the 75 min long run (the ride
    anchor is optional), week 3 the 180 min long ride; Tier-0 checker clean (TASK-055)."""
    import contextlib
    import io

    import triplan.control.season as season
    from triplan.perf.season_validation import build_profile, check_season
    from triplan.solver.safety_constraints import run_minutes

    orig = season.build_season_matrix
    monkeypatch.setattr(season, "build_season_matrix", lambda *a, **k: orig(*a, **k)[:3])
    profile, recs = build_profile("reference", "70.3"), []
    with contextlib.redirect_stdout(io.StringIO()):
        _, summary = season.generate_closed_loop_season_schedule(profile=profile, distance="70.3", time_limit_per_week=6.0,
                                                                 weeks_to_race=20, collect=recs)
    assert [r.role for r in recs] == ["base", "build", "overload"]
    longest = lambda r, f: max([f(w) for d in range(7) for w in r.schedule[d]] or [0.0])
    assert longest(recs[1], run_minutes) >= 75.0
    assert longest(recs[2], lambda w: w.duration if w.sport == "Bike" else 0.0) >= 180.0
    assert "KEYSTONE_LONG_RUN" in summary.Recalibration[1] and "KEYSTONE_LONG_RIDE" in summary.Recalibration[2]
    assert "KEYSTONE" not in summary.Recalibration[0]
    assert not check_season(recs, profile)
