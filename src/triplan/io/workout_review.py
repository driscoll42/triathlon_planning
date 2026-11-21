"""Post-workout and multi-week block diagnostic brief generator (PRD-003).

Layers deterministic within-session mechanics, lap splits, lifting-log summaries,
80/20 compliance, cross-sport 48h safety checks, and CP retest evaluation on top
of ``triplan.io.fit_ingest`` without altering any ingestion invariants.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import fitparse
import numpy as np
import pandas as pd

from triplan.control.actuals import ActualsStore
from triplan.control.mpc import ClosedLoopMPCController
from triplan.io.fit_ingest import (
    ParsedActivity,
    _safe_float,
    compute_normalized_power,
    deduplicate_activities,
    is_dev_field,
    parse_fit_file,
)
from triplan.io.garmin_wellness import compute_wellness_snapshot, default_wellness_dir
from triplan.paths import DATA_DIR, TRAINING_PLANS_DIR
from triplan.physiology.athlete_profile import (
    CHARTER_MAX_48H_AEROBIC_TSS,
    CHARTER_MAX_48H_ANS_COST,
    CHARTER_MAX_48H_MECH_STRAIN,
    CURRENT_ATHLETE,
)
from triplan.physiology.load_model import ZONES7

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _round_opt(val: Optional[float], digits: int = 2) -> Optional[float]:
    if val is None:
        return None
    return round(float(val), digits)


def _split_half_stats(
    values: Sequence[Optional[float]],
    *,
    min_samples: int = 6,
    positive_only: bool = False,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return ``(first_half_mean, second_half_mean, pct_change)`` for a sample stream."""
    clean = [
        float(v)
        for v in values
        if v is not None and (not positive_only or float(v) > 0.0)
    ]
    if len(clean) < min_samples:
        return None, None, None
    mid = len(clean) // 2
    first = float(np.mean(clean[:mid]))
    second = float(np.mean(clean[mid:]))
    pct = ((second - first) / first) * 100.0 if first > 0 else None
    return _round_opt(first, 2), _round_opt(second, 2), _round_opt(pct, 2)


def _extract_stream_and_laps(
    file_path: Path,
    sport: str,
) -> Dict[str, Any]:
    """Extract within-session streams & lap/length splits without altering ``parse_fit_file``."""
    fit = fitparse.FitFile(str(file_path))

    # Pool swim length-level half-splits (active lengths only, drills excluded from SWOLF/SR)
    swim_lengths: List[Dict[str, Optional[float]]] = []
    pool_len: Optional[float] = None
    for msg in fit.get_messages("session"):
        pool_len = _safe_float(msg.get_values().get("pool_length"))
        break

    if sport == "Swim":
        for lmsg in fit.get_messages("length"):
            lv = lmsg.get_values()
            ltype = str(lv.get("length_type") or "").lower()
            if ltype != "active":
                continue
            stroke = str(lv.get("swim_stroke") or "").lower()
            event = str(lv.get("event") or "").lower()
            is_drill = (stroke == "drill") or (event == "drill")
            ldur = _safe_float(lv.get("total_timer_time"))
            if ldur is None:
                ldur = _safe_float(lv.get("total_elapsed_time"))
            strokes = _safe_float(lv.get("total_strokes"))
            pace_100 = (
                (ldur / pool_len) * 100.0
                if (ldur is not None and ldur > 0 and pool_len is not None and pool_len > 0)
                else None
            )
            swolf = (
                (ldur + strokes)
                if (not is_drill and ldur is not None and ldur > 0 and strokes is not None and strokes > 0)
                else None
            )
            sr = (
                (strokes / (ldur / 60.0))
                if (not is_drill and ldur is not None and ldur > 0 and strokes is not None and strokes > 0)
                else None
            )
            swim_lengths.append(
                {
                    "dur_sec": ldur,
                    "pace_100_sec": pace_100,
                    "swolf": swolf,
                    "stroke_rate": sr,
                    "is_drill": 1.0 if is_drill else 0.0,
                }
            )

    # Record streams (respecting HEUR-005 Stryd CIQ Power only for Run)
    powers: List[Optional[float]] = []
    cadences: List[Optional[float]] = []
    lss_vals: List[Optional[float]] = []
    form_powers: List[Optional[float]] = []
    speeds: List[Optional[float]] = []
    hrs: List[Optional[float]] = []

    for rmsg in fit.get_messages("record"):
        native_pwr: Optional[float] = None
        stryd_pwr: Optional[float] = None
        cad: Optional[float] = None
        lss: Optional[float] = None
        fpwr: Optional[float] = None
        spd: Optional[float] = None
        hr: Optional[float] = None

        for fld in rmsg.fields:
            fname = fld.name
            val = fld.value
            dev = is_dev_field(fld)
            if fname == "heart_rate" and not dev:
                fv = _safe_float(val)
                if fv is not None and fv > 0:
                    hr = fv
            elif fname == "cadence" and not dev:
                cad = _safe_float(val)
            elif fname in ("enhanced_speed", "speed") and not dev and spd is None:
                fv = _safe_float(val)
                if fv is not None and fv >= 0:
                    spd = fv
            elif fname == "power" and not dev:
                native_pwr = _safe_float(val)
            elif dev and fname == "Power":
                stryd_pwr = _safe_float(val)
            elif dev and fname == "Form Power":
                fpwr = _safe_float(val)
            elif dev and fname == "Leg Spring Stiffness":
                lss = _safe_float(val)

        if sport == "Run":
            powers.append(stryd_pwr)
        elif sport == "Bike":
            powers.append(native_pwr)
        else:
            powers.append(None)

        cadences.append(cad)
        lss_vals.append(lss)
        form_powers.append(fpwr)
        speeds.append(spd)
        hrs.append(hr)

    # Lap summaries (capped to 25 entries for compact context)
    laps: List[Dict[str, Any]] = []
    for idx, lap_msg in enumerate(fit.get_messages("lap"), start=1):
        if idx > 25:
            break
        lv = lap_msg.get_values()
        lap_entry: Dict[str, Any] = {
            "lap": idx,
            "duration_sec": _round_opt(_safe_float(lv.get("total_timer_time")) or _safe_float(lv.get("total_elapsed_time")), 1),
            "distance_m": _round_opt(_safe_float(lv.get("total_distance")), 1),
            "avg_hr": _round_opt(_safe_float(lv.get("avg_heart_rate")), 1),
            "avg_cadence": _round_opt(_safe_float(lv.get("avg_cadence")), 1),
        }
        if sport == "Bike":
            lap_entry["avg_power"] = _round_opt(_safe_float(lv.get("avg_power")), 1)
            lap_entry["normalized_power"] = _round_opt(_safe_float(lv.get("normalized_power")), 1)
        laps.append(lap_entry)

    return {
        "powers": powers,
        "cadences": cadences,
        "lss_vals": lss_vals,
        "form_powers": form_powers,
        "speeds": speeds,
        "hrs": hrs,
        "swim_lengths": swim_lengths,
        "laps": laps,
    }


