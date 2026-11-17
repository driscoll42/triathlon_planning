"""TASK-046: attribute single-run weeks to the row that binds them (diagnosis only, no constraint changes)."""
import pandas as pd
import pytest

from triplan.control.feasibility import (SINGLE_RUN_ROWS, attribute_single_run_week, run_session_count,
                                         season_infeasibility_report, single_run_message)
from triplan.physiology.athlete_profile import CURRENT_ATHLETE as P
from triplan.solver.engine.carry_in import LoadState, WeekCarryIn
from triplan.solver.scheduler import TriathlonMILPScheduler


def _week(lib, cfg_mod=None, **load_kw):
    cfg = P.to_mesocycle_config(phase_name="Build", tss_target=200.0)
    if cfg_mod:
        cfg_mod(cfg)
    carry = WeekCarryIn(load=LoadState(run_acute=30.0, run_chronic=30.0, total_ctl=30.0, run_ctl=20.0,
                                       acwr_max=1.25, acwr_min=0.0, **load_kw))
    plans = TriathlonMILPScheduler(lib, cfg, carry_in=carry).solve_top_k_plans(k_plans=1, time_limit=3.0)
    return cfg, carry, run_session_count(plans[0]["schedule"])


def test_unconstrained_week_is_not_single_run(default_library):
    assert _week(default_library)[2] >= 2


def test_run_minutes_guard_is_named(default_library):
    cfg, carry, n = _week(default_library, run_minutes_cap=35.0)
    assert n == 1
    out = attribute_single_run_week(default_library, cfg, carry, time_limit=1.5)
    assert out["binding"] == ["run-minutes guard"] and out["label"] == "run-minutes guard"


def test_weekly_run_strain_cap_is_named(default_library):
    cfg, carry, n = _week(default_library, cfg_mod=lambda c: setattr(c, "max_weekly_run_mech_strain", 25.0))
    assert n == 1
    out = attribute_single_run_week(default_library, cfg, carry, time_limit=1.5)
    assert out["binding"] == ["weekly run strain cap"]


def test_diagnosis_does_not_mutate_the_week(default_library):
    cfg, carry, _ = _week(default_library, run_minutes_cap=35.0)
    cap, strain = carry.load.run_minutes_cap, cfg.max_weekly_run_mech_strain
    attribute_single_run_week(default_library, cfg, carry, time_limit=1.0)
    assert carry.load.run_minutes_cap == cap and cfg.max_weekly_run_mech_strain == strain


def test_season_message_rolls_up_by_week_range_and_label():
    df = pd.DataFrame({"Week": [1, 2, 3, 5, 6], "Single_Run_Binding": ["run-minutes guard"] * 3 + ["", "weekly run strain cap"],
                       "Relaxations": "", "Diagnostics": "", "Target_TSS": 100.0, "Solved_TSS": 100.0})
    msgs = single_run_message(df)
    assert any("run-minutes guard: 3 of 5 week(s) (1-3)" in m for m in msgs)
    assert any("weekly run strain cap: 1 of 5 week(s) (6)" in m for m in msgs)
    assert any("Single-run" in m for m in season_infeasibility_report(df, None, "olympic"))
    assert set(SINGLE_RUN_ROWS) >= {"run-minutes guard", "running ACWR ceiling", "weekly run strain cap", "long-run cap"}
