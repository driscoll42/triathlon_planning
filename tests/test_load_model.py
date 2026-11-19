"""TASK-021 / ADR-034: unified ANS cost, mechanical strain, strength TSS and exact Banister PMC math."""
import math

import pandas as pd
import pytest

from triplan.library import build as lb
from triplan.library.loader import load_library
from triplan.physiology import load_model as lm
from triplan.physiology import state as st
from triplan.physiology.athlete_profile import CHARTER_MAX_48H_ANS_COST, CURRENT_ATHLETE
from triplan.model.classes import triVariables


# ---------------------------------------------------------------------------------------------------------------- ANS
def test_ans_weight_vector_is_the_spec_vector_everywhere():
    assert lm.ANS_WEIGHTS == (0.0, 0.0, 0.7, 1.0, 1.4, 1.8, 3.5)
    assert triVariables.ans_cost_7zones == dict(zip(("Z1", "Z2", "ZX", "Z3", "ZY", "Z4", "Z5"), lm.ANS_WEIGHTS))
    zones = CURRENT_ATHLETE.get_8020_7zones("run")
    assert [zones[z]["ans_cost_weight"] for z in lm.ZONES7] == list(lm.ANS_WEIGHTS)


def test_ans_cost_weights_minutes_and_exempts_alactic_strides():
    # 10 min each in Z1 Z2 ZX Z3 ZY Z4 Z5 = 70 min (< 90, no surcharge)
    z7 = [10.0] * 7
    assert lm.ans_cost(z7) == pytest.approx(10 * (0.7 + 1.0 + 1.4 + 1.8 + 3.5))
    assert lm.ans_cost(z7, has_strides=True) == pytest.approx(10 * (0.7 + 1.0 + 1.4 + 1.8))   # Z5 strides exempt
    # legacy 5-zone layout (Z1, Z2, Z3, Z4, Z5) maps Z3 -> weight 1.0, Z4 -> 1.8, Z5 -> 3.5
    assert lm.ans_cost([10.0, 10.0, 10.0, 10.0, 10.0]) == pytest.approx(10 * (1.0 + 1.8 + 3.5))
    assert lm.ans_cost([0.0, 0.0, 0.0, 0.0, 5.0], has_strides=True) == 0.0
    assert lb.ans_cost([0.0, 0.0, 5.0, 0.0, 0.0]) == lm.ans_cost([0.0, 0.0, 5.0, 0.0, 0.0])
    with pytest.raises(ValueError):
        lm.ans_cost([1.0, 2.0, 3.0])


def test_long_easy_sessions_carry_only_the_duration_surcharge():
    assert lm.ans_cost([30.0, 60.0, 0, 0, 0]) == 0.0                       # 90 min easy: free
    assert lm.ans_cost([60.0, 180.0, 0, 0, 0]) == pytest.approx(lm.ANS_DURATION_SURCHARGE * 150.0)   # 240 min ride
    # TASK-050: a 240 min Z1/Z2 ride cost 372 on the old e^(0.65 (z-1)) scale (= 53% of the 700 cap); now 52.5 vs cap 120
    assert lm.ans_cost([60.0, 180.0, 0, 0, 0]) < 0.5 * CHARTER_MAX_48H_ANS_COST


# ---------------------------------------------------------------------------------------------------------- Strain
@pytest.mark.parametrize("sport,if_,legacy", [
    ("Run", 0.82, lambda t, i: (100.0 * i ** 2 * t / 60.0) * i),                   # TSS x IF
    ("Bike", 0.765, lambda t, i: 0.10 * (100.0 * i ** 2 * t / 60.0) * i ** 0.2),   # 0.10 TSS IF^0.2
    ("Swim", 0.875, lambda t, i: 0.05 * (100.0 * i ** 3 * t / 60.0)),              # 0.05 sTSS (sTSS = IF^3 h 100)
])
def test_uniform_session_reproduces_the_pre_task021_closed_forms(sport, if_, legacy):
    assert lm.uniform_strain(sport, 60.0, if_) == pytest.approx(legacy(60.0, if_), rel=1e-9)
    assert lb.mechanical_strain(sport, 0.0, if_, 60.0) == pytest.approx(legacy(60.0, if_), abs=0.06)