def match_planned_workout(
    *,
    sport: Optional[str] = None,
    workout_code: Optional[str] = None,
    activity_date: Optional[str] = None,
    plan_csv: Optional[Union[str, Path]] = None,
    library_csv: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, Any]]:
    """Lookup planned workout metadata from ``workout_library.csv`` or ``training_plans/*.csv``."""
    lib_path = Path(library_csv) if library_csv is not None else DATA_DIR / "workout_library.csv"
    plan_matches: List[Dict[str, Any]] = []

    if plan_csv is not None:
        p_path = Path(plan_csv)
        if p_path.exists():
            df_p = pd.read_csv(p_path)
            if workout_code and "Code" in df_p.columns:
                sub = df_p[df_p["Code"].astype(str) == str(workout_code)]
                if not sub.empty:
                    plan_matches.append(sub.iloc[0].to_dict())
            elif workout_code and "workout_code" in df_p.columns:
                sub = df_p[df_p["workout_code"].astype(str) == str(workout_code)]
                if not sub.empty:
                    plan_matches.append(sub.iloc[0].to_dict())
    elif workout_code:
        # Search standard daily plans in training_plans/ for matching Code
        for cand_name in ("level0_hybrid_20w_daily.csv", "optimized_olympic_16w_daily.csv", "plans_daily.csv"):
            p_path = TRAINING_PLANS_DIR / cand_name
            if not p_path.exists():
                continue
            df_p = pd.read_csv(p_path)
            col = "Code" if "Code" in df_p.columns else ("workout_code" if "workout_code" in df_p.columns else None)
            if col is None:
                continue
            sub = df_p[df_p[col].astype(str) == str(workout_code)]
            if not sub.empty:
                row = sub.iloc[0].to_dict()
                row["_plan_source"] = cand_name
                plan_matches.append(row)
                break

    lib_row: Optional[Dict[str, Any]] = None
    if workout_code and lib_path.exists():
        df_lib = pd.read_csv(lib_path)
        sub_lib = df_lib[df_lib["code"].astype(str) == str(workout_code)]
        if not sub_lib.empty:
            lib_row = sub_lib.iloc[0].to_dict()

    if not plan_matches and lib_row is None:
        return None

    matched = plan_matches[0] if plan_matches else {}
    out: Dict[str, Any] = {
        "code": workout_code or matched.get("Code") or matched.get("workout_code"),
        "sport": (lib_row or matched).get("sport") or (lib_row or matched).get("Sport") or sport,
        "duration_min": _safe_float((lib_row or matched).get("duration_min") or matched.get("Duration_min") or matched.get("Duration")),
        "tss": _safe_float((lib_row or matched).get("tss") or matched.get("TSS")),
        "intensity_factor": _safe_float((lib_row or matched).get("intensity_factor")),
        "intensity_label": matched.get("Intensity"),
        "description": (lib_row or matched).get("description") or matched.get("Description"),
        "source": matched.get("_plan_source") or ("workout_library.csv" if lib_row else None),
        "activity_date": activity_date,
    }
    return out


