"""Tests for TASK-025 (and absorbed TASK-003): ActualsStore, unified readiness alpha_d,
Garmin wellness wiring, and intra-week receding-horizon re-solve."""

from datetime import datetime
import json
from pathlib import Path

import pytest

from triplan.control.actuals import ActualsStore, CompletedActivity
from triplan.control.mpc import ClosedLoopMPCController
from triplan.io.fit_ingest import ParsedActivity
from triplan.io.garmin_wellness import compute_wellness_snapshot
from triplan.library.candidates import build_default_candidate_library
from triplan.model.phase import Phase
from triplan.paths import DATA_DIR
from triplan.physiology.state import AthletePhysiologicalState


def test_actuals_store_persistence_and_state_advance(tmp_path: Path):
    store_path = tmp_path / "actuals.json"
    store = ActualsStore(store_path)

    parsed_run = ParsedActivity(
        file_path=Path("run.fit"),
        start_time=datetime(2026, 11, 10, 7, 0, 0),
        sport="Run",
        duration_sec=2700.0,
        tss=48.0,
        mechanical_strain=52.0,
        ans_cost_val=12.0,
        zones7_min={"Z1": 10.0, "Z2": 30.0, "ZX": 5.0, "Z3": 0.0, "ZY": 0.0, "Z4": 0.0, "Z5": 0.0},
        intensity_factor=0.80,
        avg_hr=142.0,
    )
    act_run = store.record_parsed(parsed_run, workout_code="RUN_AE2")
    assert act_run.date == "2026-11-10"
    assert act_run.duration_min == 45.0
    assert act_run.zones7_min["Z2"] == 30.0

    # Upsert idempotency
    store.record_parsed(parsed_run, workout_code="RUN_AE2", activity_id=act_run.activity_id)
    assert len(store) == 1

    # Add a bike session on the same day and a brick on 2026-11-12 (2026-11-11 is rest)
    store.record(CompletedActivity(
        activity_id="bike_1",
        date="2026-11-10",
        sport="Bike",
        duration_min=60.0,
        tss=62.0,
        mechanical_strain=30.0,
        ans_cost=10.0,
        zones7_min={"Z1": 15.0, "Z2": 45.0},
    ))
    store.record(CompletedActivity(
        activity_id="brick_1",
        date="2026-11-12",
        sport="Brick",
        duration_min=90.0,
        tss=90.0,
        mechanical_strain=70.0,
        ans_cost=25.0,
        brick_run_min=30.0,
        brick_run_strain=35.0,
        brick_run_tss=35.0,
    ))

    # Reload from disk
    reloaded = ActualsStore(store_path)
    assert len(reloaded) == 3
    d10 = reloaded.daily_totals("2026-11-10")
    assert d10["tss"] == pytest.approx(110.0)
    assert d10["run_tss"] == pytest.approx(48.0)
    assert d10["bike_tss"] == pytest.approx(62.0)
    assert d10["run_mech"] == pytest.approx(52.0)
    assert d10["zones7_min"]["Z2"] == pytest.approx(75.0)

    # Advance state from actuals across 2026-11-10..2026-11-12 (3 days incl. rest day)
    state = AthletePhysiologicalState()
    mpc = ClosedLoopMPCController(state)
    ctl_before = state.total_ctl
    run_ctl_before = state.run_ctl
    mpc.advance_state_from_actuals(reloaded, "2026-11-10", "2026-11-12")
    assert state.day_index == 3
    assert state.total_ctl != ctl_before
    assert state.run_ctl != run_ctl_before


def test_garmin_readiness_uses_rhr_std_and_unified_bounds():
    mpc = ClosedLoopMPCController()

    # When rmssd_today == rmssd_baseline_mean, z_hrv == 0, so changing rmssd_baseline_std
    # MUST NOT alter alpha_d; changing rhr_baseline_std MUST alter alpha_d.
    a1 = mpc.calculate_garmin_morning_readiness(
        rmssd_today=50.0,
        rmssd_baseline_mean=50.0,
        rmssd_baseline_std=20.0,
        rhr_today=56.0,
        rhr_baseline_mean=50.0,
        rhr_baseline_std=2.0,
        sleep_score=80.0,
    )
    a2 = mpc.calculate_garmin_morning_readiness(
        rmssd_today=50.0,
        rmssd_baseline_mean=50.0,
        rmssd_baseline_std=2.0,
        rhr_today=56.0,
        rhr_baseline_mean=50.0,
        rhr_baseline_std=2.0,
        sleep_score=80.0,
    )
    assert a1 == pytest.approx(a2, abs=1e-9)
    # z_rhr = (56-50)/2 = 3.0 -> 1.00 - 0.05*3.0 = 0.85
    assert a1 == pytest.approx(0.85, abs=1e-6)

    a_wider_rhr_sd = mpc.calculate_garmin_morning_readiness(
        rmssd_today=50.0,
        rmssd_baseline_mean=50.0,
        rmssd_baseline_std=20.0,
        rhr_today=56.0,
        rhr_baseline_mean=50.0,
        rhr_baseline_std=6.0,
        sleep_score=80.0,
    )
    assert a_wider_rhr_sd > a1

    # Unified bounds [0.60, 1.00] across both methods
    s_sick = mpc.calculate_morning_readiness(15.0, 60.0, 4.0, 8.0)
    s_fresh = mpc.calculate_morning_readiness(95.0, 60.0, 9.5, 1.0)
    a_sick = mpc.calculate_garmin_morning_readiness(15.0, 60.0, 6.0, 65.0, 48.0, 40.0, rhr_baseline_std=2.0)
    a_fresh = mpc.calculate_garmin_morning_readiness(95.0, 60.0, 6.0, 42.0, 48.0, 95.0, rhr_baseline_std=2.0)
    assert s_sick == pytest.approx(0.60)
    assert a_sick == pytest.approx(0.60)
    assert s_fresh == pytest.approx(1.00)
    assert a_fresh == pytest.approx(1.00)

    # Luteal phase adjustment prevents false Red Alert when RMSSD is ~85% of baseline
    s_luteal = mpc.calculate_morning_readiness(51.0, 60.0, 8.0, 2.0, menstrual_phase="luteal")
    assert s_luteal >= 0.95


