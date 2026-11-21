"""EXP-004 sweep harness: 81-point goal-weight sensitivity & session-structure constraints.

Runs 3^4 = 81 weight vectors x 3 TSS targets (300, 500, 700) x 2 structural variants
(Variant A: soft goals only; Variant B: ADR-020 session-structure + canonical 80/20)
for N = 486 deterministic MILP solves.

Outputs CSV & JSON summaries to data/experiments/EXP-004/ (gitignored).
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from triplan.library.candidates import build_default_candidate_library
from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.solver.engine import session_quality as sq
from triplan.solver.engine.config import EngineOptions, meso_options
from triplan.solver.engine.engine import UnifiedWeekEngine

OUT_DIR = Path("data/experiments/EXP-004")

MULTIPLIERS = (0.25, 1.0, 4.0)
TSS_GRID = (300.0, 500.0, 700.0)
BASE_WEIGHTS = {
    "w_tss": 100.0,
    "w_zone": 80.0,
    "w_sport_dur": 50.0,
    "w_sport_tss": 45.0,
}

# Worker-local cache so each process builds the library once.
_WORKER_LIB: List[CandidateWorkout] = []


def _init_worker() -> None:
    global _WORKER_LIB
    os.environ["TRI_HIGHS_THREADS"] = "1"
    _WORKER_LIB = build_default_candidate_library(include_8020=True)


def _make_options(variant: str) -> EngineOptions:
    opts = meso_options()
    if variant == "A":
        opts.prefilter_grey_zone = False
        opts.key_sessions = False
        opts.canonical_8020 = False
    elif variant != "B":
        raise ValueError(f"Unknown variant {variant!r}")
    return opts


def _compute_metrics(
    workouts: Sequence[CandidateWorkout],
    cfg: MesoCycleTargetsAndConstraints,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    p = sq.SessionQualityParams()
    selected = [w for d in range(7) for w in plan["schedule"][d]]

    # 7-zone endurance distribution (strength excluded; combo cardio included)
    z7_tot = np.zeros(7, dtype=np.float64)
    for w in selected:
        z7_tot += np.asarray(sq.endurance_zone7(w), dtype=np.float64)
    endur_min = float(z7_tot.sum())
    if endur_min > 0.0:
        z1_z2_pct = 100.0 * float(z7_tot[0] + z7_tot[1]) / endur_min
        zx_pct = 100.0 * float(z7_tot[2]) / endur_min
        hi_pct = 100.0 * float(z7_tot[3] + z7_tot[4] + z7_tot[5] + z7_tot[6]) / endur_min
    else:
        z1_z2_pct = zx_pct = hi_pct = 0.0

    key_cnt = sum(1 for w in selected if sq.is_key(w, p))
    gz_cnt = sum(1 for w in selected if sq.is_grey_zone(w, p))

    realized_tss = float(plan["total_tss"])
    realized_dur = float(plan["total_duration"])
    target_tss = float(cfg.target_weekly_tss)
    nominal_dur = float(cfg.nominal_weekly_duration)

    tss_dev_pct = 100.0 * abs(realized_tss - target_tss) / max(target_tss, 1.0)

    # 5-zone L1 deviation (matches builders/goals.py _meso_rows)
    z5_tot = np.zeros(5, dtype=np.float64)
    for w in selected:
        z5_tot += np.asarray(w.zone_durations, dtype=np.float64)
    zone_l1 = sum(
        abs(float(z5_tot[z - 1]) - cfg.target_zone_pcts.get(z, 0.0) * realized_dur)
        for z in range(1, 6)
    ) / max(nominal_dur, 1.0)

    # Sport duration L1 deviation (matches builders/goals.py _meso_rows)
    sport_dur = {s: 0.0 for s in ("Swim", "Bike", "Run", "Strength")}
    for w in selected:
        if w.sport == "Brick" and w.brick_run_minutes > 0:
            sport_dur["Run"] += float(w.brick_run_minutes)
            sport_dur["Bike"] += max(0.0, float(w.duration) - float(w.brick_run_minutes))
        elif w.sport in sport_dur:
            sport_dur[w.sport] += float(w.duration)
    sport_dur_l1 = sum(
        abs(sport_dur[s] - cfg.target_sport_duration_splits.get(s, 0.0) * realized_dur)
        for s in ("Swim", "Bike", "Run", "Strength")
    ) / max(nominal_dur, 1.0)

    # Bike/Run TSS split L1 deviation (matches builders/goals.py _meso_rows)
    br_tss = {"Bike": 0.0, "Run": 0.0}
    for w in selected:
        if w.sport == "Brick" and w.brick_run_minutes > 0:
            rf = float(w.brick_run_minutes) / max(float(w.duration), 1e-9)
            br_tss["Run"] += float(w.aerobic_tss) * rf
            br_tss["Bike"] += float(w.aerobic_tss) * (1.0 - rf)
        elif w.sport in br_tss:
            br_tss[w.sport] += float(w.aerobic_tss)
    br_tot = br_tss["Bike"] + br_tss["Run"]
    sport_tss_l1 = sum(
        abs(br_tss[s] - cfg.target_bike_run_tss_splits.get(s, 0.5) * br_tot)
        for s in ("Bike", "Run")
    ) / max(target_tss, 1.0)

    codes = sorted(w.code for w in selected)
    return {
        "key_sessions_count": key_cnt,
        "grey_zone_count": gz_cnt,
        "realized_tss": round(realized_tss, 3),
        "realized_duration": round(realized_dur, 3),
        "endurance_minutes": round(endur_min, 3),
        "z1_z2_pct": round(z1_z2_pct, 3),
        "z3_zy_z4_z5_pct": round(hi_pct, 3),
        "zx_pct": round(zx_pct, 3),
        "tss_dev_pct": round(tss_dev_pct, 4),
        "zone_l1_dev": round(zone_l1, 6),
        "sport_dur_l1_dev": round(sport_dur_l1, 6),
        "sport_tss_l1_dev": round(sport_tss_l1, 6),
        "selected_codes": ";".join(codes),
    }


def _solve_one(task: Tuple[str, float, float, float, float, float]) -> Dict[str, Any]:
    variant, target_tss, m_tss, m_zone, m_sdur, m_stss = task
    lib = _WORKER_LIB if _WORKER_LIB else build_default_candidate_library(include_8020=True)
    cfg = MesoCycleTargetsAndConstraints(
        target_weekly_tss=target_tss,
        w_tss=BASE_WEIGHTS["w_tss"] * m_tss,
        w_zone=BASE_WEIGHTS["w_zone"] * m_zone,
        w_sport_dur=BASE_WEIGHTS["w_sport_dur"] * m_sdur,
        w_sport_tss=BASE_WEIGHTS["w_sport_tss"] * m_stss,
    )
    opts = _make_options(variant)
    t0 = time.perf_counter()
    eng = UnifiedWeekEngine(lib, cfg, opts)
    plans = eng.solve(k=1, time_limit=15.0)
    dt = time.perf_counter() - t0

    row: Dict[str, Any] = {
        "variant": variant,
        "target_tss": int(target_tss),
        "m_tss": m_tss,
        "m_zone": m_zone,
        "m_sport_dur": m_sdur,
        "m_sport_tss": m_stss,
        "w_tss": cfg.w_tss,
        "w_zone": cfg.w_zone,
        "w_sport_dur": cfg.w_sport_dur,
        "w_sport_tss": cfg.w_sport_tss,
        "solve_time_s": round(dt, 4),
    }
    if not plans:
        row.update({
            "status": eng.last_status or "INFEASIBLE",
            "mip_gap": None,
            "objective": None,
            "relaxations_count": None,
            "key_sessions_count": None,
            "grey_zone_count": None,
            "realized_tss": None,
            "realized_duration": None,
            "endurance_minutes": None,
            "z1_z2_pct": None,
            "z3_zy_z4_z5_pct": None,
            "zx_pct": None,
            "tss_dev_pct": None,
            "zone_l1_dev": None,
            "sport_dur_l1_dev": None,
            "sport_tss_l1_dev": None,
            "selected_codes": "",
        })
        return row

    pl = plans[0]
    row.update({
        "status": pl["status"],
        "mip_gap": round(float(pl["mip_gap"]), 6),
        "objective": round(float(pl["objective"]), 6),
        "relaxations_count": len(pl["relaxations"]),
    })
    row.update(_compute_metrics(lib, cfg, pl))
    return row


def _jaccard(codes_a: str, codes_b: str) -> float:
    sa = set(codes_a.split(";")) if codes_a else set()
    sb = set(codes_b.split(";")) if codes_b else set()
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _pareto_mask(points: List[Tuple[float, float, float, float]]) -> List[bool]:
    """Return True for non-dominated points (minimization across all 4 objectives)."""
    n = len(points)
    is_pareto = [True] * n
    for i in range(n):
        pi = points[i]
        for j in range(n):
            if i == j:
                continue
            pj = points[j]
            if all(pj[k] <= pi[k] + 1e-12 for k in range(4)) and any(pj[k] < pi[k] - 1e-12 for k in range(4)):
                is_pareto[i] = False
                break
    return is_pareto


def run_sweep(workers: int = 8) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    weight_grid = list(itertools.product(MULTIPLIERS, MULTIPLIERS, MULTIPLIERS, MULTIPLIERS))
    tasks = [
        (variant, tss, m_tss, m_zone, m_sdur, m_stss)
        for variant in ("A", "B")
        for tss in TSS_GRID
        for (m_tss, m_zone, m_sdur, m_stss) in weight_grid
    ]
    print(f"Launching EXP-004 sweep: {len(tasks)} deterministic MILP solves across {workers} workers...")
    t_start = time.perf_counter()

    results: List[Dict[str, Any]] = []
    if workers <= 1:
        _init_worker()
        for idx, t in enumerate(tasks, 1):
            results.append(_solve_one(t))
            if idx % 50 == 0 or idx == len(tasks):
                print(f"  [{idx}/{len(tasks)}] elapsed={time.perf_counter() - t_start:.1f}s")
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
            futs = [ex.submit(_solve_one, t) for t in tasks]
            for idx, fut in enumerate(as_completed(futs), 1):
                results.append(fut.result())
                if idx % 50 == 0 or idx == len(tasks):
                    print(f"  [{idx}/{len(tasks)}] elapsed={time.perf_counter() - t_start:.1f}s")

    # Sort deterministically
    results.sort(key=lambda r: (r["variant"], r["target_tss"], r["m_tss"], r["m_zone"], r["m_sport_dur"], r["m_sport_tss"]))

    # Compute Jaccard similarity vs neutral (1.0, 1.0, 1.0, 1.0) per (variant, target_tss)
    neutral_codes: Dict[Tuple[str, int], str] = {}
    for r in results:
        if (r["m_tss"], r["m_zone"], r["m_sport_dur"], r["m_sport_tss"]) == (1.0, 1.0, 1.0, 1.0):
            neutral_codes[(r["variant"], r["target_tss"])] = r["selected_codes"]
    for r in results:
        ref = neutral_codes.get((r["variant"], r["target_tss"]), "")
        r["jaccard_vs_neutral"] = round(_jaccard(r["selected_codes"], ref), 4)

    # Write grid.csv
    grid_path = OUT_DIR / "grid.csv"
    fieldnames = list(results[0].keys())
    with grid_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)

    # Aggregate 81 weight vectors across TSS_GRID for Variant B Pareto frontier
    by_w_b: Dict[Tuple[float, float, float, float], List[Dict[str, Any]]] = {}
    for r in results:
        if r["variant"] == "B" and r["tss_dev_pct"] is not None:
            k = (r["m_tss"], r["m_zone"], r["m_sport_dur"], r["m_sport_tss"])
            by_w_b.setdefault(k, []).append(r)

    w_keys = sorted(by_w_b.keys())
    pareto_rows: List[Dict[str, Any]] = []
    pts: List[Tuple[float, float, float, float]] = []
    for k in w_keys:
        grp = by_w_b[k]
        mean_tss = statistics.mean(r["tss_dev_pct"] for r in grp)
        mean_zone = statistics.mean(r["zone_l1_dev"] for r in grp)
        mean_sdur = statistics.mean(r["sport_dur_l1_dev"] for r in grp)
        mean_stss = statistics.mean(r["sport_tss_l1_dev"] for r in grp)
        pts.append((mean_tss, mean_zone, mean_sdur, mean_stss))
        pareto_rows.append({
            "m_tss": k[0], "m_zone": k[1], "m_sport_dur": k[2], "m_sport_tss": k[3],
            "mean_tss_dev_pct": round(mean_tss, 4),
            "tss_dev_300_pct": round(next(r["tss_dev_pct"] for r in grp if r["target_tss"] == 300), 4),
            "tss_dev_500_pct": round(next(r["tss_dev_pct"] for r in grp if r["target_tss"] == 500), 4),
            "tss_dev_700_pct": round(next(r["tss_dev_pct"] for r in grp if r["target_tss"] == 700), 4),
            "mean_zone_l1_dev": round(mean_zone, 6),
            "mean_sport_dur_l1_dev": round(mean_sdur, 6),
            "mean_sport_tss_l1_dev": round(mean_stss, 6),
            "mean_jaccard": round(statistics.mean(r["jaccard_vs_neutral"] for r in grp), 4),
        })
    mask = _pareto_mask(pts)
    for row, flag in zip(pareto_rows, mask):
        row["is_pareto"] = flag

    pareto_path = OUT_DIR / "pareto.csv"
    with pareto_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(pareto_rows[0].keys()))
        w.writeheader()
        w.writerows(pareto_rows)

    # Compute summary JSON
    summary: Dict[str, Any] = {"total_wall_s": round(time.perf_counter() - t_start, 2), "variants": {}}
    for v in ("A", "B"):
        vr = [r for r in results if r["variant"] == v]
        feasible = [r for r in vr if r["realized_tss"] is not None]
        gz_total = sum(r["grey_zone_count"] for r in feasible)
        gz_solves = sum(1 for r in feasible if r["grey_zone_count"] > 0)
        low_vals = [r["z1_z2_pct"] for r in feasible]
        hi_vals = [r["z3_zy_z4_z5_pct"] for r in feasible]
        low_viol = sum(1 for x in low_vals if x < 78.0 - 1e-6)
        hi_viol = sum(1 for x in hi_vals if x > 20.0 + 1e-6)
        key_vals = [r["key_sessions_count"] for r in feasible]
        key_viol = sum(1 for x in key_vals if x < 2 or x > 3)
        per_tss = {}
        for tss in (300, 500, 700):
            tr = [r for r in feasible if r["target_tss"] == tss]
            per_tss[str(tss)] = {
                "n": len(tr),
                "median_tss_dev_pct": round(statistics.median(r["tss_dev_pct"] for r in tr), 2),
                "median_jaccard": round(statistics.median(r["jaccard_vs_neutral"] for r in tr), 4),
                "z1_z2_min_pct": round(min(r["z1_z2_pct"] for r in tr), 2),
                "z1_z2_max_pct": round(max(r["z1_z2_pct"] for r in tr), 2),
                "hi_max_pct": round(max(r["z3_zy_z4_z5_pct"] for r in tr), 2),
                "gz_solves": sum(1 for r in tr if r["grey_zone_count"] > 0),
                "key_min": min(r["key_sessions_count"] for r in tr),
                "key_max": max(r["key_sessions_count"] for r in tr),
            }
        summary["variants"][v] = {
            "n_total": len(vr),
            "n_feasible": len(feasible),
            "optimal_count": sum(1 for r in feasible if r["status"] == "OPTIMAL"),
            "grey_zone_total_picks": gz_total,
            "grey_zone_solves": gz_solves,
            "z1_z2_min_pct": round(min(low_vals), 2),
            "z1_z2_max_pct": round(max(low_vals), 2),
            "hi_max_pct": round(max(hi_vals), 2),
            "z1_z2_violations": low_viol,
            "hi_violations": hi_viol,
            "key_range": [min(key_vals), max(key_vals)],
            "key_violations": key_viol,
            "median_tss_dev_pct": round(statistics.median(r["tss_dev_pct"] for r in feasible), 2),
            "median_jaccard": round(statistics.median(r["jaccard_vs_neutral"] for r in feasible), 4),
            "median_solve_time_s": round(statistics.median(r["solve_time_s"] for r in feasible), 3),
            "per_tss": per_tss,
        }

    pareto_front = [r for r in pareto_rows if r["is_pareto"]]
    neutral_row = next(
        r for r in pareto_rows
        if (r["m_tss"], r["m_zone"], r["m_sport_dur"], r["m_sport_tss"]) == (1.0, 1.0, 1.0, 1.0)
    )
    summary["pareto_count"] = len(pareto_front)
    summary["neutral_is_pareto"] = neutral_row["is_pareto"]
    summary["neutral_row"] = neutral_row
    summary["pareto_frontier"] = pareto_front

    summary_path = OUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Completed {len(results)} solves in {summary['total_wall_s']}s. Wrote {grid_path}, {pareto_path}, {summary_path}.")
    print(json.dumps(summary["variants"], indent=2))
    print(f"Pareto frontier size: {len(pareto_front)}/81; neutral (1,1,1,1) is_pareto={neutral_row['is_pareto']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EXP-004 81-point goal-weight sensitivity sweep.")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4))
    args = parser.parse_args()
    run_sweep(workers=args.workers)


if __name__ == "__main__":
    main()
