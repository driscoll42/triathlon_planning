"""TASK-055 (ADR-028): alternating long-run / long-ride keystone weeks in long-course race-block build weeks."""
import pytest

from triplan.control.periodization import (
    KEYSTONE_RIDE_LADDER, KEYSTONE_RUN_LADDER, apply_keystone_run_support, apply_keystone_week, build_season_matrix,
    keystone_kind, keystone_ride_minutes, keystone_ride_rungs, keystone_run_minutes, progression_guard_bounds,
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


def test_progression_guard_window_preserves_post_recovery_and_post_ride_keystone(monkeypatch):
    """TASK-061: 3-week rolling window references Week 2 across a ride-keystone + recovery pair so Week 5 keeps its
    >= 75 min long-run keystone without dropping the Run anchor or tripping Tier-0."""
    import contextlib
    import io

    import triplan.control.season as season
    from triplan.perf.season_validation import build_profile, check_season
    from triplan.solver.safety_constraints import run_minutes

    assert progression_guard_bounds([], 1.25) is None
    assert progression_guard_bounds([{"Solved_TSS": 0.0, "Total_Mech_Strain": 0.0}], 1.25) is None
    sample_rows = [
        {"Solved_TSS": 480.2, "Total_Mech_Strain": 448.0},  # W2 build (run keystone)
        {"Solved_TSS": 466.6, "Total_Mech_Strain": 383.4},  # W3 overload (ride keystone)
        {"Solved_TSS": 306.1, "Total_Mech_Strain": 144.6},  # W4 recovery deload
    ]
    tss_cap, mech_cap = progression_guard_bounds(sample_rows, 1.25)
    assert tss_cap == round(480.2 * 1.25, 0)
    assert mech_cap == round(448.0 * 1.25, 1)
    assert mech_cap >= 250.0  # would have been 180.8 with 1-week lookback on W4

    orig = season.build_season_matrix
    monkeypatch.setattr(season, "build_season_matrix", lambda *a, **k: orig(*a, **k)[:5])
    profile, recs = build_profile("reference", "70.3"), []
    with contextlib.redirect_stdout(io.StringIO()):
        _, summary = season.generate_closed_loop_season_schedule(
            profile=profile, distance="70.3", time_limit_per_week=6.0, weeks_to_race=20, collect=recs,
        )
    assert [r.role for r in recs] == ["base", "build", "overload", "recovery", "build"]
    longest_run = max([run_minutes(w) for d in range(7) for w in recs[4].schedule[d]] or [0.0])
    assert longest_run >= 75.0
    assert "KEYSTONE_LONG_RUN" in summary.Recalibration[4]
    assert "Run_Mech_Strain" in summary.columns
    assert not any(x.get("category") == "shorten_or_drop_anchor:Run" for x in recs[4].relaxations)
    assert not check_season(recs, profile)


def test_apply_keystone_run_support_only_raises_freq_on_90plus_run_keystone():
    from triplan.perf.season_validation import reference_profile

    prof = reference_profile()
    cfg = prof.to_mesocycle_config("Build", 400.0)
    assert cfg.sport_freq_bounds["Run"] == (4, 5)
    apply_keystone_run_support(cfg, "run", 75.0)
    assert cfg.sport_freq_bounds["Run"] == (4, 5)
    apply_keystone_run_support(cfg, "ride", 105.0)
    assert cfg.sport_freq_bounds["Run"] == (4, 5)
    apply_keystone_run_support(cfg, "run", 90.0)
    assert cfg.sport_freq_bounds["Run"] == (4, 6)

    cfg_low_win = prof.to_mesocycle_config("Build", 400.0)
    cfg_low_win.two_session_windows = [False] * 7
    apply_keystone_run_support(cfg_low_win, "run", 90.0)
    assert cfg_low_win.sport_freq_bounds["Run"] == (4, 5)


@pytest.mark.parametrize("dist,n_weeks,target_min", [("70.3", 5, 90.0), ("ironman", 7, 105.0)])
def test_reference_peak_keystone_reaches_90_and_105_min(monkeypatch, dist, n_weeks, target_min):
    """TASK-062: reference 70.3 schedules >= 90 min (by W5) and Ironman schedules >= 105 min (by W7) with 0 violations."""
    import contextlib
    import io

    import triplan.control.season as season
    from triplan.perf.season_validation import build_profile, check_season
    from triplan.solver.safety_constraints import run_minutes

    orig = season.build_season_matrix
    monkeypatch.setattr(season, "build_season_matrix", lambda *a, **k: orig(*a, **k)[:n_weeks])
    profile, recs = build_profile("reference", dist), []
    with contextlib.redirect_stdout(io.StringIO()):
        season.generate_closed_loop_season_schedule(
            profile=profile, distance=dist, time_limit_per_week=4.0,
            weeks_to_race=20 if dist == "70.3" else 24, collect=recs,
        )
    longest_run = max((run_minutes(w) for r in recs for d in range(7) for w in r.schedule[d]), default=0.0)
    assert longest_run >= target_min
    assert not check_season(recs, profile)


def test_keystone_ride_minutes_progresses_one_rung_and_respects_caps(default_library):
    """TASK-063: 70.3 180 -> 210 -> 240 -> 300, Ironman up to 360; one library rung per step; day caps clamp the top."""
    lib = default_library
    big = [90.0, 120.0, 90.0, 120.0, 90.0, 360.0, 240.0]
    assert keystone_ride_rungs(lib, big) == [180.0, 210.0, 240.0, 300.0, 360.0]
    assert keystone_ride_rungs(lib, [90.0, 300.0]) == [180.0, 210.0, 240.0, 300.0]
    assert KEYSTONE_RIDE_LADDER["70.3"][-1] == 300.0 and KEYSTONE_RIDE_LADDER["ironman"][-1] == 360.0

    last, got = 0.0, []
    for i in range(6):
        last = keystone_ride_minutes("70.3", i, last, lib, big)
        got.append(last)
    assert got == [180.0, 210.0, 210.0, 240.0, 240.0, 300.0]
    last, got = 0.0, []
    for i in range(6):
        last = keystone_ride_minutes("ironman", i, last, lib, big)
        got.append(last)
    assert got == [180.0, 210.0, 240.0, 300.0, 300.0, 360.0]

    assert keystone_ride_minutes("70.3", 5, 180.0, lib, big) == 210.0          # ladder wants 300, one rung per step
    assert keystone_ride_minutes("ironman", 99, 1e4, lib, [90.0, 300.0]) == 300.0   # reference Saturday cap clamps 360
    # CURRENT_ATHLETE-like 75 min caps host no keystone ride: the 180 min anchor stays (diagnosis fires as before)
    assert keystone_ride_minutes("ironman", 5, 1e4, lib, [75.0] * 7) == 180.0


def test_apply_keystone_week_ride_minutes_moves_the_ride_anchor(default_library):
    lib = default_library
    wk = apply_keystone_week(lib, "ride", 75.0, 210.0)
    assert {w.code for w in wk if w.is_anchor} == {"BIKE_LC_OUT_210"}
    assert not next(w for w in wk if w.code == "BIKE_DUR_OUT_DEF").is_anchor
    assert not next(w for w in wk if w.code == "RUN_AE2_LONG").is_anchor
    # default ride_minutes keeps the pre-TASK-063 behavior
    assert {w.code for w in apply_keystone_week(lib, "ride", 75.0) if w.is_anchor} == {"BIKE_DUR_OUT_DEF"}
    # a duration the library has no ride for leaves the Bike anchors untouched
    assert {w.code for w in apply_keystone_week(lib, "ride", 75.0, 333.0) if w.is_anchor} == {"BIKE_DUR_OUT_DEF"}
    # run weeks still drop every Bike anchor
    assert not any(w.is_anchor for w in apply_keystone_week(lib, "run", 90.0) if w.sport == "Bike")


@pytest.mark.slow
@pytest.mark.parametrize("dist,n_weeks,target_min", [("70.3", 10, 210.0), ("ironman", 10, 210.0)])
def test_reference_long_ride_keystone_grows_with_block(monkeypatch, dist, n_weeks, target_min):
    """TASK-063: the first ride-keystone week (W3) holds the 180 min anchor; later ones reach >= 210 min; 0 violations."""
    import contextlib
    import io

    import triplan.control.season as season
    from triplan.perf.season_validation import build_profile, check_season

    orig = season.build_season_matrix
    monkeypatch.setattr(season, "build_season_matrix", lambda *a, **k: orig(*a, **k)[:n_weeks])
    profile, recs = build_profile("reference", dist), []
    with contextlib.redirect_stdout(io.StringIO()):
        _, summary = season.generate_closed_loop_season_schedule(
            profile=profile, distance=dist, time_limit_per_week=4.0,
            weeks_to_race=20 if dist == "70.3" else 24, collect=recs,
        )
    rides = {}
    for r, note in zip(recs, summary.Recalibration):
        if "KEYSTONE_LONG_RIDE" in note:
            rides[r.week_num] = max([w.duration for d in range(7) for w in r.schedule[d] if w.sport == "Bike"] or [0.0])
    assert len(rides) >= 3
    ordered = list(rides.values())
    assert ordered[0] >= 180.0 and max(ordered) >= target_min
    assert not check_season(recs, profile)
