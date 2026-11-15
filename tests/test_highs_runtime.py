"""Tests for src/highs_runtime.py (TASK-007).

The HiGHS global scheduler is per-process, so the mixed-thread regression runs in a fresh
subprocess to avoid depending on (or polluting) the pytest process's thread lock.
"""
import os
import subprocess
import sys
import textwrap

import highspy
import numpy as np

from highs_runtime import run_and_check, apply_threads

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _tiny_mip(infeasible=False):
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    apply_threads(h)
    inf = highspy.kHighsInf
    idx = np.array([0, 1], dtype=np.int32)
    h.addVars(2, np.array([0.0, 0.0]), np.array([1.0, 1.0]))
    h.changeColsIntegrality(2, idx, np.array([highspy.HighsVarType.kInteger] * 2))
    h.changeColsCost(2, idx, np.array([-1.0, -2.0]))
    h.addRow(-inf, 1.0, 2, idx, np.array([1.0, 1.0]))
    if infeasible:
        h.addRow(3.0, inf, 2, idx, np.array([1.0, 1.0]))
    return h


def test_optimal_result_has_solution_and_zero_gap():
    r = run_and_check(_tiny_mip())
    assert r.status == "OPTIMAL" and r.has_solution and r.is_optimal
    assert r.mip_gap == 0.0
    assert r.objective == -2.0
    assert r.col_value == [0.0, 1.0]


def test_infeasible_result_exposes_no_solution():
    r = run_and_check(_tiny_mip(infeasible=True))
    assert r.status == "INFEASIBLE"
    assert not r.has_solution
    assert r.col_value == [] and r.objective is None and r.mip_gap is None


def test_mixed_thread_requests_in_one_process_no_longer_fail():
    script = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {SRC!r})
        import highspy, numpy as np
        from highs_runtime import apply_threads, run_and_check
        def solve(t):
            h = highspy.Highs(); h.setOptionValue("output_flag", False); apply_threads(h, t)
            idx = np.array([0], dtype=np.int32)
            h.addVars(1, np.array([0.0]), np.array([1.0]))
            h.changeColsIntegrality(1, idx, np.array([highspy.HighsVarType.kInteger]))
            h.changeColsCost(1, idx, np.array([-1.0]))
            return run_and_check(h).status
        print(solve(4), solve(2), solve(None))
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "TRI_HIGHS_THREADS": ""})
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["OPTIMAL", "OPTIMAL", "OPTIMAL"]


def test_raw_highs_mixed_threads_reproduces_knotset():
    """Documents the underlying HiGHS behavior the helper guards against."""
    script = textwrap.dedent("""
        import highspy, numpy as np
        def solve(t):
            h = highspy.Highs(); h.setOptionValue("output_flag", False); h.setOptionValue("threads", t)
            idx = np.array([0], dtype=np.int32)
            h.addVars(1, np.array([0.0]), np.array([1.0]))
            h.changeColsIntegrality(1, idx, np.array([highspy.HighsVarType.kInteger]))
            h.run(); return h.modelStatusToString(h.getModelStatus())
        print(solve(4) + "|" + solve(2))
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "Optimal|Not Set"
