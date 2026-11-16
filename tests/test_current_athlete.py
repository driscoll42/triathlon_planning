"""CURRENT_ATHLETE (ADR-012) profile contract and non-fallback solve checks (TASK-008)."""
import pytest

from triplan.physiology.athlete_profile import AthleteProfile, CURRENT_ATHLETE


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
    from triplan.solver.scheduler import TriathlonMILPScheduler

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


def test_current_athlete_build_week_solves(default_library):
    """DECISION-023: with 0 heavy lifts allowed during re-entry, the profile build week solves."""
    assert CURRENT_ATHLETE.reentry_phase and CURRENT_ATHLETE.heavy_strength_sessions(2) == 0
    sched, plans = _solve_profile_week(default_library, "base")
    assert plans, " | ".join(sched.last_diagnostics)
    assert not [w for d in range(7) for w in plans[0]["schedule"][d] if w.is_heavy_strength]


def _build_rows(default_library, profile, phase="base"):
    """Build (not solve) the week model from a profile and return {row_name: (lb, ub)} for week 0."""
    from triplan.solver.engine.config import meso_options
    from triplan.solver.engine.engine import UnifiedWeekEngine

    cfg = profile.to_mesocycle_config(phase)
    ctx = UnifiedWeekEngine(default_library, cfg.__dict__, meso_options()).build_model()
    _, _, _, rlb, rub = ctx.model.csr()
    return {n: (rlb[i], rub[i]) for i, n in enumerate(ctx.model.row_names)}, ctx


def test_profile_8020_and_key_session_fields_reach_the_engine(default_library):
    """TASK-035: changing the profile values changes the engine rows / config (previously silently ignored)."""
    from dataclasses import replace

    base = CURRENT_ATHLETE
    cfg = base.to_mesocycle_config("base")
    assert (cfg.intensity_low_min, cfg.intensity_low_target, cfg.intensity_mod_high_max) == (0.78, 0.8, 0.2)
    assert cfg.key_sessions_per_week == (2, 3) and cfg.am_pm_min_gap_h == 6.0

    alt = replace(base, intensity_low_min=0.70, intensity_low_target=0.75, intensity_mod_high_max=0.30,
                  key_sessions_per_week=(1, 2), am_pm_min_gap_h=8.0)
    hy = alt.to_hybrid_config()
    assert (hy["p_low_min"], hy["intensity_low_target"], hy["p_high_max"]) == (0.70, 0.75, 0.30)
    assert hy["key_sessions_per_week"] == (1, 2) and hy["am_pm_min_gap_h"] == 8.0

    rows0, ctx0 = _build_rows(default_library, base)
    rows1, ctx1 = _build_rows(default_library, alt)
    assert ctx1.cfg.am_pm_min_gap_h == 8.0 and ctx0.cfg.am_pm_min_gap_h == 6.0
    assert ctx1.cfg.key_range == (1, 2)
    assert rows1["key_sessions_max"][1] == 2 and rows0["key_sessions_max"][1] == 3
    # the 8020 rows are ratio rows (coefficients carry p); their coefficients must differ with the profile
    import numpy as np
    def coefs(ctx, name):
        s, idx, val, _, _ = ctx.model.csr()
        r = ctx.model.row_names.index(name)
        return np.sort(val[s[r]:s[r + 1]])
    assert not np.allclose(coefs(ctx0, "8020_low_min"), coefs(ctx1, "8020_low_min"))
    assert not np.allclose(coefs(ctx0, "8020_high_max"), coefs(ctx1, "8020_high_max"))


def test_profile_validation_for_new_structure_fields():
    from dataclasses import replace

    for bad in ({"am_pm_min_gap_h": 0.0}, {"key_sessions_per_week": (3, 2)},
                {"intensity_low_target": 0.5}):
        with pytest.raises(ValueError):
            replace(CURRENT_ATHLETE, **bad)
