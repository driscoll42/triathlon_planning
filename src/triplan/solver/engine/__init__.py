"""Unified week engine: ``WeekModel`` + modular constraint builders (TASK-015, ADR-017)."""

from triplan.solver.engine.config import (  # noqa: F401
    EngineOptions, LegacySpec, WeekConfig, WorkoutTable, hybrid_options, legacy_options, meso_options,
    normalize_meso_config,
)
from triplan.solver.engine.engine import UnifiedWeekEngine, solve_week  # noqa: F401
from triplan.solver.engine.model import SoftGoal, WeekModel  # noqa: F401
