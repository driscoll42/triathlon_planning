"""Shared build state passed to every constraint builder (TASK-015, ADR-017)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from triplan.solver.engine.config import (DAYS, EngineOptions, MUSCLES, SPORTS, WeekConfig, WorkoutTable)
from triplan.solver.engine.model import SoftGoal, WeekModel
from triplan.solver.safety_constraints import ModelHooks, SafetyLimits, Tier1Registry, WorkoutSets


@dataclass
class BuildContext:
    """Everything a builder needs: the model buffer, normalized inputs, and the variable registry.

    Builders read ``table`` / ``cfg`` / ``opt`` and write rows (and, for the calendar and goal
    declaration steps, columns) into ``model``. They never talk to HiGHS.
    """
    model: WeekModel
    table: WorkoutTable
    cfg: WeekConfig
    opt: EngineOptions
    limits: SafetyLimits
    sets: WorkoutSets
    allowed: Callable[[int, int], bool]

    sports: List[str] = field(default_factory=lambda: list(SPORTS))
    muscles: List[str] = field(default_factory=lambda: list(MUSCLES))
    days: List[int] = field(default_factory=lambda: list(DAYS))

    # variable registry (filled by calendar.add_variables / goals.declare_goals)
    X: Optional[np.ndarray] = None                       # (N, 7) column index of x[i, d]
    y: Dict[Tuple[str, int], int] = field(default_factory=dict)
    u: Dict[Tuple[str, int], int] = field(default_factory=dict)
    double: Dict[int, int] = field(default_factory=dict)
    rest: Dict[int, int] = field(default_factory=dict)
    brick: Dict[int, int] = field(default_factory=dict)
    z_split: Optional[int] = None
    goals: Dict[str, Any] = field(default_factory=dict)  # name -> SoftGoal | column index

    hooks: Optional[ModelHooks] = None
    reg: Optional[Tier1Registry] = None

    @property
    def n(self) -> int:
        return self.table.n

    def x(self, i: int, d: int) -> int:
        return int(self.X[i, d])

    def x_cols(self, ids) -> np.ndarray:
        """Columns of workouts ``ids`` over all days, workout-major (day inner)."""
        return self.X[np.asarray(list(ids), dtype=np.int64)].ravel()

    def day_cols(self, ids, d: int) -> np.ndarray:
        return self.X[np.asarray(list(ids), dtype=np.int64), d]

    def per_week(self, vals: np.ndarray, ids=None) -> Tuple[np.ndarray, np.ndarray]:
        """(cols, values) of a per-workout coefficient vector repeated over the 7 days."""
        ids = np.arange(self.n) if ids is None else np.asarray(list(ids), dtype=np.int64)
        return self.X[ids].ravel(), np.repeat(np.asarray(vals, dtype=np.float64)[ids], 7)
