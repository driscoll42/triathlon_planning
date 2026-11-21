"""EXP-002: Closed-Loop 16-Week Season Simulation Under Real Profile and Perturbations.

Evaluates CURRENT_ATHLETE (config/athlete_profile.yaml) + canonical 80/20 library via
generate_closed_loop_season_schedule and independent Tier-0 verification (check_season) across:
  - Cond 1a: Nominal 16-week Olympic block (reentry_weeks=0)
  - Cond 1b: Nominal 40-week season (24-week re-entry + 16-week Olympic, reentry_weeks=24)
  - Cond 2:  Week-5 Acute Illness (perturbations={5: {"type": "sickness"}})
  - Cond 3:  Week-8 Pool Closure (perturbations={8: {"type": "pool_closure"}})
  - Cond 3b: Week-8 Pool Closure on pool-enabled CURRENT_ATHLETE variant (tests Bike/Run reallocation)
  - Cond 4:  Week-11 Travel Compression (perturbations={11: {"type": "travel"}})
  - Cond 5:  Stochastic Execution Noise (n=20 seeds, p=0.90 completion, duration ~ U(0.80, 1.10))

Artifacts are written to data/experiments/EXP-002/ (gitignored).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from triplan.control.season import WeekRecord, generate_closed_loop_season_schedule
from triplan.library.candidates import build_default_candidate_library
from triplan.paths import DATA_DIR
from triplan.perf.season_validation import OK_STATUSES, check_season, season_metrics
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.solver.safety_constraints import run_minutes

OUT_DIR = DATA_DIR / "experiments" / "EXP-002"


def _scale_workout(w: Any, scale: float) -> Any:
    """Scale a CandidateWorkout's duration and load attributes linearly by `scale`."""
    return replace(
        w,
        duration=float(w.duration) * scale,
        aerobic_tss=float(w.aerobic_tss) * scale,
        mechanical_strain=float(w.mechanical_strain) * scale,
        ans_cost=float(w.ans_cost) * scale,
        axial_load=float(w.axial_load) * scale,
        neuromuscular_load=float(w.neuromuscular_load) * scale,
        cardio_minutes=float(w.cardio_minutes) * scale,
        cardio_mech_strain=float(w.cardio_mech_strain) * scale,
        brick_run_minutes=float(w.brick_run_minutes) * scale,
        brick_run_strain=float(w.brick_run_strain) * scale,
        t_thr_min=float(w.t_thr_min) * scale,
        t_cont_min=float(w.t_cont_min) * scale,
        late_min=float(w.late_min) * scale,
        zone_durations=tuple(float(z) * scale for z in w.zone_durations),
        zone7=tuple(float(z) * scale for z in w.zone7),
        cardio_zone_durations=(float(w.cardio_zone_durations[0]) * scale,
                               float(w.cardio_zone_durations[1]) * scale),
    )


class StochasticRealizer:
    """Realizes a planned weekly schedule with session completion p and duration scale U(low, high)."""

    def __init__(self, seed: int, p_complete: float = 0.90, scale_low: float = 0.80, scale_high: float = 1.10):
        self.rng = np.random.default_rng(seed)
        self.p_complete = p_complete
        self.scale_low = scale_low
        self.scale_high = scale_high

    def __call__(self, schedule: Dict[int, List[Any]], week_num: int) -> Dict[int, List[Any]]:
        realized: Dict[int, List[Any]] = {}
        for d in range(7):
            day_list: List[Any] = []
            for w in schedule.get(d, []):
                if float(self.rng.random()) <= self.p_complete:
                    s = float(self.rng.uniform(self.scale_low, self.scale_high))
                    day_list.append(_scale_workout(w, s))
            realized[d] = day_list
        return realized


