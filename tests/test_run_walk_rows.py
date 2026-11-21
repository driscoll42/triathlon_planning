"""TASK-058 (EXP-008 Finding 6, ADR-036): run/walk (walk-jog) re-entry rows on the TASK-023 impact model."""
import pytest

from triplan.control import periodization as per
from triplan.library import build as lb
from triplan.library.candidates import build_default_candidate_library
from triplan.library.intervals import interval_features
from triplan.library.loader import load_library
from triplan.library.schema import validate_table
from triplan.physiology import load_model as lm
from triplan.physiology.athlete_profile import CURRENT_ATHLETE


@pytest.fixture(scope="module")
def rows():
    lib = load_library()
    return lib[lib["code"].isin(lb.RUN_WALK_CODES)].set_index("code")


def test_rows_present_schema_valid_and_deterministic_builder(rows):
    assert set(rows.index) == set(lb.RUN_WALK_CODES) and len(rows) == 4
    validate_table(load_library(profile=None))
    assert [r["code"] for r in lb.run_walk_rows()] == lb.RUN_WALK_CODES          # a rebuild reproduces them
    for code, r in rows.iterrows():
        assert r["sport"] == "Run" and r["provenance"] == "agent_authored", code
        assert bool(r["lib_default"]) and bool(r["needs_review"]), code            # needs_review until EXP-006
        assert not r["lib_hybrid"] and not r["is_anchor"] and not r["is_test"], code


def test_walk_is_zone1_so_the_row_is_100_percent_low_intensity_and_never_hard_heavy_or_long(rows):
    for code, r in rows.iterrows():
        run_min, walk_min = lb.run_walk_split(code)
        assert r["duration_min"] == pytest.approx(run_min + walk_min) and r["z1_min"] == pytest.approx(r["duration_min"]), code
        assert r["z2_min"] == r["z3_min"] == r["z4_min"] == r["z5_min"] == r["zx_min"] == r["zy_min"] == 0, code
        assert r["ans_cost"] == 0.0, code
        assert not (r["is_hard_endurance"] or r["is_heavy_run"] or r["is_long_run"] or r["has_strides"]), code


def test_description_parses_into_the_stored_zone_minutes(rows):
    for code, r in rows.iterrows():
        assert interval_features(r.to_dict()).method == "parsed", code


def test_strain_is_the_impact_model_not_a_typed_number(rows):
    for code, r in rows.iterrows():
        run_min, walk_min = lb.run_walk_split(code)
        assert r["mechanical_strain"] == pytest.approx(lm.run_walk_mechanical_strain([run_min, 0, 0, 0, 0], walk_min), abs=0.06), code
        assert r["mechanical_strain"] == pytest.approx(lb.run_walk_strain(code), abs=0.06), code
        # walking is cheaper than running those minutes, but never free: strictly above the run minutes alone
        continuous_all = lm.zone_strain("Run", [run_min + walk_min, 0, 0, 0, 0])
        continuous_run = lm.zone_strain("Run", [run_min, 0, 0, 0, 0])
        assert continuous_run < r["mechanical_strain"] < continuous_all, code
        # the other loads follow the ordinary non-heavy-run rule on that strain
        assert r["neuromuscular_load"] == pytest.approx(0.40 * r["mechanical_strain"], abs=0.06), code
        assert r["axial_load"] == pytest.approx(0.30 * r["mechanical_strain"], abs=0.06), code


def test_duration_and_tss_are_conservative_so_no_solver_row_is_loosened(rows):
    """Walked minutes count as run minutes (run-minute guard, longest-run cap, run share) and as Zone 1 running TSS."""
    for code, r in rows.iterrows():
        run_min, walk_min = lb.run_walk_split(code)
        assert r["duration_min"] == pytest.approx(run_min + walk_min)
        _np, _if, tss = lb.power_tss([run_min + walk_min, 0, 0, 0, 0], lb.RUN_ZONE_IF, CURRENT_ATHLETE.run_cp)
        assert r["tss"] == pytest.approx(tss), code
    # same duration, same TSS, strictly less strain than the continuous Z1 run of the same length (RUN_AE1_20)
    lib = load_library().set_index("code")
    assert rows.loc["RUN_RW_1_1_20", "tss"] == pytest.approx(lib.loc["RUN_AE1_20", "tss"])
    assert rows.loc["RUN_RW_1_1_20", "mechanical_strain"] < lib.loc["RUN_AE1_20", "mechanical_strain"]


def test_the_ladder_gate_only_removes_candidates():
    cands = build_default_candidate_library()
    rw = {w.code for w in cands if w.code in lb.RUN_WALK_SPECS}
    assert rw == set(lb.RUN_WALK_CODES)
    others = [w.code for w in cands if w.code not in lb.RUN_WALK_SPECS]

    def offered(stage):
        out = per.filter_walkjog_workouts(cands, stage)
        assert [w.code for w in out if w.code not in lb.RUN_WALK_SPECS] == others     # nothing else touched
        return {w.code for w in out if w.code in lb.RUN_WALK_SPECS}

    assert offered(0) == set()                                                         # not re-entering: continuous runs
    assert offered(1) == {"RUN_RW_1_1_20"}                                             # rung 1: 1:1, 20 min
    assert offered(2) == {"RUN_RW_1_1_20", "RUN_RW_2_1_20"}                            # rung 2: 2:1, 25 min cap
    assert offered(3) == set(lb.RUN_WALK_CODES)                                        # rung 3 (3:1, 30 min) hosts all four
    assert offered(4) == set(lb.RUN_WALK_CODES)
    assert len(per.filter_walkjog_workouts([], 3)) == 0


def test_every_offered_row_fits_its_rung_run_share_and_length():
    for stage in per.WALKJOG_LADDER:
        for w in per.filter_walkjog_workouts(build_default_candidate_library(), stage.stage):
            split = lb.run_walk_split(w.code)
            if split:
                assert split[0] / sum(split) <= stage.run_fraction + 1e-9 and sum(split) <= stage.session_min, (stage, w.code)
