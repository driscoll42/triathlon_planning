"""TASK-040: long-course long sessions in the canonical library + profile-level rolling 48h caps."""
from dataclasses import replace

import pandas as pd
import pytest

from triplan.control.feasibility import peak_48h, season_infeasibility_report, time_budget_diagnosis
from triplan.library.build import (BIKE_ZONE_IF, LONG_RIDE_SPECS, LONG_RUN_SPECS, RUN_ZONE_IF, endurance_flags,
                                   endurance_loads, long_endurance_rows, power_tss)
from triplan.library.candidates import build_default_candidate_library
from triplan.library.loader import load_candidates, load_library
from triplan.library.schema import validate_table
from triplan.physiology.athlete_profile import CURRENT_ATHLETE, load_athlete_profile
from tests.test_current_athlete import _build_rows
from tests.test_season_validation import wk

RUNS, RIDES = list(LONG_RUN_SPECS), list(LONG_RIDE_SPECS)


@pytest.fixture(scope="module")
def rows():
    lib = load_library()
    return lib[lib["code"].isin(RUNS + RIDES)].set_index("code")


def test_rows_present_schema_valid_and_deterministic_builder(rows):
    assert set(rows.index) == set(RUNS + RIDES)
    validate_table(load_library(profile=None))
    assert [r["code"] for r in long_endurance_rows()] == RUNS + RIDES   # a rebuild reproduces them


def test_runs_cover_90_to_150_and_rides_210_to_360(rows):
    assert sorted(rows.loc[RUNS, "duration_min"]) == [90.0, 105.0, 120.0, 150.0]
    assert sorted(rows.loc[RIDES, "duration_min"]) == [210.0, 240.0, 300.0, 360.0]


def test_zone_split_is_pure_z1_z2_80_20_compliant(rows):
    for code, r in rows.iterrows():
        assert r["z1_min"] + r["z2_min"] == pytest.approx(r["duration_min"]), code
        for z in ("zx_min", "z3_min", "zy_min", "z4_min", "z5_min"):
            assert r[z] == 0, (code, z)
        assert not r["is_hard_endurance"] and not r["has_strides"] and not r["is_test"] and not r["is_anchor"]


def test_flags_match_canonical_rules(rows):
    for code in RUNS:
        r = rows.loc[code]
        assert bool(r["is_long_run"]) and bool(r["is_heavy_run"]), code     # HEUR-004, 48h separation
        assert not (r["is_indoor_bike"] or r["is_outdoor_bike"] or r["hi_torque_bike"])
    for code in RIDES:
        r = rows.loc[code]
        assert bool(r["is_outdoor_bike"]) and not r["is_indoor_bike"] and not r["hi_torque_bike"], code
        assert not (r["is_long_run"] or r["is_heavy_run"]), code
    for code, r in rows.iterrows():
        assert bool(r["lib_default"]) and not r["lib_hybrid"] and r["provenance"] == "agent_authored", code
    cands = {w.code: w for w in build_default_candidate_library()}
    assert cands["RUN_LC_Z2_90"].is_heavy_run and cands["RUN_LC_Z2_90"].flags.get("long_run") == 1
    assert cands["BIKE_LC_OUT_240"].flags.get("outdoor_bike") == 1


def test_values_come_from_the_shared_build_formulas(rows):
    for code, r in rows.iterrows():
        sport = r["sport"]
        z = [r["z1_min"], r["z2_min"], 0.0, 0.0, 0.0]
        cp = CURRENT_ATHLETE.run_cp if sport == "Run" else CURRENT_ATHLETE.bike_cp
        _, if_, tss = power_tss(z, RUN_ZONE_IF if sport == "Run" else BIKE_ZONE_IF, cp)
        assert (r["tss"], r["intensity_factor"]) == (tss, if_), code
        for k, v in endurance_loads(sport, tss, if_, r["duration_min"], z, False).items():
            assert r[k] == pytest.approx(v), (code, k)
        assert endurance_flags(sport, r["description"], r["duration_min"], z)["is_hard_endurance"] is False
    assert rows.loc["RUN_LC_Z2_150", "durability_score"] == 8.5 and rows.loc["RUN_LC_Z2_90", "durability_score"] == 7.5


def test_long_sessions_fit_the_tier0_daily_and_48h_caps(rows):
    cfg = CURRENT_ATHLETE.to_mesocycle_config("Build")
    for code, r in rows.iterrows():
        assert r["neuromuscular_load"] <= cfg.max_daily_neuro_load, code
        assert r["axial_load"] <= cfg.max_daily_axial_load, code
        assert r["tss"] <= cfg.max_48h_aerobic_tss, code
        assert r["ans_cost"] <= 700.0 and r["mechanical_strain"] <= cfg.max_48h_mech_strain, code


def test_current_athlete_day_caps_exclude_the_new_rows():
    """Its 45-75 min/day caps (and indoor-only rides) host none of them; the anchors are unchanged."""
    cfg = CURRENT_ATHLETE.to_mesocycle_config("Build")
    assert max(cfg.daily_time_caps) == 75.0
    lib = {w.code: w for w in build_default_candidate_library()}
    assert all(lib[c].duration > max(cfg.daily_time_caps) for c in RUNS + RIDES)
    assert lib["BIKE_DUR_OUT_DEF"].is_anchor and lib["BIKE_DUR_OUT_DEF"].duration == 180.0
    assert lib["RUN_AE2_LONG"].is_anchor and lib["RUN_AE2_LONG"].duration == 75.0
    assert len(load_candidates(library="hybrid")) == 22        # hybrid set untouched


