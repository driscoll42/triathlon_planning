---
id: HEUR-001
type: heuristic
domain: perf
title: "Guard Against Python Multiprocessing Worker Deadlocks on Windows in HiGHS Seed Race"
status: ACTIVE
created_at: 2026-11-08
supersedes: []
validated_by: []
one_liner: "Never pass unpicklable Highs objects to child workers; pass pure dicts/DataFrames and initialize Highs inside worker."
---

# `HEUR-001`: Guard Against Python Multiprocessing Worker Deadlocks on Windows in HiGHS Seed Race

- **Target Subsystem / Path:** `highs_optimization.py:optimize_weekly_workouts_multiseed()`.
- **Symptom / Failure Mode:** Process hangs indefinitely on Windows when spawning multiprocessing workers, or throws `TypeError: cannot pickle 'highspy._core.Highs' object`.
- **Root Physics / Mechanism:** On Windows, Python uses `spawn` instead of `fork` to start worker processes. Any arguments passed across process boundaries must be serialized via `pickle`. C++ wrapped pointers inside `highspy.Highs` cannot be pickled across IPC channels.
- **Permanent Operational Rule:** Never pass instantiated `Highs` solver objects into `multiprocessing.Pool` or `Process`. Pass sanitized `pandas.DataFrame` or parameter dicts, and instantiate the local `highspy.Highs()` solver instance entirely inside the target worker function.
- **Verification Check:** `python -c "import highs_optimization, pandas as pd; highs_optimization.optimize_weekly_workouts_multiseed(pd.DataFrame(), time_limit_per_worker=0.1, num_workers=2)"`.
