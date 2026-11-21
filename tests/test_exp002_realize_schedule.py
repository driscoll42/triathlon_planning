"""Tests for the EXP-002 realize_schedule hook in generate_closed_loop_season_schedule."""

from __future__ import annotations

import contextlib
import io
from dataclasses import replace

import numpy as np

from triplan.control.season import WeekRecord, generate_closed_loop_season_schedule
from triplan.library.candidates import build_default_candidate_library
from triplan.perf.season_validation import check_season
from triplan.physiology.athlete_profile import CURRENT_ATHLETE


def _scale_day(schedule, factor: float):
    out = {}
    for d, wkts in schedule.items():
        out[d] = [
            replace(
                w,
                duration=float(w.duration) * factor,
                aerobic_tss=float(w.aerobic_tss) * factor,
                mechanical_strain=float(w.mechanical_strain) * factor,
                ans_cost=float(w.ans_cost) * factor,
                axial_load=float(w.axial_load) * factor,
                neuromuscular_load=float(w.neuromuscular_load) * factor,
                cardio_minutes=float(w.cardio_minutes) * factor,
                cardio_mech_strain=float(w.cardio_mech_strain) * factor,
                brick_run_minutes=float(w.brick_run_minutes) * factor,
                brick_run_strain=float(w.brick_run_strain) * factor,
            )
            for w in wkts
        ]
    return out


def test_realize_schedule_advances_state_from_realized_loads():
    """Verify realize_schedule advances CTL/carry-in from realized rather than planned loads and stays Tier-0 clean."""
    lib = build_default_candidate_library(include_8020=True)
    records_nom: list[WeekRecord] = []
    records_half: list[WeekRecord] = []

    with contextlib.redirect_stdout(io.StringIO()):
        _, sum_nom = generate_closed_loop_season_schedule(
            profile=CURRENT_ATHLETE,
            distance="olympic",
            library=lib,
            time_limit_per_week=1.0,
            weeks_to_race=4,
            reentry_weeks=0,
            collect=records_nom,
        )
        _, sum_half = generate_closed_loop_season_schedule(
            profile=CURRENT_ATHLETE,
            distance="olympic",
            library=lib,
            time_limit_per_week=1.0,
            weeks_to_race=4,
            reentry_weeks=0,
            collect=records_half,
            realize_schedule=lambda sched, _wk: _scale_day(sched, 0.5),
        )

    assert len(records_half) == 4
    assert check_season(records_nom, CURRENT_ATHLETE) == []
    assert check_season(records_half, CURRENT_ATHLETE) == []
    assert float(sum_half["Ending_CTL"].iloc[-1]) < float(sum_nom["Ending_CTL"].iloc[-1])
