"""EXP-006: Load-Model Calibration Against Historical Garmin & Lifting Telemetry.

Calibrates and validates four physiological sub-models against the athlete's local
read-only telemetry archive (``data/garmin/`` and ``data/lifting/``):

1. Banister PMC decay: ``alpha = 1 - exp(-1/tau)`` vs legacy ``2 / (N + 1)``
   (analytical 7d/42d step response + historical daily TSS trace).
2. Planned vs realized 80/20 zone midpoints (``RUN_MID7``, ``BIKE_MID7``) from
   deduplicated ``.fit`` power streams (Stryd CIQ ``Power`` for Run, Edge 530 for Bike).
3. Running mechanical strain exponent ``beta in {1.0, 1.5, 2.0, 3.0}``: induced
   7d/28d EWMA ACWR and 35d/120d ODI distributions across historical running blocks.
4. Morning readiness ``alpha_d`` z-score mapping across 270 nocturnal HRV/RHR/sleep
   nights: legacy ``rhr_sd = 0.5 * rmssd_sd`` in ``[0.70, 1.05]`` vs separate 28-day
   rolling RHR SD in ``[0.60, 1.00]`` and calibrated ``(a, b, c)`` weights.

Usage:
    python scripts/exp006_load_calibration.py
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from triplan.control.mpc import ClosedLoopMPCController
from triplan.io.fit_ingest import CorpusAuditReport, ParsedActivity, audit_corpus
from triplan.io.garmin_wellness import (
    _load_hrv_map,
    _load_rhr_map,
    _load_sleep_map,
    _trailing_stats,
)
from triplan.paths import DATA_DIR
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.physiology.load_model import (
    BIKE_MID7,
    RUN_MID7,
    TAU_ATL_DAYS,
    TAU_CTL_DAYS,
    ZONES7,
    banister_alpha,
)
from triplan.physiology.state import ALPHA_ACUTE, ALPHA_CHRONIC


def bootstrap_ci(
    values: Sequence[float],
    stat_fn=np.median,
    n_boot: int = 1000,
    seed: int = 42,
    alpha: float = 0.05,
) -> Tuple[float, float, float]:
    """Return (point_estimate, ci_low, ci_high) via deterministic percentile bootstrap."""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return float("nan"), float("nan"), float("nan")
    est = float(stat_fn(arr))
    if len(arr) == 1:
        return est, est, est
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(n_boot, len(arr)))
    boots = np.apply_along_axis(stat_fn, 1, arr[idx])
    lo = float(np.quantile(boots, alpha / 2.0))
    hi = float(np.quantile(boots, 1.0 - alpha / 2.0))
    return est, lo, hi


def _ewma_step_response(alpha: float, days: int, daily_load: float = 100.0) -> float:
    x = 0.0
    for _ in range(days):
        x = (1.0 - alpha) * x + alpha * daily_load
    return x


def analyze_banister_pmc(daily_tss_by_date: Dict[str, float]) -> Dict[str, Any]:
    """Compare exact ``1 - exp(-1/tau)`` vs legacy ``2/(N+1)`` analytically and on history."""
    exact_ctl_a = banister_alpha(TAU_CTL_DAYS)
    exact_atl_a = banister_alpha(TAU_ATL_DAYS)
    legacy_ctl_a = 2.0 / (TAU_CTL_DAYS + 1.0)
    legacy_atl_a = 2.0 / (TAU_ATL_DAYS + 1.0)

    ctl_exact_42 = _ewma_step_response(exact_ctl_a, 42, 100.0)
    ctl_legacy_42 = _ewma_step_response(legacy_ctl_a, 42, 100.0)
    ctl_closed_42 = 100.0 * (1.0 - math.exp(-1.0))

    atl_exact_7 = _ewma_step_response(exact_atl_a, 7, 100.0)
    atl_legacy_7 = _ewma_step_response(legacy_atl_a, 7, 100.0)
    atl_closed_7 = 100.0 * (1.0 - math.exp(-1.0))

    result: Dict[str, Any] = {
        "exact_alpha_ctl": exact_ctl_a,
        "legacy_alpha_ctl": legacy_ctl_a,
        "exact_alpha_atl": exact_atl_a,
        "legacy_alpha_atl": legacy_atl_a,
        "step_42d_exact_ctl": ctl_exact_42,
        "step_42d_closed_form": ctl_closed_42,
        "step_42d_exact_rel_err_pct": abs(ctl_exact_42 - ctl_closed_42) / ctl_closed_42 * 100.0,
        "step_42d_legacy_ctl": ctl_legacy_42,
        "step_42d_legacy_divergence_pct": (ctl_legacy_42 - ctl_exact_42) / ctl_exact_42 * 100.0,
        "step_7d_exact_atl": atl_exact_7,
        "step_7d_closed_form": atl_closed_7,
        "step_7d_exact_rel_err_pct": abs(atl_exact_7 - atl_closed_7) / atl_closed_7 * 100.0,
        "step_7d_legacy_atl": atl_legacy_7,
        "step_7d_legacy_divergence_pct": (atl_legacy_7 - atl_exact_7) / atl_exact_7 * 100.0,
    }

    if daily_tss_by_date:
        d_min = date.fromisoformat(min(daily_tss_by_date))
        d_max = date.fromisoformat(max(daily_tss_by_date))
        ctl_ex, ctl_lg = 0.0, 0.0
        atl_ex, atl_lg = 0.0, 0.0
        ctl_rel_diffs: List[float] = []
        atl_rel_diffs: List[float] = []
        n_days = (d_max - d_min).days + 1
        for k in range(n_days):
            d_str = (d_min + timedelta(days=k)).isoformat()
            load = float(daily_tss_by_date.get(d_str, 0.0))
            ctl_ex = (1.0 - exact_ctl_a) * ctl_ex + exact_ctl_a * load
            ctl_lg = (1.0 - legacy_ctl_a) * ctl_lg + legacy_ctl_a * load
            atl_ex = (1.0 - exact_atl_a) * atl_ex + exact_atl_a * load
            atl_lg = (1.0 - legacy_atl_a) * atl_lg + legacy_atl_a * load
            if k >= 42 and ctl_ex >= 5.0:
                ctl_rel_diffs.append((ctl_lg - ctl_ex) / ctl_ex * 100.0)
            if k >= 42 and atl_ex >= 5.0:
                atl_rel_diffs.append((atl_lg - atl_ex) / atl_ex * 100.0)
        result["historical_span_days"] = n_days
        result["historical_ctl_active_days"] = len(ctl_rel_diffs)
        if ctl_rel_diffs:
            result["historical_ctl_legacy_overstatement_pct"] = {
                "median": float(np.median(ctl_rel_diffs)),
                "p25": float(np.quantile(ctl_rel_diffs, 0.25)),
                "p75": float(np.quantile(ctl_rel_diffs, 0.75)),
                "p95": float(np.quantile(ctl_rel_diffs, 0.95)),
            }
        if atl_rel_diffs:
            result["historical_atl_legacy_overstatement_pct"] = {
                "median": float(np.median(atl_rel_diffs)),
                "p25": float(np.quantile(atl_rel_diffs, 0.25)),
                "p75": float(np.quantile(atl_rel_diffs, 0.75)),
                "p95": float(np.quantile(atl_rel_diffs, 0.95)),
            }

    return result


def analyze_zone_midpoints(
    activities: Sequence[ParsedActivity],
    run_cp: float,
    bike_cp: float,
) -> Dict[str, Any]:
    """Compare realized IF in easy/steady sessions against assumed ``RUN_MID7`` & ``BIKE_MID7``."""
    out: Dict[str, Any] = {}
    for sport, cp, assumed_mid7 in (
        ("Run", run_cp, RUN_MID7),
        ("Bike", bike_cp, BIKE_MID7),
    ):
        sport_acts = [
            a for a in activities
            if a.ok
            and a.sport == sport
            and a.intensity_factor is not None
            and a.intensity_factor > 0.0
            and sum(a.zones7_min.values()) >= 15.0
        ]
        # Easy sessions: >= 80% of power-tracked minutes in Z1 + Z2
        easy_ifs: List[float] = []
        z1_dom_ifs: List[float] = []
        z2_dom_ifs: List[float] = []
        recent_easy_ifs: List[float] = []
        zone_shares: Dict[str, float] = {z: 0.0 for z in ZONES7}

        for a in sport_acts:
            tot = sum(a.zones7_min.values())
            for z in ZONES7:
                zone_shares[z] += a.zones7_min[z]
            low_share = (a.zones7_min["Z1"] + a.zones7_min["Z2"]) / tot
            if low_share >= 0.80:
                easy_ifs.append(float(a.intensity_factor))
                if a.start_time and a.start_time.year >= 2025:
                    recent_easy_ifs.append(float(a.intensity_factor))
            if a.zones7_min["Z1"] / tot >= 0.70:
                z1_dom_ifs.append(float(a.intensity_factor))
            if a.zones7_min["Z2"] / tot >= 0.60:
                z2_dom_ifs.append(float(a.intensity_factor))

        tot_min = sum(zone_shares.values())
        med, ci_lo, ci_hi = bootstrap_ci(easy_ifs, stat_fn=np.median)
        z2_med, z2_lo, z2_hi = bootstrap_ci(z2_dom_ifs, stat_fn=np.median)
        z1_med, z1_lo, z1_hi = bootstrap_ci(z1_dom_ifs, stat_fn=np.median)
        rec_med, rec_lo, rec_hi = bootstrap_ci(recent_easy_ifs, stat_fn=np.median)

        assumed_z2 = assumed_mid7[1]
        out[sport] = {
            "cp_anchor_w": cp,
            "n_power_sessions_ge15m": len(sport_acts),
            "n_easy_sessions": len(easy_ifs),
            "n_z1_dominant_sessions": len(z1_dom_ifs),
            "n_z2_dominant_sessions": len(z2_dom_ifs),
            "n_recent_2025_2026_easy": len(recent_easy_ifs),
            "assumed_z1_midpoint": assumed_mid7[0],
            "assumed_z2_midpoint": assumed_z2,
            "easy_if_median": med,
            "easy_if_ci95": [ci_lo, ci_hi],
            "easy_if_iqr": (
                [float(np.quantile(easy_ifs, 0.25)), float(np.quantile(easy_ifs, 0.75))]
                if easy_ifs else [float("nan"), float("nan")]
            ),
            "z2_dom_if_median": z2_med,
            "z2_dom_if_ci95": [z2_lo, z2_hi],
            "z1_dom_if_median": z1_med,
            "z1_dom_if_ci95": [z1_lo, z1_hi],
            "recent_2025_2026_easy_if_median": rec_med,
            "recent_2025_2026_easy_if_ci95": [rec_lo, rec_hi],
            "assumed_z2_over_realized_easy_pct": (
                (assumed_z2 - med) / med * 100.0 if easy_ifs and med > 0 else float("nan")
            ),
            "corpus_zone_share_pct": (
                {z: round(zone_shares[z] / tot_min * 100.0, 2) for z in ZONES7}
                if tot_min > 0 else {}
            ),
        }
    return out


def analyze_run_strain_beta(
    activities: Sequence[ParsedActivity],
    betas: Sequence[float] = (1.0, 1.5, 2.0, 3.0),
) -> Dict[str, Any]:
    """Sensitivity of daily running strain EWMA ACWR across ``beta in {1, 1.5, 2, 3}``."""
    daily_z: Dict[str, np.ndarray] = defaultdict(lambda: np.zeros(len(ZONES7), dtype=float))
    n_runs = 0
    for a in activities:
        if not a.ok or a.sport != "Run" or a.start_time is None:
            continue
        z_vec = np.array([a.zones7_min[z] for z in ZONES7], dtype=float)
        if z_vec.sum() <= 0.0:
            # Fallback for non-power runs: treat duration as Z2 equivalent for sensitivity continuity
            if a.duration_sec > 0:
                z_vec[1] = a.duration_sec / 60.0
            else:
                continue
        daily_z[a.start_time.date().isoformat()] += z_vec
        n_runs += 1

    if not daily_z:
        return {"n_runs": 0, "betas": {}}

    d_min = date.fromisoformat(min(daily_z))
    d_max = date.fromisoformat(max(daily_z))
    n_days = (d_max - d_min).days + 1
    mid = np.asarray(RUN_MID7, dtype=float)

    by_beta: Dict[str, Any] = {}
    for beta in betas:
        weights = mid ** float(beta)
        acute = 0.0
        chronic = 0.0
        acwrs: List[float] = []
        for k in range(n_days):
            d_str = (d_min + timedelta(days=k)).isoformat()
            z_vec = daily_z.get(d_str)
            strain = float(np.dot(z_vec, weights)) if z_vec is not None else 0.0
            acute = ALPHA_ACUTE * strain + (1.0 - ALPHA_ACUTE) * acute
            chronic = ALPHA_CHRONIC * strain + (1.0 - ALPHA_CHRONIC) * chronic
            if z_vec is not None and k >= 28 and chronic >= 5.0:
                acwrs.append(acute / chronic)

        arr = np.asarray(acwrs, dtype=float)
        by_beta[f"{beta:.1f}"] = {
            "beta": float(beta),
            "z4_over_z2_weight_ratio": float((RUN_MID7[5] / RUN_MID7[1]) ** float(beta)),
            "n_active_run_days_post_warmup": int(len(arr)),
            "acwr_median": float(np.median(arr)) if len(arr) else float("nan"),
            "acwr_p90": float(np.quantile(arr, 0.90)) if len(arr) else float("nan"),
            "acwr_p95": float(np.quantile(arr, 0.95)) if len(arr) else float("nan"),
            "acwr_max": float(np.max(arr)) if len(arr) else float("nan"),
            "frac_gt_1_20": float((arr > 1.20).mean()) if len(arr) else float("nan"),
            "frac_gt_1_25": float((arr > 1.25).mean()) if len(arr) else float("nan"),
            "frac_gt_1_30": float((arr > 1.30).mean()) if len(arr) else float("nan"),
        }

    return {
        "n_runs": n_runs,
        "n_run_days": len(daily_z),
        "identifiability_note": (
            "No injury-onset timestamps exist in the historical telemetry archive; "
            "beta=3.0 is retained as a sensitivity-bounded Tier-0 prior (EXP-006 Go/No-Go #3)."
        ),
        "betas": by_beta,
    }


def analyze_readiness_calibration(
    wellness_dir: Path,
    window_days: int = 28,
    min_history: int = 7,
) -> Dict[str, Any]:
    """Compare legacy buggy RHR SD readiness vs 28-day rolling HRV/RHR readiness across 270 nights."""
    hrv_map = _load_hrv_map(wellness_dir)
    rhr_map = _load_rhr_map(wellness_dir)
    sleep_map = _load_sleep_map(wellness_dir)

    dates = sorted(hrv_map.keys())
    legacy_alphas: List[float] = []
    unified_alphas: List[float] = []
    rows: List[Tuple[float, float, float]] = []  # (z_hrv, z_rhr, sleep_term)

    for d_str in dates:
        end = date.fromisoformat(d_str)
        start = end - timedelta(days=window_days)
        prior_hrv = [v for k, v in hrv_map.items() if start.isoformat() <= k < d_str]
        if len(prior_hrv) < min_history:
            continue

        rmssd_today = hrv_map[d_str]
        rmssd_mean, rmssd_std = _trailing_stats(hrv_map, d_str, window_days)
        assert rmssd_mean is not None and rmssd_std is not None

        rhr_today = rhr_map.get(d_str)
        rhr_mean, rhr_std = _trailing_stats(rhr_map, d_str, window_days)
        if rhr_today is None or rhr_mean is None or rhr_std is None:
            rhr_today_eff = rhr_mean if rhr_mean is not None else 55.0
            rhr_mean_eff = rhr_mean if rhr_mean is not None else rhr_today_eff
            rhr_std_eff = 3.0
        else:
            rhr_today_eff, rhr_mean_eff, rhr_std_eff = rhr_today, rhr_mean, rhr_std

        sleep_score = sleep_map.get(d_str, 80.0)

        # Control: legacy bug (rhr_sd = rmssd_std * 0.5, range [0.70, 1.05])
        z_hrv = (rmssd_today - rmssd_mean) / max(rmssd_std, 1e-3)
        z_rhr_bug = (rhr_today_eff - rhr_mean_eff) / max(rmssd_std * 0.5, 1.0)
        sleep_term = (sleep_score - 80.0) / 20.0
        a_old = float(np.clip(1.00 + 0.08 * z_hrv - 0.05 * z_rhr_bug + 0.04 * sleep_term, 0.70, 1.05))
        legacy_alphas.append(a_old)

        # Treatment A: production TASK-025 formula
        a_new = ClosedLoopMPCController.calculate_garmin_morning_readiness(
            rmssd_today=rmssd_today,
            rmssd_baseline_mean=rmssd_mean,
            rmssd_baseline_std=rmssd_std,
            rhr_today=rhr_today_eff,
            rhr_baseline_mean=rhr_mean_eff,
            rhr_baseline_std=rhr_std_eff,
            sleep_score=sleep_score,
        )
        unified_alphas.append(a_new)

        z_rhr_true = (rhr_today_eff - rhr_mean_eff) / max(rhr_std_eff, 1.0)
        rows.append((z_hrv, z_rhr_true, sleep_term))

    def _summarize(arr_list: List[float]) -> Dict[str, Any]:
        arr = np.asarray(arr_list, dtype=float)
        if len(arr) == 0:
            return {"n": 0}
        return {
            "n": int(len(arr)),
            "p5": float(np.quantile(arr, 0.05)),
            "p10": float(np.quantile(arr, 0.10)),
            "p15": float(np.quantile(arr, 0.15)),
            "p50": float(np.quantile(arr, 0.50)),
            "p95": float(np.quantile(arr, 0.95)),
            "frac_lt_0_95_caution": float((arr < 0.95).mean()),
            "frac_lt_0_85_amber_red": float((arr < 0.85).mean()),
            "frac_lt_0_75_red": float((arr < 0.75).mean()),
        }

    # Sensitivity sweep over (a, b, c) targeting 10%-15% of nights < 0.85
    sweep: List[Dict[str, Any]] = []
    if rows:
        z_mat = np.asarray(rows, dtype=float)
        for a_w in (0.06, 0.08, 0.10, 0.12):
            for b_w in (0.04, 0.05, 0.06, 0.08):
                for c_w in (0.03, 0.04, 0.05):
                    vals = np.clip(1.00 + a_w * z_mat[:, 0] - b_w * z_mat[:, 1] + c_w * z_mat[:, 2], 0.60, 1.00)
                    frac_85 = float((vals < 0.85).mean())
                    if 0.10 <= frac_85 <= 0.15:
                        sweep.append({
                            "a_hrv": a_w,
                            "b_rhr": b_w,
                            "c_sleep": c_w,
                            "frac_lt_0_85": round(frac_85, 4),
                            "frac_lt_0_75": round(float((vals < 0.75).mean()), 4),
                            "p10": round(float(np.quantile(vals, 0.10)), 4),
                            "p50": round(float(np.quantile(vals, 0.50)), 4),
                        })

    return {
        "total_hrv_nights": len(hrv_map),
        "evaluated_nights_with_min_history": len(unified_alphas),
        "legacy_buggy": _summarize(legacy_alphas),
        "unified_task025_008_005_004": _summarize(unified_alphas),
        "calibrated_candidates_10_to_15pct_lt_085": sweep[:10],
    }


def analyze_lifting_overlap(
    activities: Sequence[ParsedActivity],
    lifting_path: Path,
) -> Dict[str, Any]:
    """Summarize historical lifting sessions and same/next-day run overlap (HEUR-004 check)."""
    if not lifting_path.exists():
        return {"present": False}
    sessions = json.loads(lifting_path.read_text(encoding="utf-8"))
    lift_dates = sorted({str(s["date"]) for s in sessions if s.get("date")})
    run_dates = {
        a.start_time.date().isoformat()
        for a in activities
        if a.ok and a.sport == "Run" and a.start_time is not None
    }
    same_day = 0
    next_day = 0
    for ld in lift_dates:
        d = date.fromisoformat(ld)
        if ld in run_dates:
            same_day += 1
        if (d + timedelta(days=1)).isoformat() in run_dates:
            next_day += 1
    axials = [float(s.get("estimated_axial_load", 0.0)) for s in sessions]
    return {
        "present": True,
        "total_lifting_sessions": len(sessions),
        "unique_lifting_days": len(lift_dates),
        "same_day_lift_and_run_count": same_day,
        "next_day_run_after_lift_count": next_day,
        "median_estimated_axial_load": float(np.median(axials)) if axials else 0.0,
    }


def run_exp006_calibration(
    fit_dir: Optional[Path] = None,
    wellness_dir: Optional[Path] = None,
    lifting_path: Optional[Path] = None,
    out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute full EXP-006 calibration pipeline and optionally persist JSON."""
    fdir = fit_dir or (DATA_DIR / "garmin" / "activities" / "fit_files")
    wdir = wellness_dir or (DATA_DIR / "garmin" / "wellness")
    lpath = lifting_path or (DATA_DIR / "lifting" / "parsed_lifting_history.json")

    report: CorpusAuditReport = audit_corpus(
        fdir,
        run_cp=CURRENT_ATHLETE.run_cp,
        bike_cp=CURRENT_ATHLETE.bike_cp,
        swim_css=CURRENT_ATHLETE.swim_css,
    )

    daily_tss: Dict[str, float] = defaultdict(float)
    for a in report.activities:
        if a.ok and a.start_time is not None and a.tss is not None:
            daily_tss[a.start_time.date().isoformat()] += float(a.tss)

    results: Dict[str, Any] = {
        "experiment": "EXP-006",
        "corpus": {
            "total_fit_files": report.total_files,
            "parsed_ok": report.parsed_ok,
            "parse_errors": report.parse_errors,
            "deduplicated_retained": len(report.activities),
            "dropped_duplicates": report.deduplicated_count,
            "by_sport": report.by_sport,
        },
        "banister_pmc": analyze_banister_pmc(daily_tss),
        "zone_midpoints": analyze_zone_midpoints(
            report.activities,
            run_cp=CURRENT_ATHLETE.run_cp,
            bike_cp=CURRENT_ATHLETE.bike_cp,
        ),
        "run_strain_beta": analyze_run_strain_beta(report.activities),
        "readiness_alpha_d": analyze_readiness_calibration(wdir),
        "lifting_overlap": analyze_lifting_overlap(report.activities, lpath),
    }

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Run EXP-006 load-model calibration.")
    ap.add_argument(
        "--out",
        type=Path,
        default=DATA_DIR / "experiments" / "EXP-006" / "fitted_parameters.json",
        help="Output JSON path for fitted parameters.",
    )
    args = ap.parse_args()
    res = run_exp006_calibration(out_path=args.out)
    print(json.dumps(res, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
