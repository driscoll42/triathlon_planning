"""Tests for TASK-002: Swim TP export & calibration, 1-3s jitter, and CP retest telemetry wiring."""

from __future__ import annotations

from datetime import datetime
import importlib.util
from pathlib import Path
import re
from unittest.mock import patch

import pytest

from triplan.control.actuals import ActualsStore, CompletedActivity
from triplan.control.mpc import ClosedLoopMPCController
from triplan.io.fit_ingest import ParsedActivity, compute_aerobic_decoupling
from triplan.io.trainingpeaks import (
    TrainingPeaksClient,
    build_tp_workout_payload,
    fetch_8020_workouts,
    load_8020_swim_workouts_from_library,
)
from triplan.paths import REPO_ROOT


def test_swim_library_ingestion_and_tp_payload_calibration():
    workouts = load_8020_swim_workouts_from_library()
    assert len(workouts) == 71

    for w in workouts:
        payload = build_tp_workout_payload(w, library_id=999, swim_css=35.29)
        assert payload["workoutTypeId"] == 1
        assert payload["itemName"] == w["code"]
        assert payload["distancePlanned"] > 0
        assert payload["totalTimePlanned"] > 0
        assert payload["tssPlanned"] > 0
        assert payload["ifPlanned"] > 0
        struct = payload["structure"]
        assert struct["primaryLengthMetric"] == "distance"
        assert struct["primaryIntensityMetric"] == "percentOfThresholdPace"
        assert struct["visualizationDistanceUnit"] == "meter"
        assert len(struct["structure"]) >= 1

    # Verify rest steps are excluded from active distance and CSS calibration scales duration
    sci = next(w for w in workouts if w["code"] == "SCI01")
    p_slow = build_tp_workout_payload(sci, library_id=1, swim_css=30.0)
    p_fast = build_tp_workout_payload(sci, library_id=1, swim_css=40.0)
    assert p_slow["distancePlanned"] == pytest.approx(1500.0)
    assert p_fast["distancePlanned"] == pytest.approx(1500.0)
    assert p_fast["totalTimePlanned"] < p_slow["totalTimePlanned"]


def test_fetch_8020_swim_from_html_and_run_compat():
    html = """
    <table>
      <tr><th>Code</th><th>Distance</th><th>Description</th></tr>
      <tr><td>SAe1</td><td>2500</td><td>250 Zone 1, 4 x (500 Zone 2, 60" rest) 250 Zone 1</td></tr>
      <tr><td>STT1</td><td>1850</td><td>250 Zone 1, 400 Zone 3, 120" rest, 200 Zone 3, 120" rest, 1000 Zone 1</td></tr>
    </table>
    """
    swims = fetch_8020_workouts(html_content=html, sport="swim")
    assert [w["code"] for w in swims] == ["SAe01", "STT01"]
    p_sae = build_tp_workout_payload(swims[0], library_id=42)
    assert p_sae["workoutTypeId"] == 1
    assert p_sae["distancePlanned"] == pytest.approx(2500.0)
    # SAe01 has 3 top-level blocks: warmUp 250, rep 4x500 (2000), coolDown 250
    blocks = p_sae["structure"]["structure"]
    assert blocks[0]["begin"] == 0 and blocks[0]["end"] == 250
    assert blocks[1]["begin"] == 250 and blocks[1]["end"] == 2250
    assert blocks[2]["begin"] == 2250 and blocks[2]["end"] == 2500

    # Running backwards compatibility
    run_wkt = {
        "code": "RCI01",
        "description": "10 minutes Zone 1, 4 x (3 minutes Zone 3 / 2 minutes Zone 1), 10 minutes Zone 1",
        "total_duration": 2400,
    }
    p_run = build_tp_workout_payload(run_wkt, library_id=42, metric="power")
    assert p_run["workoutTypeId"] == 3
    assert p_run["structure"]["primaryIntensityMetric"] == "percentOfFtp"


