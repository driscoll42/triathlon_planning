"""TASK-028: equipment-feasible strength library (ADR-012 DECISION-016/022, ADR-008)."""
import re

import pytest

from library.loader import load_library

LEGOS_STRENGTH = {
    "STR_AA1", "STR_MS1_HEAVY", "STR_MS2_HEAVY", "STR_SM1_MAINT", "STR_SM2_MAINT",
    "STR_PLYO1", "STR_UPPER_HYP", "STR_LOWER_UNI", "STR_POST_CORE", "STR_DELOAD",
}


@pytest.fixture(scope="module")
def strength():
    lib = load_library()
    return lib[lib["sport"] == "Strength"].set_index("code")


def test_legos_rows_are_agent_authored(strength):
    for code in LEGOS_STRENGTH:
        assert strength.loc[code, "provenance"] == "agent_authored", code


def test_no_trap_bar_or_bands(strength):
    pat = re.compile(r"trap[\s-]*bar|\bband(s|ed)?\b|pull[\s-]*aparts?|face[\s-]*pulls?", re.I)
    bad = [c for c, r in strength.iterrows() if pat.search(str(r["description"])) or pat.search(str(r["name"]))]
    assert not bad, bad


def test_lb_loads_are_multiples_of_5_and_dumbbells_capped(strength):
    for code, r in strength.iterrows():
        text = str(r["description"]) + " " + str(r["name"])
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*lb", text):
            load = float(m.group(1))
            assert load % 5 == 0, (code, load)
            assert load <= 75, (code, load)  # dumbbell ceiling (ADR-008); barbell loads are %1RM


def test_ms2_heavy_conventional_deadlift(strength):
    r = strength.loc["STR_MS2_HEAVY"]
    assert "Conventional Deadlift" in r["description"]
    assert bool(r["heavy_dl"])
    # conventional DL is more axially loading than the trap bar it replaced (80/95)
    assert r["axial_load"] >= 90 and r["axial_load"] <= 120


def test_sm2_maint_conventional_deadlift(strength):
    r = strength.loc["STR_SM2_MAINT"]
    assert "Conventional Deadlift" in r["description"]
    assert bool(r["heavy_dl"])


def test_deload_uses_prone_ytw_and_upper_hyp_rear_delt_fly(strength):
    assert "Prone Y-T-W" in strength.loc["STR_DELOAD", "description"]
    assert "Rear-Delt Fly" in strength.loc["STR_UPPER_HYP", "description"]
    # pull sets preserved by the swap
    assert strength.loc["STR_UPPER_HYP", "sets_pull"] == 6
    assert strength.loc["STR_DELOAD", "sets_pull"] == 2


def test_pullups_and_nordics_retained(strength):
    assert "Pull-Ups" in strength.loc["STR_UPPER_HYP", "description"]
    assert "Nordic" in strength.loc["STR_POST_CORE", "description"]


def test_deadlift_rows_flag_heavy_dl(strength):
    for code, r in strength.iterrows():
        desc = str(r["description"])
        if re.search(r"(?<!Romanian )(?<!SL )(?<!Single-Leg DB Romanian )\bDeadlift\b", desc):
            assert bool(r["heavy_dl"]), code


def test_set_columns_consistent(strength):
    for code, r in strength.iterrows():
        assert r["bb_sets"] >= 0 and r["db_sets"] >= 0
    assert strength.loc["STR_MS2_HEAVY", "bb_sets"] == 10
    assert strength.loc["STR_MS2_HEAVY", "sets_posterior_chain"] == 7
    assert strength.loc["STR_SM2_MAINT", "bb_sets"] == 2