def test_zone_strain_is_additive_and_intensity_steep():
    easy = lm.zone_strain("Run", [60.0, 0, 0, 0, 0])
    assert lm.zone_strain("Run", [30.0, 0, 0, 0, 0]) * 2 == pytest.approx(easy)
    assert lm.zone_strain("Run", [0.0, 0, 0, 0, 60.0]) > 9 * easy             # beta = 3: (1.35 / 0.63)^3 = 9.8x per minute
    # the 7-zone layout adds ZX / ZY between the 5-zone buckets
    z5 = [10.0, 10.0, 10.0, 10.0, 10.0]
    assert lm.zone_strain("Run", z5) == pytest.approx(lm.zone_strain("Run", lm.to_zones7(z5)))
    assert lm.zone_strain("Run", [0, 0, 10.0, 0, 0, 0, 0]) < lm.zone_strain("Run", [0, 0, 0, 10.0, 0, 0, 0])


def test_brick_strain_charges_run_leg_on_run_model_and_remainder_on_bike_model():
    z_total, z_run = [10.0, 35.0, 0, 0, 0], [0.0, 15.0, 0, 0, 0]
    z_bike = [10.0, 20.0, 0, 0, 0]
    assert lm.brick_strain(z_total, z_run) == pytest.approx(lm.zone_strain("Run", z_run) + lm.zone_strain("Bike", z_bike))
    assert lm.brick_strain(z_total, z_run) >= lm.zone_strain("Run", z_run)


def test_strength_strain_is_set_intensity_not_tss_based():
    hv = ["STR_HV1_SQUAT", "STR_HV2_DEADLIFT", "STR_HV3_SQUAT_SL", "STR_HV4_DEADLIFT_SL"]
    assert sum(lm.strength_strain(c) for c in hv) / 4 == pytest.approx(145.8, abs=0.2)   # TASK-033 operating point
    heavy = [("lower", 3, 3, 0.875)]
    light = [("lower", 3, 3, 0.50)]
    assert lm.strength_strain(heavy) > 9 * lm.strength_strain(light)                     # (0.875 / 0.5)^4 = 9.4x
    assert lm.strength_strain([("upper", 3, 5, 0.85)]) < lm.strength_strain([("lower", 3, 5, 0.85)])
    assert lm.strength_strain("STR_MS1_HEAVY") > lm.strength_strain("STR_SM1_MAINT") > lm.strength_strain("STR_DELOAD")


def test_strength_tss_is_10_to_20_per_hour():
    assert lm.strength_tss(60.0, [60.0, 0, 0, 0, 0]) == pytest.approx(10.0)
    assert lm.strength_tss(60.0, [0.0, 60.0, 0, 0, 0]) == pytest.approx(20.0)
    assert lm.strength_tss(45.0, [20.0, 20.0, 0, 5.0, 0]) == pytest.approx(0.75 * (10 + 10 * 25.0 / 45.0), abs=0.01)
    # the old stacked formula gave a heavy session 56 TSS in 60 min
    assert lm.strength_tss(60.0, [25.0, 25.0, 0, 10.0, 0]) < 20.0


# -------------------------------------------------------------------------------------------------- Banister decay
def test_exact_banister_alphas():
    assert st.ALPHA_CTL == pytest.approx(1.0 - math.exp(-1.0 / 42.0), rel=1e-12)
    assert st.ALPHA_ATL == pytest.approx(1.0 - math.exp(-1.0 / 7.0), rel=1e-12)
    assert st.ALPHA_ACUTE == pytest.approx(2.0 / 8.0)        # running ACWR keeps the published 2/(N+1) EWMA convention
    assert st.ALPHA_CHRONIC == pytest.approx(2.0 / 29.0)