def test_garmin_wellness_snapshot_synthetic_and_live(tmp_path: Path):
    # Synthetic 10-day fixture
    hrv_rows = [
        {"calendarDate": f"2026-10-{10 + i:02d}", "lastNightAvg": 50.0 + (i % 3)}
        for i in range(9)
    ] + [{"calendarDate": "2026-10-19", "lastNightAvg": 35.0}]
    daily_rows = [
        {"calendarDate": f"2026-10-{10 + i:02d}", "heartRate": {"restingValue": 50.0 + (i % 2)}}
        for i in range(9)
    ] + [{"calendarDate": "2026-10-19", "heartRate": {"restingValue": 57.0}}]
    sleep_rows = [{"calendarDate": "2026-10-19", "sleepScores": {"overall": {"value": 60.0}}}]

    (tmp_path / "hrv_summaries.json").write_text(json.dumps({"hrvSummaries": hrv_rows}), encoding="utf-8")
    (tmp_path / "daily_summaries.json").write_text(json.dumps({"data": daily_rows}), encoding="utf-8")
    (tmp_path / "sleep_summaries.json").write_text(json.dumps(sleep_rows), encoding="utf-8")

    snap = compute_wellness_snapshot("2026-10-19", wellness_dir=tmp_path)
    assert snap.rmssd_today == 35.0
    assert snap.rhr_today == 57.0
    assert snap.sleep_score == 60.0
    assert snap.rhr_baseline_std is not None and snap.rhr_baseline_std > 0
    assert 0.60 <= snap.alpha_d < 0.80

    live_hrv = DATA_DIR / "garmin" / "wellness" / "hrv_summaries.json"
    if live_hrv.exists():
        live_snap = ClosedLoopMPCController.readiness_from_garmin_wellness("2026-11-08")
        assert live_snap.rmssd_today == 39.0
        assert live_snap.rhr_today == 55.0
        assert live_snap.sleep_score == 89.0
        assert 0.60 <= live_snap.alpha_d <= 1.00


def test_intra_week_receding_horizon_resolve():
    lib = build_default_candidate_library()
    mpc = ClosedLoopMPCController()
    cfg = mpc.configure_mesocycle_phase(Phase.BASE, base_tss=420.0, race_distance="olympic")

    # Initial solve for full week
    initial_plans = mpc.reoptimize_remaining_week(
        library=lib,
        config=cfg,
        today_day_idx=0,
        completed_days={},
        k_plans=1,
        time_limit=10.0,
    )
    assert initial_plans, "Initial week solve should be feasible"
    init_sched = initial_plans[0]["schedule"]

    # Days 0 (Mon) and 1 (Tue) are completed; today is Day 2 (Wed) with Red Alert alpha_d = 0.65
    completed = {0: list(init_sched[0]), 1: list(init_sched[1])}
    reopt_plans = mpc.reoptimize_remaining_week(
        library=lib,
        config=cfg,
        today_day_idx=2,
        completed_days=completed,
        today_readiness_alpha=0.65,
        k_plans=1,
        time_limit=10.0,
    )
    assert reopt_plans, "Intra-week receding-horizon re-solve should be feasible"
    reopt_sched = reopt_plans[0]["schedule"]

    # Completed days d < today must remain fixed verbatim
    for d in (0, 1):
        assert [w.code for w in reopt_sched[d]] == [w.code for w in completed[d]]

    # Today (d = 2) under Red Alert (alpha < 0.75) must not schedule hard endurance or heavy strength
    for w in reopt_sched[2]:
        assert not w.is_hard_endurance
        assert not w.is_heavy_strength