def _extract_metrics(
    label: str,
    records: List[WeekRecord],
    summary: pd.DataFrame,
    profile: Any,
    wall_s: float,
) -> Dict[str, Any]:
    viol = check_season(records, profile)
    m = season_metrics(records)

    # Distinct workouts selected across the season
    distinct_codes = {
        w.code
        for r in records if r.solved
        for d in range(7)
        for w in r.schedule.get(d, [])
    }

    # Single-run share strictly on weeks with >= 4 runs
    ge4_shares: List[float] = []
    for r in records:
        if not (r.solved and r.status in OK_STATUSES):
            continue
        runs = [run_minutes(w) for d in range(7) for w in r.schedule.get(d, []) if run_minutes(w) > 0]
        if len(runs) >= 4 and sum(runs) > 0:
            ge4_shares.append(max(runs) / sum(runs))

    # TSS within +-10% of effective target
    if len(summary):
        rel_diff = (summary["Solved_TSS"] - summary["Target_TSS"]).abs() / summary["Target_TSS"].clip(lower=1.0)
        within_10 = int((rel_diff <= 0.10 + 1e-9).sum())
        worst_gap_pct = float(rel_diff.max() * 100.0)
        race_sub = summary[summary["Block"] == "race"] if "Block" in summary.columns else summary
        race_rel = (race_sub["Solved_TSS"] - race_sub["Target_TSS"]).abs() / race_sub["Target_TSS"].clip(lower=1.0)
        race_within_10 = int((race_rel <= 0.10 + 1e-9).sum())
    else:
        within_10, race_within_10, worst_gap_pct = 0, 0, 0.0

    # Post-reentry vs reentry ACWR
    post_acwr = 0.0
    reentry_acwr = 0.0
    max_acwr_excess_over_eps = -1e9
    if len(summary):
        for idx, row in summary.iterrows():
            rec = records[idx] if idx < len(records) else None
            in_reentry = bool(rec.carry.load.reentry) if (rec and rec.carry and rec.carry.load) else False
            val = float(row["Max_Run_ACWR"])
            if in_reentry:
                reentry_acwr = max(reentry_acwr, val)
            else:
                post_acwr = max(post_acwr, val)
            max_acwr_excess_over_eps = max(max_acwr_excess_over_eps, float(row["Run_ACWR_Excess"]) - float(row["Run_ACWR_Eps"]))

    return {
        "label": label,
        "weeks": m["weeks"],
        "solved": m["solved"],
        "fallback": m["fallback"],
        "tier0_violations": len(viol),
        "violation_msgs": [str(x) for x in viol],
        "relaxations": m["relaxations"],
        "weeks_within_10pct": within_10,
        "race_weeks_within_10pct": race_within_10,
        "worst_tss_gap_pct": round(worst_gap_pct, 2),
        "peak_raw_acwr_reentry": round(reentry_acwr, 3),
        "peak_raw_acwr_post_reentry": round(post_acwr, 3),
        "peak_raw_acwr_overall": round(float(summary["Max_Run_ACWR"].max()) if len(summary) else 0.0, 3),
        "max_acwr_excess_over_eps": round(max_acwr_excess_over_eps, 4),
        "max_odi": round(float(summary["Ending_ODI"].max()) if len(summary) else 0.0, 3),
        "ending_odi": round(float(summary["Ending_ODI"].iloc[-1]) if len(summary) else 0.0, 3),
        "ending_ctl": round(float(summary["Ending_CTL"].iloc[-1]) if len(summary) else 0.0, 2),
        "peak_run_share_ge4": round(max(ge4_shares), 4) if ge4_shares else None,
        "ge4_run_weeks": len(ge4_shares),
        "peak_run_share_all": round(m["max_run_share"], 4),
        "distinct_workouts": len(distinct_codes),
        "max48_tss": round(m["max48_tss"], 1),
        "max48_ans": round(m["max48_ans"], 1),
        "max48_strain": round(m["max48_strain"], 1),
        "wall_s": round(wall_s, 2),
    }


