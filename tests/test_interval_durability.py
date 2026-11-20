"""TASK-023 (ADR-036): interval-parsed durability features and the unified 0..10 durability score."""
import pandas as pd
import pytest

from triplan.library.intervals import (apply_interval_features, durability_from_features, interval_features,
                                       parse_segments)
from triplan.library.loader import load_library


def _row(desc, sport="Run", prescription="time", **z):
    base = dict(sport=sport, description=desc, prescription=prescription, has_strides=False, is_test=False,
                z1_min=0.0, z2_min=0.0, z3_min=0.0, zx_min=0.0, zy_min=0.0, z4_min=0.0, z5_min=0.0)
    base.update(z)
    return base


# ----------------------------------------------------------------------------- parser

def test_repeats_split_blocks_and_late_minutes_use_elapsed_time():
    # 10 Z1 | 4 x (7 Z3 / 2 Z1) | 5 Z1  -> 28 thr min, each rep a separate 7 min block; total = 10 + 36 + 5 = 51 min,
    # the 60% mark is 30.6 min: reps start at 10, 19, 28, 37 -> thr after 30.6 = 7 (rep 4) + (28+7-30.6=4.4) = 11.4
    r = _row("10 minutes Zone 1, 4 x (7 minutes Zone 3/2 minutes Zone 1) 5 minutes Zone 1", z1_min=23.0, z3_min=28.0)
    f = interval_features(r)
    assert f.method == "parsed"
    assert (f.t_thr_min, f.t_cont_min) == (28.0, 7.0)
    assert f.late_min == pytest.approx(11.4)


def test_adjacent_threshold_segments_merge_into_one_continuous_block():
    r = _row("10 minutes Zone 1, 10 minutes Zone 3, 5 minutes Zone 4, 5 minutes Zone 2", z1_min=10.0, z2_min=5.0,
             z3_min=10.0, z4_min=5.0)
    f = interval_features(r)
    assert (f.t_thr_min, f.t_cont_min) == (15.0, 15.0)


def test_alactic_strides_are_not_threshold_work():
    r = _row("5 min Z1, 18 min Z2, 6 x (20 sec Z5 strides / 60 sec Z1 walk-jog), 2 min Z1", z1_min=13.0, z2_min=18.0,
             z5_min=2.0, has_strides=True)
    f = interval_features(r)
    assert (f.t_thr_min, f.t_cont_min, f.late_min) == (0.0, 0.0, 0.0)


def test_distance_rows_convert_to_minutes_through_their_own_zone_pace():
    r = _row("1 mile Zone 1, 4 x (0.5 mile Zone 3/0.5 mile Zone 2)", prescription="distance",
             z1_min=10.0, z2_min=20.0, z3_min=16.0, z1_dist=1.0, z2_dist=2.0, z3_dist=2.0)
    segs = parse_segments(r)
    assert segs is not None and sum(s.minutes for s in segs if s.zone == 3) == pytest.approx(16.0)
    f = interval_features(r)
    assert f.t_thr_min == pytest.approx(16.0) and f.t_cont_min == pytest.approx(4.0)


def test_parse_that_disagrees_with_stored_zone_minutes_falls_back_to_zones():
    r = _row("10 minutes Zone 1, 20 minutes Zone 3", z1_min=10.0, z3_min=12.0)   # stored Z3 volume disagrees
    assert parse_segments(r) is None
    f = interval_features(r)
    assert f.method == "zones" and f.t_thr_min == 12.0


def test_free_form_description_uses_explicit_nxm_hint_and_late_cue():
    r = _row("Run Threshold Cruise Intervals (3x8m @ 100% Stryd CP)", z1_min=12.0, z2_min=14.0, z4_min=20.0, z5_min=4.0)
    f = interval_features(r)
    assert f.method == "zones" and f.t_thr_min == 24.0 and f.t_cont_min == 8.0 and f.late_min == 0.0
    late = interval_features(_row("Long ride (160m Z2 + Late 20m Z3)", sport="Bike", z2_min=160.0, z3_min=20.0))
    assert late.late_min == 20.0


