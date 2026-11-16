---
id: HEUR-001
type: heuristic
domain: perf
title: "Guard Against Python Multiprocessing Worker Deadlocks on Windows in Parallel HiGHS Workers"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
one_liner: "Never pass unpicklable Highs objects to child workers; pass pure dicts/DataFrames and initialize Highs inside worker."
---

# `HEUR-001`: Guard Against Python Multiprocessing Worker Deadlocks on Windows in Parallel HiGHS Workers

- **Target Subsystem / Path:** `src/triplan/perf/stress.py:stress_test_suite()` (`ProcessPoolExecutor`, `TASK-032`) and any future process-parallel runner. The original target, `optimize_weekly_workouts_multiseed()`, was retired by `ADR-018`.
- **Symptom / Failure Mode:** Process hangs indefinitely on Windows when spawning multiprocessing workers, or throws `TypeError: cannot pickle 'highspy._core.Highs' object`.
- **Root Physics / Mechanism:** On Windows, Python uses `spawn` instead of `fork` to start worker processes. Any arguments passed across process boundaries must be serialized via `pickle`. C++ wrapped pointers inside `highspy.Highs` cannot be pickled across IPC channels.
- **Permanent Operational Rule:** Never pass instantiated `Highs` solver objects into `multiprocessing.Pool` or `Process`. Submit only top-level, picklable functions with sanitized `pandas.DataFrame` / dataclass / dict arguments, guard entry points with `if __name__ == "__main__"`, and instantiate the local `highspy.Highs()` solver instance entirely inside the target worker function.
- **Verification Check:** `TRIPLAN_STRESS_WORKERS=2 python -m triplan.perf.stress` completes without hanging (spawn start method).
