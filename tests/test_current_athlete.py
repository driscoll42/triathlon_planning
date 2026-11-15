"""CURRENT_ATHLETE (ADR-012) profile contract and non-fallback solve checks (TASK-008)."""
import pytest

from athlete_profile import AthleteProfile, CURRENT_ATHLETE


def test_two_point_cp_formula():
    res = AthleteProfile().calculate_two_point_cp(p3=300.0, p12=240.0)
    assert res["critical_power"] == pytest.approx((4 * 240 - 300) / 3)
    assert res["w_prime_joules"] == pytest.approx(180 * (300 - 220))


def test_css_formula():
    res = AthleteProfile().calculate_css(t400_sec=400.0, t200_sec=190.0)
    assert res["pace_100_sec"] == pytest.approx(105.0)
    assert res["css_m_per_min"] == pytest.approx(200.0 / 210.0 * 60.0)


@pytest.mark.parametrize("phase", ["Recovery Deload (-20%)", "Taper (-25%)", "Race Week (-50%)"])
def test_recovery_taper_race_keep_one_maintenance_strength_session(phase):
    cfg = CURRENT_ATHLETE.to_mesocycle_config(phase)
    assert cfg.sport_freq_bounds["Strength"] == (1, 1)  # DECISION-009
    assert cfg.required_heavy_strength_sessions == 0


def test_profile_reflects_decisions():
    p = CURRENT_ATHLETE
    assert not any(p.facility_access["Swim"]) and p.pool_open_days == []  # DECISION-007
    assert all(p.facility_access["Strength"])                              # DECISION-008
    assert p.sport_priority == ["Strength", "Bike", "Run", "Swim"]         # DECISION-006
    assert (p.mesocycle_build_weeks, p.mesocycle_recovery_weeks) == (3, 1) # DECISION-010
    assert p.to_hybrid_config()["p_low_min"] == pytest.approx(0.78)        # DECISION-013
    assert p.combo_cardio_max_minutes <= 20 and p.allow_lift_cardio_combo  # DECISION-021


@pytest.mark.xfail(
    reason="TASK-016/TASK-026: default library anchors (180m ride, 75m long run) cannot fit 45-75 min days; "
           "planner needs profile-driven candidates + Tier-1 slacks instead of the fallback.",
    strict=True,
)
def test_current_athlete_week_solves_without_fallback(default_library):
    from closed_loop_optimizer import TriathlonMILPScheduler

    cfg = CURRENT_ATHLETE.to_mesocycle_config("base")
    plans = TriathlonMILPScheduler(default_library, cfg).solve_top_k_plans(k_plans=1, time_limit=5.0)
    assert plans, "CURRENT_ATHLETE base week is infeasible -> season planner would use the fallback"
