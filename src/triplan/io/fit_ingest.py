"""Multi-device ``.fit`` activity parser & deduplication (TASK-024, SPEC.md, ADR-010).

Enforces hardware telemetry invariants across Garmin Enduro 3, Garmin Edge 530,
Garmin Swim 2, Stryd Model 17 Foot Pod, and Tacx Neo 2T Smart Trainer:

* **Stryd Model 17 (HEUR-005):** Extract Connect IQ developer field ``Power``
  (and ``Form Power``, ``Leg Spring Stiffness``, ``Air Power``); never read
  native ``record.power`` for running (+20% to +30% inflation). Flag
  ``sub_sport == 'treadmill'`` where ``Air Power`` is 0 W.
* **Pool Swim (HEUR-007):** Sum duration strictly across ``length`` messages
  with ``length_type == 'active'`` (never ``session.total_elapsed_time``).
  Exclude drill lengths (``swim_stroke == 'drill'`` / ``event == 'drill'``)
  from SWOLF and stroke rate while keeping their active duration in sTSS.
* **Edge 530 & Tacx Neo 2T (HEUR-006, HEUR-007):** Include zero-power records
  in average power and 30s Normalized Power; ignore ``left_right_balance``;
  deduplicate overlapping rides within 300s, preferring Edge 530.
* **Canonical 80/20 7-Zone Scale (ADR-009):** Map second-by-second power
  against ``(Z1, Z2, ZX, Z3, ZY, Z4, Z5)`` anchored to locked CP.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import fitparse
import numpy as np

from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.physiology.load_model import ZONES7, ans_cost, zone_strain

# Garmin product IDs observed in corpus & Garmin FIT SDK
GARMIN_PRODUCT_NAMES: Dict[Any, str] = {
    3121: "Edge 530",
    "edge_530": "Edge 530",
    4575: "Enduro 3",
    "enduro_3": "Enduro 3",
    3405: "Swim 2",
    "swim_2": "Swim 2",
    3113: "Forerunner 945",
    "fr945": "Forerunner 945",
    "fr70": "Forerunner 70",
}

# Device priority for same-sport 300s deduplication (higher = preferred)
_BIKE_DEVICE_PRIORITY: Dict[str, int] = {
    "Edge 530": 100,
    "Enduro 3": 50,
    "Forerunner 945": 40,
}

# Canonical 80/20 7-zone upper bounds as fraction of CP (SPEC.md §2)
RUN_ZONE_BOUNDS: Tuple[Tuple[float, str], ...] = (
    (0.76, "Z1"),
    (0.88, "Z2"),
    (0.94, "ZX"),
    (1.00, "Z3"),
    (1.03, "ZY"),
    (1.20, "Z4"),
)
BIKE_ZONE_BOUNDS: Tuple[Tuple[float, str], ...] = (
    (0.70, "Z1"),
    (0.83, "Z2"),
    (0.91, "ZX"),
    (1.00, "Z3"),
    (1.02, "ZY"),
    (1.10, "Z4"),
)


@dataclass
class ParsedActivity:
    """Normalized activity summary extracted from a single ``.fit`` file."""

    file_path: Path
    start_time: Optional[datetime] = None
    sport: str = "Other"
    raw_sport: Optional[str] = None
    sub_sport: Optional[str] = None
    device_name: str = "Unknown"
    garmin_product: Optional[Any] = None
    serial_number: Optional[int] = None

    duration_sec: float = 0.0
    elapsed_sec: float = 0.0
    distance_m: float = 0.0

    avg_power: Optional[float] = None
    normalized_power: Optional[float] = None
    intensity_factor: Optional[float] = None
    tss: Optional[float] = None
    mechanical_strain: Optional[float] = None
    ans_cost_val: Optional[float] = None

    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    avg_cadence: Optional[float] = None
    efficiency_factor: Optional[float] = None
    decoupling_pct: Optional[float] = None
    zones7_min: Dict[str, float] = field(default_factory=lambda: {z: 0.0 for z in ZONES7})

    # Run / Stryd Model 17 fields
    is_treadmill: bool = False
    has_stryd_power: bool = False
    has_native_run_power: bool = False
    avg_form_power: Optional[float] = None
    avg_leg_spring_stiffness: Optional[float] = None
    avg_air_power: Optional[float] = None

    # Pool swim fields
    active_lengths: int = 0
    rest_lengths: int = 0
    drill_lengths: int = 0
    avg_swolf: Optional[float] = None
    avg_stroke_rate: Optional[float] = None

    record_count: int = 0
    parse_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.parse_error is None


@dataclass
class CorpusAuditReport:
    """Aggregate audit summary over a directory of ``.fit`` files."""

    total_files: int
    parsed_ok: int
    parse_errors: int
    deduplicated_count: int
    dropped_duplicates: List[Tuple[Path, Path, float]]
    by_device: Dict[str, int]
    by_sport: Dict[str, int]
    stryd_ciq_runs: int
    native_only_runs: int
    treadmill_runs: int
    pool_swims: int
    mean_swim_elapsed_over_active: Optional[float]
    activities: List[ParsedActivity]
    errors: List[Tuple[Path, str]]


def is_dev_field(fld: Any) -> bool:
    """Return True if a ``fitparse`` field (or mock field) is a CIQ developer field."""
    if getattr(fld, "is_dev", False):
        return True
    inner = getattr(fld, "field", None)
    if inner is None:
        return False
    if isinstance(inner, fitparse.records.DevField):
        return True
    return getattr(inner, "dev_data_index", None) is not None


def classify_power_zone(power: float, cp: float, sport: str) -> str:
    """Map instantaneous power (W) to canonical 80/20 7-zone label."""
    if cp <= 0:
        return "Z1"
    ratio = float(power) / float(cp)
    bounds = RUN_ZONE_BOUNDS if sport == "Run" else BIKE_ZONE_BOUNDS
    for upper, zone in bounds:
        if ratio < upper:
            return zone
    return "Z5"


def compute_normalized_power(powers: Sequence[float]) -> Optional[float]:
    """Compute Coggan 30-second rolling Normalized Power (including 0W coasting)."""
    if not powers:
        return None
    arr = np.asarray(powers, dtype=float)
    if arr.size == 0:
        return None
    window = 30
    if arr.size >= window:
        csum = np.cumsum(np.insert(arr, 0, 0.0))
        rolling = (csum[window:] - csum[:-window]) / float(window)
    else:
        rolling = np.array([float(np.mean(arr))], dtype=float)
    return float(np.mean(rolling ** 4) ** 0.25)


def compute_aerobic_decoupling(
    powers: Sequence[Optional[float]],
    hrs: Sequence[Optional[float]],
    min_samples: int = 60,
) -> Tuple[Optional[float], Optional[float]]:
    """Compute Efficiency Factor (EF = NP / AvgHR) and Pw:HR decoupling (%).

    Splits paired (power, hr) samples into first and second halves and returns
    ``(efficiency_factor, decoupling_pct)`` where
    ``decoupling_pct = ((EF_1 - EF_2) / EF_1) * 100``.
    """
    paired = [
        (float(p), float(h))
        for p, h in zip(powers, hrs)
        if p is not None and h is not None and h > 0
    ]
    if len(paired) < min_samples:
        return None, None

    all_p = [p for p, _ in paired]
    all_hr = [h for _, h in paired]
    np_all = compute_normalized_power(all_p)
    mean_hr = float(np.mean(all_hr))
    if np_all is None or mean_hr <= 0:
        return None, None
    ef = float(np_all / mean_hr)

    mid = len(paired) // 2
    p1 = [p for p, _ in paired[:mid]]
    h1 = [h for _, h in paired[:mid]]
    p2 = [p for p, _ in paired[mid:]]
    h2 = [h for _, h in paired[mid:]]
    np1 = compute_normalized_power(p1)
    np2 = compute_normalized_power(p2)
    hr1 = float(np.mean(h1))
    hr2 = float(np.mean(h2))
    if np1 is None or np2 is None or hr1 <= 0 or hr2 <= 0:
        return ef, None
    ef1 = np1 / hr1
    ef2 = np2 / hr2
    if ef1 <= 0:
        return ef, None
    decoupling = ((ef1 - ef2) / ef1) * 100.0
    return float(ef), float(decoupling)


def _normalize_sport(raw_sport: Optional[str], sub_sport: Optional[str]) -> str:
    s = (str(raw_sport).lower() if raw_sport is not None else "").strip()
    sub = (str(sub_sport).lower() if sub_sport is not None else "").strip()
    if s == "running":
        return "Run"
    if s == "cycling":
        return "Bike"
    if s == "swimming":
        return "Swim"
    if s in ("walking", "hiking"):
        return "Walk"
    if s == "training" or "strength" in sub:
        return "Strength"
    return "Other"


def _resolve_device_name(garmin_product: Any, manufacturer: Any) -> str:
    if garmin_product in GARMIN_PRODUCT_NAMES:
        return GARMIN_PRODUCT_NAMES[garmin_product]
    if garmin_product is not None:
        return f"Garmin-{garmin_product}"
    if manufacturer is not None:
        return str(manufacturer)
    return "Unknown"


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _compute_record_dts(timestamps: Sequence[Optional[datetime]]) -> List[float]:
    """Compute per-record duration (seconds), clamping pause gaps to 1.0s."""
    n = len(timestamps)
    if n == 0:
        return []
    dts = [1.0] * n
    for i in range(1, n):
        t_prev, t_curr = timestamps[i - 1], timestamps[i]
        if t_prev is not None and t_curr is not None:
            delta = (t_curr - t_prev).total_seconds()
            if 0.0 < delta <= 10.0:
                dts[i] = delta
    return dts


def _parse_fit_object(
    fit: Any,
    file_path: Path,
    run_cp: float,
    bike_cp: float,
    swim_css: float,
) -> ParsedActivity:
    act = ParsedActivity(file_path=file_path)

    # 1. File ID / device metadata
    for msg in fit.get_messages("file_id"):
        vals = msg.get_values()
        gp = vals.get("garmin_product") or vals.get("product")
        act.garmin_product = gp
        act.serial_number = vals.get("serial_number")
        act.device_name = _resolve_device_name(gp, vals.get("manufacturer"))
        if act.start_time is None and isinstance(vals.get("time_created"), datetime):
            act.start_time = vals.get("time_created")
        break

    # 2. Session summary
    session_vals: Dict[str, Any] = {}
    for msg in fit.get_messages("session"):
        session_vals = msg.get_values()
        break

    raw_sport = session_vals.get("sport")
    sub_sport = session_vals.get("sub_sport")
    act.raw_sport = str(raw_sport) if raw_sport is not None else None
    act.sub_sport = str(sub_sport) if sub_sport is not None else None
    act.sport = _normalize_sport(act.raw_sport, act.sub_sport)
    act.is_treadmill = (act.sub_sport or "").lower() == "treadmill"

    if isinstance(session_vals.get("start_time"), datetime):
        act.start_time = session_vals["start_time"]
    elif isinstance(session_vals.get("timestamp"), datetime) and act.start_time is None:
        act.start_time = session_vals["timestamp"]

    act.elapsed_sec = _safe_float(session_vals.get("total_elapsed_time")) or 0.0
    timer_sec = _safe_float(session_vals.get("total_timer_time")) or act.elapsed_sec
    act.duration_sec = timer_sec
    act.distance_m = _safe_float(session_vals.get("total_distance")) or 0.0

    # 3. Pool swim length parsing (HEUR-007: strictly active lengths; exclude drills from SWOLF)
    if act.sport == "Swim":
        lengths = list(fit.get_messages("length"))
        if lengths:
            active_sec = 0.0
            swolf_samples: List[float] = []
            stroke_rates: List[float] = []
            pool_len = _safe_float(session_vals.get("pool_length"))
            for lmsg in lengths:
                lv = lmsg.get_values()
                ltype = str(lv.get("length_type") or "").lower()
                stroke = str(lv.get("swim_stroke") or "").lower()
                event = str(lv.get("event") or "").lower()
                ldur = _safe_float(lv.get("total_timer_time"))
                if ldur is None:
                    ldur = _safe_float(lv.get("total_elapsed_time")) or 0.0
                if ltype == "active":
                    act.active_lengths += 1
                    active_sec += ldur
                    is_drill = (stroke == "drill") or (event == "drill")
                    if is_drill:
                        act.drill_lengths += 1
                    else:
                        strokes = _safe_float(lv.get("total_strokes"))
                        if strokes is not None and strokes > 0 and ldur > 0:
                            swolf_samples.append(ldur + strokes)
                            stroke_rates.append(strokes / (ldur / 60.0))
                else:
                    act.rest_lengths += 1

            act.duration_sec = active_sec
            if pool_len is not None and pool_len > 0 and act.active_lengths > 0:
                act.distance_m = float(act.active_lengths) * pool_len
            if swolf_samples:
                act.avg_swolf = float(np.mean(swolf_samples))
            if stroke_rates:
                act.avg_stroke_rate = float(np.mean(stroke_rates))

        # Compute sTSS from active duration & active distance (SPEC.md / clean_file.py)
        if act.duration_sec > 0 and act.distance_m > 0 and swim_css > 0:
            css_mps = swim_css / 60.0
            nss_mps = act.distance_m / act.duration_sec
            sif = nss_mps / css_mps
            act.intensity_factor = float(sif)
            act.tss = float((act.duration_sec / 3600.0) * (sif ** 3) * 100.0)
            act.mechanical_strain = float(0.05 * act.tss)

    # 4. Record-level stream extraction
    timestamps: List[Optional[datetime]] = []
    power_samples: List[Optional[float]] = []
    hr_record_samples: List[Optional[float]] = []
    hr_samples: List[float] = []
    cadence_samples: List[float] = []
    form_power_samples: List[float] = []
    lss_samples: List[float] = []
    air_power_samples: List[float] = []

    for rmsg in fit.get_messages("record"):
        act.record_count += 1
        rec_ts: Optional[datetime] = None
        native_pwr: Optional[float] = None
        stryd_pwr: Optional[float] = None
        rec_hr: Optional[float] = None

        for fld in rmsg.fields:
            fname = fld.name
            val = fld.value
            dev = is_dev_field(fld)

            if fname == "timestamp" and isinstance(val, datetime):
                rec_ts = val
            elif fname == "heart_rate" and not dev:
                fv = _safe_float(val)
                if fv is not None and fv > 0:
                    rec_hr = fv
                    hr_samples.append(fv)
            elif fname == "cadence" and not dev:
                fv = _safe_float(val)
                if fv is not None:
                    cadence_samples.append(fv)
            elif fname == "power" and not dev:
                # Native power: valid for Bike; NEVER used for Run (HEUR-005)
                fv = _safe_float(val)
                if fv is not None:
                    native_pwr = fv
                    if act.sport == "Run":
                        act.has_native_run_power = True
            elif dev and fname == "Power":
                # Stryd CIQ developer field 'Power' (case-sensitive)
                fv = _safe_float(val)
                if fv is not None:
                    stryd_pwr = fv
                    act.has_stryd_power = True
            elif dev and fname == "Form Power":
                fv = _safe_float(val)
                if fv is not None:
                    form_power_samples.append(fv)
            elif dev and fname == "Leg Spring Stiffness":
                fv = _safe_float(val)
                if fv is not None:
                    lss_samples.append(fv)
            elif dev and fname == "Air Power":
                fv = _safe_float(val)
                if fv is not None:
                    air_power_samples.append(fv)
            # Note: Tacx Neo 2T 'left_right_balance' is intentionally ignored (HEUR-006).

        timestamps.append(rec_ts)
        hr_record_samples.append(rec_hr)
        if act.sport == "Run":
            power_samples.append(stryd_pwr)
        elif act.sport == "Bike":
            power_samples.append(native_pwr)
        else:
            power_samples.append(None)

    if act.start_time is None and timestamps and timestamps[0] is not None:
        act.start_time = timestamps[0]

    if hr_samples:
        act.avg_hr = float(np.mean(hr_samples))
        act.max_hr = float(np.max(hr_samples))
    else:
        act.avg_hr = _safe_float(session_vals.get("avg_heart_rate"))
        act.max_hr = _safe_float(session_vals.get("max_heart_rate"))

    if cadence_samples:
        act.avg_cadence = float(np.mean(cadence_samples))
    if form_power_samples:
        act.avg_form_power = float(np.mean(form_power_samples))
    if lss_samples:
        act.avg_leg_spring_stiffness = float(np.mean(lss_samples))
    if air_power_samples:
        act.avg_air_power = float(np.mean(air_power_samples))

    # 5. Power, 7-Zone TiZ, NP, IF, TSS, Strain & ANS for Run & Bike
    if act.sport in ("Run", "Bike"):
        cp = run_cp if act.sport == "Run" else bike_cp
        dts = _compute_record_dts(timestamps)
        valid_powers = [p for p in power_samples if p is not None]
        if valid_powers:
            act.avg_power = float(np.mean(valid_powers))
            np_val = compute_normalized_power(valid_powers)
            act.normalized_power = np_val
            if np_val is not None and cp > 0:
                if_val = np_val / cp
                act.intensity_factor = float(if_val)
                dur_for_tss = act.duration_sec if act.duration_sec > 0 else float(sum(dts))
                act.tss = float((dur_for_tss / 3600.0) * (if_val ** 2) * 100.0)

            zones_sec: Dict[str, float] = {z: 0.0 for z in ZONES7}
            for p, dt in zip(power_samples, dts):
                if p is not None:
                    z = classify_power_zone(p, cp, act.sport)
                    zones_sec[z] += dt
            act.zones7_min = {z: zones_sec[z] / 60.0 for z in ZONES7}
            z_vec = [act.zones7_min[z] for z in ZONES7]
            act.mechanical_strain = zone_strain(act.sport, z_vec)
            act.ans_cost_val = ans_cost(z_vec, duration=sum(z_vec))

            ef_val, dec_val = compute_aerobic_decoupling(power_samples, hr_record_samples)
            if ef_val is None and act.normalized_power is not None and act.avg_hr and act.avg_hr > 0:
                ef_val = float(act.normalized_power / act.avg_hr)
            act.efficiency_factor = ef_val
            act.decoupling_pct = dec_val

    return act


def parse_fit_file(
    file_path: Union[str, Path],
    *,
    run_cp: Optional[float] = None,
    bike_cp: Optional[float] = None,
    swim_css: Optional[float] = None,
    strict: bool = False,
) -> ParsedActivity:
    """Parse a single ``.fit`` activity file under SPEC.md / ADR-010 invariants."""
    path = Path(file_path)
    r_cp = float(run_cp if run_cp is not None else CURRENT_ATHLETE.run_cp)
    b_cp = float(bike_cp if bike_cp is not None else CURRENT_ATHLETE.bike_cp)
    s_css = float(swim_css if swim_css is not None else CURRENT_ATHLETE.swim_css)
    try:
        fit = fitparse.FitFile(str(path))
        return _parse_fit_object(fit, path, r_cp, b_cp, s_css)
    except Exception as exc:
        if strict:
            raise
        return ParsedActivity(file_path=path, parse_error=f"{type(exc).__name__}: {exc}")


def _activity_priority(act: ParsedActivity) -> Tuple[int, int, float, str]:
    """Preference key for deduplication: device priority, record count, duration, path."""
    dev_prio = _BIKE_DEVICE_PRIORITY.get(act.device_name, 0) if act.sport == "Bike" else 0
    return (dev_prio, act.record_count, act.duration_sec, str(act.file_path))


def deduplicate_activities(
    activities: Iterable[ParsedActivity],
    *,
    window_sec: float = 300.0,
) -> Tuple[List[ParsedActivity], List[Tuple[ParsedActivity, ParsedActivity, float]]]:
    """Deduplicate overlapping same-sport activities within ``window_sec`` (default 300s).

    For overlapping rides (e.g. Edge 530 + Enduro 3), retains the Edge 530 recording.
    Returns ``(kept, dropped)`` where each entry in ``dropped`` is
    ``(dropped_activity, kept_activity, abs_delta_seconds)``.
    """
    valid = [a for a in activities if a.ok]
    with_ts = sorted(
        [a for a in valid if a.start_time is not None],
        key=lambda a: (a.sport, a.start_time, str(a.file_path)),
    )
    no_ts = [a for a in valid if a.start_time is None]

    kept: List[ParsedActivity] = []
    dropped: List[Tuple[ParsedActivity, ParsedActivity, float]] = []

    for cand in with_ts:
        matched_idx: Optional[int] = None
        matched_delta: float = 0.0
        for idx in range(len(kept) - 1, -1, -1):
            prev = kept[idx]
            if prev.sport != cand.sport:
                break
            delta = abs((cand.start_time - prev.start_time).total_seconds())  # type: ignore[operator]
            if delta < window_sec:
                matched_idx = idx
                matched_delta = delta
                break
        if matched_idx is None:
            kept.append(cand)
        else:
            existing = kept[matched_idx]
            if _activity_priority(cand) > _activity_priority(existing):
                kept[matched_idx] = cand
                dropped.append((existing, cand, matched_delta))
            else:
                dropped.append((cand, existing, matched_delta))

    kept.extend(no_ts)
    return kept, dropped


def audit_corpus(
    directory: Union[str, Path],
    *,
    run_cp: Optional[float] = None,
    bike_cp: Optional[float] = None,
    swim_css: Optional[float] = None,
    window_sec: float = 300.0,
) -> CorpusAuditReport:
    """Sweep all ``*.fit`` files in ``directory``, deduplicate, and summarize."""
    root = Path(directory)
    fit_files = sorted(root.glob("*.fit"))
    parsed = [
        parse_fit_file(f, run_cp=run_cp, bike_cp=bike_cp, swim_css=swim_css)
        for f in fit_files
    ]
    errors = [(a.file_path, a.parse_error or "unknown") for a in parsed if not a.ok]
    kept, dropped = deduplicate_activities(parsed, window_sec=window_sec)

    by_device = dict(Counter(a.device_name for a in kept).most_common())
    by_sport = dict(Counter(a.sport for a in kept).most_common())
    runs = [a for a in kept if a.sport == "Run"]
    stryd_runs = sum(1 for a in runs if a.has_stryd_power)
    native_only = sum(1 for a in runs if a.has_native_run_power and not a.has_stryd_power)
    treadmill = sum(1 for a in runs if a.is_treadmill)
    pool_swims = [a for a in kept if a.sport == "Swim" and a.active_lengths > 0 and a.duration_sec > 0]
    ratios = [a.elapsed_sec / a.duration_sec for a in pool_swims if a.elapsed_sec > 0]

    return CorpusAuditReport(
        total_files=len(fit_files),
        parsed_ok=len(fit_files) - len(errors),
        parse_errors=len(errors),
        deduplicated_count=len(dropped),
        dropped_duplicates=[(d.file_path, k.file_path, dt) for d, k, dt in dropped],
        by_device=by_device,
        by_sport=by_sport,
        stryd_ciq_runs=stryd_runs,
        native_only_runs=native_only,
        treadmill_runs=treadmill,
        pool_swims=len(pool_swims),
        mean_swim_elapsed_over_active=float(np.mean(ratios)) if ratios else None,
        activities=kept,
        errors=errors,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-device .fit activity parser & corpus auditor.")
    parser.add_argument("--audit-corpus", type=str, required=True, help="Directory of .fit files to audit.")
    parser.add_argument("--window-sec", type=float, default=300.0, help="Deduplication window in seconds.")
    args = parser.parse_args(argv)

    rep = audit_corpus(args.audit_corpus, window_sec=args.window_sec)
    print(f"FIT Corpus Audit: {args.audit_corpus}")
    print(f"  Total files:       {rep.total_files}")
    print(f"  Parsed OK:         {rep.parsed_ok}")
    print(f"  Parse errors:      {rep.parse_errors}")
    print(f"  Dedup dropped:     {rep.deduplicated_count} (window < {args.window_sec:.0f}s)")
    print(f"  Retained total:    {len(rep.activities)}")
    print(f"  By sport:          {rep.by_sport}")
    print(f"  By device:         {rep.by_device}")
    print(f"  Runs (Stryd CIQ):  {rep.stryd_ciq_runs} | native-only: {rep.native_only_runs} | treadmill: {rep.treadmill_runs}")
    ratio_str = f"{rep.mean_swim_elapsed_over_active:.3f}x" if rep.mean_swim_elapsed_over_active else "n/a"
    print(f"  Pool swims:        {rep.pool_swims} (mean elapsed/active = {ratio_str})")
    if rep.errors:
        print("  Errors (first 5):")
        for p, err in rep.errors[:5]:
            print(f"    {p.name}: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