def analyze_fit_diagnostics(
    file_path: Union[str, Path],
    *,
    run_cp: Optional[float] = None,
    bike_cp: Optional[float] = None,
    swim_css: Optional[float] = None,
    mass_kg: Optional[float] = None,
    planned_code: Optional[str] = None,
    plan_csv: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Parse a single ``.fit`` file and return deterministic coaching & invariant diagnostics."""
    path = Path(file_path)
    r_cp = float(run_cp if run_cp is not None else CURRENT_ATHLETE.run_cp)
    b_cp = float(bike_cp if bike_cp is not None else CURRENT_ATHLETE.bike_cp)
    s_css = float(swim_css if swim_css is not None else CURRENT_ATHLETE.swim_css)

    parsed = parse_fit_file(path, run_cp=r_cp, bike_cp=b_cp, swim_css=s_css)
    if not parsed.ok:
        return {
            "file": str(path),
            "ok": False,
            "parse_error": parsed.parse_error,
        }

    streams = _extract_stream_and_laps(path, parsed.sport)
    missing_streams: List[str] = []

    z_total = sum(parsed.zones7_min.values())
    low_min = parsed.zones7_min.get("Z1", 0.0) + parsed.zones7_min.get("Z2", 0.0)
    zx_zy_min = parsed.zones7_min.get("ZX", 0.0) + parsed.zones7_min.get("ZY", 0.0)
    mod_high_min = (
        parsed.zones7_min.get("Z3", 0.0)
        + parsed.zones7_min.get("ZY", 0.0)
        + parsed.zones7_min.get("Z4", 0.0)
        + parsed.zones7_min.get("Z5", 0.0)
    )

    sport_diag: Dict[str, Any] = {}
    if parsed.sport == "Run":
        if not parsed.has_stryd_power:
            missing_streams.append("stryd_ciq_power")
        if parsed.avg_hr is None:
            missing_streams.append("heart_rate")
        if parsed.avg_leg_spring_stiffness is None:
            missing_streams.append("leg_spring_stiffness")
        if parsed.avg_form_power is None:
            missing_streams.append("form_power")

        fp_ratio = (
            parsed.avg_form_power / parsed.avg_power
            if (parsed.avg_form_power is not None and parsed.avg_power and parsed.avg_power > 0)
            else None
        )
        lss_1, lss_2, lss_chg = _split_half_stats(streams["lss_vals"], positive_only=True)
        cad_1, cad_2, cad_chg = _split_half_stats(streams["cadences"], positive_only=True)
        pwr_1, pwr_2, pwr_chg = _split_half_stats(streams["powers"], positive_only=True)

        valid_speeds = [s for s in streams["speeds"] if s is not None and s > 0.5]
        avg_speed_mps = float(np.mean(valid_speeds)) if valid_speeds else None
        ecor = (
            parsed.avg_power / (avg_speed_mps * float(mass_kg))
            if (mass_kg and mass_kg > 0 and avg_speed_mps and avg_speed_mps > 0 and parsed.avg_power)
            else None
        )
        sport_diag = {
            "has_stryd_power": parsed.has_stryd_power,
            "native_run_power_present_ignored": parsed.has_native_run_power,
            "is_treadmill": parsed.is_treadmill,
            "avg_air_power_w": _round_opt(parsed.avg_air_power, 1),
            "avg_form_power_w": _round_opt(parsed.avg_form_power, 1),
            "form_power_ratio": _round_opt(fp_ratio, 3),
            "avg_lss_kn_m": _round_opt(parsed.avg_leg_spring_stiffness, 2),
            "lss_first_half": lss_1,
            "lss_second_half": lss_2,
            "lss_change_pct": lss_chg,
            "power_first_half_w": pwr_1,
            "power_second_half_w": pwr_2,
            "power_change_pct": pwr_chg,
            "avg_cadence_spm": _round_opt(parsed.avg_cadence, 1),
            "cadence_first_half": cad_1,
            "cadence_second_half": cad_2,
            "cadence_change_pct": cad_chg,
            "avg_speed_mps": _round_opt(avg_speed_mps, 3),
            "ecor_w_per_kg_mps": _round_opt(ecor, 3),
        }

    elif parsed.sport == "Bike":
        valid_pwrs = [p for p in streams["powers"] if p is not None]
        if not valid_pwrs:
            missing_streams.append("power")
        if parsed.avg_hr is None:
            missing_streams.append("heart_rate")

        vi = (
            parsed.normalized_power / parsed.avg_power
            if (parsed.normalized_power is not None and parsed.avg_power and parsed.avg_power > 0)
            else None
        )
        zero_pwr_pct = (
            (sum(1 for p in valid_pwrs if p == 0.0) / len(valid_pwrs)) * 100.0
            if valid_pwrs
            else None
        )
        pwr_1, pwr_2, pwr_chg = _split_half_stats(streams["powers"], positive_only=False)
        cad_1, cad_2, cad_chg = _split_half_stats(streams["cadences"], positive_only=True)
        is_indoor = (parsed.sub_sport or "").lower() in ("virtual_activity", "indoor_cycling", "trainer") or (
            parsed.distance_m == 0.0 and parsed.duration_sec > 300
        )
        sport_diag = {
            "variability_index": _round_opt(vi, 3),
            "zero_power_pct": _round_opt(zero_pwr_pct, 1),
            "power_first_half_w": pwr_1,
            "power_second_half_w": pwr_2,
            "power_change_pct": pwr_chg,
            "avg_cadence_rpm": _round_opt(parsed.avg_cadence, 1),
            "cadence_first_half": cad_1,
            "cadence_second_half": cad_2,
            "cadence_change_pct": cad_chg,
            "positional_context": (
                "indoor_neo2t_aero_baseline"
                if is_indoor
                else f"outdoor_edge530_aero_vs_hoods_delta_{CURRENT_ATHLETE.aero_position_watts_delta:.0f}W"
            ),
            "neo2t_lr_balance_ignored": True,
        }

    elif parsed.sport == "Swim":
        slens = streams["swim_lengths"]
        if not slens:
            missing_streams.append("pool_length_records")
        pace_100 = (
            (parsed.duration_sec / (parsed.distance_m / 100.0))
            if (parsed.duration_sec > 0 and parsed.distance_m > 0)
            else None
        )
        css_pace_100 = (100.0 / (s_css / 60.0)) if s_css > 0 else None
        pace_1, pace_2, pace_chg = _split_half_stats([x["pace_100_sec"] for x in slens], positive_only=True)
        sw_1, sw_2, sw_chg = _split_half_stats([x["swolf"] for x in slens], positive_only=True)
        sr_1, sr_2, sr_chg = _split_half_stats([x["stroke_rate"] for x in slens], positive_only=True)
        rest_sec = max(0.0, parsed.elapsed_sec - parsed.duration_sec) if parsed.elapsed_sec > 0 else None
        sport_diag = {
            "active_lengths": parsed.active_lengths,
            "rest_lengths": parsed.rest_lengths,
            "drill_lengths": parsed.drill_lengths,
            "active_duration_min": _round_opt(parsed.duration_sec / 60.0, 2),
            "rest_duration_min": _round_opt(rest_sec / 60.0, 2) if rest_sec is not None else None,
            "active_ratio": _round_opt(parsed.duration_sec / parsed.elapsed_sec, 3) if parsed.elapsed_sec > 0 else None,
            "pace_per_100m_sec": _round_opt(pace_100, 2),
            "css_pace_per_100m_sec": _round_opt(css_pace_100, 2),
            "pace_first_half_sec": pace_1,
            "pace_second_half_sec": pace_2,
            "pace_change_pct": pace_chg,
            "avg_swolf": _round_opt(parsed.avg_swolf, 2),
            "swolf_first_half": sw_1,
            "swolf_second_half": sw_2,
            "swolf_change_pct": sw_chg,
            "avg_stroke_rate": _round_opt(parsed.avg_stroke_rate, 1),
            "stroke_rate_first_half": sr_1,
            "stroke_rate_second_half": sr_2,
            "stroke_rate_change_pct": sr_chg,
        }

    act_date = parsed.start_time.date().isoformat() if parsed.start_time else None
    planned = match_planned_workout(
        sport=parsed.sport,
        workout_code=planned_code,
        activity_date=act_date,
        plan_csv=plan_csv,
    )
    planned_comparison: Optional[Dict[str, Any]] = None
    moderate_rut_flag = False
    if parsed.intensity_factor is not None and parsed.intensity_factor > 0.78:
        if planned and str(planned.get("intensity_label") or "").upper() == "EASY":
            moderate_rut_flag = True
    if planned is not None:
        dur_min = parsed.duration_sec / 60.0
        p_dur = planned.get("duration_min")
        p_tss = planned.get("tss")
        p_if = planned.get("intensity_factor")
        planned_comparison = {
            "planned": planned,
            "duration_err_pct": _round_opt(((dur_min - p_dur) / p_dur) * 100.0, 1) if p_dur else None,
            "tss_err_pct": _round_opt(((parsed.tss - p_tss) / p_tss) * 100.0, 1) if (p_tss and parsed.tss is not None) else None,
            "if_err_pct": _round_opt(((parsed.intensity_factor - p_if) / p_if) * 100.0, 1)
            if (p_if and parsed.intensity_factor is not None)
            else None,
            "moderate_intensity_rut_reclassified_hard": moderate_rut_flag,
        }

    return {
        "file": str(path),
        "ok": True,
        "date": act_date,
        "start_time": parsed.start_time.isoformat() if parsed.start_time else None,
        "sport": parsed.sport,
        "sub_sport": parsed.sub_sport,
        "device": parsed.device_name,
        "duration_min": _round_opt(parsed.duration_sec / 60.0, 2),
        "elapsed_min": _round_opt(parsed.elapsed_sec / 60.0, 2),
        "distance_m": _round_opt(parsed.distance_m, 1),
        "avg_power_w": _round_opt(parsed.avg_power, 1),
        "normalized_power_w": _round_opt(parsed.normalized_power, 1),
        "intensity_factor": _round_opt(parsed.intensity_factor, 3),
        "tss": _round_opt(parsed.tss, 1),
        "mechanical_strain": _round_opt(parsed.mechanical_strain, 1),
        "ans_cost": _round_opt(parsed.ans_cost_val, 1),
        "avg_hr": _round_opt(parsed.avg_hr, 1),
        "max_hr": _round_opt(parsed.max_hr, 1),
        "efficiency_factor": _round_opt(parsed.efficiency_factor, 3),
        "decoupling_pct": _round_opt(parsed.decoupling_pct, 2),
        "zones7_min": {z: _round_opt(parsed.zones7_min[z], 2) for z in ZONES7},
        "polarity": {
            "low_z1_z2_pct": _round_opt((low_min / z_total) * 100.0, 1) if z_total > 0 else None,
            "mod_high_pct": _round_opt((mod_high_min / z_total) * 100.0, 1) if z_total > 0 else None,
            "zone_x_y_min": _round_opt(zx_zy_min, 2),
        },
        "moderate_intensity_rut_flag": moderate_rut_flag,
        "sport_diagnostics": sport_diag,
        "laps": streams["laps"],
        "planned_comparison": planned_comparison,
        "missing_streams": missing_streams,
    }


def summarize_lifting_window(
    start_date: str,
    end_date: str,
    *,
    lifting_json: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Summarize structured lifting sessions from ``parsed_lifting_history.json`` in ``[start_date, end_date]``."""
    lpath = Path(lifting_json) if lifting_json is not None else DATA_DIR / "lifting" / "parsed_lifting_history.json"
    if not lpath.exists():
        return {
            "available": False,
            "source": str(lpath),
            "session_count": 0,
            "sessions": [],
        }

    raw = json.loads(lpath.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raw = raw.get("sessions", []) if isinstance(raw, dict) else []

    in_window = [
        s for s in raw
        if isinstance(s, dict) and s.get("date") and start_date <= str(s["date"]) <= end_date
    ]
    in_window.sort(key=lambda s: str(s["date"]))

    pattern_sets: Dict[str, int] = defaultdict(int)
    bb_sets = 0
    db_sets = 0
    other_sets = 0
    total_working_sets = 0
    total_tonnage = 0.0
    total_axial = 0.0
    max_session_axial = 0.0
    best_e1rm_by_lift: Dict[str, float] = {}
    heavy_dl_dates: List[str] = []
    heavy_sq_dates: List[str] = []
    compact_sessions: List[Dict[str, Any]] = []

    for sess in in_window:
        s_date = str(sess["date"])
        s_sets = int(sess.get("total_working_sets") or 0)
        s_tonnage = float(sess.get("total_working_tonnage_lbs") or 0.0)
        s_axial = float(sess.get("estimated_axial_load") or 0.0)
        total_working_sets += s_sets
        total_tonnage += s_tonnage
        total_axial += s_axial
        max_session_axial = max(max_session_axial, s_axial)

        for pat, cnt in (sess.get("movement_pattern_sets") or {}).items():
            pattern_sets[str(pat)] += int(cnt or 0)

        ex_summaries: List[Dict[str, Any]] = []
        for ex in sess.get("exercises") or []:
            ename = str(ex.get("exercise") or "")
            equip = str(ex.get("equipment") or "")
            sets_done = int(ex.get("sets_completed") or 0)
            e1rm = _safe_float(ex.get("best_e1rm_lbs"))
            top_wt = _safe_float(ex.get("top_weight_lbs"))

            if equip == "Barbell":
                bb_sets += sets_done
            elif equip == "Dumbbell":
                db_sets += sets_done
            else:
                other_sets += sets_done

            if e1rm is not None:
                prev = best_e1rm_by_lift.get(ename)
                if prev is None or e1rm > prev:
                    best_e1rm_by_lift[ename] = round(e1rm, 1)

            low_name = ename.lower()
            if "deadlift" in low_name and sets_done > 0 and s_date not in heavy_dl_dates:
                heavy_dl_dates.append(s_date)
            if "squat" in low_name and sets_done > 0 and s_date not in heavy_sq_dates:
                heavy_sq_dates.append(s_date)

            ex_summaries.append(
                {
                    "exercise": ename,
                    "pattern": ex.get("movement_pattern"),
                    "equipment": equip,
                    "sets": sets_done,
                    "top_set": ex.get("top_set"),
                    "top_weight_lbs": top_wt,
                    "best_e1rm_lbs": round(e1rm, 1) if e1rm is not None else None,
                }
            )

        compact_sessions.append(
            {
                "date": s_date,
                "sheets": sess.get("sheets_referenced") or [],
                "working_sets": s_sets,
                "working_tonnage_lbs": round(s_tonnage, 1),
                "estimated_axial_load": round(s_axial, 1),
                "movement_pattern_sets": dict(sess.get("movement_pattern_sets") or {}),
                "exercises": ex_summaries,
            }
        )

    bb_db_denom = bb_sets + db_sets
    bb_share = (bb_sets / bb_db_denom) if bb_db_denom > 0 else None

    return {
        "available": True,
        "source": str(lpath),
        "start_date": start_date,
        "end_date": end_date,
        "session_count": len(compact_sessions),
        "total_working_sets": total_working_sets,
        "total_working_tonnage_lbs": round(total_tonnage, 1),
        "total_axial_load": round(total_axial, 1),
        "max_session_axial_load": round(max_session_axial, 1),
        "movement_pattern_sets": dict(pattern_sets),
        "equipment_sets": {
            "barbell": bb_sets,
            "dumbbell": db_sets,
            "other": other_sets,
            "barbell_share_vs_db": _round_opt(bb_share, 3),
            "meets_60pct_barbell_rule": (bb_share >= CURRENT_ATHLETE.min_barbell_set_share) if bb_share is not None else None,
        },
        "best_e1rm_lbs": best_e1rm_by_lift,
        "deadlift_dates": heavy_dl_dates,
        "squat_dates": heavy_sq_dates,
        "sessions": compact_sessions,
    }


def _select_fit_files_for_window(
    fit_dir: Path,
    start_date: str,
    end_date: str,
) -> List[Path]:
    """Return ``*.fit`` files likely overlapping ``[start_date, end_date]`` (fast date-prefix filter)."""
    if not fit_dir.exists():
        return []
    start_buf = (date.fromisoformat(start_date) - timedelta(days=1)).isoformat()
    end_buf = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    selected: List[Path] = []
    for f in sorted(fit_dir.glob("*.fit")):
        m = _DATE_PREFIX_RE.match(f.name)
        if m:
            d_str = m.group(1)
            if start_buf <= d_str <= end_buf:
                selected.append(f)
        else:
            selected.append(f)
    return selected


def _rolling_48h_peaks(daily_map: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """Compute peak 2-day rolling TSS, mechanical strain, and ANS cost across ``daily_map``."""
    if not daily_map:
        return {"peak_48h_tss": 0.0, "peak_48h_mech_strain": 0.0, "peak_48h_ans_cost": 0.0}
    dates = sorted(daily_map.keys())
    cur = date.fromisoformat(dates[0])
    end = date.fromisoformat(dates[-1])
    peak_tss = 0.0
    peak_mech = 0.0
    peak_ans = 0.0
    while cur <= end:
        d0 = cur.isoformat()
        d1 = (cur + timedelta(days=1)).isoformat()
        tss = daily_map.get(d0, {}).get("tss", 0.0) + daily_map.get(d1, {}).get("tss", 0.0)
        mech = daily_map.get(d0, {}).get("mech_strain", 0.0) + daily_map.get(d1, {}).get("mech_strain", 0.0)
        ans = daily_map.get(d0, {}).get("ans_cost", 0.0) + daily_map.get(d1, {}).get("ans_cost", 0.0)
        peak_tss = max(peak_tss, tss)
        peak_mech = max(peak_mech, mech)
        peak_ans = max(peak_ans, ans)
        cur += timedelta(days=1)
    return {
        "peak_48h_tss": round(peak_tss, 1),
        "peak_48h_mech_strain": round(peak_mech, 1),
        "peak_48h_ans_cost": round(peak_ans, 1),
    }


def _check_deadlift_run_collisions(
    dl_dates: Sequence[str],
    run_activities: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Check HEUR-004 same-day or adjacent-day (+1d) deadlift-to-run collisions."""
    if not dl_dates or not run_activities:
        return []
    dl_set = {date.fromisoformat(d) for d in dl_dates}
    collisions: List[Dict[str, Any]] = []
    for act in run_activities:
        adate_str = act.get("date")
        if not adate_str:
            continue
        adate = date.fromisoformat(adate_str)
        if adate in dl_set:
            collisions.append({"type": "same_day", "date": adate_str, "file": Path(act["file"]).name})
        elif (adate - timedelta(days=1)) in dl_set:
            collisions.append(
                {
                    "type": "next_day_after_deadlift",
                    "deadlift_date": (adate - timedelta(days=1)).isoformat(),
                    "run_date": adate_str,
                    "file": Path(act["file"]).name,
                }
            )
    return collisions


def build_review_brief(
    *,
    fit_path: Optional[Union[str, Path]] = None,
    fit_dir: Optional[Union[str, Path]] = None,
    target_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: int = 7,
    lifting_json: Optional[Union[str, Path]] = None,
    wellness_dir: Optional[Union[str, Path]] = None,
    actuals_path: Optional[Union[str, Path]] = None,
    record: bool = False,
    planned_code: Optional[str] = None,
    plan_csv: Optional[Union[str, Path]] = None,
    mass_kg: Optional[float] = None,
    run_cp: Optional[float] = None,
    bike_cp: Optional[float] = None,
    swim_css: Optional[float] = None,
    current_week: int = 1,
    last_test_week: int = 0,
) -> Dict[str, Any]:
    """Generate a single-workout or multi-day block telemetry brief (read-only unless ``record=True``)."""
    r_cp = float(run_cp if run_cp is not None else CURRENT_ATHLETE.run_cp)
    b_cp = float(bike_cp if bike_cp is not None else CURRENT_ATHLETE.bike_cp)
    s_css = float(swim_css if swim_css is not None else CURRENT_ATHLETE.swim_css)

    f_dir = Path(fit_dir) if fit_dir is not None else DATA_DIR / "garmin" / "activities" / "fit_files"
    w_dir = Path(wellness_dir) if wellness_dir is not None else default_wellness_dir()
    a_path = Path(actuals_path) if actuals_path is not None else DATA_DIR / "actuals.json"

    # Single-workout mode
    if fit_path is not None:
        fpath = Path(fit_path)
        diag = analyze_fit_diagnostics(
            fpath,
            run_cp=r_cp,
            bike_cp=b_cp,
            swim_css=s_css,
            mass_kg=mass_kg,
            planned_code=planned_code,
            plan_csv=plan_csv,
        )
        act_date = diag.get("date")
        recorded_id: Optional[str] = None
        if record and diag.get("ok"):
            parsed = parse_fit_file(fpath, run_cp=r_cp, bike_cp=b_cp, swim_css=s_css)
            store = ActualsStore(path=a_path, auto_save=True)
            comp = store.record_parsed(parsed, workout_code=planned_code)
            recorded_id = comp.activity_id

        trailing_48h_acts: List[Dict[str, Any]] = []
        lifting_ctx: Dict[str, Any] = {"available": False, "session_count": 0, "sessions": []}
        wellness_ctx: Optional[Dict[str, Any]] = None
        collisions: List[Dict[str, Any]] = []

        if act_date:
            d_obj = date.fromisoformat(act_date)
            prev_day = (d_obj - timedelta(days=1)).isoformat()
            prev_2d = (d_obj - timedelta(days=2)).isoformat()
            cand_files = _select_fit_files_for_window(f_dir, prev_day, act_date)
            parsed_cands = [
                parse_fit_file(f, run_cp=r_cp, bike_cp=b_cp, swim_css=s_css)
                for f in cand_files
            ]
            kept, _ = deduplicate_activities(parsed_cands, window_sec=300.0)
            daily_totals: Dict[str, Dict[str, float]] = defaultdict(lambda: {"tss": 0.0, "mech_strain": 0.0, "ans_cost": 0.0})
            for pa in kept:
                if pa.start_time is None:
                    continue
                pd_str = pa.start_time.date().isoformat()
                if prev_day <= pd_str <= act_date:
                    daily_totals[pd_str]["tss"] += float(pa.tss or 0.0)
                    daily_totals[pd_str]["mech_strain"] += float(pa.mechanical_strain or 0.0)
                    daily_totals[pd_str]["ans_cost"] += float(pa.ans_cost_val or 0.0)
                    trailing_48h_acts.append(
                        {
                            "date": pd_str,
                            "sport": pa.sport,
                            "device": pa.device_name,
                            "duration_min": _round_opt(pa.duration_sec / 60.0, 1),
                            "tss": _round_opt(pa.tss, 1),
                            "mechanical_strain": _round_opt(pa.mechanical_strain, 1),
                            "ans_cost": _round_opt(pa.ans_cost_val, 1),
                            "file": pa.file_path.name,
                        }
                    )
            peaks = _rolling_48h_peaks(daily_totals)
            lifting_ctx = summarize_lifting_window(prev_2d, act_date, lifting_json=lifting_json)
            if diag.get("sport") == "Run":
                collisions = _check_deadlift_run_collisions(lifting_ctx.get("deadlift_dates", []), [diag])
            snap = compute_wellness_snapshot(target_date=act_date, wellness_dir=w_dir)
            wellness_ctx = asdict(snap)
        else:
            peaks = {"peak_48h_tss": 0.0, "peak_48h_mech_strain": 0.0, "peak_48h_ans_cost": 0.0}

        return {
            "mode": "single_workout",
            "recorded_to_actuals": recorded_id,
            "thresholds": {"run_cp": r_cp, "bike_cp": b_cp, "swim_css": s_css},
            "activity": diag,
            "cross_sport_context": {
                "trailing_48h_activities": trailing_48h_acts,
                "rolling_48h_peaks": peaks,
                "caps": {
                    "charter_48h_tss": CHARTER_MAX_48H_AEROBIC_TSS,
                    "charter_48h_mech_strain": CHARTER_MAX_48H_MECH_STRAIN,
                    "charter_48h_ans_cost": CHARTER_MAX_48H_ANS_COST,
                    "athlete_48h_ans_cost": CURRENT_ATHLETE.max_48h_ans_cost,
                },
                "deadlift_run_collisions": collisions,
                "recent_lifting": lifting_ctx,
                "wellness": wellness_ctx,
            },
        }

    # Block / Date-Window mode
    if start_date and end_date:
        s_date, e_date = start_date, end_date
    elif target_date:
        e_date = target_date
        s_date = (date.fromisoformat(e_date) - timedelta(days=max(days, 1) - 1)).isoformat()
    else:
        e_date = date.today().isoformat()
        s_date = (date.fromisoformat(e_date) - timedelta(days=max(days, 1) - 1)).isoformat()

    cand_files = _select_fit_files_for_window(f_dir, s_date, e_date)
    parsed_all = [
        parse_fit_file(f, run_cp=r_cp, bike_cp=b_cp, swim_css=s_css)
        for f in cand_files
    ]
    parse_errors = [{"file": p.file_path.name, "error": p.parse_error} for p in parsed_all if not p.ok]
    kept, dropped = deduplicate_activities(parsed_all, window_sec=300.0)

    # Filter strictly to [s_date, e_date]
    kept_in_window = [
        pa for pa in kept
        if pa.start_time is not None and s_date <= pa.start_time.date().isoformat() <= e_date
    ]
    kept_in_window.sort(key=lambda a: a.start_time or datetime.min)

    store = ActualsStore(path=a_path if record else None, auto_save=record)
    if not record and a_path.exists():
        # Read-only load of existing actuals for CP telemetry baseline if present
        store = ActualsStore(path=a_path, auto_save=False)

    diags_by_sport: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    daily_totals = defaultdict(lambda: {"tss": 0.0, "mech_strain": 0.0, "ans_cost": 0.0})
    endurance_zones: Dict[str, float] = {z: 0.0 for z in ZONES7}
    sport_totals: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"sessions": 0, "duration_min": 0.0, "tss": 0.0, "mech_strain": 0.0, "ans_cost": 0.0}
    )

    recorded_ids: List[str] = []
    for pa in kept_in_window:
        if record:
            comp = store.record_parsed(pa)
            recorded_ids.append(comp.activity_id)
        elif pa.start_time is not None:
            # Populate in-memory store for CP retest evaluation without touching disk
            from triplan.control.actuals import CompletedActivity

            store.record(CompletedActivity.from_parsed_activity(pa))

        diag = analyze_fit_diagnostics(
            pa.file_path,
            run_cp=r_cp,
            bike_cp=b_cp,
            swim_css=s_css,
            mass_kg=mass_kg,
        )
        diags_by_sport[pa.sport].append(diag)
        d_str = pa.start_time.date().isoformat()  # type: ignore[union-attr]
        daily_totals[d_str]["tss"] += float(pa.tss or 0.0)
        daily_totals[d_str]["mech_strain"] += float(pa.mechanical_strain or 0.0)
        daily_totals[d_str]["ans_cost"] += float(pa.ans_cost_val or 0.0)

        st = sport_totals[pa.sport]
        st["sessions"] += 1
        st["duration_min"] += float(pa.duration_sec) / 60.0
        st["tss"] += float(pa.tss or 0.0)
        st["mech_strain"] += float(pa.mechanical_strain or 0.0)
        st["ans_cost"] += float(pa.ans_cost_val or 0.0)

        if pa.sport in ("Run", "Bike", "Swim"):
            for z in ZONES7:
                endurance_zones[z] += float(pa.zones7_min.get(z, 0.0))

    total_z_min = sum(endurance_zones.values())
    low_min = endurance_zones["Z1"] + endurance_zones["Z2"]
    zx_min = endurance_zones["ZX"]
    mod_high_min = endurance_zones["Z3"] + endurance_zones["ZY"] + endurance_zones["Z4"] + endurance_zones["Z5"]

    lifting_summary = summarize_lifting_window(s_date, e_date, lifting_json=lifting_json)
    peaks = _rolling_48h_peaks(daily_totals)
    collisions = _check_deadlift_run_collisions(
        lifting_summary.get("deadlift_dates", []),
        diags_by_sport.get("Run", []),
    )

    # Weekly single-run max share check (ADR-003 / ADR-022)
    runs_by_iso_week: Dict[str, List[float]] = defaultdict(list)
    for r in diags_by_sport.get("Run", []):
        if r.get("date") and r.get("duration_min") is not None:
            iso = date.fromisoformat(r["date"]).isocalendar()
            wk_key = f"{iso.year}-W{iso.week:02d}"
            runs_by_iso_week[wk_key].append(float(r["duration_min"]))
    run_ceiling_checks: List[Dict[str, Any]] = []
    for wk_key, durs in sorted(runs_by_iso_week.items()):
        tot_r = sum(durs)
        max_r = max(durs)
        ratio = (max_r / tot_r) if tot_r > 0 else 0.0
        run_ceiling_checks.append(
            {
                "iso_week": wk_key,
                "run_count": len(durs),
                "total_run_min": round(tot_r, 1),
                "longest_run_min": round(max_r, 1),
                "max_single_run_ratio": round(ratio, 3),
                "exceeds_32pct_rule": (len(durs) >= 4 and ratio > CURRENT_ATHLETE.max_single_run_vol_ratio),
            }
        )

    cp_retest = {
        "Run": ClosedLoopMPCController.evaluate_cp_retest_from_actuals(
            store,
            "run",
            current_week=current_week,
            last_test_week=last_test_week,
            as_of_date=e_date,
            is_reentry_phase=CURRENT_ATHLETE.reentry_phase,
        ),
        "Bike": ClosedLoopMPCController.evaluate_cp_retest_from_actuals(
            store,
            "bike",
            current_week=current_week,
            last_test_week=last_test_week,
            as_of_date=e_date,
            is_reentry_phase=CURRENT_ATHLETE.reentry_phase,
        ),
    }

    wellness_snap = asdict(compute_wellness_snapshot(target_date=e_date, wellness_dir=w_dir))

    def _sport_rollups(sport_name: str) -> Dict[str, Any]:
        items = diags_by_sport.get(sport_name, [])
        efs = [x["efficiency_factor"] for x in items if x.get("efficiency_factor") is not None]
        decs = [x["decoupling_pct"] for x in items if x.get("decoupling_pct") is not None]
        roll: Dict[str, Any] = {
            "totals": {k: round(v, 1) if isinstance(v, float) else v for k, v in sport_totals[sport_name].items()},
            "mean_ef": _round_opt(float(np.mean(efs)), 3) if efs else None,
            "mean_decoupling_pct": _round_opt(float(np.mean(decs)), 2) if decs else None,
            "activities": items,
        }
        if sport_name == "Run":
            lss_vals = [x["sport_diagnostics"].get("avg_lss_kn_m") for x in items if x["sport_diagnostics"].get("avg_lss_kn_m") is not None]
            fp_ratios = [x["sport_diagnostics"].get("form_power_ratio") for x in items if x["sport_diagnostics"].get("form_power_ratio") is not None]
            roll["mean_lss_kn_m"] = _round_opt(float(np.mean(lss_vals)), 2) if lss_vals else None
            roll["mean_form_power_ratio"] = _round_opt(float(np.mean(fp_ratios)), 3) if fp_ratios else None
            roll["run_ceiling_checks"] = run_ceiling_checks
        elif sport_name == "Bike":
            vis = [x["sport_diagnostics"].get("variability_index") for x in items if x["sport_diagnostics"].get("variability_index") is not None]
            roll["mean_vi"] = _round_opt(float(np.mean(vis)), 3) if vis else None
        elif sport_name == "Swim":
            swolfs = [x["sport_diagnostics"].get("avg_swolf") for x in items if x["sport_diagnostics"].get("avg_swolf") is not None]
            paces = [x["sport_diagnostics"].get("pace_per_100m_sec") for x in items if x["sport_diagnostics"].get("pace_per_100m_sec") is not None]
            roll["mean_swolf"] = _round_opt(float(np.mean(swolfs)), 2) if swolfs else None
            roll["mean_pace_per_100m_sec"] = _round_opt(float(np.mean(paces)), 2) if paces else None
        return roll

    return {
        "mode": "block_review",
        "window": {"start_date": s_date, "end_date": e_date},
        "recorded_to_actuals_count": len(recorded_ids),
        "thresholds": {"run_cp": r_cp, "bike_cp": b_cp, "swim_css": s_css},
        "ingestion": {
            "files_scanned": len(cand_files),
            "parse_errors": parse_errors,
            "dedup_dropped": [
                {"dropped": d.file_path.name, "kept": k.file_path.name, "delta_sec": round(dt, 1)}
                for d, k, dt in dropped
            ],
            "activities_in_window": len(kept_in_window),
        },
        "sports": {
            "Run": _sport_rollups("Run"),
            "Bike": _sport_rollups("Bike"),
            "Swim": _sport_rollups("Swim"),
            "Strength": lifting_summary,
        },
        "cross_sport_summary": {
            "polarity_80_20": {
                "endurance_zone_min": round(total_z_min, 1),
                "low_z1_z2_pct": _round_opt((low_min / total_z_min) * 100.0, 1) if total_z_min > 0 else None,
                "mod_high_pct": _round_opt((mod_high_min / total_z_min) * 100.0, 1) if total_z_min > 0 else None,
                "zone_x_pct": _round_opt((zx_min / total_z_min) * 100.0, 1) if total_z_min > 0 else None,
                "zones7_min": {z: round(endurance_zones[z], 1) for z in ZONES7},
            },
            "rolling_48h_peaks": peaks,
            "caps": {
                "charter_48h_tss": CHARTER_MAX_48H_AEROBIC_TSS,
                "charter_48h_mech_strain": CHARTER_MAX_48H_MECH_STRAIN,
                "charter_48h_ans_cost": CHARTER_MAX_48H_ANS_COST,
                "athlete_48h_ans_cost": CURRENT_ATHLETE.max_48h_ans_cost,
            },
            "deadlift_run_collisions": collisions,
            "cp_retest_evaluation": cp_retest,
            "wellness": wellness_snap,
        },
    }


def format_brief_markdown(brief: Dict[str, Any]) -> str:
    """Render a compact Markdown coaching brief from ``build_review_brief()``."""
    lines: List[str] = []
    if brief.get("mode") == "single_workout":
        act = brief["activity"]
        if not act.get("ok"):
            return f"# Workout Brief Error\n\nFailed to parse `{act.get('file')}`: `{act.get('parse_error')}`"
        lines.append(f"# Post-Workout Brief: {act['sport']} ({act.get('date') or 'unknown date'})")
        lines.append(
            f"- **File:** `{Path(act['file']).name}` | **Device:** `{act['device']}` | "
            f"**Duration:** `{act['duration_min']} min` | **TSS:** `{act['tss']}` | "
            f"**Strain:** `{act['mechanical_strain']}` | **ANS:** `{act['ans_cost']}`"
        )
        lines.append(
            f"- **Intensity:** IF `{act['intensity_factor']}` | NP `{act['normalized_power_w']} W` | "
            f"AvgHR `{act['avg_hr']}` | EF `{act['efficiency_factor']}` | Decoupling `{act['decoupling_pct']}%`"
        )
        pol = act["polarity"]
        lines.append(
            f"- **Polarity:** Z1/Z2 `{pol['low_z1_z2_pct']}%` | Mod+High `{pol['mod_high_pct']}%` | "
            f"Zone X+Y `{pol['zone_x_y_min']} min` | Rut Flag: `{act['moderate_intensity_rut_flag']}`"
        )
        if act["sport_diagnostics"]:
            lines.append(f"- **Sport Diagnostics:** `{json.dumps(act['sport_diagnostics'])}`")
        ctx = brief["cross_sport_context"]
        lines.append(
            f"- **48h Context Peaks:** TSS `{ctx['rolling_48h_peaks']['peak_48h_tss']}` | "
            f"Strain `{ctx['rolling_48h_peaks']['peak_48h_mech_strain']}` | "
            f"ANS `{ctx['rolling_48h_peaks']['peak_48h_ans_cost']}`"
        )
        if ctx["deadlift_run_collisions"]:
            lines.append(f"- **HEUR-004 Collisions:** `{ctx['deadlift_run_collisions']}`")
        if act["missing_streams"]:
            lines.append(f"- **Missing Streams:** `{', '.join(act['missing_streams'])}`")
        return "\n".join(lines)

    win = brief["window"]
    lines.append(f"# Block Telemetry Brief: {win['start_date']} to {win['end_date']}")
    ing = brief["ingestion"]
    lines.append(
        f"- **Activities:** {ing['activities_in_window']} in window "
        f"(scanned {ing['files_scanned']}, dedup dropped {len(ing['dedup_dropped'])}, errors {len(ing['parse_errors'])})"
    )
    for sp in ("Run", "Bike", "Swim"):
        sd = brief["sports"][sp]
        t = sd["totals"]
        lines.append(
            f"- **{sp}:** {t['sessions']} sessions | {t['duration_min']} min | "
            f"TSS {t['tss']} | Strain {t['mech_strain']} | Mean EF `{sd['mean_ef']}` | Decoupling `{sd['mean_decoupling_pct']}%`"
        )
    st = brief["sports"]["Strength"]
    lines.append(
        f"- **Strength:** {st['session_count']} sessions | {st.get('total_working_sets', 0)} working sets | "
        f"BB share `{st.get('equipment_sets', {}).get('barbell_share_vs_db')}` | "
        f"Patterns `{st.get('movement_pattern_sets', {})}`"
    )
    cs = brief["cross_sport_summary"]
    pol = cs["polarity_80_20"]
    lines.append(
        f"- **80/20 Polarity:** Low `{pol['low_z1_z2_pct']}%` | Mod+High `{pol['mod_high_pct']}%` | "
        f"Zone X `{pol['zone_x_pct']}%`"
    )
    lines.append(f"- **48h Peaks:** `{cs['rolling_48h_peaks']}`")
    if cs["deadlift_run_collisions"]:
        lines.append(f"- **HEUR-004 Collisions:** `{cs['deadlift_run_collisions']}`")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Post-workout & block telemetry brief generator (PRD-003).")
    parser.add_argument("--fit", type=str, default=None, help="Single .fit file path for post-workout review.")
    parser.add_argument("--fit-dir", type=str, default=None, help="Directory of .fit files (default: data/garmin/activities/fit_files).")
    parser.add_argument("--date", type=str, default=None, help="Anchor end date YYYY-MM-DD (default: today).")
    parser.add_argument("--start", type=str, default=None, help="Window start YYYY-MM-DD (overrides --days).")
    parser.add_argument("--end", type=str, default=None, help="Window end YYYY-MM-DD (overrides --days).")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days when --fit is omitted (default: 7).")
    parser.add_argument("--lifting-json", type=str, default=None, help="Path to parsed_lifting_history.json.")
    parser.add_argument("--wellness-dir", type=str, default=None, help="Path to Garmin wellness directory.")
    parser.add_argument("--actuals", type=str, default=None, help="Path to ActualsStore JSON (default: data/actuals.json).")
    parser.add_argument("--record", action="store_true", help="Persist reviewed .fit activity(ies) into ActualsStore.")
    parser.add_argument("--planned-code", type=str, default=None, help="Optional planned workout code for delta comparison.")
    parser.add_argument("--plan-csv", type=str, default=None, help="Optional training_plans/*.csv path.")
    parser.add_argument("--mass-kg", type=float, default=None, help="Optional athlete mass (kg) for Run ECOR.")
    parser.add_argument("--current-week", type=int, default=1, help="Current mesocycle week for CP retest check.")
    parser.add_argument("--last-test-week", type=int, default=0, help="Week of last CP test for retest check.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Output format (default: json).")
    args = parser.parse_args(argv)

    brief = build_review_brief(
        fit_path=args.fit,
        fit_dir=args.fit_dir,
        target_date=args.date,
        start_date=args.start,
        end_date=args.end,
        days=args.days,
        lifting_json=args.lifting_json,
        wellness_dir=args.wellness_dir,
        actuals_path=args.actuals,
        record=args.record,
        planned_code=args.planned_code,
        plan_csv=args.plan_csv,
        mass_kg=args.mass_kg,
        current_week=args.current_week,
        last_test_week=args.last_test_week,
    )

    if args.format == "markdown":
        print(format_brief_markdown(brief))
    else:
        print(json.dumps(brief, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
