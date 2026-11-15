"""
Triathlon Planning & Closed-Loop Season Optimizer.
Combines 80/20 endurance principles with HiGHS MILP and 3-Tier Closed-Loop MPC.
"""

import os
import sys

# Ensure src directory is on sys.path for direct module imports
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from athlete_profile import CURRENT_ATHLETE, AthleteProfile
from classes import triVariables, workout
from closed_loop_optimizer import (
    CandidateWorkout,
    MesoCycleTargetsAndConstraints,
    TriathlonMILPScheduler,
    ClosedLoopMPCController,
    AthletePhysiologicalState,
    generate_closed_loop_season_schedule,
)
from highs_optimization import (
    optimize_weekly_workouts,
    optimize_weekly_workouts_multiseed,
    optimize_weekly_schedule,
    optimize_multi_week_step_cycle,
)

__version__ = "0.2.0"
__all__ = [
    "AthleteProfile",
    "CURRENT_ATHLETE",
    "triVariables",
    "workout",
    "CandidateWorkout",
    "MesoCycleTargetsAndConstraints",
    "TriathlonMILPScheduler",
    "ClosedLoopMPCController",
    "AthletePhysiologicalState",
    "generate_closed_loop_season_schedule",
    "optimize_weekly_workouts",
    "optimize_weekly_workouts_multiseed",
    "optimize_weekly_schedule",
    "optimize_multi_week_step_cycle",
]
