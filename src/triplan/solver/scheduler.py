"""
Closed-Loop Triathlon Training Optimizer (Production Implementation)
Based on: 'Closed-Loop Triathlon Training Optimizer: Physiological & Mathematical System Specification'
Authors/Sources: Joe Friel, Jim Vance, Patrick Hagerman, Matt Fitzgerald, Ed Maunder, Stephen Seiler.

Key Architecture:
- Dual-load modeling: Aerobic TSS vs Mechanical Strain
- ANS Costing: SPEC.md 7-zone weights + long-duration surcharge (physiology.load_model, ADR-034)
- Asynchronous tissue remodeling tracking (Cardio tau=35d, Tendon/Bone tau=120d, ODI <= 1.35)
- EWMA Mechanical ACWR (Acute tau=7d, Chronic tau=28d)
- Exact algebraic linearizations (Single run <= 32% weekly run volume, TiZ ratios, Sport splits)
- Keep-Hard-Days-Hard same-day strength stacking (AM endurance + PM strength)
- Adjacency constraints: No consecutive hard days, no consecutive heavy runs (>=48h), no heavy run after strength
- 48h rolling glycogen/ANS/mechanical strain caps with robust infeasibility recovery
- Sequential Top-K distinct plan generation via Canonical No-Good Integer Cuts
- 3-Tier Closed-Loop Model Predictive Control (MPC):
    * Tier 1: Daily Morning Readiness Scalar (HRV RMSSD, sleep, soreness)
    * Tier 2: Weekly Receding-Horizon Update (Banister PMC, EWMA ACWR, ODI)
    * Tier 3: Mesocycle 3:1 Step-Cycles & Fast-Decay Taper
"""
from typing import Any, Dict, List, Optional

from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.solver.engine.config import (
    DAYS, SPORTS, WEEKDAYS, WEEKENDS, ZONES, meso_options, normalize_meso_config,
)
from triplan.solver.engine.engine import UnifiedWeekEngine


# =============================================================================
# 3. MILP OPTIMIZATION ENGINE (HiGHS) -- thin facade over the unified week engine (ADR-017)
# =============================================================================

class TriathlonMILPScheduler:
    """
    Production Mixed-Integer Linear Programming (MILP) 7-Day Triathlon Scheduler.

    Since TASK-015 this class is a facade: the model is built and solved by
    ``triplan.solver.engine.UnifiedWeekEngine`` with the ``meso_options()`` preset (partition + brick
    session model, scheduler goals, canonical no-good cuts over active x columns).

    Constraint hierarchy (ADR-013, TASK-016/017):
    - Tier 0 (hard, never relaxed): daily time caps, facility/modality gating, workout uniqueness,
      rolling 48h TSS/ANS/strain caps, axial/neuromuscular caps, single-run share cap (32% with
      >= 4 runs, explicit pigeonhole cap below), hard/easy adjacency, 48h heavy-run separation,
      heavy strength +-24h vs threshold/long runs, biomechanical antagonism matrix.
      Shared with HybridAthleteMILPOptimizer via ``safety_constraints``.
    - Tier 1 (named, reported slacks, lexicographic by relaxation order): anchors, min sport
      frequencies, strides, heavy-strength count (floor 1), rest days (floor 1).
    - Tier 2 (goal programming): TSS, zones, sport splits, bike/run TSS split, durability.
    Top-K distinct plans via canonical No-Good cuts. The caller's config is never mutated.
    """

    def __init__(
        self,
        workouts: List[CandidateWorkout],
        config: MesoCycleTargetsAndConstraints,
        carry_in: Optional[Any] = None,
    ) -> None:
        self.workouts = list(workouts)
        self.cfg, self.notes = normalize_meso_config(config)  # deep copy: never mutate the caller's (TASK-013)
        self.num_workouts = len(self.workouts)
        self.days = list(DAYS)
        self.weekdays = list(WEEKDAYS)
        self.weekends = list(WEEKENDS)
        self.sports = list(SPORTS)
        self.zones = list(ZONES)
        self.last_diagnostics: List[str] = []
        self.last_status: Optional[str] = None
        self.engine = UnifiedWeekEngine(self.workouts, self.cfg, meso_options(), notes=self.notes,
                                       carry_in=carry_in)
        self.limits = self.engine.limits
        self.sets = self.engine.sets

    def _allowed(self, i: int, d: int) -> bool:
        """Unary Tier-0 gating: time cap, facility access, outdoor-bike weekend rule."""
        return self.engine.allowed(i, d)

    def explain_feasibility(self) -> List[str]:
        return self.engine.explain_feasibility()

    def solve_top_k_plans(self, k_plans: int = 3, time_limit: float = 10.0) -> List[Dict[str, Any]]:
        """
        Builds the 3-tier MILP and solves it lexicographically (Tier-1 slack first, then Tier-2 goals),
        applying canonical No-Good Integer Cuts to extract the top K distinct weekly schedules.
        Returns [] when Tier 0 is infeasible; see ``self.last_diagnostics`` for the plain-English reason.
        """
        plans = self.engine.solve(k_plans, time_limit)
        self.last_status = self.engine.last_status
        self.last_diagnostics = self.engine.last_diagnostics
        return plans
