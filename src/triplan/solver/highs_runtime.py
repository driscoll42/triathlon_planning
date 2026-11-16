"""
Process-wide HiGHS runtime helpers (TASK-007).

1. Thread unification. HiGHS initializes one global task scheduler per process. If a later
   ``Highs`` instance requests a different ``threads`` value, ``run()`` returns ``kError`` with
   model status ``kNotset`` and an all-zero ``col_value``. Callers then read that as
   "no workouts selected". ``apply_threads`` locks a single per-process thread count: the
   first request wins, and the ``TRI_HIGHS_THREADS`` environment variable overrides it.
   Worker processes in the multi-seed race each get their own lock, so per-worker
   thread counts still work.

2. Status checks. ``run_and_check`` runs the model and exposes the solution only when HiGHS
   reports a feasible primal solution. It returns an explicit status string and the MIP gap,
   so a time-limited incumbent is never labeled ``OPTIMAL``.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from dataclasses import dataclass, field
from typing import List, Optional

import highspy

logger = logging.getLogger(__name__)

DEFAULT_THREADS = 2  # Matches ADR-006 per-worker sizing; HiGHS branch-and-bound is largely serial.

_lock = threading.Lock()
_process_threads: Optional[int] = None
_warned_requests: set = set()


def process_threads() -> Optional[int]:
    """The thread count locked for this process, or None if no solve has been configured yet."""
    return _process_threads


def apply_threads(h: "highspy.Highs", requested: Optional[int] = None) -> int:
    """Set ``threads`` on ``h`` to the single process-wide value and return it."""
    global _process_threads
    with _lock:
        if _process_threads is None:
            env = os.environ.get("TRI_HIGHS_THREADS", "").strip()
            if env:
                _process_threads = max(1, int(env))
            elif requested is not None and int(requested) > 0:
                _process_threads = int(requested)
            else:
                _process_threads = DEFAULT_THREADS
        locked = _process_threads
        if requested is not None and int(requested) > 0 and int(requested) != locked \
                and int(requested) not in _warned_requests:
            _warned_requests.add(int(requested))
            logger.warning(
                "HiGHS threads=%s requested but this process is locked to threads=%s "
                "(global scheduler can only be initialized once); using %s.",
                requested, locked, locked,
            )
    h.setOptionValue("threads", locked)
    if locked == 1:
        h.setOptionValue("parallel", "off")
    return locked


def _reset_for_tests() -> None:
    """Testing hook: clear the per-process lock and HiGHS's global scheduler."""
    global _process_threads
    with _lock:
        _process_threads = None
        _warned_requests.clear()
    reset = getattr(highspy.Highs, "resetGlobalScheduler", None)
    if reset is not None:
        reset(True)


_STATUS_NAMES = {
    highspy.HighsModelStatus.kOptimal: "OPTIMAL",
    highspy.HighsModelStatus.kInfeasible: "INFEASIBLE",
    highspy.HighsModelStatus.kUnbounded: "UNBOUNDED",
    highspy.HighsModelStatus.kUnboundedOrInfeasible: "UNBOUNDED_OR_INFEASIBLE",
    highspy.HighsModelStatus.kTimeLimit: "TIME_LIMIT",
    highspy.HighsModelStatus.kNotset: "ERROR",
}


@dataclass
class SolveResult:
    status: str                       # e.g. OPTIMAL, TIME_LIMIT_FEASIBLE, TIME_LIMIT_NO_SOLUTION, INFEASIBLE, ERROR
    model_status: "highspy.HighsModelStatus"
    has_solution: bool                # True only when HiGHS holds a feasible primal solution
    objective: Optional[float] = None
    mip_gap: Optional[float] = None   # Relative MIP gap; 0.0 at proven optimality, None if unavailable
    col_value: List[float] = field(default_factory=list)

    @property
    def is_optimal(self) -> bool:
        return self.status == "OPTIMAL"


def run_and_check(h: "highspy.Highs") -> SolveResult:
    """Run ``h`` and return a validated SolveResult (never trust ``col_value`` without this)."""
    run_status = h.run()
    ms = h.getModelStatus()
    info = h.getInfo()

    feasible = (
        run_status != highspy.HighsStatus.kError
        and info.primal_solution_status == highspy.kSolutionStatusFeasible
    )

    base = _STATUS_NAMES.get(ms)
    if base is None:
        base = h.modelStatusToString(ms).upper().replace(" ", "_").replace("-", "_")
    if run_status == highspy.HighsStatus.kError:
        status = "ERROR"
    elif base in ("OPTIMAL", "INFEASIBLE", "UNBOUNDED", "UNBOUNDED_OR_INFEASIBLE", "ERROR"):
        status = base
    else:
        # Limits / interrupts: distinguish "stopped with an incumbent" from "stopped empty-handed".
        status = f"{base}_FEASIBLE" if feasible else f"{base}_NO_SOLUTION"

    has_solution = feasible and status != "INFEASIBLE"
    gap = getattr(info, "mip_gap", None)
    if gap is not None and (not math.isfinite(gap) or gap < 0):
        gap = None
    if status == "OPTIMAL" and gap is None:
        gap = 0.0

    return SolveResult(
        status=status,
        model_status=ms,
        has_solution=has_solution,
        objective=float(info.objective_function_value) if has_solution else None,
        mip_gap=float(gap) if (gap is not None and has_solution) else None,
        col_value=list(h.getSolution().col_value) if has_solution else [],
    )
