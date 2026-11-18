"""Canonical workout library and athlete-profile schema tests (TASK-010 / TASK-011)."""
import os

import pandas as pd
import pytest
from pandera.errors import SchemaErrors
from pydantic import ValidationError

from triplan.library.loader import LIBRARY_PATH, legacy_frame, load_candidates, load_library, select
from triplan.library.schema import WORKOUT_COLUMNS, Workout, validate_table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def lib():
    return load_library()


# ----------------------------------------------------------------------------- canonical table

def test_canonical_library_validates_and_has_expected_shape(lib):
    assert list(lib.columns) == WORKOUT_COLUMNS
    assert lib["code"].is_unique
    counts = lib.groupby("provenance").size().to_dict()
    assert counts == {"8020_library": 479, "agent_authored": 61, "hand_coded_default": 24, "hand_coded_hybrid": 18}  # +4 TASK-033 heavy variants, +8 TASK-040 long-course, +14 TASK-051 gap-fill
    assert lib["lib_default"].sum() == 38 + 14 and lib["lib_hybrid"].sum() == 22


def test_stale_duplicate_sources_are_gone():
    for rel in ("data/Run.csv", "data/Bike.csv", "data/Swim.csv", "data/strength_workouts.csv",
                "data/brick_workouts.csv", "training_plans/brick_workouts.csv", "recommended_workout_legos.csv"):
        assert not os.path.exists(os.path.join(ROOT, rel)), rel


def test_solve_time_inference_is_deleted():
    from triplan.solver import scheduler, hybrid
    from triplan.control import mpc

    for mod in (scheduler, hybrid, mpc):
        assert not hasattr(mod, "enrich_workouts_from_df")


def test_seven_zone_minutes_sum_to_duration(lib):
    zones = lib[["z1_min", "z2_min", "zx_min", "z3_min", "zy_min", "z4_min", "z5_min"]].sum(axis=1)
    assert ((zones - lib["duration_min"]).abs() <= 1.0).all()
    assert (lib[["zx_min", "zy_min"]] == 0).all().all()  # 80/20 prescriptions never target ZX / ZY


# ----------------------------------------------------------------------------- audit fixes

def test_no_swim_is_tagged_as_strides(lib):
    # Old enrich: 'SS' in code tagged every SSP*/SSI*/SW_CSS_* swim as strides (zero Z5 ANS cost).
    assert not lib[(lib["sport"] == "Swim") & lib["has_strides"]].shape[0]
    assert set(lib.loc[lib["has_strides"], "code"]) == {
        "RUN_SS1_STR", "RUN_SS2_STR", "RUN_SS1_STR_DEF", "RN_Z2_STRIDE", "BIKE_SS1_CAD", "RUN_AE1_STR30", "BIKE_AE2_CAD_STR",
        "RUN_AE1_STR25", "BIKE_TH_STR_45", "BIKE_TE_Z3_STR_45"}   # +3 TASK-051 gap-fill


def test_80_20_rides_are_trainer_eligible_not_guessed_by_duration(lib):
    rides = lib[(lib["provenance"] == "8020_library") & (lib["sport"] == "Bike")]
    assert rides["is_indoor_bike"].all() and (rides["duration_min"] > 75).any()


def test_swim_hardness_is_minutes_based_not_if(lib):
    easy_swims = lib[(lib["sport"] == "Swim") & (lib["z3_min"] + lib["z4_min"] + lib["z5_min"] == 0)]
    assert not easy_swims["is_hard_endurance"].any()


def test_zone_corrections_applied(lib):
    rta1 = lib.set_index("code").loc["RTa1"]
    assert (rta1["duration_min"], rta1["z1_min"], rta1["z4_min"]) == (11.0, 9.0, 2.0)
    tq2 = lib.set_index("code").loc["BK_FR_TQ2"]
    assert (tq2["duration_min"], tq2["z1_min"], tq2["z2_min"], tq2["z3_min"]) == (81.0, 35.0, 10.0, 36.0)
    assert tq2["hi_torque_bike"]  # "50-60 RPM" low-cadence work


