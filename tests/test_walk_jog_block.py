"""TASK-023 (ADR-036): walk-jog re-entry impact model and progression ladder (the library rows are TASK-058)."""
import pytest

from triplan.control import periodization as per
from triplan.model.phase import Phase
from triplan.physiology import load_model as lm

Z1_10 = [10.0, 0.0, 0.0, 0.0, 0.0]


def test_walk_minutes_cost_something_but_far_less_than_running_them():
    z1_minute = lm.zone_strain("Run", [1.0, 0, 0, 0, 0])
    frac = lm.walk_dose_fraction()
    assert 0.0 < frac < 0.5                      # a walked minute is a fraction of an easy run minute...
    raw = (lm.WALK_CADENCE_SPM / lm.RUN_CADENCE_SPM) * (lm.WALK_PEAK_GRF_BW / lm.RUN_PEAK_GRF_BW) ** lm.BONE_STRAIN_EXPONENT
    assert frac == pytest.approx(lm.WALK_SAFETY_FACTOR * raw) and frac > raw    # ...inflated, never optimistic
    assert lm.run_walk_mechanical_strain(Z1_10, 10.0) == pytest.approx(lm.zone_strain("Run", Z1_10) + 10 * frac * z1_minute)


def test_without_walking_the_impact_model_is_the_run_zone_model():
    z = [5.0, 10.0, 0.0, 5.0, 0.0]
    assert lm.run_walk_mechanical_strain(z, 0.0) == pytest.approx(lm.zone_strain("Run", z))
    assert lm.run_walk_mechanical_strain(z, 1.0) > lm.zone_strain("Run", z)
    with pytest.raises(ValueError):
        lm.run_walk_mechanical_strain(z, -1.0)


def test_a_run_walk_session_is_never_costlier_than_running_all_of_it():
    total = 30.0
    for stage in per.WALKJOG_LADDER:
        run_m, walk_m = stage.split_minutes(total)
        assert run_m + walk_m == pytest.approx(total)
        mixed = lm.run_walk_mechanical_strain([run_m, 0, 0, 0, 0], walk_m)
        assert mixed < lm.zone_strain("Run", [total, 0, 0, 0, 0])
        assert lm.run_walk_contacts(run_m, walk_m) < lm.run_walk_contacts(total, 0.0)


def test_the_ladder_progresses_in_run_share_and_length():
    runs = [s.run_fraction for s in per.WALKJOG_LADDER]
    mins = [s.session_min for s in per.WALKJOG_LADDER]
    assert runs == sorted(runs) and mins == sorted(mins) and per.WALKJOG_LADDER[0].run_fraction == 0.5
    assert per.walkjog_stage(0) is None and per.walkjog_stage(99) is None and per.walkjog_stage(2).walk_s == 60


def test_rung_moves_up_in_build_weeks_and_holds_in_deload_weeks():
    n = 0
    seq = []
    for role in (Phase.BASE, Phase.BUILD, Phase.OVERLOAD, Phase.RECOVERY, Phase.BUILD, Phase.BUILD, Phase.BUILD):
        n = per.next_walkjog_stage(n, role, True)
        seq.append(n)
    assert seq == [1, 2, 3, 3, 4, 4, 4]                      # one rung per build week, held in recovery, capped at the top
    assert per.next_walkjog_stage(3, Phase.BUILD, False) == 0   # not re-entering (run CTL >= exit) -> off
