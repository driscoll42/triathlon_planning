"""EXP-003 benchmark harness: sparse build, s_i/Balas scaling, EXP-001 race retest, and Top-5 monotonicity.

Outputs CSV & JSON summaries to data/experiments/EXP-003/ (gitignored).
"""

from __future__ import annotations

import csv
import dataclasses
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from triplan.library.candidates import build_default_candidate_library
from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.perf.season_validation import reference_profile
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.solver.engine.config import EngineOptions, meso_options, normalize_meso_config
from triplan.solver.engine.engine import UnifiedWeekEngine

OUT_DIR = Path("data/experiments/EXP-003")


def _peak_rss_mb() -> float:
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        hproc = k32.GetCurrentProcess()
        if k32.K32GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
            return float(pmc.PeakWorkingSetSize) / (1024.0 * 1024.0)
    except Exception:
        pass
    try:
        import resource  # type: ignore

        return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0
    except Exception:
        return 0.0


def build_pool(base_lib: List[CandidateWorkout], target_n: int, seed: int) -> List[CandidateWorkout]:
    """Build a deterministic N-workout pool stratified across sports + anchors, expanding with jitter above len."""
    rng = np.random.default_rng(seed)
    anchors = [w for w in base_lib if getattr(w, "is_anchor", False) or (getattr(w, "flags", None) or {}).get("test", 0)]
    strength_core = [w for w in base_lib if w.sport == "Strength" and w not in anchors]
    endurance = [w for w in base_lib if w not in anchors and w not in strength_core]

    must_keep = list(anchors)
    # Keep balanced strength cover (HLM + UL) so split coherence and patterns stay feasible.
    if len(must_keep) + len(strength_core) <= max(16, target_n // 3):
        must_keep.extend(strength_core)
    else:
        idx_s = rng.choice(len(strength_core), size=min(len(strength_core), max(8, target_n // 5)), replace=False)
        must_keep.extend(strength_core[int(i)] for i in sorted(idx_s))

    need_end = max(0, target_n - len(must_keep))
    chosen: List[CandidateWorkout] = list(must_keep)
    if need_end <= len(endurance):
        # Stratified across Swim/Bike/Run/Brick.
        by_sp: Dict[str, List[CandidateWorkout]] = {}
        for w in endurance:
            by_sp.setdefault(w.sport, []).append(w)
        all_picks: List[CandidateWorkout] = []
        rem = need_end
        sports = sorted(by_sp)
        for s_idx, sp in enumerate(sports):
            pool_sp = by_sp[sp]
            share = rem // (len(sports) - s_idx)
            take = min(len(pool_sp), max(1, share)) if rem > 0 else 0
            if take > 0:
                idxs = rng.choice(len(pool_sp), size=take, replace=False)
                all_picks.extend(pool_sp[int(i)] for i in sorted(idxs))
                rem -= take
        if len(all_picks) < need_end:
            used_codes = {w.code for w in all_picks}
            leftover = [w for w in endurance if w.code not in used_codes]
            extra_idx = rng.choice(len(leftover), size=min(len(leftover), need_end - len(all_picks)), replace=False)
            all_picks.extend(leftover[int(i)] for i in sorted(extra_idx))
        chosen.extend(all_picks[:need_end])
    else:
        chosen.extend(endurance)

    out: List[CandidateWorkout] = []
    # Slight non-zero jitter on optional non-anchor endurance sessions across replicates while clamping N.
    for idx, w in enumerate(chosen[:target_n]):
        if idx < len(must_keep):
            out.append(dataclasses.replace(w, id=idx))
        else:
            jitter = float(rng.integers(-2, 3))
            scale = max(0.85, (float(w.duration) + jitter) / max(float(w.duration), 1.0))
            out.append(dataclasses.replace(
                w,
                id=idx,
                duration=round(float(w.duration) * scale, 2),
                aerobic_tss=round(float(w.aerobic_tss) * scale, 2),
                mechanical_strain=round(float(w.mechanical_strain) * scale, 2),
                ans_cost=round(float(w.ans_cost) * scale, 2),
            ))

    # Synthetic expansion above canonical size (e.g. N=500).
    k_syn = 0
    while len(out) < target_n:
        src = base_lib[int(rng.integers(0, len(base_lib)))]
        factor = float(rng.uniform(0.92, 1.08))
        nid = len(out)
        out.append(dataclasses.replace(
            src,
            id=nid,
            code=f"{src.code}_SYN{k_syn}",
            duration=round(max(15.0, float(src.duration) * factor), 2),
            aerobic_tss=round(max(5.0, float(src.aerobic_tss) * factor), 2),
            mechanical_strain=round(max(1.0, float(src.mechanical_strain) * factor), 2),
            ans_cost=round(max(1.0, float(src.ans_cost) * factor), 2),
            is_anchor=False,
        ))
        k_syn += 1
    return out


def _bench_cfg():
    return normalize_meso_config(MesoCycleTargetsAndConstraints(
        daily_time_caps=[60.0, 105.0, 60.0, 105.0, 60.0, 240.0, 240.0],
        weekday_single_workout_cap=60.0,
        facility_access={"Swim": [False] * 5 + [True] * 2, "Bike": [True] * 7,
                         "Run": [True] * 7, "Strength": [True] * 5 + [False] * 2},
        target_weekly_tss=500.0, nominal_weekly_duration=640.0, max_48h_ans_cost=120.0,
    ))


def _unpruned_options() -> EngineOptions:
    opt = meso_options()
    opt.prefilter_grey_zone = False
    opt.filter_feasible = False
    opt.prune_dominated = False
    opt.prune_8020 = False
    return opt


def _worker_solve(args: Tuple[int, int, float, int, Optional[int], bool]) -> Dict[str, Any]:
    """Isolated process worker (necessary because HiGHS threads option is process-global)."""
    target_n, rep_seed, time_limit, threads, random_seed, pruned = args
    base_lib = build_default_candidate_library()
    pool = build_pool(base_lib, target_n, rep_seed) if target_n != -1 else base_lib
    cfg, notes = _bench_cfg()
    opt = meso_options() if pruned else _unpruned_options()
    if not pruned:
        # Preserve exact unpruned size (still set threads/seed cleanly on solver).
        pass
    ti0 = time.perf_counter()
    eng = UnifiedWeekEngine(pool, cfg, opt, notes=notes)
    ti1 = time.perf_counter()
    ctx = eng.build_model()
    tb1 = time.perf_counter()
    starts, indices, _, _, _ = ctx.model.csr()
    nnz = int(len(indices))
    n_rows, n_cols = int(ctx.model.num_rows), int(ctx.model.num_cols)

    # Set threads/random_seed via monkeypatch on _new_solver cleanly for Condition 2.
    orig_new = eng._new_solver

    def _patched_new(c, tl):
        h = orig_new(c, tl)
        if threads is not None:
            h.setOptionValue("threads", int(threads))
        if random_seed is not None:
            h.setOptionValue("random_seed", int(random_seed))
        return h

    eng._new_solver = _patched_new  # type: ignore[method-assign]
    ts0 = time.perf_counter()
    plans = eng.solve(k=1, time_limit=time_limit)
    ts1 = time.perf_counter()
    p = plans[0] if plans else None
    return {
        "n_pool": len(pool),
        "n_model": len(eng.workouts),
        "rep_seed": rep_seed,
        "threads": threads,
        "random_seed": random_seed,
        "init_ms": (ti1 - ti0) * 1000.0,
        "build_ms": (tb1 - ti1) * 1000.0,
        "solve_s": (ts1 - ts0),
        "nnz": nnz,
        "rows": n_rows,
        "cols": n_cols,
        "status": p["status"] if p else "INFEASIBLE",
        "objective": float(p["objective"]) if p else float("inf"),
        "mip_gap": float(p["mip_gap"]) if (p and p.get("mip_gap") is not None) else 1.0,
        "rss_mb": _peak_rss_mb(),
    }


def _run_race_worker(target_n: int, rep_seed: int, n_seeds: int, threads_per_seed: int, time_limit: float) -> Dict[str, Any]:
    t0 = time.perf_counter()
    tasks = [(target_n, rep_seed, time_limit, threads_per_seed, 1000 + s, False) for s in range(n_seeds)]
    results = []
    with ProcessPoolExecutor(max_workers=min(n_seeds, max(1, (os.cpu_count() or 8) // threads_per_seed))) as ex:
        futs = [ex.submit(_worker_solve, a) for a in tasks]
        for f in as_completed(futs):
            results.append(f.result())
    wall_s = time.perf_counter() - t0
    best = min(results, key=lambda r: (r["status"] not in ("OPTIMAL", "TIME_LIMIT_FEASIBLE"), r["objective"], r["mip_gap"]))
    best["wall_s"] = wall_s
    best["cpu_thread_s"] = sum(r["solve_s"] * threads_per_seed for r in results)
    return best


def _pct(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = (len(s) - 1) * (p / 100.0)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


def _profile_topk_and_ablation() -> Dict[str, Any]:
    base_lib = build_default_candidate_library()
    sc_ref = reference_profile()
    profiles = [
        ("BENCH_500", *_bench_cfg()),
        ("CURRENT", *normalize_meso_config(CURRENT_ATHLETE.to_mesocycle_config("build", 220.0))),
        ("REFERENCE", *normalize_meso_config(sc_ref.to_mesocycle_config("build", 450.0))),
    ]
    out: Dict[str, Any] = {"profiles": [], "topk": []}
    for pname, cfg, notes in profiles:
        # n=10 jittered reps on pruned pool
        s_times, b_times, gaps, objs = [], [], [], []
        n_mod, n_rows, n_cols, nnz = 0, 0, 0, 0
        for rep in range(10):
            rng = np.random.default_rng(42 + rep)
            jlib = []
            for idx, w in enumerate(base_lib):
                if getattr(w, "is_anchor", False) or (getattr(w, "flags", None) or {}).get("test", 0):
                    jlib.append(w)
                else:
                    sc = 1.0 + float(rng.uniform(-0.02, 0.02))
                    jlib.append(dataclasses.replace(
                        w,
                        duration=round(float(w.duration) * sc, 2),
                        aerobic_tss=round(float(w.aerobic_tss) * sc, 2),
                    ))
            ti0 = time.perf_counter()
            eng = UnifiedWeekEngine(jlib, cfg, meso_options(), notes=notes)
            ctx = eng.build_model()
            b_times.append((time.perf_counter() - ti0) * 1000.0)
            _, idx_c, _, _, _ = ctx.model.csr()
            n_mod, n_rows, n_cols, nnz = len(eng.workouts), int(ctx.model.num_rows), int(ctx.model.num_cols), int(len(idx_c))
            ts0 = time.perf_counter()
            plans = eng.solve(k=1, time_limit=5.0)
            s_times.append(time.perf_counter() - ts0)
            p = plans[0]
            gaps.append(float(p["mip_gap"] or 0.0) * 100.0)
            objs.append(float(p["objective"]))
        out["profiles"].append({
            "profile": pname, "n_pruned": n_mod, "rows": n_rows, "cols": n_cols, "nnz": nnz,
            "init_build_med_ms": round(statistics.median(b_times), 1),
            "solve_med_s": round(statistics.median(s_times), 3),
            "solve_p95_s": round(_pct(s_times, 95), 3),
            "solve_max_s": round(max(s_times), 3),
            "gap_max_pct": round(max(gaps), 2),
            "obj_med": round(statistics.median(objs), 4),
        })

        # Top-5 monotonicity check (both default 1% gap and exact Tier-2 0% gap)
        for gap_val in (0.01, 0.0):
            opt = meso_options()
            opt.mip_rel_gap = gap_val
            eng = UnifiedWeekEngine(base_lib, cfg, opt, notes=notes)
            t0 = time.perf_counter()
            plans = eng.solve(k=5, time_limit=6.0)
            dt = time.perf_counter() - t0
            p_objs = [round(float(p["objective"]), 4) for p in plans]
            p_gaps = [round(float(p["mip_gap"] or 0.0) * 100.0, 2) for p in plans]
            mono = all(p_objs[i] <= p_objs[i + 1] + 1e-6 for i in range(len(p_objs) - 1))
            sets_codes = [frozenset(w.code for d in range(7) for w in p["schedule"][d]) for p in plans]
            distinct = len(set(sets_codes)) == len(plans)
            out["topk"].append({
                "profile": pname, "mip_rel_gap": gap_val, "k": len(plans),
                "total_s": round(dt, 3), "monotone": mono, "all_distinct_sets": distinct,
                "objs": p_objs, "gaps_pct": p_gaps,
            })
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
    }
    print("=== EXP-003 Condition 1: Unpruned Scaling N in {40, 80, 160, 324, 500} (n=10 reps, t=4) ===", flush=True)
    cond1_rows: List[Dict[str, Any]] = []
    cond1_summary: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=1) as ex:
        # Run sequential isolated workers so HiGHS threads=4 has zero contention!
        for n_target in (40, 80, 160, 324, 500):
            reps = []
            for rep in range(10):
                r = ex.submit(_worker_solve, (n_target, 42 + rep, 10.0, 4, 42, False)).result()
                reps.append(r)
                cond1_rows.append({"condition": "cond1_unpruned", "target_n": n_target, **r})
            solves = [r["solve_s"] for r in reps]
            builds = [r["init_ms"] + r["build_ms"] for r in reps]
            gaps = [r["mip_gap"] * 100.0 for r in reps]
            n_opt = sum(1 for r in reps if r["status"] == "OPTIMAL")
            summ = {
                "target_n": n_target, "rows": reps[0]["rows"], "cols": reps[0]["cols"], "nnz": reps[0]["nnz"],
                "init_build_med_ms": round(statistics.median(builds), 1),
                "solve_med_s": round(statistics.median(solves), 3),
                "solve_p95_s": round(_pct(solves, 95), 3),
                "solve_max_s": round(max(solves), 3),
                "gap_med_pct": round(statistics.median(gaps), 2),
                "gap_p95_pct": round(_pct(gaps, 95), 2),
                "optimal_pct": int(round(100 * n_opt / len(reps))),
                "rss_max_mb": round(max(r["rss_mb"] for r in reps), 1),
            }
            cond1_summary.append(summ)
            print(f"  N={n_target:3d}: build={summ['init_build_med_ms']:5.1f}ms nnz={summ['nnz']:5d} | "
                  f"med={summ['solve_med_s']:5.2f}s p95={summ['solve_p95_s']:5.2f}s "
                  f"gap_med={summ['gap_med_pct']:5.2f}% gap_p95={summ['gap_p95_pct']:5.2f}% opt={summ['optimal_pct']:3d}% "
                  f"rss={summ['rss_max_mb']:.1f}MB", flush=True)

    print("\n=== EXP-003 Condition 2 (EXP-001 Retest): 15-seed x 2-thread Race vs Single Solve (N=324, 500) ===", flush=True)
    cond2_summary: List[Dict[str, Any]] = []
    for n_target in (324, 500):
        # Match wall-clock cap=5.0s for single (threads=4 -> 20 CPU thread-s max) vs 15x2 race (threads=2 -> up to 150 CPU thread-s).
        race_objs, race_walls, race_cpus, race_gaps = [], [], [], []
        sing_objs, sing_walls, sing_cpus, sing_gaps = [], [], [], []
        with ProcessPoolExecutor(max_workers=1) as ex_s:
            for rep in range(5):
                rs = 100 + rep
                r_single = ex_s.submit(_worker_solve, (n_target, rs, 5.0, 4, 42, False)).result()
                r_race = _run_race_worker(n_target, rs, 15, 2, 5.0)
                sing_objs.append(r_single["objective"])
                sing_walls.append(r_single["solve_s"])
                sing_cpus.append(r_single["solve_s"] * 4)
                sing_gaps.append(r_single["mip_gap"] * 100.0)
                race_objs.append(r_race["objective"])
                race_walls.append(r_race["wall_s"])
                race_cpus.append(r_race["cpu_thread_s"])
                race_gaps.append(r_race["mip_gap"] * 100.0)
                cond1_rows.append({"condition": "cond2_single_t4", "target_n": n_target, **r_single})
                cond1_rows.append({"condition": "cond2_race_15x2", "target_n": n_target, **r_race})
        for mode, objs, walls, cpus, gaps in [
            ("single_t4", sing_objs, sing_walls, sing_cpus, sing_gaps),
            ("race_15x2", race_objs, race_walls, race_cpus, race_gaps),
        ]:
            c2 = {
                "target_n": n_target, "mode": mode,
                "wall_med_s": round(statistics.median(walls), 3),
                "cpu_thread_s_med": round(statistics.median(cpus), 2),
                "obj_med": round(statistics.median(objs), 4),
                "gap_med_pct": round(statistics.median(gaps), 2),
                "gap_max_pct": round(max(gaps), 2),
            }
            cond2_summary.append(c2)
            print(f"  N={n_target:3d} {mode:10s}: wall={c2['wall_med_s']:5.2f}s cpu_s={c2['cpu_thread_s_med']:6.1f}s "
                  f"obj_med={c2['obj_med']:8.4f} gap_med={c2['gap_med_pct']:5.2f}% gap_max={c2['gap_max_pct']:5.2f}%", flush=True)

    print("\n=== EXP-003 Condition 3: Production Pruned Pipeline + Top-5 Monotonicity ===", flush=True)
    cond3 = _profile_topk_and_ablation()
    for pr in cond3["profiles"]:
        print(f"  {pr['profile']:10s} (N={pr['n_pruned']:3d} nnz={pr['nnz']:5d}): "
              f"init+build={pr['init_build_med_ms']:5.1f}ms | med={pr['solve_med_s']:5.2f}s "
              f"p95={pr['solve_p95_s']:5.2f}s max_gap={pr['gap_max_pct']:5.2f}%", flush=True)
    for tk in cond3["topk"]:
        print(f"  Top-5 {tk['profile']:10s} gap={tk['mip_rel_gap']:.2f}: total={tk['total_s']:5.2f}s "
              f"mono={tk['monotone']} distinct={tk['all_distinct_sets']} objs={tk['objs']}", flush=True)

    with (OUT_DIR / "exp003_raw.csv").open("w", newline="", encoding="utf-8") as f:
        keys = sorted({k for row in cond1_rows for k in row.keys()})
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(cond1_rows)

    summary = {"meta": meta, "cond1": cond1_summary, "cond2": cond2_summary, "cond3": cond3}
    (OUT_DIR / "exp003_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSaved EXP-003 results to {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
