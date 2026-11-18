"""TASK-051: per-sport library gap-fill (EXP-008): short easy runs, 40-55 min quality runs / rides, pool variants, lifts."""
import pytest

from triplan.library.build import (BIKE_ZONE_IF, GAP_ENDURANCE_SPECS, GAP_FILL_CODES, GAP_STRENGTH_SPECS, RUN_ZONE_IF,
                                   endurance_flags, endurance_loads, gap_fill_rows, power_tss, swim_time_tss)
from triplan.library.candidates import build_default_candidate_library
from triplan.library.loader import load_candidates, load_library
from triplan.library.schema import validate_table
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.solver.engine.session_quality import SessionQualityParams, quality

ENDURANCE = list(GAP_ENDURANCE_SPECS)
STRENGTH = list(GAP_STRENGTH_SPECS)


@pytest.fixture(scope="module")
def rows():
    lib = load_library()
    return lib[lib["code"].isin(GAP_FILL_CODES)].set_index("code")


def test_rows_present_schema_valid_and_deterministic_builder(rows):
    assert set(rows.index) == set(GAP_FILL_CODES)
    validate_table(load_library(profile=None))
    assert [r["code"] for r in gap_fill_rows()] == GAP_FILL_CODES        # a rebuild reproduces them
    for code, r in rows.iterrows():
        assert r["provenance"] == "agent_authored" and bool(r["lib_default"]) and bool(r["needs_review"]), code
        assert not r["lib_hybrid"] and not r["is_anchor"] and not r["is_test"], code


def test_no_grey_zone_sessions(rows):
    """80/20 7-zone compliance: ZX / ZY are 0 and every endurance row is either clean easy or classified hard (q_i >= 5)."""
    params = SessionQualityParams()
    cands = {w.code: w for w in build_default_candidate_library()}
    for code in ENDURANCE:
        assert rows.loc[code, "zx_min"] == 0 and rows.loc[code, "zy_min"] == 0, code
        q = quality(cands[code], params)
        assert q == 0.0 or q >= params.q_hard[cands[code].sport], (code, q)


def test_endurance_loads_use_the_shared_formulas(rows):
    prof = CURRENT_ATHLETE
    for code, (sport, z, desc, _adapt, outdoor) in GAP_ENDURANCE_SPECS.items():
        r, zz = rows.loc[code], [float(x) for x in z]
        assert r["duration_min"] == pytest.approx(sum(zz)), code
        if sport == "Swim":
            _np, if_, tss = swim_time_tss(zz, prof.swim_css)
        else:
            _np, if_, tss = power_tss(zz, RUN_ZONE_IF if sport == "Run" else BIKE_ZONE_IF,
                                      prof.run_cp if sport == "Run" else prof.bike_cp)
        assert r["tss"] == pytest.approx(tss) and r["intensity_factor"] == pytest.approx(if_), code
        flags = endurance_flags(sport, desc, sum(zz), zz)
        for k, v in flags.items():
            if k in ("is_indoor_bike", "is_outdoor_bike") and outdoor:
                continue
            assert bool(r[k]) == bool(v), (code, k)
        loads = endurance_loads(sport, tss, if_, sum(zz), zz, bool(flags.get("has_strides")))
        for k, v in loads.items():
            assert r[k] == pytest.approx(v), (code, k)


def test_short_easy_runs_fit_the_reentry_window(rows):
    for code, minutes in (("RUN_AE1_15", 15), ("RUN_AE1_20", 20)):
        r = rows.loc[code]
        assert r["duration_min"] == minutes and r["z1_min"] == minutes and not r["is_hard_endurance"], code
        assert not r["is_heavy_run"] and not r["is_long_run"] and not r["has_strides"], code
    # a walk-jog row is NOT authored: the schema cannot represent a walk component (TASK-058)
    assert not any("walk" in c.lower() for c in rows.index)