def test_brick_run_flag_fixed_and_collisions_renamed(lib):
    t = lib.set_index("code")
    assert t.loc["RUN_TB1_BRICK_DEF", "is_hard_endurance"]
    for code in ("BIKE_DUR_OUT", "BIKE_TE1_PYR", "RUN_SS1_STR", "RUN_TB1_BRICK", "STR_MS1_HEAVY"):
        assert t.loc[code, "provenance"] == "agent_authored"
        assert t.loc[code + "_DEF", "provenance"] == "hand_coded_default"


def test_all_28_mile_long_runs_recovered_as_distance_prescribed(lib):
    rl = lib[lib["code"].str.match(r"^RL(\d|FF|SP|MS)")]
    assert len(rl) == 28
    assert (rl["prescription"] == "distance").all() and (rl["distance_unit"] == "mile").all()
    assert (rl["tss"] > 0).all() and rl["is_long_run"].all()


@pytest.mark.parametrize("desc, expected", [
    ("1 mile Zone 1, 4.5 miles Zone 2, 0.5 mile Zone 1", [1.5, 4.5, 0, 0, 0]),
    ("0.5 mile Zone 1, 1 mile Zone 2, 8 x (0.25 mile Zone 3/0.75 mile Zone 2) 0.5 mile Zone 1", [1.0, 7.0, 2.0, 0, 0]),
    ("1.5 miles Zone 1, 16 miles at race pace, 1 mile Zone 2, 1.5 miles Zone 1", [3.0, 1.0, 16.0, 0, 0]),
])
def test_parse_mile_prescription(desc, expected):
    from triplan.library.build import parse_mile_prescription

    assert parse_mile_prescription(desc) == pytest.approx(expected)


def test_long_run_duration_follows_athlete_easy_pace():
    from dataclasses import replace
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE
    from triplan.library.build import run_zone_paces

    def rl1(profile):
        return load_library(profile=profile).set_index("code").loc["RL1"]

    slow, fast = replace(CURRENT_ATHLETE, easy_run_pace_min_per_mile=14.5), replace(CURRENT_ATHLETE, easy_run_pace_min_per_mile=10.0)
    p = run_zone_paces(14.5)
    assert rl1(slow)["duration_min"] == pytest.approx(1.5 * p[0] + 4.5 * p[1], abs=0.01)
    assert rl1(slow)["z2_min"] == pytest.approx(4.5 * 14.5, abs=0.01)  # Zone 2 = easy pace
    assert rl1(fast)["duration_min"] < rl1(slow)["duration_min"]


# ----------------------------------------------------------------------------- TASK-013 thresholds

def test_swims_are_distance_prescribed_and_follow_css():
    from dataclasses import replace
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE
    from triplan.library.build import SWIM_ZONE_CSS_MULT, css_units_per_min

    swims = load_library()
    swims = swims[(swims["sport"] == "Swim") & (swims["provenance"] == "8020_library")]
    assert len(swims) == 71 and (swims["prescription"] == "distance").all()
    assert swims.set_index("code").loc[["STT1", "STT2"], "is_test"].all()

    def row(css):
        return load_library(profile=replace(CURRENT_ATHLETE, swim_css=css)).set_index("code").loc["STT1"]

    slow, fast = row(40.0), row(50.0)
    css = css_units_per_min(40.0, CURRENT_ATHLETE.swim_pool_length)
    assert slow["z3_min"] == pytest.approx(slow["z3_dist"] / (css * SWIM_ZONE_CSS_MULT[2]), abs=0.01)
    assert fast["duration_min"] < slow["duration_min"]


def test_normalized_power_follows_current_cp(lib):
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE

    rides = lib[(lib["sport"] == "Bike") & (lib["prescription"] == "time") & (lib["intensity_factor"] > 0)]
    assert (rides["normalized_power"] - rides["intensity_factor"] * CURRENT_ATHLETE.bike_cp).abs().max() < 0.01


def test_profile_is_immutable_and_cp_retest_returns_new_profile():
    import dataclasses
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE

    with pytest.raises(dataclasses.FrozenInstanceError):
        CURRENT_ATHLETE.run_cp = 999.0
    assert CURRENT_ATHLETE.bike_ftp == CURRENT_ATHLETE.bike_cp  # read-only alias, single source of truth
    before = CURRENT_ATHLETE.bike_cp
    res = CURRENT_ATHLETE.apply_cp_retest_result("bike", p3=170.0, p12=142.0, auto_lock=True)
    assert CURRENT_ATHLETE.bike_cp == before
    assert res["updated_profile"].bike_cp != before and res["updated_profile"].thresholds_verified