def test_brick_flag_strength_and_tests():
    b = interval_features(_row("Bike 30 min Z2 -> Run 10 min Z3", sport="Brick", z2_min=30.0, z3_min=10.0))
    assert b.brick
    assert interval_features(_row("anything", sport="Strength", z3_min=5.0)).t_thr_min == 0.0
    assert interval_features(_row("CP test", is_test=True, z5_min=15.0)).t_thr_min == 0.0


# ----------------------------------------------------------------------------- unified score

def test_durability_is_one_bounded_scale_driven_by_the_features():
    easy = interval_features(_row("45 minutes Zone 2", z2_min=45.0))
    thr = interval_features(_row("10 minutes Zone 1, 2 x (15 minutes Zone 3/5 minutes Zone 1), 10 minutes Zone 1",
                                 z1_min=40.0, z3_min=30.0))
    d_easy, d_thr = (durability_from_features("Run", 45.0, easy), durability_from_features("Run", 60.0, thr))
    assert 1.5 <= d_easy < d_thr <= 10.0
    assert durability_from_features("Run", 600.0, interval_features(_row("600 minutes Zone 2", z2_min=600.0))) <= 10.0
    assert durability_from_features("Strength", 60.0, interval_features(_row("x", sport="Strength"))) == 2.0
    # a longer session of the same content is never LESS durable; a brick adds the brick term
    assert durability_from_features("Bike", 180.0, easy) > durability_from_features("Bike", 60.0, easy)
    run_like = interval_features(_row("30 minutes Zone 2, 10 minutes Zone 3", z2_min=30.0, z3_min=10.0))
    brick = interval_features(_row("Bike 30 min Z2 -> Run 10 min Z3", sport="Brick", z2_min=30.0, z3_min=10.0))
    assert brick.brick and not run_like.brick
    assert durability_from_features("Brick", 40.0, brick) > durability_from_features("Run", 40.0, run_like)


# ----------------------------------------------------------------------------- the canonical library

@pytest.fixture(scope="module")
def stored_library():
    return load_library(profile=None)


def test_library_durability_is_the_derived_value_on_one_scale(stored_library):
    lib = stored_library
    assert lib["durability_score"].between(0.0, 10.0).all()
    again = apply_interval_features(lib)
    for col in ("t_thr_min", "t_cont_min", "late_min", "durability_score"):
        pd.testing.assert_series_equal(lib[col].astype(float), again[col].astype(float), check_exact=False, atol=1e-6)


def test_library_features_are_consistent_with_zone_minutes(stored_library):
    lib = stored_library
    endurance = lib[lib["sport"] != "Strength"]
    assert (endurance["t_cont_min"] <= endurance["t_thr_min"] + 1e-6).all()
    assert (endurance["late_min"] <= endurance["t_thr_min"] + 1e-6).all()
    zone_thr = endurance[["z3_min", "zy_min", "z4_min", "z5_min"]].sum(axis=1)
    assert (endurance["t_thr_min"] <= zone_thr + 1.0).all()                     # never more than the zone columns hold
    assert (lib.loc[lib["sport"] == "Strength", ["t_thr_min", "t_cont_min", "late_min"]] == 0).all().all()


def test_scraped_80_20_rows_are_parsed_not_guessed(stored_library):
    scraped = stored_library[stored_library["provenance"] == "8020_library"]
    methods = [interval_features(r).method for r in scraped.to_dict(orient="records")]
    assert methods.count("parsed") / len(methods) >= 0.97


def test_no_hand_typed_second_scale_remains(stored_library):
    # the retired hybrid scale reached 85; hand-coded rows are now derived like every other row
    hybrid = stored_library[stored_library["provenance"] == "hand_coded_hybrid"]
    assert hybrid["durability_score"].max() <= 10.0
    assert stored_library.set_index("code").loc["BK_LONG_DUR", "late_min"] == 20.0
