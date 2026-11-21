"""Unit tests for EXP-006 load-model calibration functions."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

import sys
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from exp006_load_calibration import (  # noqa: E402
    analyze_banister_pmc,
    analyze_lifting_overlap,
    analyze_readiness_calibration,
    analyze_run_strain_beta,
    analyze_zone_midpoints,
)
from triplan.io.fit_ingest import ParsedActivity  # noqa: E402


def test_banister_pmc_step_response_and_history():
    # 60 days of constant 100 TSS/day
    daily = {
        (datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat(): 100.0
        for i in range(60)
    }
    res = analyze_banister_pmc(daily)
    assert res["step_42d_exact_rel_err_pct"] < 1e-6
    assert res["step_7d_exact_rel_err_pct"] < 1e-6
    assert res["step_42d_exact_ctl"] == pytest.approx(63.212, abs=0.05)
    assert res["step_42d_legacy_ctl"] == pytest.approx(86.46, abs=0.10)
    assert res["step_42d_legacy_divergence_pct"] > 35.0
    assert res["historical_ctl_active_days"] == 18


def test_zone_midpoints_and_run_strain_beta_on_synthetic_activities():
    acts = []
    for i in range(40):
        dt = datetime(2026, 3, 1, 7, 0, 0) + timedelta(days=i)
        # Easy 45-min Z2 run + 60-min Z2 bike every 2 days
        acts.append(ParsedActivity(
            file_path=Path(f"run_{i}.fit"),
            start_time=dt,
            sport="Run",
            duration_sec=2700.0,
            intensity_factor=0.79,
            tss=46.8,
            zones7_min={"Z1": 10.0, "Z2": 35.0, "ZX": 0.0, "Z3": 0.0, "ZY": 0.0, "Z4": 0.0, "Z5": 0.0},
        ))
        if i % 2 == 0:
            acts.append(ParsedActivity(
                file_path=Path(f"bike_{i}.fit"),
                start_time=dt + timedelta(hours=4),
                sport="Bike",
                duration_sec=3600.0,
                intensity_factor=0.74,
                tss=54.8,
                zones7_min={"Z1": 15.0, "Z2": 45.0, "ZX": 0.0, "Z3": 0.0, "ZY": 0.0, "Z4": 0.0, "Z5": 0.0},
            ))

    zm = analyze_zone_midpoints(acts, run_cp=200.0, bike_cp=135.0)
    assert zm["Run"]["n_easy_sessions"] == 40
    assert zm["Run"]["easy_if_median"] == pytest.approx(0.79, abs=1e-6)
    assert zm["Bike"]["n_easy_sessions"] == 20
    assert zm["Bike"]["easy_if_median"] == pytest.approx(0.74, abs=1e-6)

    sb = analyze_run_strain_beta(acts, betas=(1.0, 2.0, 3.0))
    assert sb["n_runs"] == 40
    assert set(sb["betas"].keys()) == {"1.0", "2.0", "3.0"}
    assert sb["betas"]["3.0"]["z4_over_z2_weight_ratio"] > sb["betas"]["2.0"]["z4_over_z2_weight_ratio"]


def test_readiness_calibration_and_lifting_overlap(tmp_path: Path):
    wdir = tmp_path / "wellness"
    wdir.mkdir()
    hrv_items = []
    daily_items = []
    sleep_items = []
    for i in range(40):
        d = (datetime(2026, 4, 1) + timedelta(days=i)).date().isoformat()
        # Deterministic oscillation with one suppressed night
        rmssd = 30.0 if i == 35 else 50.0 + (i % 5) * 2.0
        rhr = 58.0 if i == 35 else 50.0 + (i % 3)
        hrv_items.append({"calendarDate": d, "lastNightAvg": rmssd})
        daily_items.append({"calendarDate": d, "heartRate": {"restingValue": rhr}})
        sleep_items.append({"calendarDate": d, "sleepScores": {"overall": {"value": 80}}})

    (wdir / "hrv_summaries.json").write_text(json.dumps({"hrvSummaries": hrv_items}), encoding="utf-8")
    (wdir / "daily_summaries.json").write_text(json.dumps({"data": daily_items}), encoding="utf-8")
    (wdir / "sleep_summaries.json").write_text(json.dumps({"sleepSummaries": sleep_items}), encoding="utf-8")

    res = analyze_readiness_calibration(wdir, window_days=28, min_history=7)
    assert res["total_hrv_nights"] == 40
    assert res["evaluated_nights_with_min_history"] == 33
    assert 0.60 <= res["unified_task025_008_005_004"]["p5"] <= 1.00

    lpath = tmp_path / "lifting.json"
    lpath.write_text(json.dumps([
        {"date": "2026-04-02", "estimated_axial_load": 50.0},
        {"date": "2026-04-05", "estimated_axial_load": 60.0},
    ]), encoding="utf-8")
    acts = [
        ParsedActivity(file_path=Path("r1.fit"), start_time=datetime(2026, 4, 2, 7, 0), sport="Run"),
        ParsedActivity(file_path=Path("r2.fit"), start_time=datetime(2026, 4, 6, 7, 0), sport="Run"),
    ]
    ov = analyze_lifting_overlap(acts, lpath)
    assert ov["total_lifting_sessions"] == 2
    assert ov["same_day_lift_and_run_count"] == 1
    assert ov["next_day_run_after_lift_count"] == 1
