"""Garmin wellness telemetry loader for unified morning readiness alpha_d (TASK-025, TASK-003).

Reads ``data/garmin/wellness/hrv_summaries.json``, ``daily_summaries.json``, and
``sleep_summaries.json`` to compute trailing 28-day baselines (mean + SD) for HRV
rMSSD and Resting Heart Rate (RHR) plus sleep score, feeding
``ClosedLoopMPCController.calculate_garmin_morning_readiness``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from triplan.paths import DATA_DIR


def default_wellness_dir() -> Path:
    return DATA_DIR / "garmin" / "wellness"


@dataclass
class WellnessSnapshot:
    """Morning wellness telemetry and computed readiness scalar alpha_d in [0.60, 1.00]."""

    date: str
    rmssd_today: Optional[float]
    rmssd_baseline_mean: Optional[float]
    rmssd_baseline_std: Optional[float]
    rhr_today: Optional[float]
    rhr_baseline_mean: Optional[float]
    rhr_baseline_std: Optional[float]
    sleep_score: Optional[float]
    alpha_d: float


def _load_hrv_map(wellness_dir: Path) -> Dict[str, float]:
    path = wellness_dir / "hrv_summaries.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("hrvSummaries", raw) if isinstance(raw, dict) else raw
    out: Dict[str, float] = {}
    for row in items:
        d = row.get("calendarDate")
        val = row.get("lastNightAvg")
        if d and val is not None:
            out[str(d)] = float(val)
    return out


def _load_rhr_map(wellness_dir: Path) -> Dict[str, float]:
    path = wellness_dir / "daily_summaries.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("data", raw) if isinstance(raw, dict) else raw
    out: Dict[str, float] = {}
    for row in items:
        d = row.get("calendarDate")
        hr = row.get("heartRate") or {}
        val = hr.get("restingValue") if isinstance(hr, dict) else row.get("restingHeartRate")
        if d and val is not None:
            out[str(d)] = float(val)
    return out


def _load_sleep_map(wellness_dir: Path) -> Dict[str, float]:
    path = wellness_dir / "sleep_summaries.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("sleepSummaries", raw) if isinstance(raw, dict) else raw
    out: Dict[str, float] = {}
    for row in items:
        d = row.get("calendarDate")
        scores = row.get("sleepScores") or {}
        overall = scores.get("overall") if isinstance(scores, dict) else None
        val = overall.get("value") if isinstance(overall, dict) else None
        if d and val is not None:
            out[str(d)] = float(val)
    return out


def _trailing_stats(series: Dict[str, float], target_date: str, window_days: int) -> Tuple[Optional[float], Optional[float]]:
    end = date.fromisoformat(target_date)
    start = end - timedelta(days=window_days)
    vals: List[float] = [
        v for d_str, v in series.items()
        if start.isoformat() <= d_str < target_date
    ]
    if not vals:
        return None, None
    arr = np.asarray(vals, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if len(arr) >= 2 else None
    return mean, std


def compute_wellness_snapshot(
    target_date: Optional[str] = None,
    wellness_dir: Optional[Path] = None,
    window_days: int = 28,
) -> WellnessSnapshot:
    """Loads Garmin wellness JSONs and computes unified ``alpha_d`` in ``[0.60, 1.00]``."""
    from triplan.control.mpc import ClosedLoopMPCController

    wdir = Path(wellness_dir) if wellness_dir is not None else default_wellness_dir()
    hrv_map = _load_hrv_map(wdir)
    rhr_map = _load_rhr_map(wdir)
    sleep_map = _load_sleep_map(wdir)

    if target_date is None:
        if hrv_map:
            target_date = max(hrv_map.keys())
        elif rhr_map:
            target_date = max(rhr_map.keys())
        else:
            return WellnessSnapshot(
                date=date.today().isoformat(),
                rmssd_today=None,
                rmssd_baseline_mean=None,
                rmssd_baseline_std=None,
                rhr_today=None,
                rhr_baseline_mean=None,
                rhr_baseline_std=None,
                sleep_score=None,
                alpha_d=1.0,
            )

    rmssd_today = hrv_map.get(target_date)
    rmssd_mean, rmssd_std = _trailing_stats(hrv_map, target_date, window_days)
    rhr_today = rhr_map.get(target_date)
    rhr_mean, rhr_std = _trailing_stats(rhr_map, target_date, window_days)
    sleep_score = sleep_map.get(target_date)

    if rmssd_today is None or rmssd_mean is None:
        alpha = 1.0
    else:
        alpha = ClosedLoopMPCController.calculate_garmin_morning_readiness(
            rmssd_today=rmssd_today,
            rmssd_baseline_mean=rmssd_mean,
            rmssd_baseline_std=rmssd_std if rmssd_std is not None else max(0.15 * rmssd_mean, 1.0),
            rhr_today=rhr_today if rhr_today is not None else (rhr_mean if rhr_mean is not None else 55.0),
            rhr_baseline_mean=rhr_mean if rhr_mean is not None else (rhr_today if rhr_today is not None else 55.0),
            rhr_baseline_std=rhr_std if rhr_std is not None else 3.0,
            sleep_score=sleep_score if sleep_score is not None else 80.0,
        )

    return WellnessSnapshot(
        date=target_date,
        rmssd_today=rmssd_today,
        rmssd_baseline_mean=rmssd_mean,
        rmssd_baseline_std=rmssd_std,
        rhr_today=rhr_today,
        rhr_baseline_mean=rhr_mean,
        rhr_baseline_std=rhr_std,
        sleep_score=sleep_score,
        alpha_d=alpha,
    )
