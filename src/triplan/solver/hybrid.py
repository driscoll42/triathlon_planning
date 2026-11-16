"""Dual-objective hybrid athlete MILP optimizer (HAOS), a thin facade over the unified engine (TASK-015)."""

from typing import Dict, List, Optional, Any

from triplan.solver.engine.config import MUSCLES, SPORTS, WorkoutTable, ZONES, hybrid_options
from triplan.solver.engine.engine import UnifiedWeekEngine


class HybridAthleteMILPOptimizer:
    """
    Dual-Objective Hybrid Athlete Optimizer (HAOS) - Production MILP Scheduler.
    Solver Engine: HiGHS (highspy), via ``triplan.solver.engine.UnifiedWeekEngine`` with the
    ``hybrid_options()`` preset (<=2 workouts/day, <=1 per sport/day, rest window 1..2 days).

    Solves the rolling 7-day multi-sport triathlon (Swim, Bike, Run) and maximal
    barbell/dumbbell strength scheduling problem with exact ratio linearizations,
    biomechanical/axial conflict exclusion rules, and Canonical No-Good Integer Cuts.
    Supports both Upper/Lower and Full-Body Texas/HLM strength split architectures.
    """

    def __init__(
        self,
        library: List[Any],
        config: Any,
        carry_in: Optional[Any] = None,
    ):
        self.library = library
        self.N = len(library)
        self.config = config
        self.days = list(range(7))
        self.day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.sports = list(SPORTS)
        self.muscles = list(MUSCLES)
        self.zones = list(ZONES)
        self.last_diagnostics: List[str] = []
        self.last_status: Optional[str] = None
        self.engine = UnifiedWeekEngine(library, config, hybrid_options(), carry_in=carry_in)
        self.limits = self.engine.limits
        self.sets = self.engine.sets

    def _get_cfg_val(self, key: str, default: Any) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _allowed(self, i: int, d: int) -> bool:
        """Unary Tier-0 gating: daily time cap, pool days, outdoor-ride days, optional facility access."""
        return self.engine.allowed(i, d)

    def explain_feasibility(self) -> List[str]:
        return self.engine.explain_feasibility()

    def _get_zone_minutes(self, w: Any) -> Dict[int, float]:
        zm = WorkoutTable._zone_minutes(w)
        return {z: zm[z - 1] for z in ZONES}

    def solve_top_k_plans(self, k_solutions: int = 2, time_limit: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Builds the MILP in HiGHS and iteratively applies Canonical No-Good
        Integer Cuts and structural split cuts to extract the Top-K optimal plans.
        """
        plans = self.engine.solve(k_solutions, time_limit)
        self.last_status = self.engine.last_status
        self.last_diagnostics = self.engine.last_diagnostics
        return plans