def _run_job(job: Dict[str, Any]) -> Tuple[Dict[str, Any], pd.DataFrame]:
    t0 = time.time()
    lib = build_default_candidate_library(include_8020=True)
    profile = CURRENT_ATHLETE
    if job.get("pool_enabled"):
        fac = dict(profile.facility_access)
        fac["Swim"] = [False, False, False, False, False, True, True]
        freq = dict(profile.sport_freq_bounds)
        freq["Swim"] = (1, 2)
        profile = replace(profile, facility_access=fac, sport_freq_bounds=freq)

    realizer = None
    if job.get("seed") is not None:
        realizer = StochasticRealizer(seed=int(job["seed"]))

    records: List[WeekRecord] = []
    with contextlib.redirect_stdout(io.StringIO()):
        _daily, summary = generate_closed_loop_season_schedule(
            profile=profile,
            distance="olympic",
            library=lib,
            time_limit_per_week=float(job.get("time_limit", 1.5)),
            perturbations=job.get("perturbations"),
            weeks_to_race=16,
            reentry_weeks=int(job.get("reentry_weeks", 0)),
            collect=records,
            realize_schedule=realizer,
        )
    metrics = _extract_metrics(job["label"], records, summary, profile, time.time() - t0)
    return metrics, summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run EXP-002 closed-loop season perturbation benchmark.")
    ap.add_argument("--time-limit", type=float, default=1.5, help="Per-week HiGHS time limit (s)")
    ap.add_argument("--mc-seeds", type=int, default=20, help="Number of Monte Carlo seeds for Cond 5")
    ap.add_argument("--workers", type=int, default=8, help="Parallel worker processes")
    args = ap.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    jobs: List[Dict[str, Any]] = [
        {"label": "Cond1a_Nominal_16w", "reentry_weeks": 0, "perturbations": None, "time_limit": args.time_limit},
        {"label": "Cond1b_Nominal_40w", "reentry_weeks": 24, "perturbations": None, "time_limit": args.time_limit},
        {"label": "Cond2_Wk5_Sickness", "reentry_weeks": 0, "perturbations": {5: {"type": "sickness"}}, "time_limit": args.time_limit},
        {"label": "Cond3_Wk8_PoolClosure", "reentry_weeks": 0, "perturbations": {8: {"type": "pool_closure"}}, "time_limit": args.time_limit},
        {"label": "Cond3b_Wk8_PoolClosure_PoolOpen", "reentry_weeks": 0, "perturbations": {8: {"type": "pool_closure"}}, "pool_enabled": True, "time_limit": args.time_limit},
        {"label": "Cond4_Wk11_Travel", "reentry_weeks": 0, "perturbations": {11: {"type": "travel"}}, "time_limit": args.time_limit},
    ]
    for i in range(args.mc_seeds):
        jobs.append({
            "label": f"Cond5_MC_seed{42 + i}",
            "reentry_weeks": 0,
            "perturbations": None,
            "seed": 42 + i,
            "time_limit": args.time_limit,
        })

    t_start = time.time()
    print(f"Launching EXP-002 ({len(jobs)} season runs, workers={args.workers}, time_limit={args.time_limit}s)...")
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for metrics, summary in ex.map(_run_job, jobs):
            results.append(metrics)
            if not metrics["label"].startswith("Cond5_MC_"):
                summary.to_csv(OUT_DIR / f"{metrics['label']}_summary.csv", index=False)
                print(
                    f"  {metrics['label']:<34} | weeks={metrics['weeks']:>2} | fb={metrics['fallback']} | "
                    f"viol={metrics['tier0_violations']} | <=10%TSS={metrics['weeks_within_10pct']:>2}/{metrics['weeks']:<2} | "
                    f"postACWR={metrics['peak_raw_acwr_post_reentry']:.3f} | "
                    f"distinct={metrics['distinct_workouts']:>2} | {metrics['wall_s']:.1f}s"
                )

    mc = [r for r in results if r["label"].startswith("Cond5_MC_")]
    mc_summary: Dict[str, Any] = {}
    if mc:
        def _stats(vals: List[float]) -> Dict[str, float]:
            arr = np.asarray(vals, dtype=float)
            return {
                "median": round(float(np.median(arr)), 3),
                "q25": round(float(np.percentile(arr, 25)), 3),
                "q75": round(float(np.percentile(arr, 75)), 3),
                "p95": round(float(np.percentile(arr, 95)), 3),
                "min": round(float(np.min(arr)), 3),
                "max": round(float(np.max(arr)), 3),
            }

        ge4_vals = [r["peak_run_share_ge4"] for r in mc if r["peak_run_share_ge4"] is not None]
        mc_summary = {
            "n_seeds": len(mc),
            "total_fallbacks": int(sum(r["fallback"] for r in mc)),
            "total_tier0_violations": int(sum(r["tier0_violations"] for r in mc)),
            "weeks_within_10pct": _stats([r["weeks_within_10pct"] for r in mc]),
            "worst_tss_gap_pct": _stats([r["worst_tss_gap_pct"] for r in mc]),
            "peak_raw_acwr_post_reentry": _stats([r["peak_raw_acwr_post_reentry"] for r in mc]),
            "peak_raw_acwr_overall": _stats([r["peak_raw_acwr_overall"] for r in mc]),
            "max_odi": _stats([r["max_odi"] for r in mc]),
            "distinct_workouts": _stats([r["distinct_workouts"] for r in mc]),
            "peak_run_share_ge4_max": round(max(ge4_vals), 4) if ge4_vals else None,
        }
        pd.DataFrame(mc).to_csv(OUT_DIR / "Cond5_MC_runs.csv", index=False)

    payload = {
        "total_wall_s": round(time.time() - t_start, 2),
        "deterministic": [r for r in results if not r["label"].startswith("Cond5_MC_")],
        "monte_carlo_summary": mc_summary,
        "monte_carlo_runs": mc,
    }
    (OUT_DIR / "exp002_results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n--- Cond 5 (Monte Carlo, n=%d) ---" % len(mc))
    print(json.dumps(mc_summary, indent=2))
    print(f"\nSaved EXP-002 artifacts to {OUT_DIR} in {payload['total_wall_s']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