def test_diagnosis_reports_no_library_gap_for_the_reference_profile():
    from triplan.perf.season_validation import reference_profile
    lib = build_default_candidate_library()
    for dist in ("70.3", "ironman"):
        assert not [m for m in time_budget_diagnosis(reference_profile(), dist, lib)
                    if "not in the workout library" in m]
    old = [w for w in lib if w.code not in RUNS + RIDES]
    assert any("long run" in m and "not in the workout library" in m
               for m in time_budget_diagnosis(reference_profile(), "ironman", old))


# ----------------------------------------------------------------------------- profile-level 48h caps
def test_profile_48h_caps_round_trip_and_keep_charter_values():
    p = load_athlete_profile()
    assert (p.max_48h_aerobic_tss, p.max_48h_mech_strain, p.max_48h_ans_cost) == (295.0, 260.0, 450.0)
    assert (CURRENT_ATHLETE.max_48h_aerobic_tss, CURRENT_ATHLETE.max_48h_mech_strain) == (295.0, 260.0)
    cfg = CURRENT_ATHLETE.to_mesocycle_config("Build")
    assert (cfg.max_48h_aerobic_tss, cfg.max_48h_mech_strain) == (295.0, 260.0)
    hy = CURRENT_ATHLETE.to_hybrid_config()
    assert (hy["max_48h_aerobic_tss"], hy["max_48h_mech_strain"]) == (295.0, 260.0)


def test_profile_48h_caps_reach_the_engine_rows(default_library):
    tight = replace(CURRENT_ATHLETE, max_48h_aerobic_tss=200.0, max_48h_mech_strain=150.0, max_48h_ans_cost=400.0)
    assert tight.to_mesocycle_config("Build").max_48h_aerobic_tss == 200.0
    assert tight.to_hybrid_config()["max_48h_mech_strain"] == 150.0
    _, ctx0 = _build_rows(default_library, CURRENT_ATHLETE)
    _, ctx1 = _build_rows(default_library, tight)
    assert (ctx0.limits.max_48h_tss, ctx0.limits.max_48h_mech) == (295.0, 260.0)
    assert (ctx1.limits.max_48h_tss, ctx1.limits.max_48h_mech, ctx1.limits.max_48h_ans) == (200.0, 150.0, 400.0)


@pytest.mark.parametrize("bad", [{"max_48h_aerobic_tss": 296.0}, {"max_48h_mech_strain": 261.0},
                                 {"max_48h_ans_cost": 701.0}, {"max_48h_aerobic_tss": 0.0},
                                 {"max_48h_mech_strain": -1.0}])
def test_profile_48h_caps_are_validated_against_the_charter_ceilings(bad):
    with pytest.raises(ValueError):
        replace(CURRENT_ATHLETE, **bad)


def test_reference_profile_keeps_charter_48h_caps():
    from triplan.perf.season_validation import reference_profile
    r = reference_profile()
    assert (r.max_48h_aerobic_tss, r.max_48h_mech_strain, r.max_48h_ans_cost) == (295.0, 260.0, 700.0)


# ----------------------------------------------------------------------------- season diagnosis
def test_peak_48h_and_binding_cap_message():
    sched = {0: [wk(tss=100.0, ans=300.0, mech=50.0)], 1: [wk(tss=150.0, ans=390.0, mech=60.0, id=2)]}
    assert peak_48h(sched) == {"TSS": 250.0, "ANS": 690.0, "Strain": 110.0}
    summary = pd.DataFrame([{"Week": 5, "Target_TSS": 700.0, "Solved_TSS": 570.0, "Relaxations": "", "Diagnostics": "",
                             "Max_48h_TSS": 250.0, "Max_48h_ANS": 690.0, "Max_48h_Strain": 110.0}])
    from triplan.perf.season_validation import reference_profile
    msgs = season_infeasibility_report(summary, reference_profile(), "ironman")
    hit = [m for m in msgs if m.startswith("Rolling 48h")]
    assert len(hit) == 1 and "ANS cost cap 700" in hit[0] and "not reachable" in hit[0]


def test_unhostable_long_course_rows_are_dropped_only_when_no_day_cap_hosts_them():
    from triplan.control.periodization import drop_unhostable_long_sessions
    lib = build_default_candidate_library()
    cur = {w.code for w in drop_unhostable_long_sessions(lib, CURRENT_ATHLETE.daily_time_caps)}
    assert not (cur & set(RUNS + RIDES))
    assert {"BIKE_DUR_OUT_DEF", "RUN_AE2_LONG", "BIKE_AE2_OUT"} <= cur        # anchors and legacy sessions untouched
    assert len(cur) == len(lib) - 8
    ref = {w.code for w in drop_unhostable_long_sessions(lib, [90.0, 120.0, 90.0, 120.0, 90.0, 300.0, 240.0])}
    assert set(RUNS + RIDES[:-1]) <= ref and "BIKE_LC_OUT_360" not in ref      # 360 min exceeds the 300 min Saturday cap