def test_42_days_of_100_tss_gives_ctl_63_not_86():
    s = st.AthletePhysiologicalState(total_ctl=0.0, total_atl=0.0, bike_ctl=0.0, bike_atl=0.0, run_ctl=0.0, run_atl=0.0,
                                     swim_ctl=0.0, swim_atl=0.0)
    for _ in range(42):
        s.step_day(100.0, 0.0, 100.0, 0.0, 0.0)
    assert s.total_ctl == pytest.approx(100.0 * (1.0 - math.exp(-42.0 / 42.0)), rel=1e-9)   # 63.21
    assert s.bike_ctl == pytest.approx(63.2, abs=0.05)
    assert s.total_atl == pytest.approx(100.0 * (1.0 - math.exp(-6.0)), rel=1e-9)


def test_total_ctl_includes_strength_tss_like_metabolic_fitness():
    kw = dict(total_ctl=0.0, total_atl=0.0, bike_ctl=0.0, bike_atl=0.0, run_ctl=0.0, run_atl=0.0, swim_ctl=0.0, swim_atl=0.0,
              metabolic_fitness_state=0.0)
    a, b = st.AthletePhysiologicalState(**kw), st.AthletePhysiologicalState(**kw)
    for _ in range(14):
        a.step_day(115.0, 0.0, 100.0, 0.0, 0.0)                         # 15 TSS of strength folded into the all-sport total
        b.step_day(115.0, 0.0, 100.0, 0.0, 0.0, daily_strength_tss=15.0)
    assert a.total_ctl == pytest.approx(b.total_ctl) and a.total_atl == pytest.approx(b.total_atl)
    expected = 115.0 * (1.0 - (1.0 - st.ALPHA_CTL) ** 14)
    assert a.total_ctl == pytest.approx(expected, rel=1e-9)
    assert a.bike_ctl == pytest.approx(100.0 * (1.0 - (1.0 - st.ALPHA_CTL) ** 14), rel=1e-9)   # per-sport CTL excludes it
    # metabolic fitness M(t) integrates the same all-sport load with the exact tau = 35 d decay
    assert a.metabolic_fitness_state == pytest.approx(115.0 * (1.0 - math.exp(-14.0 / 35.0)), rel=1e-9)


# ---------------------------------------------------------------------------------- Library coherence (data/ CSV)
@pytest.fixture(scope="module")
def library_df():
    return load_library(profile=CURRENT_ATHLETE)


def _z7(r):
    return [0.0 if pd.isna(r[c]) else float(r[c]) for c in ("z1_min", "z2_min", "zx_min", "z3_min", "zy_min", "z4_min", "z5_min")]


def test_every_library_row_uses_the_unified_ans_and_strain(library_df):
    for _, r in library_df.iterrows():
        z7, dur = _z7(r), float(r["duration_min"])
        strides = bool(r["has_strides"]) if not pd.isna(r["has_strides"]) else False
        assert r["ans_cost"] == pytest.approx(lm.ans_cost(z7, strides, dur), abs=0.06), r["code"]
        if r["sport"] in ("Run", "Bike", "Swim"):
            assert r["mechanical_strain"] == pytest.approx(lm.zone_strain(r["sport"], z7), abs=0.06), r["code"]
        if r["sport"] == "Strength":
            assert r["mechanical_strain"] == pytest.approx(lm.strength_strain(r["code"]), abs=0.06), r["code"]


def test_every_strength_row_has_realistic_aerobic_tss(library_df):
    s = library_df[library_df["sport"] == "Strength"]
    assert len(s) >= 20 and set(s["code"]) <= set(lm.STRENGTH_SET_SPECS)
    per_hour = s["tss"] / (s["duration_min"] / 60.0)
    assert per_hour.between(10.0 - 0.1, 20.0 + 0.1).all(), per_hour[~per_hour.between(9.9, 20.1)]


def test_recompute_is_idempotent(library_df):
    again = lb.recompute_load_columns(library_df)
    for c in ("ans_cost", "mechanical_strain", "tss", "neuromuscular_load", "axial_load", "brick_run_strain"):
        pd.testing.assert_series_equal(again[c], library_df[c], check_names=False, atol=0.06, rtol=0)


def test_brick_run_leg_strain_is_the_run_model_of_the_run_zones():
    loads = lb.brick_run_loads([0.0, 10.0, 0.0, 5.0, 0.0])
    assert loads["brick_run_strain"] == pytest.approx(lm.zone_strain("Run", [0.0, 10.0, 0.0, 5.0, 0.0]), abs=0.06)