def test_hard_rows_meet_the_library_rule_and_stride_variants_are_not_hard(rows):
    for code in ("BIKE_TE_Z3_45", "BIKE_TH_45", "BIKE_TH_STR_45", "BIKE_TE_Z3_STR_45"):
        r = rows.loc[code]
        assert bool(r["is_hard_endurance"]), code
        assert r["z3_min"] >= 15 or r["z4_min"] >= 5, code
    for code in ("RUN_AE1_STR25", "BIKE_TH_STR_45", "BIKE_TE_Z3_STR_45"):
        assert bool(rows.loc[code, "has_strides"]), code
        assert rows.loc[code, "z5_min"] <= 2, code                   # alactic seconds only
    assert not rows.loc["RUN_AE1_STR25", "is_hard_endurance"]
    for code in ("BIKE_TH_STR_45", "BIKE_TE_Z3_STR_45"):             # stride-bearing quality: one session, two structure rows
        assert bool(rows.loc[code, "hi_torque_bike"]) and bool(rows.loc[code, "is_indoor_bike"]), code


def test_easy_rows_are_100_percent_low_intensity(rows):
    for code in ("RUN_AE1_15", "RUN_AE1_20", "BIKE_AE1_20", "BIKE_AE2_120", "BIKE_AE2_OUT_120", "SWIM_AE1_20", "SWIM_AE1_40",
                 "SWIM_AE2_65"):
        r = rows.loc[code]
        assert r["z1_min"] + r["z2_min"] == pytest.approx(r["duration_min"]), code
        assert not r["is_hard_endurance"], code


def test_bike_flags_and_pool_flags(rows):
    for code in rows.index:
        r = rows.loc[code]
        if r["sport"] == "Bike":
            assert bool(r["is_outdoor_bike"]) == (code == "BIKE_AE2_OUT_120"), code
            assert bool(r["is_indoor_bike"]) != bool(r["is_outdoor_bike"]), code
        if r["sport"] == "Swim":
            assert bool(r["pool"]), code
        if r["sport"] == "Run":
            assert bool(r["is_long_run"]) == (r["duration_min"] >= 60), code


def test_strength_rows_are_non_heavy_cap_safe_and_split_neutral(rows):
    cfg = CURRENT_ATHLETE.to_mesocycle_config("Build")
    for code in STRENGTH:
        r = rows.loc[code]
        assert not r["is_heavy_strength"] and not r["heavy_dl"], code
        assert r["axial_load"] <= cfg.max_daily_axial_load and r["neuromuscular_load"] <= cfg.max_daily_neuro_load, code
        assert r["split_group"] in ("ANY", "HLM"), code
        if r["bb_sets"] + r["db_sets"]:
            assert r["bb_sets"] / (r["bb_sets"] + r["db_sets"]) >= 0.6, code    # never dilutes the barbell share
    assert set(STRENGTH) == {"STR_PREHAB_25"}      # barbell maintenance rows measured and not shipped (TASK-060)


def test_default_library_gained_exactly_the_gap_fill_rows():
    lib = load_library()
    assert lib["lib_default"].sum() == 38 + len(GAP_FILL_CODES)
    assert lib["lib_hybrid"].sum() == 22                                   # hybrid set untouched
    codes = {w.code for w in load_candidates(library="default")}
    assert set(GAP_FILL_CODES) <= codes


def test_existing_rows_unchanged_by_the_migration():
    lib = load_library().set_index("code")
    for code in ("RUN_AE1_REC30", "RUN_AE2_LONG", "BIKE_DUR_OUT_DEF", "STR_SM1_CORE", "RRe1"):
        assert code in lib.index
    assert bool(lib.loc["RUN_AE2_LONG", "is_anchor"]) and bool(lib.loc["BIKE_DUR_OUT_DEF", "is_anchor"])
    assert not bool(lib.loc["RRe1", "lib_default"])                        # 80/20 scrape rows are not flagged (rebuild-safe)


def test_every_default_run_has_a_30_minute_or_shorter_easy_option():
    runs = [w for w in build_default_candidate_library() if w.sport == "Run" and not w.is_hard_endurance]
    assert {15.0, 20.0, 25.0} <= {w.duration for w in runs if w.duration <= 30}
    assert any(w.has_strides and w.duration <= 30 for w in runs)
