"""Tests for post-workout & block diagnostic brief generator and review assets (PRD-003)."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from triplan.io.workout_review import (
    analyze_fit_diagnostics,
    build_review_brief,
    format_brief_markdown,
    summarize_lifting_window,
)
from triplan.paths import REPO_ROOT


@dataclass
class _StubField:
    name: str
    value: Any
    is_dev: bool = False
    field: Any = None


class _StubMessage:
    def __init__(self, values: Dict[str, Any], fields: Optional[List[_StubField]] = None):
        self._values = dict(values)
        self.fields = (
            fields
            if fields is not None
            else [_StubField(name=k, value=v) for k, v in values.items()]
        )

    def get_values(self) -> Dict[str, Any]:
        return dict(self._values)


class _StubFitFile:
    def __init__(self, messages: Dict[str, List[_StubMessage]]):
        self._messages = messages

    def get_messages(self, name: str) -> List[_StubMessage]:
        return self._messages.get(name, [])


def test_run_diagnostics_stryd_lss_decay_ecor_and_rut_flag(tmp_path: Path):
    """Run diagnostics extract Stryd CIQ power, LSS decay, ECOR, and Moderate-Intensity Rut."""
    t0 = datetime(2026, 11, 20, 7, 0, 0)
    records = []
    for i in range(60):
        lss = 10.5 if i < 30 else 9.5  # -9.52% LSS decay in 2nd half
        cad = 176 if i < 30 else 170
        records.append(
            _StubMessage(
                {},
                fields=[
                    _StubField("timestamp", t0 + timedelta(seconds=i)),
                    _StubField("heart_rate", 148),
                    _StubField("cadence", cad),
                    _StubField("enhanced_speed", 3.0),
                    _StubField("power", 260, is_dev=False),  # native inflated -> must ignore
                    _StubField("Power", 170, is_dev=True),   # 170 / 200 CP = 0.85 IF (> 0.78)
                    _StubField("Form Power", 42.5, is_dev=True),
                    _StubField("Leg Spring Stiffness", lss, is_dev=True),
                    _StubField("Air Power", 0.0, is_dev=True),
                ],
            )
        )
    stub = _StubFitFile(
        {
            "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
            "session": [
                _StubMessage(
                    {
                        "sport": "running",
                        "sub_sport": "treadmill",
                        "start_time": t0,
                        "total_timer_time": 60.0,
                        "total_elapsed_time": 60.0,
                        "total_distance": 180.0,
                    }
                )
            ],
            "lap": [_StubMessage({"total_timer_time": 60.0, "total_distance": 180.0, "avg_heart_rate": 148})],
            "record": records,
        }
    )

    plan_csv = tmp_path / "plan.csv"
    plan_csv.write_text("Code,Sport,Duration_min,TSS,Intensity,Description\nEASY_RUN,Run,30,20,EASY,Easy Run\n", encoding="utf-8")
    fit_path = tmp_path / "2026-11-20_running_1.fit"
    fit_path.write_bytes(b"")

    with patch("fitparse.FitFile", return_value=stub):
        diag = analyze_fit_diagnostics(
            fit_path,
            run_cp=200.0,
            mass_kg=75.0,
            planned_code="EASY_RUN",
            plan_csv=plan_csv,
        )

    assert diag["ok"] is True
    assert diag["sport"] == "Run"
    assert diag["avg_power_w"] == pytest.approx(170.0)
    assert diag["moderate_intensity_rut_flag"] is True
    sd = diag["sport_diagnostics"]
    assert sd["has_stryd_power"] is True
    assert sd["native_run_power_present_ignored"] is True
    assert sd["is_treadmill"] is True
    assert sd["form_power_ratio"] == pytest.approx(0.25, abs=0.002)
    assert sd["lss_first_half"] == pytest.approx(10.5)
    assert sd["lss_second_half"] == pytest.approx(9.5)
    assert sd["lss_change_pct"] == pytest.approx(-9.52, abs=0.05)
    assert sd["ecor_w_per_kg_mps"] == pytest.approx(170.0 / (3.0 * 75.0), abs=0.005)
    assert len(diag["laps"]) == 1


def test_run_missing_streams_reported_explicitly(tmp_path: Path):
    """Missing Stryd/HR streams must appear in missing_streams rather than synthesizing zeros."""
    t0 = datetime(2026, 11, 20, 7, 0, 0)
    stub = _StubFitFile(
        {
            "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
            "session": [_StubMessage({"sport": "running", "start_time": t0, "total_timer_time": 600.0})],
            "record": [_StubMessage({}, fields=[_StubField("timestamp", t0 + timedelta(seconds=i))]) for i in range(10)],
        }
    )
    fit_path = tmp_path / "missing.fit"
    fit_path.write_bytes(b"")
    with patch("fitparse.FitFile", return_value=stub):
        diag = analyze_fit_diagnostics(fit_path, run_cp=200.0)

    assert diag["ok"] is True
    assert "stryd_ciq_power" in diag["missing_streams"]
    assert "heart_rate" in diag["missing_streams"]
    assert diag["avg_power_w"] is None


def test_bike_diagnostics_vi_zero_pct_and_cadence_fade(tmp_path: Path):
    """Bike diagnostics compute VI, zero-power %, half-splits, and indoor positional context."""
    t0 = datetime(2026, 11, 19, 6, 0, 0)
    records = []
    for i in range(60):
        pwr = 0.0 if i < 6 else (120.0 if i < 30 else 100.0)
        cad = 90 if i < 30 else 80
        records.append(
            _StubMessage(
                {},
                fields=[
                    _StubField("timestamp", t0 + timedelta(seconds=i)),
                    _StubField("heart_rate", 132),
                    _StubField("power", pwr),
                    _StubField("cadence", cad),
                ],
            )
        )
    stub = _StubFitFile(
        {
            "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 3121})],
            "session": [
                _StubMessage(
                    {
                        "sport": "cycling",
                        "sub_sport": "indoor_cycling",
                        "start_time": t0,
                        "total_timer_time": 60.0,
                        "total_distance": 0.0,
                    }
                )
            ],
            "record": records,
        }
    )
    fit_path = tmp_path / "2026-11-19_cycling_1.fit"
    fit_path.write_bytes(b"")
    with patch("fitparse.FitFile", return_value=stub):
        diag = analyze_fit_diagnostics(fit_path, bike_cp=135.0)

    assert diag["sport"] == "Bike"
    sd = diag["sport_diagnostics"]
    assert sd["zero_power_pct"] == pytest.approx(10.0)
    assert sd["variability_index"] >= 1.0
    assert sd["cadence_first_half"] == pytest.approx(90.0)
    assert sd["cadence_second_half"] == pytest.approx(80.0)
    assert sd["positional_context"] == "indoor_neo2t_aero_baseline"
    assert sd["neo2t_lr_balance_ignored"] is True


def test_swim_diagnostics_active_lengths_and_drill_exclusion(tmp_path: Path):
    """Swim diagnostics exclude drill lengths from half-split SWOLF/stroke rate (HEUR-007)."""
    t0 = datetime(2026, 11, 18, 7, 0, 0)
    lengths = []
    # 4 active freestyle first half (25s, 18 strokes -> SWOLF 43)
    for _ in range(4):
        lengths.append(
            _StubMessage(
                {
                    "length_type": "active",
                    "swim_stroke": "freestyle",
                    "total_timer_time": 25.0,
                    "total_strokes": 18,
                }
            )
        )
    # 2 drill lengths (must be excluded from SWOLF/SR)
    for _ in range(2):
        lengths.append(
            _StubMessage(
                {
                    "length_type": "active",
                    "swim_stroke": "drill",
                    "total_timer_time": 40.0,
                    "total_strokes": 0,
                }
            )
        )
    # 4 active freestyle second half (28s, 20 strokes -> SWOLF 48)
    for _ in range(4):
        lengths.append(
            _StubMessage(
                {
                    "length_type": "active",
                    "swim_stroke": "freestyle",
                    "total_timer_time": 28.0,
                    "total_strokes": 20,
                }
            )
        )
    # 2 rest lengths
    for _ in range(2):
        lengths.append(_StubMessage({"length_type": "rest", "total_timer_time": 20.0}))

    stub = _StubFitFile(
        {
            "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
            "session": [
                _StubMessage(
                    {
                        "sport": "swimming",
                        "start_time": t0,
                        "pool_length": 25.0,
                        "total_elapsed_time": 400.0,
                    }
                )
            ],
            "length": lengths,
        }
    )
    fit_path = tmp_path / "2026-11-18_swim_1.fit"
    fit_path.write_bytes(b"")
    with patch("fitparse.FitFile", return_value=stub):
        diag = analyze_fit_diagnostics(fit_path, swim_css=40.0)

    sd = diag["sport_diagnostics"]
    assert sd["active_lengths"] == 10
    assert sd["drill_lengths"] == 2
    assert sd["rest_lengths"] == 2
    assert sd["swolf_first_half"] == pytest.approx(43.0)
    assert sd["swolf_second_half"] == pytest.approx(48.0)
    assert sd["swolf_change_pct"] == pytest.approx(((48.0 - 43.0) / 43.0) * 100.0, abs=0.1)


def test_lifting_summary_and_block_brief_read_only_vs_record(tmp_path: Path):
    """Block brief summarizes lifting, checks HEUR-004 collisions, and stays read-only unless record=True."""
    lifting_path = tmp_path / "lifting.json"
    lifting_path.write_text(
        json.dumps(
            [
                {
                    "date": "2026-11-19",
                    "sheets_referenced": ["HLM"],
                    "total_working_sets": 10,
                    "total_working_tonnage_lbs": 8000.0,
                    "estimated_axial_load": 58.0,
                    "movement_pattern_sets": {"Posterior_Chain": 5, "Push": 5},
                    "exercises": [
                        {
                            "exercise": "Deadlift",
                            "movement_pattern": "Posterior_Chain",
                            "equipment": "Barbell",
                            "sets_completed": 5,
                            "top_set": "5 @ 205 lbs",
                            "top_weight_lbs": 205.0,
                            "best_e1rm_lbs": 239.2,
                        },
                        {
                            "exercise": "DB Bench",
                            "movement_pattern": "Push",
                            "equipment": "Dumbbell",
                            "sets_completed": 5,
                            "top_set": "8 @ 55 lbs",
                            "top_weight_lbs": 55.0,
                            "best_e1rm_lbs": 69.7,
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    lift_sum = summarize_lifting_window("2026-11-15", "2026-11-21", lifting_json=lifting_path)
    assert lift_sum["session_count"] == 1
    assert lift_sum["equipment_sets"]["barbell_share_vs_db"] == pytest.approx(0.5)
    assert lift_sum["equipment_sets"]["meets_60pct_barbell_rule"] is False
    assert lift_sum["deadlift_dates"] == ["2026-11-19"]

    # Create one synthetic Run on 2026-11-20 (next day after deadlift -> HEUR-004 collision)
    fit_dir = tmp_path / "fits"
    fit_dir.mkdir()
    run_fit = fit_dir / "2026-11-20_running_99.fit"
    run_fit.write_bytes(b"")
    t0 = datetime(2026, 11, 20, 8, 0, 0)
    run_stub = _StubFitFile(
        {
            "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
            "session": [_StubMessage({"sport": "running", "start_time": t0, "total_timer_time": 1800.0})],
            "record": [
                _StubMessage(
                    {},
                    fields=[
                        _StubField("timestamp", t0 + timedelta(seconds=i)),
                        _StubField("heart_rate", 140),
                        _StubField("Power", 160, is_dev=True),
                    ],
                )
                for i in range(60)
            ],
        }
    )

    actuals_path = tmp_path / "actuals.json"
    with patch("fitparse.FitFile", return_value=run_stub):
        brief_ro = build_review_brief(
            fit_dir=fit_dir,
            start_date="2026-11-15",
            end_date="2026-11-21",
            lifting_json=lifting_path,
            wellness_dir=tmp_path / "no_wellness",
            actuals_path=actuals_path,
            record=False,
        )

    assert not actuals_path.exists()
    assert brief_ro["recorded_to_actuals_count"] == 0
    assert brief_ro["ingestion"]["activities_in_window"] == 1
    assert len(brief_ro["cross_sport_summary"]["deadlift_run_collisions"]) == 1
    assert brief_ro["cross_sport_summary"]["deadlift_run_collisions"][0]["type"] == "next_day_after_deadlift"
    md = format_brief_markdown(brief_ro)
    assert "Block Telemetry Brief: 2026-11-15 to 2026-11-21" in md

    with patch("fitparse.FitFile", return_value=run_stub):
        brief_rec = build_review_brief(
            fit_dir=fit_dir,
            start_date="2026-11-15",
            end_date="2026-11-21",
            lifting_json=lifting_path,
            wellness_dir=tmp_path / "no_wellness",
            actuals_path=actuals_path,
            record=True,
        )
    assert brief_rec["recorded_to_actuals_count"] == 1
    assert actuals_path.exists()


def test_review_skills_and_workflow_assets_valid():
    """Verify post-workout-review and block-review skills, rubrics, agent prompts, and workflow script."""
    post_dir = REPO_ROOT / ".agents" / "skills" / "post-workout-review"
    assert (post_dir / "SKILL.md").exists()
    for fname in ("run.md", "bike.md", "swim.md", "strength.md"):
        assert (post_dir / "references" / fname).exists()

    block_dir = REPO_ROOT / ".agents" / "skills" / "block-review"
    assert (block_dir / "SKILL.md").exists()
    for fname in ("run_analyst.md", "bike_analyst.md", "swim_analyst.md", "strength_analyst.md"):
        assert (block_dir / "agents" / fname).exists()

    wf_script = block_dir / "scripts" / "block_review_workflow.py"
    assert wf_script.exists()
    # Verify valid Python syntax (allowing top-level await used by run_workflow)
    ast.parse(wf_script.read_text(encoding="utf-8"), mode="exec")
