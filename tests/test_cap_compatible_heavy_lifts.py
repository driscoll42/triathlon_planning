"""TASK-033: heavy lifts that fit the profile's daily Tier-0 axial / neuromuscular caps (ADR-012 DECISION-023)."""
import re

import pytest

from triplan.control.season import _schedulable_heavy_lifts
from triplan.library.build import HEAVY_VARIANTS, strength_loads
from triplan.library.compound import make_lift_easy_combos
from triplan.library.loader import load_candidates, load_library
from triplan.library.schema import validate_table
from triplan.physiology.athlete_profile import CURRENT_ATHLETE

CODES = list(HEAVY_VARIANTS)


@pytest.fixture(scope="module")
def rows():
    lib = load_library()
    return lib[lib["code"].isin(CODES)].set_index("code")


def test_rows_present_and_schema_valid(rows):
    assert set(rows.index) == set(CODES)
    validate_table(load_library(profile=None))


def test_heavy_variants_fit_daily_caps(rows):
    cfg = CURRENT_ATHLETE.to_mesocycle_config("Build")
    for code, r in rows.iterrows():
        assert bool(r["is_heavy_strength"]), code
        assert r["axial_load"] <= cfg.max_daily_axial_load == 65.0, code
        assert r["neuromuscular_load"] <= cfg.max_daily_neuro_load == 75.0, code


def test_loads_come_from_the_shared_formula(rows):
    for code, v in HEAVY_VARIANTS.items():
        want = strength_loads(v["lower"], v["bench"])
        assert rows.loc[code, "axial_load"] == want["axial_load"]
        assert rows.loc[code, "neuromuscular_load"] == want["neuromuscular_load"]


def test_barbell_share_sets_and_flags(rows):
    for code, r in rows.iterrows():
        assert r["bb_sets"] / (r["bb_sets"] + r["db_sets"]) >= 0.60, code
        assert r["sets_anterior_chain"] + r["sets_posterior_chain"] + r["sets_push"] + r["sets_pull"] \
            == r["bb_sets"] + r["db_sets"] - (r["bb_sets"] + r["db_sets"] - (r["bb_sets"] + r["db_sets"])), code
        assert r["split_group"] == "HLM" and bool(r["heavy_press"]) and (bool(r["heavy_sq"]) ^ bool(r["heavy_dl"])), code
        assert bool(r["heavy_dl"]) == bool(re.search(r"Conventional Deadlift", r["description"])), code
        assert "heavy lift" in r["description"] and "87.5% 1RM" in r["description"]
        assert not re.search(r"trap[\s-]*bar|\bbands?\b", r["description"], re.I)  # DECISION-022


def test_default_library_now_has_schedulable_heavy_lifts():
    cfg = CURRENT_ATHLETE.to_mesocycle_config("Build")
    from triplan.library.candidates import build_default_candidate_library
    assert _schedulable_heavy_lifts(build_default_candidate_library(), cfg) >= len(CODES)


def test_lift_easy_combos_exist_for_variants_and_bike_only():
    lib = [w for w in load_candidates(library="default") if w.code in CODES]
    combos = make_lift_easy_combos(lib)
    for code in CODES:
        sports = {w.cardio_sport for w in combos if w.code.startswith(code)}
        assert sports == {"Bike"}, code      # heavy lower body: Bike only
