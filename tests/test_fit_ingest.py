"""Tests for multi-device .fit activity parser & deduplication (TASK-024, EXP-005)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import fitparse
import pytest

from triplan.io.fit_ingest import (
    ParsedActivity,
    classify_power_zone,
    compute_normalized_power,
    deduplicate_activities,
    parse_fit_file,
)
from triplan.paths import DATA_DIR
from triplan.physiology.load_model import ZONES7, ans_cost, zone_strain


# ---------------------------------------------------------------------------
# Lightweight in-memory FIT message stubs (matching fitparse.FitFile interface)
# ---------------------------------------------------------------------------
@dataclass
class _StubField:
    name: str
    value: Any
    is_dev: bool = False
    field: Any = None


class _StubMessage:
    def __init__(self, values: Dict[str, Any], fields: Optional[List[_StubField]] = None):
        self._values = dict(values)
        self.fields = fields if fields is not None else [
            _StubField(name=k, value=v) for k, v in values.items()
        ]

    def get_values(self) -> Dict[str, Any]:
        return dict(self._values)


class _StubFitFile:
    def __init__(self, messages: Dict[str, List[_StubMessage]]):
        self._messages = messages

    def get_messages(self, name: str) -> List[_StubMessage]:
        return self._messages.get(name, [])


def _run_parse(stub: _StubFitFile, **kwargs) -> ParsedActivity:
    with patch("triplan.io.fit_ingest.fitparse.FitFile", return_value=stub):
        return parse_fit_file("synthetic.fit", **kwargs)


# ---------------------------------------------------------------------------
# Fixture A & B: Stryd Model 17 CIQ Power vs Native Power + Treadmill
# ---------------------------------------------------------------------------
def test_stryd_ciq_power_extracted_and_native_power_ignored():
    """Fixture A: Native record.power (310W) must NEVER leak; CIQ 'Power' (250W) wins."""
    t0 = datetime(2026, 7, 13, 8, 0, 0)
    records = []
    for i in range(60):
        records.append(
            _StubMessage(
                {},
                fields=[
                    _StubField("timestamp", t0 + timedelta(seconds=i)),
                    _StubField("heart_rate", 145),
                    _StubField("power", 310, is_dev=False),  # +24% inflated native power
                    _StubField("Power", 250, is_dev=True),   # Stryd CIQ dev field
                    _StubField("Form Power", 62, is_dev=True),
                    _StubField("Leg Spring Stiffness", 10.4, is_dev=True),
                    _StubField("Air Power", 8, is_dev=True),
                ],
            )
        )
    stub = _StubFitFile({
        "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
        "session": [_StubMessage({
            "sport": "running", "sub_sport": "generic",
            "start_time": t0, "total_timer_time": 60.0, "total_elapsed_time": 60.0,
        })],
        "record": records,
    })

    act = _run_parse(stub, run_cp=200.0)
    assert act.ok
    assert act.device_name == "Enduro 3"
    assert act.sport == "Run"
    assert act.has_stryd_power is True
    assert act.has_native_run_power is True
    assert act.avg_power == pytest.approx(250.0)
    assert act.normalized_power == pytest.approx(250.0)
    assert act.avg_form_power == pytest.approx(62.0)
    assert act.avg_leg_spring_stiffness == pytest.approx(10.4)
    assert act.avg_air_power == pytest.approx(8.0)


def test_run_without_stryd_ciq_never_falls_back_to_native_power():
    """Native record.power alone on a run must NOT be ingested as run power."""
    t0 = datetime(2026, 7, 13, 8, 0, 0)
    records = [
        _StubMessage({}, fields=[
            _StubField("timestamp", t0 + timedelta(seconds=i)),
            _StubField("power", 310, is_dev=False),
        ])
        for i in range(10)
    ]
    stub = _StubFitFile({
        "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
        "session": [_StubMessage({"sport": "running", "start_time": t0, "total_timer_time": 10.0})],
        "record": records,
    })
    act = _run_parse(stub, run_cp=200.0)
    assert act.has_stryd_power is False
    assert act.has_native_run_power is True
    assert act.avg_power is None
    assert act.normalized_power is None


def test_fitparse_devfield_instance_detection():
    """Real fitparse.records.DevField (without .is_dev attr) is recognized as dev field."""
    t0 = datetime(2026, 7, 13, 8, 0, 0)
    dev_inner = fitparse.records.DevField(
        dev_data_index=1, def_num=0, type="uint16", name="Power", units="Watts"
    )
    records = [
        _StubMessage({}, fields=[
            _StubField("timestamp", t0 + timedelta(seconds=i)),
            _StubField("power", 310, is_dev=False),
            _StubField("Power", 240, is_dev=False, field=dev_inner),
        ])
        for i in range(5)
    ]
    stub = _StubFitFile({
        "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
        "session": [_StubMessage({"sport": "running", "start_time": t0, "total_timer_time": 5.0})],
        "record": records,
    })
    act = _run_parse(stub, run_cp=200.0)
    assert act.has_stryd_power is True
    assert act.avg_power == pytest.approx(240.0)


def test_treadmill_run_flagged_and_zero_air_power():
    """Fixture B: sub_sport == 'treadmill' sets is_treadmill=True and Air Power=0W."""
    t0 = datetime(2026, 7, 14, 7, 0, 0)
    records = [
        _StubMessage({}, fields=[
            _StubField("timestamp", t0 + timedelta(seconds=i)),
            _StubField("Power", 210, is_dev=True),
            _StubField("Air Power", 0.0, is_dev=True),
        ])
        for i in range(10)
    ]
    stub = _StubFitFile({
        "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
        "session": [_StubMessage({
            "sport": "running", "sub_sport": "treadmill",
            "start_time": t0, "total_timer_time": 10.0,
        })],
        "record": records,
    })
    act = _run_parse(stub, run_cp=200.0)
    assert act.is_treadmill is True
    assert act.avg_air_power == pytest.approx(0.0)
    assert act.avg_power == pytest.approx(210.0)


# ---------------------------------------------------------------------------
# Fixture C: Pool Swim Active Lengths & Drill Exclusion
# ---------------------------------------------------------------------------
def test_pool_swim_active_lengths_and_drill_swolf_exclusion():
    """Fixture C: Sum only active lengths; exclude drills from SWOLF; include drills in sTSS."""
    t0 = datetime(2026, 6, 1, 6, 30, 0)
    lengths = [
        # 2 active freestyle lengths: 30s + 15 strokes (SWOLF=45), 32s + 16 strokes (SWOLF=48)
        _StubMessage({"length_type": "active", "swim_stroke": "freestyle", "total_timer_time": 30.0, "total_strokes": 15, "event": "length"}),
        _StubMessage({"length_type": "active", "swim_stroke": "freestyle", "total_timer_time": 32.0, "total_strokes": 16, "event": "length"}),
        # 1 rest length (idle): 40s — MUST NOT be counted in duration or sTSS
        _StubMessage({"length_type": "idle", "swim_stroke": None, "total_timer_time": 40.0, "total_strokes": 0, "event": "length"}),
        # 1 active drill length: 38s, 0 strokes — included in active duration, EXCLUDED from SWOLF
        _StubMessage({"length_type": "active", "swim_stroke": "drill", "total_timer_time": 38.0, "total_strokes": 0, "event": "drill"}),
    ]
    stub = _StubFitFile({
        "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 3405})],
        "session": [_StubMessage({
            "sport": "swimming", "sub_sport": "lap_swimming",
            "start_time": t0,
            "total_elapsed_time": 140.0,  # includes 40s wall rest
            "total_timer_time": 140.0,
            "pool_length": 25.0,
        })],
        "length": lengths,
    })

    act = _run_parse(stub, swim_css=40.0)  # 40 m/min = 0.6667 m/s
    assert act.device_name == "Swim 2"
    assert act.sport == "Swim"
    assert act.active_lengths == 3
    assert act.rest_lengths == 1
    assert act.drill_lengths == 1
    assert act.duration_sec == pytest.approx(100.0)  # 30 + 32 + 38 (excludes 40s rest)
    assert act.distance_m == pytest.approx(75.0)     # 3 * 25m
    assert act.avg_swolf == pytest.approx(46.5)      # mean(45, 48), drill excluded
    assert act.avg_stroke_rate == pytest.approx(30.0)  # both are 30 spm

    # Verify sTSS uses active_duration_sec (100s), not total_elapsed_time (140s)
    nss = 75.0 / 100.0
    sif = nss / (40.0 / 60.0)
    expected_stss = (100.0 / 3600.0) * (sif ** 3) * 100.0
    assert act.tss == pytest.approx(expected_stss)


# ---------------------------------------------------------------------------
# Fixture D: 300-Second Multi-Device Ride Deduplication
# ---------------------------------------------------------------------------
def test_deduplicate_overlapping_rides_prefers_edge_530():
    """Fixture D: Rides <300s apart deduplicate to Edge 530; >=300s or diff sport kept."""
    t0 = datetime(2026, 6, 15, 9, 0, 0)
    edge = ParsedActivity(
        file_path=Path("edge.fit"), start_time=t0, sport="Bike",
        device_name="Edge 530", garmin_product=3121, record_count=3600, duration_sec=3600.0,
    )
    enduro_dup = ParsedActivity(
        file_path=Path("enduro.fit"), start_time=t0 + timedelta(seconds=120), sport="Bike",
        device_name="Enduro 3", garmin_product=4575, record_count=3650, duration_sec=3650.0,
    )
    enduro_brick_run = ParsedActivity(
        file_path=Path("brick_run.fit"), start_time=t0 + timedelta(seconds=120), sport="Run",
        device_name="Enduro 3", garmin_product=4575, record_count=1200, duration_sec=1200.0,
    )
    later_ride = ParsedActivity(
        file_path=Path("later_ride.fit"), start_time=t0 + timedelta(seconds=301), sport="Bike",
        device_name="Enduro 3", garmin_product=4575, record_count=600, duration_sec=600.0,
    )

    kept, dropped = deduplicate_activities([enduro_dup, edge, enduro_brick_run, later_ride])
    kept_paths = {a.file_path.name for a in kept}
    assert kept_paths == {"edge.fit", "brick_run.fit", "later_ride.fit"}
    assert len(dropped) == 1
    assert dropped[0][0].file_path.name == "enduro.fit"
    assert dropped[0][1].file_path.name == "edge.fit"
    assert dropped[0][2] == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# Fixture E: Tacx Neo 2T Stream Hygiene (0W included, L/R balance ignored)
# ---------------------------------------------------------------------------
def test_tacx_neo_includes_zeros_and_ignores_lr_balance():
    """Fixture E: 0W coasting records included in avg/NP; left_right_balance ignored."""
    t0 = datetime(2026, 6, 20, 10, 0, 0)
    # 30s at 200W + 30s coasting at 0W -> avg=100W
    powers = [200.0] * 30 + [0.0] * 30
    records = [
        _StubMessage({}, fields=[
            _StubField("timestamp", t0 + timedelta(seconds=i)),
            _StubField("power", p, is_dev=False),
            _StubField("left_right_balance", 99.0 if i % 2 == 0 else 1.0, is_dev=False),
        ])
        for i, p in enumerate(powers)
    ]
    stub = _StubFitFile({
        "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 3121})],
        "session": [_StubMessage({
            "sport": "cycling", "sub_sport": "indoor_cycling",
            "start_time": t0, "total_timer_time": 60.0, "total_elapsed_time": 60.0,
        })],
        "record": records,
    })

    act = _run_parse(stub, bike_cp=150.0)
    assert act.device_name == "Edge 530"
    assert act.sport == "Bike"
    assert act.avg_power == pytest.approx(100.0)
    assert act.normalized_power == pytest.approx(compute_normalized_power(powers))
    assert not hasattr(act, "left_right_balance")


# ---------------------------------------------------------------------------
# Fixture F: Canonical 80/20 7-Zone Time-in-Zone Mapping
# ---------------------------------------------------------------------------
def test_seven_zone_tiz_mapping_run_and_bike():
    """Fixture F: 10s in each of the 7 zones sums to 70s (1.1667 min) with exact load parity."""
    t0 = datetime(2026, 7, 1, 6, 0, 0)
    # Run CP = 200W -> Z1(<152), Z2(152-176), ZX(176-188), Z3(188-200), ZY(200-206), Z4(206-240), Z5(>240)
    run_watts = [120, 165, 182, 194, 203, 220, 260]
    assert [classify_power_zone(w, 200.0, "Run") for w in run_watts] == list(ZONES7)

    # Bike CP = 200W -> Z1(<140), Z2(140-166), ZX(166-182), Z3(182-200), ZY(200-204), Z4(204-220), Z5(>220)
    bike_watts = [110, 150, 174, 190, 202, 212, 250]
    assert [classify_power_zone(w, 200.0, "Bike") for w in bike_watts] == list(ZONES7)

    # Build 60s per zone (420s = 7.0 min total) on Run
    records = []
    sec = 0
    for w in run_watts:
        for _ in range(60):
            records.append(_StubMessage({}, fields=[
                _StubField("timestamp", t0 + timedelta(seconds=sec)),
                _StubField("Power", w, is_dev=True),
            ]))
            sec += 1
    stub = _StubFitFile({
        "file_id": [_StubMessage({"manufacturer": "garmin", "garmin_product": 4575})],
        "session": [_StubMessage({"sport": "running", "start_time": t0, "total_timer_time": 420.0})],
        "record": records,
    })
    act = _run_parse(stub, run_cp=200.0)
    for z in ZONES7:
        assert act.zones7_min[z] == pytest.approx(1.0)
    assert sum(act.zones7_min.values()) == pytest.approx(7.0)
    assert act.mechanical_strain == pytest.approx(zone_strain("Run", [1.0] * 7))
    assert act.ans_cost_val == pytest.approx(ans_cost([1.0] * 7, duration=7.0))


# ---------------------------------------------------------------------------
# Real-file smoke tests (run locally when corpus is present)
# ---------------------------------------------------------------------------
_FIT_DIR = DATA_DIR / "garmin" / "activities" / "fit_files"
_REAL_RUN = _FIT_DIR / "2026-06-22_running_23339527727.fit"
_REAL_SWIM = _FIT_DIR / "2024-05-09_lap_swimming_15304263682.fit"
_REAL_DUAL_RUN = _FIT_DIR / "2023-08-27_multi_sport_11886648267.fit"
_REAL_TREADMILL = _FIT_DIR / "2021-10-01_treadmill_running_7584781510.fit"
_REAL_EDGE_INDOOR = _FIT_DIR / "2026-01-10_indoor_cycling_21505214216.fit"
_REAL_DEDUP_A = _FIT_DIR / "2020-08-29_indoor_cycling_5457585443.fit"
_REAL_DEDUP_B = _FIT_DIR / "2020-08-29_indoor_cycling_5458261651.fit"


@pytest.mark.skipif(not _REAL_RUN.exists(), reason="Local Garmin FIT corpus not present")
def test_real_enduro3_stryd_run_file():
    act = parse_fit_file(_REAL_RUN)
    assert act.ok
    assert act.device_name == "Enduro 3"
    assert act.sport == "Run"
    assert act.has_stryd_power is True
    assert act.avg_power is not None and act.avg_power > 0


@pytest.mark.skipif(not _REAL_SWIM.exists(), reason="Local Garmin FIT corpus not present")
def test_real_swim2_pool_file():
    act = parse_fit_file(_REAL_SWIM)
    assert act.ok
    assert act.device_name == "Swim 2"
    assert act.sport == "Swim"
    assert act.active_lengths == 44
    assert act.duration_sec < act.elapsed_sec
    assert act.tss is not None and act.tss > 0


@pytest.mark.skipif(not _REAL_DUAL_RUN.exists(), reason="Local Garmin FIT corpus not present")
def test_real_dual_stryd_and_native_power_run():
    act = parse_fit_file(_REAL_DUAL_RUN)
    assert act.ok
    assert act.sport == "Run"
    assert act.has_stryd_power is True
    assert act.has_native_run_power is True
    assert act.avg_power == pytest.approx(222.3, abs=0.2)


@pytest.mark.skipif(not _REAL_TREADMILL.exists(), reason="Local Garmin FIT corpus not present")
def test_real_treadmill_zero_air_power():
    act = parse_fit_file(_REAL_TREADMILL)
    assert act.ok
    assert act.sport == "Run"
    assert act.is_treadmill is True
    assert act.has_stryd_power is True
    assert act.avg_air_power == pytest.approx(0.0)


@pytest.mark.skipif(not _REAL_EDGE_INDOOR.exists(), reason="Local Garmin FIT corpus not present")
def test_real_edge530_indoor_cycling_neo2t():
    act = parse_fit_file(_REAL_EDGE_INDOOR)
    assert act.ok
    assert act.device_name == "Edge 530"
    assert act.sport == "Bike"
    assert act.sub_sport == "indoor_cycling"
    assert act.avg_power is not None and act.avg_power > 0
    assert act.normalized_power is not None and act.normalized_power >= act.avg_power
    assert not hasattr(act, "left_right_balance")


@pytest.mark.skipif(
    not (_REAL_DEDUP_A.exists() and _REAL_DEDUP_B.exists()),
    reason="Local Garmin FIT corpus not present",
)
def test_real_300s_ride_deduplication():
    a = parse_fit_file(_REAL_DEDUP_A)
    b = parse_fit_file(_REAL_DEDUP_B)
    assert a.ok and b.ok
    kept, dropped = deduplicate_activities([a, b], window_sec=300.0)
    assert len(kept) == 1
    assert len(dropped) == 1
    assert dropped[0][2] == pytest.approx(294.0, abs=1.0)