def test_trainingpeaks_and_garmin_1_to_3s_jitter_rate_limiting():
    client = TrainingPeaksClient(auth_cookie="dummy", library_id=1)
    assert client.jitter_range == (1.0, 3.0)
    with pytest.raises(ValueError):
        TrainingPeaksClient(auth_cookie="dummy", library_id=1, jitter_range=(0.5, 1.0))

    sleeps = []
    fake_now = [100.0]

    def fake_time():
        return fake_now[0]

    def fake_sleep(sec):
        sleeps.append(sec)
        fake_now[0] += sec

    with patch("triplan.io.trainingpeaks.time.time", side_effect=fake_time), \
         patch("triplan.io.trainingpeaks.time.sleep", side_effect=fake_sleep):
        client._rate_limit()  # first request: no sleep needed
        assert sleeps == []
        fake_now[0] += 0.05
        client._rate_limit()  # second request: must sleep in [0.95, 2.95] so interval in [1.0, 3.0]
        assert len(sleeps) == 1
        assert 0.95 <= sleeps[0] <= 2.95

    # Verify scripts/sync_garmin_data.py polite_jitter_sleep and absence of sub-1s time.sleep
    garmin_script = REPO_ROOT / "scripts" / "sync_garmin_data.py"
    src_text = garmin_script.read_text(encoding="utf-8")
    for m in re.finditer(r"time\.sleep\(([\d\.]+)\)", src_text):
        assert float(m.group(1)) >= 1.0, f"Found sub-1s sleep in sync_garmin_data.py: {m.group(0)}"

    spec = importlib.util.spec_from_file_location("sync_garmin_data", garmin_script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    with patch.object(mod.time, "sleep") as mock_sleep:
        delay = mod.polite_jitter_sleep()
        assert 1.0 <= delay <= 3.0
        mock_sleep.assert_called_once_with(delay)


def test_cp_retest_triggers_wired_to_parsed_decoupling_and_ef(tmp_path: Path):
    # 1. Verify aerobic decoupling & EF calculation
    powers_steady = [200.0] * 120
    hrs_steady = [140.0] * 120
    ef_steady, dec_steady = compute_aerobic_decoupling(powers_steady, hrs_steady)
    assert ef_steady == pytest.approx(200.0 / 140.0, rel=1e-4)
    assert dec_steady == pytest.approx(0.0, abs=1e-6)

    # Second half HR drifts +10% -> EF drops -> positive decoupling
    hrs_drift = [140.0] * 60 + [154.0] * 60
    ef_drift, dec_drift = compute_aerobic_decoupling(powers_steady, hrs_drift)
    assert ef_drift is not None and dec_drift is not None
    assert dec_drift > 5.0

    # 2. Persist parsed activities in ActualsStore and trigger DATA_DRIVEN_RETEST_TRIGGERED
    store = ActualsStore(tmp_path / "actuals.json")
    # Baseline window (29..56 days prior to 2026-11-20): EF ~ 1.40
    for day_offset in (35, 42, 49):
        d = f"2026-10-{day_offset - 30:02d}" if day_offset > 30 else f"2026-09-{day_offset:02d}"
        store.record(CompletedActivity(
            activity_id=f"base_{day_offset}",
            date=d,
            sport="Bike",
            duration_min=60.0,
            intensity_factor=0.75,
            efficiency_factor=1.40,
            decoupling_pct=4.5,
        ))

    # Recent 28-day window (2026-10-24 .. 2026-11-20): EF ~ 1.50 (+7.14%), decoupling 1.8% (< 3.0%)
    for i, day_str in enumerate(("2026-11-01", "2026-11-08", "2026-11-15")):
        parsed = ParsedActivity(
            file_path=Path(f"ride_{i}.fit"),
            start_time=datetime.fromisoformat(f"{day_str}T07:00:00"),
            sport="Bike",
            duration_sec=3600.0,
            intensity_factor=0.76,
            avg_hr=140.0,
            efficiency_factor=1.50,
            decoupling_pct=1.8,
        )
        store.record_parsed(parsed)

    reloaded = ActualsStore(tmp_path / "actuals.json")
    res = ClosedLoopMPCController.evaluate_cp_retest_triggers(
        sport="bike",
        current_week=5,
        last_test_week=1,
        actuals_store=reloaded,
        as_of_date="2026-11-20",
    )
    assert res["retest_recommended"] is True
    assert res["action"] == "DATA_DRIVEN_RETEST_TRIGGERED"
    assert res["rolling_decoupling_pct"] == pytest.approx(1.8)
    assert res["rolling_ef_change_pct"] >= 5.0
