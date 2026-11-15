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


def _solve_profile_week(default_library, phase):
    from closed_loop_optimizer import TriathlonMILPScheduler

    sched = TriathlonMILPScheduler(default_library, CURRENT_ATHLETE.to_mesocycle_config(phase))
    return sched, sched.solve_top_k_plans(k_plans=1, time_limit=5.0)


@pytest.mark.parametrize("phase", ["base", "Build (+10%)", "Recovery Deload (-20%)"])
def test_profile_week_either_solves_or_explains_why(default_library, phase):
    """TASK-016: never a silent fallback. Either a plan (relaxations named) or plain-English reasons."""
    sched, plans = _solve_profile_week(default_library, phase)
    if plans:
        assert plans[0]["status"] in {"OPTIMAL", "TIME_LIMIT_FEASIBLE"}
        for r in plans[0]["relaxations"]:
            assert r["message"]
    else:
        assert sched.last_diagnostics, "infeasible week with no explanation"


def test_recovery_week_solves_with_one_maintenance_lift(default_library):
    _, plans = _solve_profile_week(default_library, "Recovery Deload (-20%)")
    assert plans
    lifts = [w for d in range(7) for w in plans[0]["schedule"][d] if w.sport == "Strength"]
    assert len(lifts) == 1 and not lifts[0].is_heavy_strength


@pytest.mark.xfail(
    reason="AWAITING ATHLETE DECISION: profile daily axial 65 / neuro 75 caps exclude the library's only heavy "
           "lift (STR_MS1_HEAVY, 85/85) while the heavy-strength floor is 1 (DECISION-006). Resolve by raising the "
           "caps, allowing 0 heavy sessions during re-entry, or adding lighter heavy lifts (TASK-028).",
    strict=True,
)
def test_current_athlete_build_week_solves(default_library):
    sched, plans = _solve_profile_week(default_library, "base")
    assert plans, " | ".join(sched.last_diagnostics)
