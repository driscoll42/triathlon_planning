"""TASK-023 (ADR-036): the ``Phase`` enum replaces substring phase matching."""
import pytest

from triplan.control import periodization as per
from triplan.control.mpc import ClosedLoopMPCController as MPC
from triplan.model.phase import Phase
from triplan.physiology.athlete_profile import CURRENT_ATHLETE


def test_general_foundation_is_base_not_the_build_1_default():
    """The old ``"base" in phase_name`` test missed it and fell through to Build 1 (heavy strength 1, durability 25)."""
    assert Phase.parse("General Foundation") is Phase.BASE
    foundation = MPC.configure_mesocycle_phase("General Foundation")
    base = MPC.configure_mesocycle_phase(Phase.BASE)
    assert foundation == base
    assert foundation.required_heavy_strength_sessions == 2 and foundation.w_durability == 10.0


def test_every_display_and_step_label_the_season_generates_resolves():
    names = [n for dist in per._PHASE_NAMES.values() for n in dist] + list(per._REENTRY_PHASES)
    assert names
    for n in names:
        Phase.parse(n)
    for dist in ("sprint", "olympic", "70.3", "ironman"):
        for w in per.build_season_matrix(dist, reentry_weeks=6):
            assert Phase.parse(w.phase_name) in Phase
            assert Phase.parse(w.step_type) is not None


def test_unknown_labels_raise_instead_of_defaulting():
    with pytest.raises(ValueError):
        Phase.parse("Mesocycle Week 2")
    with pytest.raises(ValueError):
        MPC.configure_mesocycle_phase("Peak Threshold Blast")
    assert Phase.try_parse("Mesocycle Week 2") is None and Phase.try_parse(None) is None


@pytest.mark.parametrize("label,phase", [
    ("base", Phase.BASE), ("Build", Phase.BUILD), ("Build (+10%)", Phase.BUILD), ("Build 2 Overload", Phase.OVERLOAD),
    ("Overload (+10%)", Phase.OVERLOAD), ("Recovery Deload (-20%)", Phase.RECOVERY), ("Taper (-25%)", Phase.TAPER),
    ("Race Week (-50%)", Phase.RACE), ("Taper & Sharpen", Phase.TAPER), (Phase.RACE, Phase.RACE),
])
def test_parse_known_labels(label, phase):
    assert Phase.parse(label) is phase


def test_phase_equals_its_string_value_so_summaries_and_csvs_are_unchanged():
    assert Phase.RECOVERY == "recovery" and {"recovery": 1}[Phase.RECOVERY] == 1
    assert [w.role for w in per.build_season_matrix("sprint")][:2] == ["base", "build"]
    assert all(isinstance(w.role, Phase) for w in per.build_season_matrix("olympic", reentry_weeks=4))


def test_race_week_shares_the_taper_targets_instead_of_falling_through_to_build_1():
    assert MPC.configure_mesocycle_phase(Phase.RACE) == MPC.configure_mesocycle_phase(Phase.TAPER)
    assert MPC.configure_mesocycle_phase(Phase.RACE).required_heavy_strength_sessions == 0
    assert MPC.configure_mesocycle_phase(Phase.BUILD).required_heavy_strength_sessions == 1


def test_profile_config_uses_phase_is_deload_for_maintenance_strength():
    for label in ("recovery", "Taper", Phase.RACE):
        cfg = CURRENT_ATHLETE.to_mesocycle_config(label)
        assert cfg.required_heavy_strength_sessions == 0
        n = CURRENT_ATHLETE.recovery_week_strength_sessions
        assert cfg.sport_freq_bounds["Strength"] == (n, n)
    assert CURRENT_ATHLETE.to_mesocycle_config(Phase.BUILD).sport_freq_bounds["Strength"] != (0, 0)


def test_hybrid_wave_selects_week_by_phase_only_when_it_names_one():
    wave4 = MPC.configure_hybrid_mesocycle_phase(None, week_in_cycle=4)
    assert MPC.configure_hybrid_mesocycle_phase(Phase.RECOVERY, week_in_cycle=1) == wave4
    assert MPC.configure_hybrid_mesocycle_phase("Mesocycle Week 2", week_in_cycle=2).target_weekly_tss == \
        pytest.approx(round(610.0 * 1.06, 1))


def test_tte_mode_follows_the_phase():
    modes = {p: per.tte_mode_for(p) for p in Phase}
    assert modes[Phase.BASE] == modes[Phase.BUILD] == modes[Phase.OVERLOAD] == "progress"
    assert modes[Phase.RECOVERY] == "deload" and modes[Phase.TAPER] == modes[Phase.RACE] == "off"