def test_triv_has_no_athlete_thresholds():
    from triplan.model.classes import triVariables

    for name in ("run_ftp", "bike_ftp", "swim_css"):
        assert not hasattr(triVariables, name)


# ----------------------------------------------------------------------------- schema rejects bad rows

def _row(**over):
    base = load_library().iloc[[0]].copy()
    for k, v in over.items():
        base[k] = v
    return base


@pytest.mark.parametrize("over, fragment", [
    ({"z1_min": 999.0}, "zone minutes"),
    ({"tss": 0.0}, "tss"),
    ({"sport": "Rowing"}, "sport"),
    ({"provenance": "unknown"}, "provenance"),
])
def test_table_schema_rejects(over, fragment):
    with pytest.raises(SchemaErrors) as e:
        validate_table(_row(**over))
    assert fragment in str(e.value)


def test_table_schema_rejects_duplicate_codes():
    df = load_library().iloc[[0, 0]]
    with pytest.raises(SchemaErrors):
        validate_table(df)


def test_row_model_rejects_cross_sport_flags():
    rec = load_library().set_index("code").loc["CRe1"].to_dict()
    rec["code"] = "CRe1"
    rec["heavy_dl"] = True
    with pytest.raises(ValidationError, match="strength flag"):
        Workout(**{k: ((None if k == "distance" else "") if (isinstance(v, float) and pd.isna(v)) else v)
                   for k, v in rec.items()})


# ----------------------------------------------------------------------------- loader

def test_loader_sets_and_legacy_frame(lib):
    assert len(load_candidates(library="default", include_8020=False)) == 38 + 14   # TASK-051 gap-fill core
    assert len(load_candidates(library="hybrid", include_8020=False)) == 22
    assert len(load_candidates(library="default")) == 38 + 14 + 479                 # core + 80/20 options
    assert len(load_candidates(library="hybrid")) == 22 + 479
    no_tests = select(lib, library="default", include_tests=False)
    assert not no_tests["is_test"].any()
    legacy = legacy_frame(select(lib, provenance=["8020_library"]))
    assert {"Code", "Type", "Duration", "Zone 1", "TSS", "High Duration", "Low Duration"} <= set(legacy.columns)


def test_refresh_from_scrape_is_idempotent_and_keeps_authored_rows(lib):
    from triplan.library.build import refresh_from_8020_scrape

    xl = [os.path.join(ROOT, "data", f) for f in ("Run.xlsx", "Bike.xlsx", "Swim.xlsx")]
    out = refresh_from_8020_scrape(lib, *xl)
    key = ["code"]
    a = lib.sort_values(key).reset_index(drop=True)
    b = out.sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


# ----------------------------------------------------------------------------- athlete profile YAML

def test_profile_yaml_is_the_validated_source():
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE, PROFILE_PATH, load_athlete_profile

    assert os.path.exists(PROFILE_PATH)
    assert load_athlete_profile() == CURRENT_ATHLETE


def test_profile_yaml_requires_every_field(tmp_path):
    import yaml
    from triplan.physiology.athlete_profile import PROFILE_PATH, load_athlete_profile

    data = yaml.safe_load(open(PROFILE_PATH, encoding="utf-8"))
    del data["bike_cp"]
    data["unexpected"] = 1
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="missing fields \\['bike_cp'\\]; unknown fields \\['unexpected'\\]"):
        load_athlete_profile(str(p))


def test_profile_semantic_validation():
    from triplan.physiology.athlete_profile import AthleteProfile

    with pytest.raises(ValueError, match="7 entries"):
        AthleteProfile(daily_time_caps=[60.0] * 6)
    with pytest.raises(ValueError, match="sport_freq_bounds"):
        AthleteProfile(sport_freq_bounds={"Swim": (0, 0), "Bike": (3, 2), "Run": (2, 3), "Strength": (2, 3)})
    with pytest.raises(ValueError, match="tier1_relaxation_order"):
        AthleteProfile(tier1_relaxation_order=["reduce_everything"])
