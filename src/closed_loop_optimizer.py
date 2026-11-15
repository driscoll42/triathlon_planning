"""
Closed-Loop Triathlon Training Optimizer (Production Implementation)
Based on: 'Closed-Loop Triathlon Training Optimizer: Physiological & Mathematical System Specification'
Authors/Sources: Joe Friel, Jim Vance, Patrick Hagerman, Matt Fitzgerald, Ed Maunder, Stephen Seiler.

Key Architecture:
- Dual-load modeling: Aerobic TSS vs Mechanical Strain
- Exponential ANS Costing: e^(0.65 * (z - 1))
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

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple, Any
import math
import numpy as np
import pandas as pd
import copy
import logging

import highspy

from highs_runtime import apply_threads, run_and_check
from safety_constraints import (
    DEFAULT_RELAXATION_ORDER,
    LexState,
    ModelHooks,
    SafetyLimits,
    Tier1Registry,
    add_tier0_safety,
    add_tier1_targets,
    classify_workouts,
    explain_feasibility,
    lexicographic_solve,
    run_share_report,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 1. DATA MODELS & WORKOUT SPECIFICATION
# =============================================================================

@dataclass(frozen=True)
class CandidateWorkout:
    """Represents a discrete candidate workout in the training library."""
    id: int
    code: str
    name: str
    sport: str                         # 'Swim', 'Bike', 'Run', 'Strength', 'Brick'
    duration: float                    # Total duration in minutes
    aerobic_tss: float                 # Metabolic / cardiovascular TSS
    mechanical_strain: float           # Peripheral orthopedic / eccentric strain
    ans_cost: float                    # Exponentially weighted ANS recovery cost
    durability_score: float            # Fatigue-resistance stimulus [0.0 .. 10.0]
    zone_durations: Tuple[float, float, float, float, float]  # Minutes in Z1..Z5
    is_hard_endurance: bool = False    # True for Z3/Z4/Z5 breakthrough sessions
    is_heavy_run: bool = False         # True for tempo/threshold/interval/long runs (>=70m)
    is_heavy_strength: bool = False    # True for MS/SM heavy lower-body resistance
    is_anchor: bool = False            # True for mandatory keystone sessions
    has_strides: bool = False          # True for alactic neuromuscular micro-dosing
    is_indoor_bike: bool = False       # True if executable on weekday smart trainer
    preference_penalty: float = 0.1    # Soft schedule friction cost [0.0 .. 1.0]

    # Hybrid Athlete Extensions (Barbell, Capped DBs, Spinal Axial Load, Split Groups)
    split_group: str = "ANY"           # 'UL' (Upper/Lower), 'HLM' (Full-Body Texas/HLM), or 'ANY'
    bb_sets: int = 0                   # Effective sets on primary Olympic Barbell
    db_sets: int = 0                   # Effective sets on <= 75 lb Dumbbells / Bodyweight
    neuromuscular_load: float = 0.0    # Central & peripheral neuromuscular load (N_i)
    axial_load: float = 0.0            # Lumbar L1-S1 compressive/shear load (X_i)
    sets_by_muscle: Dict[str, int] = field(default_factory=dict) # Anterior_Chain, Posterior_Chain, Push, Pull
    flags: Dict[str, int] = field(default_factory=dict)          # Specific biomechanical flags


@dataclass
class MesoCycleTargetsAndConstraints:
    """Encapsulates calendar boundaries, facility access, physiological targets, and weights."""
    # Calendar & Facility Constraints (Index 0 = Mon .. 6 = Sun)
    daily_time_caps: List[float] = field(default_factory=lambda: [60.0, 105.0, 60.0, 105.0, 60.0, 240.0, 240.0])
    weekday_single_workout_cap: float = 60.0
    facility_access: Dict[str, List[bool]] = field(default_factory=lambda: {
        "Swim":     [False, False, False, False, False, True, True],  # Weekend pool access default
        "Bike":     [True,  True,  True,  True,  True,  True, True],
        "Run":      [True,  True,  True,  True,  True,  True, True],
        "Strength": [True,  True,  True,  True,  True,  False, False],
    })

    # Weekly Physiological & Periodization Targets
    target_weekly_tss: float = 540.0
    nominal_weekly_duration: float = 640.0
    target_zone_pcts: Dict[int, float] = field(default_factory=lambda: {
        1: 0.25, 2: 0.55, 3: 0.14, 4: 0.05, 5: 0.01
    })
    target_sport_duration_splits: Dict[str, float] = field(default_factory=lambda: {
        "Swim": 0.15, "Bike": 0.45, "Run": 0.33, "Strength": 0.07
    })
    target_bike_run_tss_splits: Dict[str, float] = field(default_factory=lambda: {
        "Bike": 0.60, "Run": 0.40
    })

    # Hybrid 4-Sport Targets (Swim, Bike, Run, Strength) & Movement Pattern Sets
    target_sport_tss: Dict[str, float] = field(default_factory=lambda: {
        "Swim": 100.0, "Bike": 260.0, "Run": 198.0, "Strength": 52.0
    })
    target_movement_sets: Dict[str, float] = field(default_factory=lambda: {
        "Anterior_Chain": 10.0, "Posterior_Chain": 10.0, "Push": 10.0, "Pull": 10.0
    })
    min_barbell_set_share: float = 0.60          # >= 60% of strength sets via Olympic Barbell
    max_daily_axial_load: float = 120.0          # Daily lumbar L1-S1 spinal load ceiling
    max_weekly_axial_load: float = 320.0         # Weekly lumbar L1-S1 spinal load ceiling
    max_daily_neuro_load: float = 130.0          # Daily central neuromuscular load ceiling (ADR-008)
    max_weekly_neuro_load: float = 360.0         # Weekly neuromuscular load ceiling (ADR-008)
    readiness_alpha: List[float] = field(default_factory=lambda: [1.0] * 7)
    pool_open_days: List[int] = field(default_factory=lambda: [0, 2, 4])
    outdoor_bike_days: List[int] = field(default_factory=lambda: [5, 6])

    # Safety & Orthopedic Guardrails
    max_single_run_vol_ratio: float = 0.32       # Single run <= 32% of weekly run duration
    max_48h_aerobic_tss: float = 295.0           # Rolling 48h glycogen/immune cap
    max_48h_ans_cost: float = 700.0              # Rolling 48h autonomic strain cap (calibrated safe)
    max_48h_mech_strain: float = 260.0           # Rolling 48h mechanical strain cap
    max_weekly_run_mech_strain: float = 350.0    # EWMA CMS bound (ACWR <= 1.30)
    # Pigeonhole single-run caps for weeks with < 4 runs (32% unattainable); explicit & reported (ADR-013)
    pigeonhole_run_share: Dict[int, float] = field(default_factory=lambda: {3: 0.40, 2: 0.60})
    # Tier-1 relaxation order, cheapest first (DECISION-006); see safety_constraints.Tier1Registry
    tier1_relaxation_order: List[str] = field(default_factory=lambda: list(DEFAULT_RELAXATION_ORDER))

    # Weekly Frequency Bounds
    sport_freq_bounds: Dict[str, Tuple[int, int]] = field(default_factory=lambda: {
        "Swim": (2, 2),
        "Bike": (3, 4),
        "Run": (4, 5),
        "Strength": (1, 2),
    })
    required_heavy_strength_sessions: int = 1    # 2 in Base, 1 in Build, 0 in Taper
    min_stride_sessions: int = 1
    max_stride_sessions: int = 2
    min_rest_days: int = 1
    max_rest_days: int = 1

    # Multi-Objective Goal Programming Weights
    w_tss: float = 100.0
    w_zone: float = 80.0
    w_sport_dur: float = 50.0
    w_sport_tss: float = 45.0
    w_durability: float = 25.0
    w_friction: float = 5.0
    w_double_session: float = 0.5
    w_ans: float = 0.20
    w_rest: float = 18.0
    w_brick: float = 4.0
    w_sets_neg: float = 150.0
    w_sets_pos: float = 100.0
    w_tiz: float = 1.0

    # DEPRECATED no-op (TASK-016): rolling 48h caps and the single-run cap are Tier-0 hard constraints
    # and are never softened. Unmeetable *targets* surface as named Tier-1 relaxations instead.
    enable_soft_48h_slacks: bool = True


# =============================================================================
# 2. DEFAULT RESEARCH CANDIDATE LIBRARY & ENRICHMENT ENGINE
# =============================================================================

def build_default_candidate_library() -> List[CandidateWorkout]:
    """
    Constructs the candidate workout library integrating Friel, Vance, and Hagerman protocols.
    Includes standard endurance workouts and baseline strength sessions.
    """
    return [
        # SWIM (Pool gated to Saturday & Sunday by default)
        CandidateWorkout(0, "SWIM_AE1_PDLC", "Swim AE1/SS1 PDLC Technique & Drills", "Swim", 45, 34.0, 2.2, 58.0, 2.0, (20, 25, 0, 0, 0), False, False, False, False, False, False, 0.1, "ANY", 0, 0, 10.0, 0.0, {}, {"pool": 1}),
        CandidateWorkout(1, "SWIM_ME1_CSS", "Swim ME1 CSS Threshold Cruise 5x400m", "Swim", 55, 56.0, 4.1, 185.0, 6.5, (15, 15, 5, 20, 0), True, False, False, False, False, False, 0.2, "ANY", 0, 0, 20.0, 0.0, {}, {"swim_th": 1, "pool": 1}),
        CandidateWorkout(2, "SWIM_AE2_END", "Swim AE2 Aerobic Endurance 2500m", "Swim", 50, 42.0, 3.0, 82.0, 4.5, (10, 35, 5, 0, 0), False, False, False, False, False, False, 0.1, "ANY", 0, 0, 12.0, 0.0, {}, {"pool": 1}),
        CandidateWorkout(3, "SWIM_AE1_30", "Swim AE1 Short Technique & Recovery", "Swim", 30, 22.0, 1.5, 38.0, 1.5, (15, 15, 0, 0, 0), False, False, False, False, False, False, 0.1, "ANY", 0, 0, 8.0, 0.0, {}, {"pool": 1}),

        # BIKE (Weekdays: Indoor Smart-Trainer <= 60m; Weekends: Outdoor up to 180m)
        CandidateWorkout(4, "BIKE_AE1_30", "Bike AE1 Active Recovery Easy Spin", "Bike", 30, 20.0, 2.0, 38.0, 1.5, (15, 15, 0, 0, 0), False, False, False, False, False, True, 0.1, "ANY", 0, 0, 8.0, 3.0, {}, {}),
        CandidateWorkout(5, "BIKE_AE2_45", "Bike AE2 Short Smart-Trainer Spin", "Bike", 45, 33.0, 3.3, 65.0, 2.5, (10, 35, 0, 0, 0), False, False, False, False, False, True, 0.1, "ANY", 0, 0, 10.0, 4.0, {}, {}),
        CandidateWorkout(6, "BIKE_AE2_Z2", "Bike AE2 Smart-Trainer Aerobic Spin", "Bike", 60, 44.0, 4.4, 92.0, 3.0, (15, 45, 0, 0, 0), False, False, False, False, False, True, 0.1, "ANY", 0, 0, 12.0, 5.0, {}, {}),
        CandidateWorkout(7, "BIKE_SS1_CAD", "Bike AE2 + 6x15s Alactic Cadence Bursts", "Bike", 60, 46.0, 4.6, 95.0, 3.5, (15, 43, 0, 0, 2), False, False, False, False, True, True, 0.1, "ANY", 0, 0, 15.0, 5.0, {}, {}),
        CandidateWorkout(8, "BIKE_TE1_PYR", "Bike TE1 Pyramidal Tempo 2x20m Z3", "Bike", 60, 58.0, 6.5, 175.0, 6.8, (10, 15, 35, 0, 0), True, False, False, False, False, True, 0.2, "ANY", 0, 0, 35.0, 8.0, {}, {}),
        CandidateWorkout(9, "BIKE_ME3_OU", "Bike ME3 Crisscross Over-Under Z3/Z4", "Bike", 60, 68.0, 8.2, 245.0, 7.8, (10, 15, 15, 20, 0), True, False, False, False, False, True, 0.2, "ANY", 0, 0, 44.0, 10.0, {}, {"hi_torque_bike": 1}),
        CandidateWorkout(10, "BIKE_DUR_OUT", "Bike Outdoor Durability Ride >1800kJ + Late Z3/Z4", "Bike", 180, 145.0, 18.0, 380.0, 9.8, (30, 110, 25, 15, 0), True, False, False, True, False, False, 0.0, "ANY", 0, 0, 30.0, 18.0, {}, {"outdoor_bike": 1}),
        CandidateWorkout(11, "BIKE_AE2_OUT", "Bike Outdoor Long Aerobic Base Z2", "Bike", 165, 115.0, 13.5, 260.0, 7.5, (35, 120, 10, 0, 0), False, False, False, False, False, False, 0.1, "ANY", 0, 0, 25.0, 15.0, {}, {"outdoor_bike": 1}),

        # RUN
        CandidateWorkout(12, "RUN_AE1_REC30", "Run AE1 30m Recovery Jog", "Run", 30, 22.0, 20.0, 38.0, 1.5, (15, 15, 0, 0, 0), False, False, False, False, False, False, 0.1, "ANY", 0, 0, 10.0, 8.0, {}, {}),
        CandidateWorkout(13, "RUN_AE1_REC", "Run AE1 Easy Recovery Jog", "Run", 45, 34.0, 32.0, 62.0, 2.0, (25, 20, 0, 0, 0), False, False, False, False, False, False, 0.1, "ANY", 0, 0, 14.0, 12.0, {}, {}),
        CandidateWorkout(14, "RUN_SS1_STR", "Run AE2 + 6x15s Alactic Hill Strides", "Run", 55, 46.0, 45.0, 88.0, 4.0, (15, 38, 0, 0, 2), False, False, False, False, True, False, 0.1, "ANY", 0, 0, 18.0, 14.0, {}, {}),
        CandidateWorkout(15, "RUN_AE2_BASE", "Run AE2 Steady Aerobic Base Run", "Run", 60, 52.0, 52.0, 98.0, 4.5, (15, 45, 0, 0, 0), False, False, False, False, False, False, 0.1, "ANY", 0, 0, 20.0, 18.0, {}, {}),
        CandidateWorkout(16, "RUN_TE1_PYR", "Run TE1 Pyramidal Tempo 25m LT1-LT2", "Run", 60, 64.0, 72.0, 168.0, 7.2, (15, 20, 25, 0, 0), True, True, False, False, False, False, 0.2, "ANY", 0, 0, 48.0, 28.0, {}, {"heavy_run": 1}),
        CandidateWorkout(17, "RUN_TB1_BRICK", "Run TB1 T2 Durability Brick Run off Bike", "Run", 30, 34.0, 40.0, 95.0, 8.8, (5, 10, 15, 0, 0), False, False, False, False, False, False, 0.1, "ANY", 0, 0, 12.0, 10.0, {}, {}),
        CandidateWorkout(18, "RUN_AE2_LONG", "Run AE2 Weekend Long Run (Anchor)", "Run", 75, 70.0, 82.0, 135.0, 8.5, (20, 50, 5, 0, 0), False, True, False, True, False, False, 0.0, "ANY", 0, 0, 28.0, 26.0, {}, {"long_run": 1}),

        # STRENGTH
        CandidateWorkout(19, "STR_MS1_HEAVY", "Strength MS Heavy Squat/RDL/Step-Up 4x4 @85% 1RM + Plyo", "Strength", 45, 15.0, 65.0, 90.0, 5.5, (30, 15, 0, 0, 0), False, False, True, False, False, False, 0.15, "ANY", 8, 4, 85.0, 85.0, {"Anterior_Chain": 6, "Posterior_Chain": 6}, {"heavy_sq": 1, "heavy_dl": 1}),
        CandidateWorkout(20, "STR_SM1_CORE", "Strength SM Maintenance & Core Stabilization", "Strength", 40, 12.0, 35.0, 50.0, 3.5, (30, 10, 0, 0, 0), False, False, False, False, False, False, 0.15, "ANY", 4, 6, 45.0, 25.0, {"Push": 4, "Pull": 4}, {}),

        # CRITICAL POWER / CSS TESTING BENCHMARKS (ADR-009 / ADR-011)
        CandidateWorkout(21, "BIKE_CP_TEST", "Bike Two-Point CP Test (P3 180s + 30m Recov + P12 720s)", "Bike", 75, 78.0, 14.0, 85.0, 20.0, (25, 20, 0, 15, 15), True, False, False, False, False, True, 0.2, "ANY", 0, 0, 48.0, 12.0, {}, {"bike_test": 1, "test": 1}),
        CandidateWorkout(22, "RUN_CP_TEST", "Run Two-Point CP Test (P3 180s + 30m Recov + P12 720s)", "Run", 70, 74.0, 88.0, 92.0, 20.0, (20, 25, 0, 15, 10), True, True, False, False, False, False, 0.2, "ANY", 0, 0, 52.0, 28.0, {}, {"run_test": 1, "heavy_run": 1, "test": 1}),
        CandidateWorkout(23, "SWIM_CSS_TEST", "Swim CSS Benchmark Test (400m TT + 10m Easy + 200m TT)", "Swim", 50, 52.0, 4.0, 50.0, 18.0, (15, 15, 0, 20, 0), True, False, False, False, False, False, 0.2, "ANY", 0, 0, 22.0, 0.0, {}, {"swim_th": 1, "pool": 1, "test": 1}),
    ]


def build_hybrid_candidate_library() -> List[CandidateWorkout]:
    """
    Constructs the complete 15-workout Dual-Objective Hybrid Athlete Library
    incorporating the mathematical and physiological blueprint:
    - Upper/Lower Barbell + Dumbbell Split ('UL')
    - Full-Body Heavy-Light-Medium / Texas Method Split ('HLM')
    - Gated Swimming (Mon/Wed/Fri CSS threshold and Z2 recovery)
    - Indoor and Weekend Outdoor Cycling
    - Polarized Running with alactic uphill strides, T2 transition bricks, and long durability runs.
    """
    return [
        # --- ARCHITECTURE 1: UPPER / LOWER BARBELL + CAPPED DB SPLIT ---
        CandidateWorkout(
            id=0, code="UL_BB_LOWER_SQ",
            name="Upper/Lower: Barbell Low-Bar Squat (4x5 @ 83%) + DB Split Squat (3x10/leg @ 65lb)",
            sport="Strength", duration=60.0, aerobic_tss=18.0, mechanical_strain=28.0,
            ans_cost=66.0, durability_score=20.0, zone_durations=(45.0, 10.0, 5.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=True, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="UL", bb_sets=5, db_sets=4, neuromuscular_load=84.0, axial_load=85.0,
            sets_by_muscle={"Anterior_Chain": 7, "Posterior_Chain": 2, "Push": 0, "Pull": 0},
            flags={"heavy_sq": 1, "heavy_dl": 0, "heavy_press": 0}
        ),
        CandidateWorkout(
            id=1, code="UL_BB_UPPER_ME",
            name="Upper/Lower: Barbell Bench (4x5 @ 84%) + Strict OHP (3x5 @ 80%) + Weighted Chins + DB Incline/Row (75lb)",
            sport="Strength", duration=60.0, aerobic_tss=16.0, mechanical_strain=12.0,
            ans_cost=54.0, durability_score=16.0, zone_durations=(45.0, 15.0, 0.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="UL", bb_sets=11, db_sets=4, neuromuscular_load=72.0, axial_load=35.0,
            sets_by_muscle={"Anterior_Chain": 0, "Posterior_Chain": 0, "Push": 8, "Pull": 7},
            flags={"heavy_sq": 0, "heavy_dl": 0, "heavy_press": 1}
        ),
        CandidateWorkout(
            id=2, code="UL_BB_POST_DL",
            name="Upper/Lower: Barbell Deadlift (1x5 @ 88%) + Barbell RDL (3x6) + SL DB RDL & Cuban Press (75lb)",
            sport="Strength", duration=60.0, aerobic_tss=18.0, mechanical_strain=32.0,
            ans_cost=72.0, durability_score=22.0, zone_durations=(45.0, 10.0, 5.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=True, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="UL", bb_sets=8, db_sets=7, neuromuscular_load=90.0, axial_load=92.0,
            sets_by_muscle={"Anterior_Chain": 3, "Posterior_Chain": 8, "Push": 2, "Pull": 3},
            flags={"heavy_sq": 0, "heavy_dl": 1, "heavy_press": 0}
        ),

        # --- ARCHITECTURE 2: FULL-BODY HLM / TEXAS METHOD BARBELL + DB SPLIT ---
        CandidateWorkout(
            id=3, code="HLM_BB_VOL",
            name="Texas/HLM Volume: Low-Bar Squat (5x5 @ 76%) + Bench Press (5x5 @ 76%) + 1-Arm DB Row (3x10 @ 75lb)",
            sport="Strength", duration=60.0, aerobic_tss=18.0, mechanical_strain=28.0,
            ans_cost=66.0, durability_score=20.0, zone_durations=(40.0, 15.0, 5.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=True, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="HLM", bb_sets=10, db_sets=3, neuromuscular_load=85.0, axial_load=85.0,
            sets_by_muscle={"Anterior_Chain": 5, "Posterior_Chain": 2, "Push": 5, "Pull": 3},
            flags={"heavy_sq": 1, "heavy_dl": 0, "heavy_press": 0}
        ),
        CandidateWorkout(
            id=4, code="HLM_LGT_DB_DE",
            name="Texas/HLM Light DE + DB: DE Squat (6x2 @ 60%) + Strict OHP (3x5 @ 80%) + DB Split Squat & Incline DB (75lb)",
            sport="Strength", duration=60.0, aerobic_tss=16.0, mechanical_strain=16.0,
            ans_cost=54.0, durability_score=16.0, zone_durations=(45.0, 15.0, 0.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="HLM", bb_sets=6, db_sets=9, neuromuscular_load=52.0, axial_load=35.0,
            sets_by_muscle={"Anterior_Chain": 3, "Posterior_Chain": 3, "Push": 5, "Pull": 4},
            flags={"heavy_sq": 0, "heavy_dl": 0, "heavy_press": 1}
        ),
        CandidateWorkout(
            id=5, code="HLM_BB_INT",
            name="Texas/HLM Intensity: Low-Bar Squat (1x5RM @ 90%) + Deadlift (1x5 @ 88%) + Weighted Chins + DB SL RDL",
            sport="Strength", duration=60.0, aerobic_tss=18.0, mechanical_strain=30.0,
            ans_cost=72.0, durability_score=22.0, zone_durations=(45.0, 10.0, 5.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=True, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="HLM", bb_sets=8, db_sets=3, neuromuscular_load=92.0, axial_load=92.0,
            sets_by_muscle={"Anterior_Chain": 2, "Posterior_Chain": 5, "Push": 0, "Pull": 3},
            flags={"heavy_sq": 1, "heavy_dl": 1, "heavy_press": 0}
        ),

        # --- SWIM WORKOUTS (Facility Gated: Mon/Wed/Fri) ---
        CandidateWorkout(
            id=6, code="SW_CSS_TH",
            name="Swim CSS Threshold (10x100m @ CSS w/ 15s rest, 2800m)",
            sport="Swim", duration=55.0, aerobic_tss=58.0, mechanical_strain=4.0,
            ans_cost=40.0, durability_score=26.0, zone_durations=(15.0, 15.0, 5.0, 20.0, 0.0),
            is_hard_endurance=True, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=20.0, axial_load=0.0,
            flags={"swim_th": 1, "pool": 1}
        ),
        CandidateWorkout(
            id=7, code="SW_Z2_AERO",
            name="Swim Zone 2 Aerobic Endurance & EVF Catch Drills (2500m)",
            sport="Swim", duration=50.0, aerobic_tss=42.0, mechanical_strain=3.0,
            ans_cost=18.0, durability_score=22.0, zone_durations=(15.0, 35.0, 0.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=10.0, axial_load=0.0,
            flags={"swim_th": 0, "pool": 1}
        ),

        # --- BIKE WORKOUTS (Smart Trainer Daily; Long Road Ride Sat/Sun) ---
        CandidateWorkout(
            id=8, code="BK_TH_OU",
            name="Indoor Smart Trainer Over-Unders (3x10m @ 95/105% FTP)",
            sport="Bike", duration=60.0, aerobic_tss=80.0, mechanical_strain=14.0,
            ans_cost=62.0, durability_score=36.0, zone_durations=(12.0, 12.0, 0.0, 30.0, 6.0),
            is_hard_endurance=True, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=True, preference_penalty=0.1,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=44.0, axial_load=10.0,
            flags={"hi_torque_bike": 1}
        ),
        CandidateWorkout(
            id=9, code="BK_Z2_SPIN",
            name="Indoor Smart Trainer Zone 2 Aerobic Efficiency (68% FTP @ 92 RPM)",
            sport="Bike", duration=60.0, aerobic_tss=48.0, mechanical_strain=7.0,
            ans_cost=18.0, durability_score=30.0, zone_durations=(15.0, 45.0, 0.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=True, preference_penalty=0.1,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=12.0, axial_load=5.0,
            flags={}
        ),
        CandidateWorkout(
            id=10, code="BK_LONG_DUR",
            name="Outdoor Weekend Long Durability Ride (160m Z2 + Late 20m Z3)",
            sport="Bike", duration=160.0, aerobic_tss=132.0, mechanical_strain=22.0,
            ans_cost=50.0, durability_score=85.0, zone_durations=(25.0, 115.0, 20.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.0,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=30.0, axial_load=18.0,
            flags={"outdoor_bike": 1}
        ),

        # --- RUN WORKOUTS (Long Run 60m / 190m Total = 31.58% <= 32.0% Cap) ---
        CandidateWorkout(
            id=11, code="RN_TH_CRUISE",
            name="Run Threshold Cruise Intervals (3x8m @ 100% Stryd CP / NGP)",
            sport="Run", duration=50.0, aerobic_tss=64.0, mechanical_strain=74.0,
            ans_cost=62.0, durability_score=36.0, zone_durations=(12.0, 14.0, 0.0, 20.0, 4.0),
            is_hard_endurance=True, is_heavy_run=True, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=48.0, axial_load=28.0,
            flags={"heavy_run": 1}
        ),
        CandidateWorkout(
            id=12, code="RN_Z2_STRIDE",
            name="Run Zone 2 Aerobic + 6x8s Alactic Uphill Strides (Full 90s Walk Rest)",
            sport="Run", duration=45.0, aerobic_tss=44.0, mechanical_strain=42.0,
            ans_cost=22.0, durability_score=30.0, zone_durations=(10.0, 34.0, 0.0, 0.0, 1.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=True, is_indoor_bike=False, preference_penalty=0.1,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=18.0, axial_load=14.0,
            flags={}
        ),
        CandidateWorkout(
            id=13, code="RN_Z2_BRICK",
            name="Run Zone 2 Aerobic / T2 Transition Brick Run (35m Flat)",
            sport="Run", duration=35.0, aerobic_tss=32.0, mechanical_strain=30.0,
            ans_cost=15.0, durability_score=26.0, zone_durations=(10.0, 25.0, 0.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.1,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=12.0, axial_load=10.0,
            flags={}
        ),
        CandidateWorkout(
            id=14, code="RN_LONG_Z2",
            name="Weekend Long Run (60m Zone 2 Aerobic Durability, Pa:Hr < 5%)",
            sport="Run", duration=60.0, aerobic_tss=58.0, mechanical_strain=62.0,
            ans_cost=42.0, durability_score=65.0, zone_durations=(15.0, 45.0, 0.0, 0.0, 0.0),
            is_hard_endurance=False, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.0,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=24.0, axial_load=22.0,
            flags={"long_run": 1}
        ),

        # --- BENCHMARK CRITICAL POWER & CSS TESTS (ADR-009 / ADR-011) ---
        CandidateWorkout(
            id=15, code="BK_CP_TEST",
            name="Indoor Smart Trainer Two-Point CP Test (P3 180s + 30m Recov + P12 720s)",
            sport="Bike", duration=75.0, aerobic_tss=78.0, mechanical_strain=14.0,
            ans_cost=85.0, durability_score=20.0, zone_durations=(25.0, 20.0, 0.0, 15.0, 15.0),
            is_hard_endurance=True, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=True, preference_penalty=0.2,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=48.0, axial_load=12.0,
            flags={"hi_torque_bike": 1, "bike_test": 1, "test": 1}
        ),
        CandidateWorkout(
            id=16, code="RN_CP_TEST",
            name="Run Two-Point CP Test (P3 180s + 30m Recov + P12 720s)",
            sport="Run", duration=65.0, aerobic_tss=72.0, mechanical_strain=85.0,
            ans_cost=90.0, durability_score=20.0, zone_durations=(20.0, 20.0, 0.0, 15.0, 10.0),
            is_hard_endurance=True, is_heavy_run=True, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.2,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=52.0, axial_load=28.0,
            flags={"heavy_run": 1, "run_test": 1, "test": 1}
        ),
        CandidateWorkout(
            id=17, code="SW_CSS_TEST",
            name="Swim CSS Benchmark Test (400m TT + 10m Easy + 200m TT)",
            sport="Swim", duration=50.0, aerobic_tss=52.0, mechanical_strain=4.0,
            ans_cost=45.0, durability_score=18.0, zone_durations=(15.0, 15.0, 0.0, 20.0, 0.0),
            is_hard_endurance=True, is_heavy_run=False, is_heavy_strength=False, is_anchor=False,
            has_strides=False, is_indoor_bike=False, preference_penalty=0.2,
            split_group="ANY", bb_sets=0, db_sets=0, neuromuscular_load=22.0, axial_load=0.0,
            flags={"swim_th": 1, "pool": 1, "test": 1}
        ),
    ]


def calculate_ans_cost(zone_durations: Tuple[float, float, float, float, float], has_strides: bool = False) -> float:
    """
    Autonomic Recovery Cost:
    C_ans = sum_{z=1}^5 T_z * e^(0.65 * (z - 1))
    If has_strides is True, Zone 5 alactic burst is exempted from the 13.46x multiplier (weighted at 1.0).
    """
    weights = [math.exp(0.65 * z) for z in range(5)]  # [1.0, 1.9155, 3.6693, 7.0287, 13.4637]
    cost = 0.0
    for z in range(5):
        if z == 4 and has_strides:
            cost += zone_durations[z] * 1.0
        else:
            cost += zone_durations[z] * weights[z]
    return float(cost)


def calculate_mechanical_strain(sport: str, aerobic_tss: float, intensity_factor: float, duration: float) -> float:
    """
    Calculates peripheral orthopedic strain:
    - Swim: S=0.05
    - Bike: S=0.10 * (IF)^0.2
    - Run: S=1.00 * (IF)^1.0
    - Strength: S=1.50 (heavy tension/eccentric load)
    """
    s = sport.capitalize()
    if s == "Swim":
        return round(0.05 * aerobic_tss, 1)
    elif s == "Bike":
        return round(0.10 * aerobic_tss * (intensity_factor ** 0.2), 1)
    elif s == "Run":
        return round(1.00 * aerobic_tss * (intensity_factor ** 1.0), 1)
    elif s == "Strength":
        return round(1.50 * max(aerobic_tss, 15.0) * (duration / 45.0) * 2.5, 1)
    elif s == "Brick":
        # Approximate 50% bike strain + 50% run strain
        return round(0.55 * aerobic_tss * intensity_factor, 1)
    return round(0.50 * aerobic_tss, 1)


def enrich_workouts_from_df(df: pd.DataFrame, base_id: int = 100) -> List[CandidateWorkout]:
    """
    Enriches arbitrary workout DataFrames (e.g. from Bike.xlsx, Run.xlsx, Swim.xlsx)
    with dual-load, ANS, durability, and physiological categorization attributes.
    """
    enriched = []
    for idx, row in df.iterrows():
        code = str(row.get('Code', f'WKT_{idx}'))
        sport = str(row.get('Type', row.get('sport', 'Bike'))).capitalize()
        duration = float(row.get('Duration', row.get('duration', 60.0)))
        tss = float(row.get('TSS', row.get('tss', 50.0)))
        if tss <= 0 or duration <= 0:
            continue

        z1 = float(row.get('Zone 1', 0.0))
        z2 = float(row.get('Zone 2', 0.0))
        z3 = float(row.get('Zone 3', 0.0))
        z4 = float(row.get('Zone 4', 0.0))
        z5 = float(row.get('Zone 5', 0.0))
        tot_z = z1 + z2 + z3 + z4 + z5
        if tot_z <= 0:
            z1 = duration * 0.3
            z2 = duration * 0.7
            zone_durs = (z1, z2, 0.0, 0.0, 0.0)
        else:
            # Scale to match total duration if slight mismatch
            scale = duration / tot_z
            zone_durs = (z1 * scale, z2 * scale, z3 * scale, z4 * scale, z5 * scale)

        int_fac = float(row.get('Intensity Factor', row.get('intensity_factor', 0.75)))
        high_dur = zone_durs[2] + zone_durs[3] + zone_durs[4]

        # Flags
        is_hard = (high_dur >= 15.0) or (int_fac >= 0.85)
        has_strides = ('SS' in code or 'cadence' in code.lower() or 'stride' in code.lower())
        is_heavy_run = (sport == 'Run' and (duration >= 70.0 or is_hard))
        is_heavy_str = (sport == 'Strength' and ('MS' in code or 'HEAVY' in code.upper()))
        is_indoor = (sport == 'Bike' and duration <= 75.0)

        # Durability score estimation
        durability = 2.0
        if duration >= 150:
            durability = 8.5
        elif duration >= 120:
            durability = 7.0
        elif sport == 'Run' and duration >= 70:
            durability = 7.5
        elif 'Brick' in sport or 'TB' in code:
            durability = 8.0
        elif high_dur >= 25.0:
            durability = 6.5

        ans = calculate_ans_cost(zone_durs, has_strides=has_strides)
        mech = calculate_mechanical_strain(sport, tss, int_fac, duration)

        # Neuromuscular and Axial Spinal Load estimation
        flags_dict: Dict[str, int] = {}
        split_group = "ANY"
        bb_sets = 0
        db_sets = 0
        sets_by_muscle: Dict[str, float] = {}

        # Known strength configuration lookup table
        KNOWN_STRENGTH_CONFIGS = {
            "STR_AA1": {
                "split_group": "HLM", "bb_sets": 0, "db_sets": 12, "neuro_load": 45.0, "ax_load": 30.0,
                "sets_by_muscle": {"Anterior_Chain": 3.0, "Posterior_Chain": 3.0, "Push": 3.0, "Pull": 3.0},
                "flags": {"heavy_sq": 0, "heavy_dl": 0, "heavy_press": 0}
            },
            "STR_MS1_HEAVY": {
                "split_group": "HLM", "bb_sets": 8, "db_sets": 0, "neuro_load": 105.0, "ax_load": 90.0,
                "sets_by_muscle": {"Anterior_Chain": 3.0, "Posterior_Chain": 2.0, "Push": 3.0, "Pull": 0.0},
                "flags": {"heavy_sq": 1, "heavy_dl": 1, "heavy_press": 1}
            },
            "STR_MS2_HEAVY": {
                "split_group": "HLM", "bb_sets": 10, "db_sets": 0, "neuro_load": 95.0, "ax_load": 80.0,
                "sets_by_muscle": {"Anterior_Chain": 0.0, "Posterior_Chain": 7.0, "Push": 3.0, "Pull": 0.0},
                "flags": {"heavy_sq": 0, "heavy_dl": 1, "heavy_press": 1}
            },
            "STR_SM1_MAINT": {
                "split_group": "HLM", "bb_sets": 6, "db_sets": 0, "neuro_load": 65.0, "ax_load": 60.0,
                "sets_by_muscle": {"Anterior_Chain": 2.0, "Posterior_Chain": 2.0, "Push": 2.0, "Pull": 0.0},
                "flags": {"heavy_sq": 1, "heavy_dl": 1, "heavy_press": 1}
            },
            "STR_SM2_MAINT": {
                "split_group": "HLM", "bb_sets": 2, "db_sets": 4, "neuro_load": 55.0, "ax_load": 45.0,
                "sets_by_muscle": {"Anterior_Chain": 2.0, "Posterior_Chain": 2.0, "Push": 2.0, "Pull": 0.0},
                "flags": {"heavy_sq": 0, "heavy_dl": 1, "heavy_press": 1}
            },
            "STR_PLYO1": {
                "split_group": "UL", "bb_sets": 0, "db_sets": 0, "neuro_load": 80.0, "ax_load": 15.0,
                "sets_by_muscle": {"Anterior_Chain": 4.0, "Posterior_Chain": 4.0, "Push": 0.0, "Pull": 0.0},
                "flags": {"heavy_sq": 0, "heavy_dl": 0, "heavy_press": 0}
            },
            "STR_UPPER_HYP": {
                "split_group": "UL", "bb_sets": 0, "db_sets": 12, "neuro_load": 50.0, "ax_load": 10.0,
                "sets_by_muscle": {"Anterior_Chain": 0.0, "Posterior_Chain": 0.0, "Push": 6.0, "Pull": 6.0},
                "flags": {"heavy_sq": 0, "heavy_dl": 0, "heavy_press": 1}
            },
            "STR_LOWER_UNI": {
                "split_group": "UL", "bb_sets": 0, "db_sets": 11, "neuro_load": 60.0, "ax_load": 25.0,
                "sets_by_muscle": {"Anterior_Chain": 5.0, "Posterior_Chain": 6.0, "Push": 0.0, "Pull": 0.0},
                "flags": {"heavy_sq": 0, "heavy_dl": 0, "heavy_press": 0}
            },
            "STR_POST_CORE": {
                "split_group": "UL", "bb_sets": 3, "db_sets": 3, "neuro_load": 55.0, "ax_load": 15.0,
                "sets_by_muscle": {"Anterior_Chain": 0.0, "Posterior_Chain": 6.0, "Push": 0.0, "Pull": 0.0},
                "flags": {"heavy_sq": 0, "heavy_dl": 0, "heavy_press": 0}
            },
            "STR_DELOAD": {
                "split_group": "HLM", "bb_sets": 0, "db_sets": 0, "neuro_load": 15.0, "ax_load": 5.0,
                "sets_by_muscle": {"Anterior_Chain": 2.0, "Posterior_Chain": 2.0, "Push": 2.0, "Pull": 2.0},
                "flags": {"heavy_sq": 0, "heavy_dl": 0, "heavy_press": 0}
            },
        }

        desc_lower = str(row.get('Description', '')).lower()
        if sport == 'Swim':
            flags_dict['pool'] = 1
            if is_hard or 'th' in code.lower() or 'css' in code.lower():
                flags_dict['swim_th'] = 1
                neuro_load = 20.0
            else:
                neuro_load = 10.0
            ax_load = 0.0
        elif sport == 'Bike':
            if not is_indoor:
                flags_dict['outdoor_bike'] = 1
            if is_hard or 'tq' in code.lower() or 'torque' in desc_lower:
                flags_dict['hi_torque_bike'] = 1
                neuro_load = round(0.55 * tss, 1)
                ax_load = 10.0
            else:
                neuro_load = round(0.25 * tss, 1)
                ax_load = 5.0
        elif sport == 'Run':
            if is_heavy_run:
                flags_dict['heavy_run'] = 1
                neuro_load = round(0.65 * mech, 1)
                ax_load = round(0.38 * mech, 1)
            else:
                neuro_load = round(0.40 * mech, 1)
                ax_load = round(0.30 * mech, 1)
            if duration >= 60.0:
                flags_dict['long_run'] = 1
        elif sport == 'Strength':
            if code in KNOWN_STRENGTH_CONFIGS:
                cfg_k = KNOWN_STRENGTH_CONFIGS[code]
                split_group = cfg_k["split_group"]
                bb_sets = cfg_k["bb_sets"]
                db_sets = cfg_k["db_sets"]
                neuro_load = cfg_k["neuro_load"]
                ax_load = cfg_k["ax_load"]
                sets_by_muscle = cfg_k["sets_by_muscle"]
                flags_dict.update(cfg_k["flags"])
            else:
                if 'upper' in code.lower() or 'upper' in desc_lower:
                    split_group = "UL"
                elif 'lower' in code.lower() or 'lower' in desc_lower:
                    split_group = "UL"
                else:
                    split_group = "HLM"

                if 'squat' in desc_lower:
                    flags_dict['heavy_sq'] = 1
                if 'deadlift' in desc_lower or 'rdl' in desc_lower:
                    flags_dict['heavy_dl'] = 1
                if 'press' in desc_lower or 'bench' in desc_lower:
                    flags_dict['heavy_press'] = 1

                if is_heavy_str:
                    neuro_load = 85.0
                    ax_load = 85.0
                    bb_sets = 8
                    db_sets = 2
                else:
                    neuro_load = 45.0
                    ax_load = 30.0
                    bb_sets = 2
                    db_sets = 6
        elif 'Brick' in sport:
            flags_dict['brick'] = 1
            durability = 8.5
            neuro_load = round(0.55 * mech, 1)
            ax_load = round(0.35 * mech, 1)
        else:
            neuro_load = round(0.4 * mech, 1)
            ax_load = round(0.2 * mech, 1)

        cw = CandidateWorkout(
            id=base_id + len(enriched),
            code=code,
            name=str(row.get('Description', f'{sport} {code}')),
            sport=sport,
            duration=duration,
            aerobic_tss=tss,
            mechanical_strain=mech,
            ans_cost=round(ans, 1),
            durability_score=durability,
            zone_durations=zone_durs,
            is_hard_endurance=is_hard,
            is_heavy_run=is_heavy_run,
            is_heavy_strength=is_heavy_str,
            is_anchor=False,  # Raw database candidates are not mandatory anchors by default
            has_strides=has_strides,
            is_indoor_bike=is_indoor,
            preference_penalty=0.1,
            split_group=split_group,
            bb_sets=bb_sets,
            db_sets=db_sets,
            neuromuscular_load=neuro_load,
            axial_load=ax_load,
            sets_by_muscle=sets_by_muscle,
            flags=flags_dict
        )
        enriched.append(cw)
    return enriched


# =============================================================================
# 3. MILP OPTIMIZATION ENGINE (HiGHS)
# =============================================================================

class TriathlonMILPScheduler:
    """
    Production Mixed-Integer Linear Programming (MILP) 7-Day Triathlon Scheduler.

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
        config: MesoCycleTargetsAndConstraints
    ) -> None:
        self.workouts = list(workouts)
        self.cfg = copy.deepcopy(config)  # never mutate the caller's config (TASK-013)
        self.num_workouts = len(self.workouts)
        self.days = list(range(7))
        self.weekdays = [0, 1, 2, 3, 4]
        self.weekends = [5, 6]
        self.sports = ["Swim", "Bike", "Run", "Strength"]
        self.zones = [1, 2, 3, 4, 5]
        self.notes: List[str] = []
        self.last_diagnostics: List[str] = []
        self.last_status: Optional[str] = None

        if self.cfg.sport_freq_bounds is None:
            self.cfg.sport_freq_bounds = {"Swim": (2, 2), "Bike": (3, 4), "Run": (4, 5), "Strength": (1, 2)}

        # Tier-2 only: a sport with no facility access gets a 0% duration-split target, redistributed
        # proportionally across the other sports. Frequency minimums are NOT touched; an unmeetable
        # minimum becomes a reported Tier-1 relaxation.
        for s in self.sports:
            if sum(self.cfg.facility_access.get(s, [True] * 7)) == 0 and \
                    self.cfg.target_sport_duration_splits.get(s, 0.0) > 0:
                zero_split = self.cfg.target_sport_duration_splits[s]
                self.cfg.target_sport_duration_splits[s] = 0.0
                rem_sum = sum(v for k, v in self.cfg.target_sport_duration_splits.items() if k != s)
                if rem_sum > 0:
                    for k in self.cfg.target_sport_duration_splits:
                        if k != s:
                            self.cfg.target_sport_duration_splits[k] += zero_split * (
                                self.cfg.target_sport_duration_splits[k] / rem_sum)
                self.notes.append(f"No {s} access this week: its {zero_split:.0%} duration-split target was "
                                  f"redistributed to the other sports.")

        self.limits = SafetyLimits.from_config(self.cfg)
        self.sets = classify_workouts(self.workouts)

    def _allowed(self, i: int, d: int) -> bool:
        """Unary Tier-0 gating: time cap, facility access, outdoor-bike weekend rule."""
        w = self.workouts[i]
        if w.duration > self.cfg.daily_time_caps[d] + 1e-9:
            return False
        sports = ["Bike", "Run"] if w.sport == "Brick" else [w.sport]
        if not all(self.cfg.facility_access.get(s, [True] * 7)[d] for s in sports):
            return False
        if w.sport == "Bike" and d in self.weekdays and not w.is_indoor_bike:
            return False
        return True

    def explain_feasibility(self) -> List[str]:
        return self.notes + explain_feasibility(self.workouts, self.cfg, self.limits, self._allowed, self.sets)

    @staticmethod
    def _add_clean_row(
        h: highspy.Highs,
        col_coeff_map: Dict[int, float],
        lower_bound: float,
        upper_bound: float,
        tol: float = 1e-9
    ) -> None:
        """Consolidates duplicate column indices, sorts, and adds clean row to HiGHS."""
        filtered = [(col, val) for col, val in sorted(col_coeff_map.items()) if abs(val) > tol]
        if not filtered:
            if lower_bound > tol or upper_bound < -tol:
                raise ValueError(f"Empty row with infeasible bounds [{lower_bound}, {upper_bound}]")
            return
        indices = np.array([col for col, _ in filtered], dtype=np.int32)
        values = np.array([val for _, val in filtered], dtype=np.double)
        h.addRow(float(lower_bound), float(upper_bound), len(indices), indices, values)

    def solve_top_k_plans(self, k_plans: int = 3, time_limit: float = 10.0) -> List[Dict[str, Any]]:
        """
        Builds the 3-tier MILP and solves it lexicographically (Tier-1 slack first, then Tier-2 goals),
        applying canonical No-Good Integer Cuts to extract the top K distinct weekly schedules.
        Returns [] when Tier 0 is infeasible; see ``self.last_diagnostics`` for the plain-English reason.
        """
        inf = highspy.kHighsInf
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        if time_limit is not None and time_limit > 0:
            h.setOptionValue("time_limit", float(time_limit))
        h.changeObjectiveSense(highspy.ObjSense.kMinimize)

        diagnostics = self.explain_feasibility()
        self.last_diagnostics = diagnostics

        col_counter = 0
        def add_var(lb: float, ub: float, cost: float, is_integer: bool) -> int:
            nonlocal col_counter
            idx = col_counter
            h.addVar(float(lb), float(ub))
            h.changeColCost(idx, float(cost))
            if is_integer:
                h.changeColIntegrality(idx, highspy.HighsVarType.kInteger)
            col_counter += 1
            return idx

        def add_row(coeff_map: Dict[int, float], lb: float, ub: float) -> None:
            self._add_clean_row(h, coeff_map, lb, ub)

        # ---------------------------------------------------------------------
        # 1. DECISION VARIABLES
        # ---------------------------------------------------------------------
        max_durab = max((w.durability_score for w in self.workouts), default=1.0)

        # Primary binary assignment variables: x[i, d] in {0, 1}
        x_vars: Dict[Tuple[int, int], int] = {}
        for i, w in enumerate(self.workouts):
            for d in self.days:
                net_cost = (
                    -self.cfg.w_durability * (w.durability_score / max(max_durab, 1e-9))
                    + self.cfg.w_friction * w.preference_penalty
                )
                x_vars[(i, d)] = add_var(0.0, 1.0, net_cost, is_integer=True)

        # Auxiliary binary variables: sport occurrence y[s, d] in {0, 1}
        y_vars: Dict[Tuple[str, int], int] = {}
        for s in self.sports:
            for d in self.days:
                y_vars[(s, d)] = add_var(0.0, 1.0, 0.0, is_integer=True)

        # Double session w_double[d], rest day u_rest[d], brick indicator b_brick[d]
        w_double: Dict[int, int] = {}
        u_rest: Dict[int, int] = {}
        b_brick: Dict[int, int] = {}
        for d in self.days:
            w_double[d] = add_var(0.0, 1.0, self.cfg.w_double_session, is_integer=True)
            u_rest[d] = add_var(0.0, 1.0, 0.0, is_integer=True)
            b_brick[d] = add_var(0.0, 1.0, 0.0, is_integer=True)

        # Tier-2 goal-programming deviations
        tss_scale = self.cfg.w_tss / max(self.cfg.target_weekly_tss, 1.0)
        e_tss_pos = add_var(0.0, inf, tss_scale, is_integer=False)
        e_tss_neg = add_var(0.0, inf, tss_scale, is_integer=False)

        zone_scale = self.cfg.w_zone / max(self.cfg.nominal_weekly_duration, 1.0)
        e_zone_pos: Dict[int, int] = {}
        e_zone_neg: Dict[int, int] = {}
        for z in self.zones:
            e_zone_pos[z] = add_var(0.0, inf, zone_scale, is_integer=False)
            e_zone_neg[z] = add_var(0.0, inf, zone_scale, is_integer=False)

        sdur_scale = self.cfg.w_sport_dur / max(self.cfg.nominal_weekly_duration, 1.0)
        e_sdur_pos: Dict[str, int] = {}
        e_sdur_neg: Dict[str, int] = {}
        for s in self.sports:
            e_sdur_pos[s] = add_var(0.0, inf, sdur_scale, is_integer=False)
            e_sdur_neg[s] = add_var(0.0, inf, sdur_scale, is_integer=False)

        stss_scale = self.cfg.w_sport_tss / max(self.cfg.target_weekly_tss, 1.0)
        e_stss_pos: Dict[str, int] = {}
        e_stss_neg: Dict[str, int] = {}
        for s in ["Bike", "Run"]:
            e_stss_pos[s] = add_var(0.0, inf, stss_scale, is_integer=False)
            e_stss_neg[s] = add_var(0.0, inf, stss_scale, is_integer=False)

        hooks = ModelHooks(
            x=lambda i, d: x_vars[(i, d)],
            add_row=add_row,
            add_var=lambda lb, ub, integer: add_var(lb, ub, 0.0, integer),
            allowed=self._allowed,
        )

        # ---------------------------------------------------------------------
        # 2. TIER 0 — STRUCTURAL & ORTHOPEDIC HARD CONSTRAINTS
        # ---------------------------------------------------------------------
        # Workout library uniqueness (<= 1 per week; anchors handled in Tier 1)
        for i in range(self.num_workouts):
            add_row({x_vars[(i, d)]: 1.0 for d in self.days}, 0.0, 1.0)

        # Daily duration ceilings
        for d in self.days:
            add_row({x_vars[(i, d)]: self.workouts[i].duration for i in range(self.num_workouts)},
                    0.0, self.cfg.daily_time_caps[d])

        # Auxiliary linking, session partition (<= 2 sessions/day; weekend brick may add a third leg)
        for d in self.days:
            for s in self.sports:
                s_map = {x_vars[(i, d)]: 1.0 for i, w in enumerate(self.workouts) if w.sport == s}
                s_map[y_vars[(s, d)]] = -1.0
                add_row(s_map, 0.0, 0.0)

            part_map = {x_vars[(i, d)]: 1.0 for i in range(self.num_workouts)}
            part_map[u_rest[d]] = 1.0
            part_map[w_double[d]] = -1.0
            if d in self.weekends:
                part_map[b_brick[d]] = -1.0
            add_row(part_map, 1.0, 1.0)
            add_row({u_rest[d]: 1.0, w_double[d]: 1.0}, 0.0, 1.0)

            add_row({y_vars[("Bike", d)]: 1.0, y_vars[("Run", d)]: 1.0, b_brick[d]: -1.0}, -inf, 1.0)
            add_row({b_brick[d]: 1.0, y_vars[("Bike", d)]: -1.0}, -inf, 0.0)
            add_row({b_brick[d]: 1.0, y_vars[("Run", d)]: -1.0}, -inf, 0.0)

        S = self.sets
        for d in self.days:
            # At most 1 hard endurance workout per day
            add_row({x_vars[(i, d)]: 1.0 for i, w in enumerate(self.workouts) if w.is_hard_endurance}, 0.0, 1.0)
            # Heavy strength (any heavy lower lift) never shares a day with a threshold/long run
            add_row({x_vars[(i, d)]: 1.0 for i in S.heavy_strength + S.heavy_or_long_runs}, 0.0, 1.0)
        for d in range(6):
            # Conservative +-24h buffer between ANY heavy strength and threshold/long runs (superset of HEUR-004)
            r = {x_vars[(i, d)]: 1.0 for i in S.heavy_strength}
            r.update({x_vars[(j, d + 1)]: 1.0 for j in S.heavy_or_long_runs})
            add_row(r, 0.0, 1.0)
            r = {x_vars[(i, d)]: 1.0 for i in S.heavy_or_long_runs}
            r.update({x_vars[(j, d + 1)]: 1.0 for j in S.heavy_strength})
            add_row(r, 0.0, 1.0)

        # Shared Tier-0 safety layer: gating, 48h caps, axial/neuro, hard/easy, HEUR-004, antagonism, run share
        add_tier0_safety(hooks, self.workouts, self.limits, S)

        # Weekly running mechanical strain ceiling (ACWR & ODI guardrail)
        add_row({x_vars[(i, d)]: self.workouts[i].mechanical_strain for i in S.runs for d in self.days},
                0.0, self.cfg.max_weekly_run_mech_strain)

        # ---------------------------------------------------------------------
        # 3. TIER 1 — NAMED, REPORTED SLACKS (lexicographic by relaxation order)
        # ---------------------------------------------------------------------
        reg = Tier1Registry(hooks, getattr(self.cfg, "tier1_relaxation_order", None))
        add_tier1_targets(
            reg, self.workouts, self.cfg, S,
            x=lambda i, d: x_vars[(i, d)],
            sport_day=lambda s, d: y_vars[(s, d)] if s in self.sports else None,
            rest_day=lambda d: u_rest[d],
        )

        # ---------------------------------------------------------------------
        # 4. TIER 2 — GOAL PROGRAMMING
        # ---------------------------------------------------------------------
        tss_goal_map = {x_vars[(i, d)]: self.workouts[i].aerobic_tss for i in range(self.num_workouts) for d in self.days}
        tss_goal_map[e_tss_neg] = 1.0
        tss_goal_map[e_tss_pos] = -1.0
        add_row(tss_goal_map, self.cfg.target_weekly_tss, self.cfg.target_weekly_tss)

        for z in self.zones:
            p_z = self.cfg.target_zone_pcts.get(z, 0.0)
            z_map: Dict[int, float] = {}
            for i, w in enumerate(self.workouts):
                net_z_minutes = w.zone_durations[z - 1] - p_z * w.duration
                for d in self.days:
                    z_map[x_vars[(i, d)]] = net_z_minutes
            z_map[e_zone_neg[z]] = 1.0
            z_map[e_zone_pos[z]] = -1.0
            add_row(z_map, 0.0, 0.0)

        for s in self.sports:
            r_s = self.cfg.target_sport_duration_splits.get(s, 0.0)
            sd_map: Dict[int, float] = {}
            for i, w in enumerate(self.workouts):
                indicator = 1.0 if w.sport == s else 0.0
                net_s_dur = (indicator - r_s) * w.duration
                for d in self.days:
                    sd_map[x_vars[(i, d)]] = net_s_dur
            sd_map[e_sdur_neg[s]] = 1.0
            sd_map[e_sdur_pos[s]] = -1.0
            add_row(sd_map, 0.0, 0.0)

        for s in ["Bike", "Run"]:
            r_tss = self.cfg.target_bike_run_tss_splits.get(s, 0.5)
            st_map: Dict[int, float] = {}
            for i, w in enumerate(self.workouts):
                if w.sport in ("Bike", "Run"):
                    indicator = 1.0 if w.sport == s else 0.0
                    net_s_tss = (indicator - r_tss) * w.aerobic_tss
                    for d in self.days:
                        st_map[x_vars[(i, d)]] = net_s_tss
            st_map[e_stss_neg[s]] = 1.0
            st_map[e_stss_pos[s]] = -1.0
            add_row(st_map, 0.0, 0.0)

        # ---------------------------------------------------------------------
        # 5. LEXICOGRAPHIC SOLVE WITH CANONICAL NO-GOOD INTEGER CUTS
        # ---------------------------------------------------------------------
        h.setOptionValue("mip_rel_gap", 0.01)
        h.setOptionValue("mip_heuristic_effort", 0.20)
        apply_threads(h)

        plans: List[Dict[str, Any]] = []
        lex = LexState()

        for k in range(k_plans):
            res = lexicographic_solve(h, reg.costs(), lex)
            if k == 0:
                self.last_status = res.status
            if not res.has_solution:
                if k == 0:
                    if res.status == "INFEASIBLE" and not diagnostics:
                        self.last_diagnostics = diagnostics + [
                            "Tier-0 safety caps cannot all be satisfied together (no single obvious cause found pre-solve; "
                            "check 48h TSS/ANS/strain, axial/neuro caps and day caps against the library)."]
                    logger.warning("TriathlonMILPScheduler: no plan (%s). %s", res.status, " | ".join(self.last_diagnostics))
                break

            col_vals = res.col_value
            active_cols: List[int] = []
            schedule_by_day: Dict[int, List[CandidateWorkout]] = {d: [] for d in self.days}
            for (i, d), col_idx in x_vars.items():
                if col_vals[col_idx] > 0.5:
                    active_cols.append(col_idx)
                    schedule_by_day[d].append(self.workouts[i])

            total_tss = sum(w.aerobic_tss for d in self.days for w in schedule_by_day[d])
            total_mech = sum(w.mechanical_strain for d in self.days for w in schedule_by_day[d])
            total_dur = sum(w.duration for d in self.days for w in schedule_by_day[d])
            total_ans = sum(w.ans_cost for d in self.days for w in schedule_by_day[d])
            run_share = run_share_report(schedule_by_day, self.limits)

            plans.append({
                "rank": k + 1,
                "status": res.status,
                "mip_gap": res.mip_gap,
                "objective": res.objective,
                "tier1_penalty": lex.tier1_penalty,
                "relaxations": reg.report(col_vals),
                "diagnostics": list(diagnostics),
                "run_share": run_share,
                "total_tss": total_tss,
                "total_mech_strain": total_mech,
                "total_duration": total_dur,
                "total_ans_cost": total_ans,
                "max_single_run_ratio": run_share["max_single_run_share"],
                "schedule": schedule_by_day,
                "soft_slack_violations": {},  # Tier-0 caps are hard since TASK-016; kept for compatibility
            })

            # Sparse Canonical No-Good Integer Cut: sum_{col in Active} x_col <= |Active| - 1
            add_row({col: 1.0 for col in active_cols}, -inf, float(len(active_cols) - 1))

        return plans


def prune_candidate_library(workouts: List[CandidateWorkout], target_tss: float = 500.0, max_per_sport: int = 25) -> List[CandidateWorkout]:
    """
    Input-driven candidate pruning: filters duplicates and selects top representative workouts
    per sport covering low-endurance, high-intensity, strides, anchors, and varying durations.
    Drastically accelerates MILP solving on libraries with 100+ items.
    """
    if len(workouts) <= 40:
        return workouts

    pruned: List[CandidateWorkout] = []
    by_sport: Dict[str, List[CandidateWorkout]] = {}
    for w in workouts:
        by_sport.setdefault(w.sport, []).append(w)

    for s, w_list in by_sport.items():
        must_keep = [w for w in w_list if w.is_anchor or w.has_strides or w.is_heavy_strength]
        remaining = [w for w in w_list if w not in must_keep]
        remaining.sort(key=lambda x: (x.duration, x.aerobic_tss))
        slots_left = max(6, max_per_sport - len(must_keep))
        if len(remaining) > slots_left:
            step = len(remaining) / slots_left
            selected = [remaining[int(i * step)] for i in range(slots_left)]
        else:
            selected = remaining
        pruned.extend(must_keep + selected)

    # Re-index unique workouts
    seen_codes = set()
    unique_pruned = []
    for w in pruned:
        if w.code not in seen_codes:
            seen_codes.add(w.code)
            unique_pruned.append(w)
    return unique_pruned


# =============================================================================
# 3B. DUAL-OBJECTIVE HYBRID ATHLETE MILP OPTIMIZER (HAOS)
# =============================================================================

class HybridAthleteMILPOptimizer:
    """
    Dual-Objective Hybrid Athlete Optimizer (HAOS) - Production MILP Scheduler.
    Solver Engine: HiGHS (highspy).

    Solves the rolling 7-day multi-sport triathlon (Swim, Bike, Run) and maximal
    barbell/dumbbell strength scheduling problem with exact ratio linearizations,
    biomechanical/axial conflict exclusion rules, and Canonical No-Good Integer Cuts.
    Supports both Upper/Lower and Full-Body Texas/HLM strength split architectures.
    """

    def __init__(
        self,
        library: List[Any],
        config: Any
    ):
        self.library = library
        self.N = len(library)
        self.config = config
        self.days = list(range(7))
        self.day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.sports = ["Swim", "Bike", "Run", "Strength"]
        self.muscles = ["Posterior_Chain", "Anterior_Chain", "Push", "Pull"]
        self.zones = [1, 2, 3, 4, 5]
        self.limits = SafetyLimits.from_config(self.config)
        self.sets = classify_workouts(self.library)
        self.last_diagnostics: List[str] = []
        self.last_status: Optional[str] = None

    def _get_cfg_val(self, key: str, default: Any) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _allowed(self, i: int, d: int) -> bool:
        """Unary Tier-0 gating: daily time cap, pool days, outdoor-ride days, optional facility access."""
        w = self.library[i]
        caps = self._get_cfg_val("daily_duration_caps", self._get_cfg_val("daily_time_caps", [120, 120, 75, 120, 120, 220, 180]))
        if float(getattr(w, "duration", 0.0)) > float(caps[d]) + 1e-9:
            return False
        flg = getattr(w, "flags", {}) or {}
        if flg.get("pool", 0) == 1 and d not in set(self._get_cfg_val("pool_open_days", [0, 2, 4])):
            return False
        if flg.get("outdoor_bike", 0) == 1 and d not in set(self._get_cfg_val("outdoor_bike_days", [5, 6])):
            return False
        access = self._get_cfg_val("facility_access", None)
        if access and not access.get(w.sport, [True] * 7)[d]:
            return False
        return True

    def explain_feasibility(self) -> List[str]:
        return explain_feasibility(self.library, self.config, self.limits, self._allowed, self.sets)

    def _get_zone_minutes(self, w: Any) -> Dict[int, float]:
        zm = getattr(w, "zone_minutes", None)
        if zm:
            return zm
        zd = getattr(w, "zone_durations", None)
        if zd and len(zd) >= 5:
            return {z: float(zd[z - 1]) for z in range(1, 6)}
        dur = float(getattr(w, "duration", 60.0))
        return {1: dur * 0.35, 2: dur * 0.45, 3: dur * 0.15, 4: dur * 0.05, 5: 0.0}

    def solve_top_k_plans(self, k_solutions: int = 2, time_limit: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Builds the MILP in HiGHS and iteratively applies Canonical No-Good
        Integer Cuts and structural split cuts to extract the Top-K optimal plans.
        """
        idx = 0
        x_idx = {(i, d): idx + i * 7 + d for i in range(self.N) for d in self.days}
        idx += self.N * 7

        y_idx = {(s, d): idx + s_i * 7 + d for s_i, s in enumerate(self.sports) for d in self.days}
        idx += len(self.sports) * 7

        u_idx = {(m, d): idx + m_i * 7 + d for m_i, m in enumerate(self.muscles) for d in self.days}
        idx += len(self.muscles) * 7

        w_idx = {d: idx + d for d in self.days}; idx += 7
        r_idx = {d: idx + d for d in self.days}; idx += 7
        b_idx = {d: idx + d for d in self.days}; idx += 7
        z_split = idx; idx += 1
        num_binary_vars = idx

        # Continuous Goal Programming Slack/Surplus Variables (>= 0)
        e_tss_pos = {s: idx + i for i, s in enumerate(self.sports)}; idx += 4
        e_tss_neg = {s: idx + i for i, s in enumerate(self.sports)}; idx += 4
        e_sets_pos = {m: idx + i for i, m in enumerate(self.muscles)}; idx += 4
        e_sets_neg = {m: idx + i for i, m in enumerate(self.muscles)}; idx += 4
        e_tiz_low_neg = idx; idx += 1
        e_tiz_hi_pos = idx; idx += 1
        total_vars = idx

        col_cost = np.zeros(total_vars, dtype=np.float64)
        col_lower = np.zeros(total_vars, dtype=np.float64)
        col_upper = np.ones(total_vars, dtype=np.float64)
        col_upper[num_binary_vars:] = 1e4

        weights = self._get_cfg_val("weights", {
            "w_tss_neg": 150.0, "w_tss_pos": 100.0,
            "w_sets_neg": 150.0, "w_sets_pos": 100.0,
            "w_tiz": 1.0, "w_ans": 0.20, "w_durability": 0.35,
            "w_double": 5.0, "w_rest": 18.0, "w_brick": 4.0,
        })
        target_tss = self._get_cfg_val("target_tss", self._get_cfg_val("target_sport_tss", {
            "Swim": 100.0, "Bike": 260.0, "Run": 198.0, "Strength": 52.0
        }))
        target_sets = self._get_cfg_val("target_sets", self._get_cfg_val("target_movement_sets", {
            "Anterior_Chain": 10.0, "Posterior_Chain": 10.0, "Push": 10.0, "Pull": 10.0
        }))

        for i, w in enumerate(self.library):
            ans = float(getattr(w, "ans_cost", 20.0))
            durab = float(getattr(w, "durability_score", 2.0))
            for d in self.days:
                col_cost[x_idx[(i, d)]] = (
                    weights.get("w_ans", 0.20) * ans
                    - weights.get("w_durability", 0.35) * durab
                )

        for d in self.days:
            col_cost[w_idx[d]] = weights.get("w_double", 5.0)
            col_cost[r_idx[d]] = -weights.get("w_rest", 18.0)
            col_cost[b_idx[d]] = -weights.get("w_brick", 4.0)

        for s in self.sports:
            t_s = float(target_tss.get(s, 100.0))
            col_cost[e_tss_pos[s]] = weights.get("w_tss_pos", 100.0) / max(1.0, t_s)
            col_cost[e_tss_neg[s]] = weights.get("w_tss_neg", 150.0) / max(1.0, t_s)

        for m in self.muscles:
            t_m = float(target_sets.get(m, 10.0))
            col_cost[e_sets_pos[m]] = weights.get("w_sets_pos", 100.0) / max(1.0, t_m)
            col_cost[e_sets_neg[m]] = weights.get("w_sets_neg", 150.0) / max(1.0, t_m)

        col_cost[e_tiz_low_neg] = weights.get("w_tiz", 1.0)
        col_cost[e_tiz_hi_pos] = weights.get("w_tiz", 1.0)

        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        if time_limit is not None and time_limit > 0:
            h.setOptionValue("time_limit", float(time_limit))
        inf = highspy.kHighsInf

        h.addCols(
            total_vars,
            col_cost,
            col_lower,
            col_upper,
            0,
            np.array([0] * total_vars, dtype=np.int32),
            np.array([], dtype=np.int32),
            np.array([], dtype=np.float64),
        )

        integrality = np.array(
            [highspy.HighsVarType.kInteger] * num_binary_vars
            + [highspy.HighsVarType.kContinuous] * (total_vars - num_binary_vars)
        )
        h.changeColsIntegrality(
            total_vars,
            np.arange(total_vars, dtype=np.int32),
            integrality,
        )

        def add_sparse_row(coeff_map: Dict[int, float], lb_val: float, ub_val: float):
            nz_cols = [c for c, v in coeff_map.items() if abs(v) > 1e-9]
            nz_vals = [float(coeff_map[c]) for c in nz_cols]
            h.addRow(
                float(lb_val),
                float(ub_val),
                len(nz_cols),
                np.array(nz_cols, dtype=np.int32),
                np.array(nz_vals, dtype=np.float64),
            )

        # C1: Template uniqueness
        for i in range(self.N):
            add_sparse_row({x_idx[(i, d)]: 1.0 for d in self.days}, 0.0, 1.0)

        # C2: Coherent Strength Split Architecture (z_split=1 -> UL, z_split=0 -> HLM)
        for i, w in enumerate(self.library):
            grp = getattr(w, "split_group", "ANY")
            if grp == "UL":
                row = {x_idx[(i, d)]: 1.0 for d in self.days}
                row[z_split] = -1.0
                add_sparse_row(row, -inf, 0.0)
            elif grp == "HLM":
                row = {x_idx[(i, d)]: 1.0 for d in self.days}
                row[z_split] = 1.0
                add_sparse_row(row, 0.0, 1.0)

        # C3: Daily Duration Ceilings, Max 2 Workouts/Day, Max 1 Workout/Sport/Day
        daily_caps = self._get_cfg_val("daily_duration_caps", self._get_cfg_val("daily_time_caps", [120, 120, 75, 120, 120, 220, 180]))

        for d in self.days:
            add_sparse_row(
                {x_idx[(i, d)]: float(getattr(self.library[i], "duration", 60.0)) for i in range(self.N)},
                0.0,
                float(daily_caps[d]),
            )
            add_sparse_row({x_idx[(i, d)]: 1.0 for i in range(self.N)}, 0.0, 2.0)

            for s in self.sports:
                add_sparse_row(
                    {x_idx[(i, d)]: 1.0 for i in range(self.N) if getattr(self.library[i], "sport", "") == s},
                    0.0,
                    1.0,
                )

        # C5: Auxiliary Variable Linking (y_{s,d}, u_{m,d}, w_d, r_d, b_d)
        for d in self.days:
            row_w = {x_idx[(i, d)]: 1.0 for i in range(self.N)}
            row_w[w_idx[d]] = -1.0
            add_sparse_row(row_w, -inf, 1.0)

            for i in range(self.N):
                add_sparse_row({r_idx[d]: 1.0, x_idx[(i, d)]: 1.0}, 0.0, 1.0)

            for s in self.sports:
                s_ids = [i for i in range(self.N) if getattr(self.library[i], "sport", "") == s]
                for i in s_ids:
                    add_sparse_row({x_idx[(i, d)]: 1.0, y_idx[(s, d)]: -1.0}, -inf, 0.0)
                row_ys = {x_idx[(i, d)]: -1.0 for i in s_ids}
                row_ys[y_idx[(s, d)]] = 1.0
                add_sparse_row(row_ys, -inf, 0.0)

            for m in self.muscles:
                m_ids = [i for i in range(self.N) if (getattr(self.library[i], "sets_by_muscle", {}) or {}).get(m, 0) > 0]
                for i in m_ids:
                    add_sparse_row({x_idx[(i, d)]: 1.0, u_idx[(m, d)]: -1.0}, -inf, 0.0)

            add_sparse_row({b_idx[d]: 1.0, y_idx[("Bike", d)]: -1.0}, -inf, 0.0)
            add_sparse_row({b_idx[d]: 1.0, y_idx[("Run", d)]: -1.0}, -inf, 0.0)

        # Rest day is exact: r_d = 1 iff no workout on day d (r_d + x_id <= 1 above; here r_d + sum_i x_id >= 1)
        for d in self.days:
            row_rest = {x_idx[(i, d)]: 1.0 for i in range(self.N)}
            row_rest[r_idx[d]] = 1.0
            add_sparse_row(row_rest, 1.0, inf)

        str_ids = [i for i, w in enumerate(self.library) if getattr(w, "sport", "") == "Strength"]

        # C6/C7a: Shared Tier-0 safety layer (TASK-017): unary gating, 48h TSS/ANS/strain, daily+weekly
        # axial & neuromuscular caps, hard/easy adjacency, 48h heavy-run separation, deadlift +-24h vs
        # threshold AND long runs, antagonism matrix, non-consecutive strength, single-run share on ALL runs.
        def hy_add_var(lb: float, ub: float, integer: bool) -> int:
            h.addVar(float(lb), float(ub))
            col = h.getNumCol() - 1
            if integer:
                h.changeColIntegrality(col, highspy.HighsVarType.kInteger)
            return col

        hooks = ModelHooks(x=lambda i, d: x_idx[(i, d)], add_row=add_sparse_row, add_var=hy_add_var,
                           allowed=self._allowed)
        add_tier0_safety(hooks, self.library, self.limits, self.sets)

        # Tier-1: sport minimum frequencies and rest days (named, reported slacks)
        reg = Tier1Registry(hooks, self._get_cfg_val("tier1_relaxation_order", None))
        add_tier1_targets(reg, self.library, self.config, self.sets, x=lambda i, d: x_idx[(i, d)],
                          sport_day=lambda s_, d: y_idx.get((s_, d)), rest_day=lambda d: r_idx[d],
                          rest_bounds_default=(1, 2))  # original hybrid rest-day window

        # 7b: Barbell Main Lifts Share (>= 60% of total weekly strength sets)
        beta_bb = float(self._get_cfg_val("min_barbell_set_share", 0.60))
        row_bb = {}
        for i in str_ids:
            w = self.library[i]
            bb_s = int(getattr(w, "bb_sets", 0))
            db_s = int(getattr(w, "db_sets", 0))
            tot_w_sets = bb_s + db_s
            coeff = float(bb_s) - beta_bb * float(tot_w_sets)
            for d in self.days:
                row_bb[x_idx[(i, d)]] = coeff
        if row_bb:
            add_sparse_row(row_bb, 0.0, inf)

        # 7c: Polarized / Pyramidal Time-in-Zone Distribution (Z1+Z2 >= 78%, Z4+Z5 <= 20%)
        endur_ids = [i for i, w in enumerate(self.library) if getattr(w, "sport", "") in ["Swim", "Bike", "Run"]]
        p_low_min = float(self._get_cfg_val("p_low_min", 0.78))
        p_high_max = float(self._get_cfg_val("p_high_max", 0.20))
        row_tiz_low = {e_tiz_low_neg: 1.0}
        row_tiz_hi = {e_tiz_hi_pos: -1.0}
        for i in endur_ids:
            w = self.library[i]
            zm = self._get_zone_minutes(w)
            z12 = zm.get(1, 0.0) + zm.get(2, 0.0)
            z45 = zm.get(4, 0.0) + zm.get(5, 0.0)
            tot_z = sum(zm.values())
            for d in self.days:
                row_tiz_low[x_idx[(i, d)]] = z12 - p_low_min * tot_z
                row_tiz_hi[x_idx[(i, d)]] = z45 - p_high_max * tot_z
        add_sparse_row(row_tiz_low, 0.0, inf)
        add_sparse_row(row_tiz_hi, -inf, 0.0)

        # C8: Goal Programming Target Equations
        for s in self.sports:
            row_tss = {e_tss_neg[s]: 1.0, e_tss_pos[s]: -1.0}
            t_s = float(target_tss.get(s, 100.0))
            for i in [idx_w for idx_w, w in enumerate(self.library) if getattr(w, "sport", "") == s]:
                for d in self.days:
                    row_tss[x_idx[(i, d)]] = float(getattr(self.library[i], "aerobic_tss", 0.0))
            add_sparse_row(row_tss, t_s, t_s)

        for m in self.muscles:
            row_m = {e_sets_neg[m]: 1.0, e_sets_pos[m]: -1.0}
            t_m = float(target_sets.get(m, 10.0))
            for i, w in enumerate(self.library):
                sets_m = (getattr(w, "sets_by_muscle", {}) or {}).get(m, 0)
                if sets_m > 0:
                    for d in self.days:
                        row_m[x_idx[(i, d)]] = float(sets_m)
            add_sparse_row(row_m, t_m, t_m)

        apply_threads(h)
        h.setOptionValue("mip_rel_gap", 0.01)
        diagnostics = self.explain_feasibility()
        self.last_diagnostics = diagnostics
        lex = LexState()
        solutions = []
        for k in range(k_solutions):
            res = lexicographic_solve(h, reg.costs(), lex)
            if k == 0:
                self.last_status = res.status
            if not res.has_solution:
                if k == 0:
                    logger.warning("HybridAthleteMILPOptimizer: no plan (%s). %s", res.status, " | ".join(diagnostics))
                break

            sol = res.col_value

            selected_pairs = []
            schedule_by_day: Dict[int, List[Any]] = {d: [] for d in self.days}
            for i in range(self.N):
                for d in self.days:
                    if sol[x_idx[(i, d)]] > 0.5:
                        selected_pairs.append((i, d))
                        schedule_by_day[d].append(self.library[i])

            split_val = round(sol[z_split])
            split_label = (
                "Upper/Lower Barbell + DB Split"
                if split_val == 1
                else "Full-Body HLM / Texas Method Split"
            )

            total_tss_solved = sum(float(getattr(w, "aerobic_tss", 0.0)) for d in self.days for w in schedule_by_day[d])
            total_axial_solved = sum(float(getattr(w, "axial_load", 0.0)) for d in self.days for w in schedule_by_day[d])
            total_dur_solved = sum(float(getattr(w, "duration", 0.0)) for d in self.days for w in schedule_by_day[d])

            solutions.append({
                "option": chr(65 + k),
                "rank": k + 1,
                "split_architecture": split_label,
                "status": res.status,
                "mip_gap": res.mip_gap,
                "objective_value": res.objective,
                "tier1_penalty": lex.tier1_penalty,
                "relaxations": reg.report(sol),
                "diagnostics": list(diagnostics),
                "run_share": run_share_report(schedule_by_day, self.limits),
                "total_tss": total_tss_solved,
                "total_axial_load": total_axial_solved,
                "total_duration": total_dur_solved,
                "schedule": schedule_by_day,
                "selected_pairs": selected_pairs,
            })

            # Append Canonical No-Good Integer Cut
            cut_row = {
                x_idx[(i, d)]: (1.0 if (i, d) in selected_pairs else -1.0)
                for i in range(self.N)
                for d in self.days
            }
            add_sparse_row(cut_row, -inf, float(len(selected_pairs) - 1))
            add_sparse_row({z_split: 1.0}, float(1 - split_val), float(1 - split_val))

        return solutions


# =============================================================================
# 4. THREE-TIER CLOSED-LOOP MODEL PREDICTIVE CONTROL (MPC)
# =============================================================================

@dataclass
class AthletePhysiologicalState:
    """Tracks running exponential convolution states across days."""
    day_index: int = 0
    metabolic_fitness_state: float = 50.0       # M(t), tau = 35d
    connective_tissue_state: float = 45.0       # C(t), tau = 120d (or 210d for masters)
    running_acute_mech: float = 40.0            # Acute mechanical load, tau = 7d
    running_chronic_mech: float = 38.0          # Chronic mechanical load, tau = 28d
    masters_mode: bool = False                  # When True, tau_c=210d and stricter ACWR caution

    # Single-Sport & Composite Banister PMC
    bike_ctl: float = 55.0
    bike_atl: float = 50.0
    run_ctl: float = 35.0
    run_atl: float = 32.0
    swim_ctl: float = 25.0
    swim_atl: float = 22.0
    total_ctl: float = 115.0
    total_atl: float = 104.0

    @property
    def orthopedic_decoupling_index(self) -> float:
        """ODI = M(t) / C(t). Safe <= 1.35 (<= 1.20 in masters mode)."""
        return self.metabolic_fitness_state / max(self.connective_tissue_state, 1.0)

    @property
    def running_mechanical_acwr(self) -> float:
        """EWMA ACWR = Acute / Chronic. Sweet spot 0.80 - 1.30 (0.80 - 1.15 in masters mode)."""
        return self.running_acute_mech / max(self.running_chronic_mech, 1.0)

    @property
    def total_tsb(self) -> float:
        """Training Stress Balance = CTL - ATL."""
        return self.total_ctl - self.total_atl

    def step_day(self, daily_aerobic_tss: float, daily_run_mech: float, daily_bike_tss: float, daily_run_tss: float, daily_swim_tss: float):
        """Updates all convolution and EWMA states for 1 elapsed calendar day."""
        self.day_index += 1
        lambda_m = 1.0 / 35.0
        # Masters athlete tendon and bone remodeling constant is slower (tau_c = 210 days vs 120 days)
        tau_c = 210.0 if self.masters_mode else 120.0
        lambda_c = 1.0 / tau_c
        self.metabolic_fitness_state = (1.0 - lambda_m) * self.metabolic_fitness_state + lambda_m * daily_aerobic_tss
        self.connective_tissue_state = (1.0 - lambda_c) * self.connective_tissue_state + lambda_c * daily_run_mech

        # EWMA Mechanical ACWR
        alpha_a = 2.0 / (7.0 + 1.0)
        alpha_c = 2.0 / (28.0 + 1.0)
        self.running_acute_mech = alpha_a * daily_run_mech + (1.0 - alpha_a) * self.running_acute_mech
        self.running_chronic_mech = alpha_c * daily_run_mech + (1.0 - alpha_c) * self.running_chronic_mech

        # Banister PMC (ATL tau=7d, CTL tau=42d)
        alpha_atl = 2.0 / (7.0 + 1.0)
        alpha_ctl = 2.0 / (42.0 + 1.0)
        self.bike_atl = alpha_atl * daily_bike_tss + (1.0 - alpha_atl) * self.bike_atl
        self.bike_ctl = alpha_ctl * daily_bike_tss + (1.0 - alpha_ctl) * self.bike_ctl
        self.run_atl = alpha_atl * daily_run_tss + (1.0 - alpha_atl) * self.run_atl
        self.run_ctl = alpha_ctl * daily_run_tss + (1.0 - alpha_ctl) * self.run_ctl
        self.swim_atl = alpha_atl * daily_swim_tss + (1.0 - alpha_atl) * self.swim_atl
        self.swim_ctl = alpha_ctl * daily_swim_tss + (1.0 - alpha_ctl) * self.swim_ctl

        tot_tss = daily_bike_tss + daily_run_tss + daily_swim_tss
        self.total_atl = alpha_atl * tot_tss + (1.0 - alpha_atl) * self.total_atl
        self.total_ctl = alpha_ctl * tot_tss + (1.0 - alpha_ctl) * self.total_ctl


class ClosedLoopMPCController:
    """
    Three-Tier Model Predictive Control (MPC) Feedback Controller.
    - Tier 1: Daily Micro-Adjustments (Readiness Scalar S_readiness)
    - Tier 2: Weekly Receding-Horizon Optimization & Safety Recalibration
    - Tier 3: Mesocycle Periodization Progression (3:1 Step-Cycles & Taper)
    """

    def __init__(self, athlete_state: Optional[AthletePhysiologicalState] = None):
        self.state = athlete_state or AthletePhysiologicalState()
        self.daily_logs: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # TIER 1: DAILY MICRO-ADJUSTMENTS
    # -------------------------------------------------------------------------
    @staticmethod
    def calculate_morning_readiness(
        rmssd_today: float,
        rmssd_7d_baseline: float,
        sleep_hours: float,
        muscle_soreness_1_to_10: float,
        menstrual_phase: Optional[str] = None
    ) -> float:
        """
        Calculates daily readiness scalar S_readiness in [0.3 .. 1.3].
        If menstrual_phase is 'luteal' (days 18-24), adjusts for natural progesterone-induced
        vagal suppression by scaling baseline expectation by 0.85, preventing false Red Alerts.
        """
        effective_baseline = rmssd_7d_baseline
        if menstrual_phase and menstrual_phase.lower() in ("luteal", "mid-luteal"):
            effective_baseline = rmssd_7d_baseline * 0.85

        hrv_ratio = rmssd_today / max(effective_baseline, 1.0)
        sleep_ratio = sleep_hours / 8.0
        soreness_ratio = 1.0 - (muscle_soreness_1_to_10 / 10.0)
        s = 0.5 * hrv_ratio + 0.3 * sleep_ratio + 0.2 * soreness_ratio
        return float(np.clip(s, 0.3, 1.3))

    def evaluate_tier1_daily_action(
        self,
        readiness_scalar: float,
        scheduled_workout: CandidateWorkout
    ) -> Dict[str, Any]:
        """
        Evaluates Tier 1 daily action without reshuffling calendar slots:
        - Red Alert (S < 0.65 or Soreness > 3/10): Substitute hard/strength session with AE1 or Rest
        - Amber Caution (0.65 <= S < 0.85): Truncate interval volume by 25% and scale intensity
        - Green Nominal (0.85 <= S <= 1.15): Execute as prescribed
        - Supercompensation (S > 1.15): Boost intensity target +3-5%
        """
        if readiness_scalar < 0.65:
            # RED ALERT
            if scheduled_workout.is_hard_endurance or scheduled_workout.is_heavy_strength:
                action = "SUBSTITUTE_ACTIVE_RECOVERY"
                adj_dur = round(scheduled_workout.duration * 0.5, 0)
                adj_tss = round(scheduled_workout.aerobic_tss * 0.4, 0)
                adj_mech = round(scheduled_workout.mechanical_strain * 0.3, 0)
                adj_ans = round(scheduled_workout.ans_cost * 0.2, 0)
                reason = "Red Alert (S < 0.65): High autonomic or musculoskeletal fatigue. Replaced with AE1 Recovery."
            else:
                action = "REDUCE_VOLUME_50"
                adj_dur = round(scheduled_workout.duration * 0.5, 0)
                adj_tss = round(scheduled_workout.aerobic_tss * 0.5, 0)
                adj_mech = round(scheduled_workout.mechanical_strain * 0.5, 0)
                adj_ans = round(scheduled_workout.ans_cost * 0.5, 0)
                reason = "Red Alert (S < 0.65): Easy workout volume halved to protect vagal recovery."
        elif readiness_scalar < 0.85:
            # AMBER CAUTION
            action = "TRUNCATE_INTERVALS_25"
            adj_dur = scheduled_workout.duration
            adj_tss = round(scheduled_workout.aerobic_tss * readiness_scalar, 0)
            adj_mech = round(scheduled_workout.mechanical_strain * readiness_scalar, 0)
            adj_ans = round(scheduled_workout.ans_cost * readiness_scalar, 0)
            reason = f"Amber Caution (S={readiness_scalar:.2f}): Interval volume scaled by readiness factor."
        elif readiness_scalar <= 1.15:
            # GREEN NOMINAL
            action = "EXECUTE_AS_PRESCRIBED"
            adj_dur = scheduled_workout.duration
            adj_tss = scheduled_workout.aerobic_tss
            adj_mech = scheduled_workout.mechanical_strain
            adj_ans = scheduled_workout.ans_cost
            reason = "Green Nominal: Homeostasis nominal. Proceed with full workout."
        else:
            # SUPERCOMPENSATION PEAK
            action = "SUPERCOMPENSATION_BOOST"
            adj_dur = scheduled_workout.duration
            adj_tss = round(scheduled_workout.aerobic_tss * 1.05, 0)
            adj_mech = round(scheduled_workout.mechanical_strain * 1.03, 0)
            adj_ans = round(scheduled_workout.ans_cost * 1.05, 0)
            reason = f"Supercompensation (S={readiness_scalar:.2f}): High freshness. Power/pace nudged +5%."

        return {
            "status": action,
            "readiness_scalar": readiness_scalar,
            "reason": reason,
            "adjusted_duration": adj_dur,
            "adjusted_tss": adj_tss,
            "adjusted_mechanical_strain": adj_mech,
            "adjusted_ans_cost": adj_ans,
        }

    # -------------------------------------------------------------------------
    # HYBRID ATHLETE TIER 1 & TIER 2 EXTENSIONS (Wearables & Kalman Filter)
    # -------------------------------------------------------------------------
    @staticmethod
    def calculate_garmin_morning_readiness(
        rmssd_today: float,
        rmssd_baseline_mean: float,
        rmssd_baseline_std: float,
        rhr_today: float,
        rhr_baseline_mean: float,
        sleep_score: float,
    ) -> float:
        """
        Computes continuous morning readiness scalar alpha_d in [0.70, 1.05]
        using Z-scores for nocturnal rMSSD and Resting Heart Rate plus sleep architecture.
        Formula: alpha_d = clip(1.00 + 0.08 * Z_hrv - 0.05 * Z_rhr + 0.04 * ((sleep - 80) / 20), 0.70, 1.05)
        """
        z_hrv = (rmssd_today - rmssd_baseline_mean) / max(rmssd_baseline_std, 1e-3)
        z_rhr = (rhr_today - rhr_baseline_mean) / max(rmssd_baseline_std * 0.5, 1.0)
        sleep_term = (sleep_score - 80.0) / 20.0
        alpha = 1.00 + 0.08 * z_hrv - 0.05 * z_rhr + 0.04 * sleep_term
        return float(np.clip(alpha, 0.70, 1.05))

    def evaluate_tier1_hybrid_action(
        self,
        readiness_alpha: float,
        scheduled_workout: CandidateWorkout
    ) -> Dict[str, Any]:
        """
        Applies Tier 1 operational auto-regulation across lifting and endurance:
        - Green Zone (alpha >= 0.95): Prescribed working load. If alpha >= 1.03, +2.5% load boost on top set.
        - Yellow Caution (0.85 <= alpha < 0.95): Scale working weights down (W_actual = alpha * W_plan), cap endurance at Z2/Z3.
        - Red Alert (alpha < 0.85): Convert heavy axial squats/deadlifts to light mobility/decompression; endurance to Z1 active recovery.
        """
        flg = getattr(scheduled_workout, "flags", {}) or {}
        is_axial_heavy = flg.get("heavy_sq", 0) == 1 or flg.get("heavy_dl", 0) == 1
        sport = scheduled_workout.sport

        if readiness_alpha < 0.85:
            # RED PARASYMPATHETIC SUPPRESSION ZONE
            if sport == "Strength" and is_axial_heavy:
                action = "CONVERT_TO_MOBILITY_DECOMPRESSION"
                adj_dur = min(scheduled_workout.duration, 30.0)
                adj_tss = 8.0
                adj_axial = 5.0
                adj_neuro = 15.0
                reason = f"Red Alert (alpha={readiness_alpha:.2f}): Heavy axial lifts converted to decompression & mobility."
            elif scheduled_workout.is_hard_endurance:
                action = "CONVERT_TO_ZONE_1_RECOVERY"
                adj_dur = round(scheduled_workout.duration * 0.5, 0)
                adj_tss = round(scheduled_workout.aerobic_tss * 0.4, 0)
                adj_axial = round(scheduled_workout.axial_load * 0.4, 0)
                adj_neuro = round(scheduled_workout.neuromuscular_load * 0.3, 0)
                reason = f"Red Alert (alpha={readiness_alpha:.2f}): Threshold intervals replaced with Zone 1 active recovery."
            else:
                action = "REDUCE_VOLUME_50"
                adj_dur = round(scheduled_workout.duration * 0.5, 0)
                adj_tss = round(scheduled_workout.aerobic_tss * 0.5, 0)
                adj_axial = round(scheduled_workout.axial_load * 0.5, 0)
                adj_neuro = round(scheduled_workout.neuromuscular_load * 0.5, 0)
                reason = f"Red Alert (alpha={readiness_alpha:.2f}): Workout volume halved for autonomic restoration."
            load_factor = 0.0
        elif readiness_alpha < 0.95:
            # YELLOW CAUTION ZONE
            action = "SCALE_DOWN_WORKING_WEIGHTS"
            adj_dur = scheduled_workout.duration
            adj_tss = round(scheduled_workout.aerobic_tss * readiness_alpha, 0)
            adj_axial = round(scheduled_workout.axial_load * readiness_alpha, 0)
            adj_neuro = round(scheduled_workout.neuromuscular_load * readiness_alpha, 0)
            load_factor = round(readiness_alpha, 2)
            reason = f"Yellow Caution (alpha={readiness_alpha:.2f}): Barbell working weights scaled by {readiness_alpha:.2f}."
        elif readiness_alpha >= 1.03:
            # SUPERCOMPENSATION PEAK
            action = "AUTOREGULATORY_BOOST_2_5_PCT"
            adj_dur = scheduled_workout.duration
            adj_tss = round(scheduled_workout.aerobic_tss * 1.03, 0)
            adj_axial = round(scheduled_workout.axial_load * 1.025, 0)
            adj_neuro = round(scheduled_workout.neuromuscular_load * 1.05, 0)
            load_factor = 1.025
            reason = f"Supercompensation (alpha={readiness_alpha:.2f}): Fresh CNS. Top set load boosted +2.5%."
        else:
            # GREEN NOMINAL
            action = "EXECUTE_AS_PRESCRIBED"
            adj_dur = scheduled_workout.duration
            adj_tss = scheduled_workout.aerobic_tss
            adj_axial = scheduled_workout.axial_load
            adj_neuro = scheduled_workout.neuromuscular_load
            load_factor = 1.0
            reason = "Green Nominal: Homeostasis nominal. Execute prescribed barbell loads and intervals."

        return {
            "status": action,
            "readiness_alpha": readiness_alpha,
            "reason": reason,
            "load_factor": load_factor,
            "adjusted_duration": adj_dur,
            "adjusted_tss": adj_tss,
            "adjusted_axial_load": adj_axial,
            "adjusted_neuromuscular_load": adj_neuro,
        }

    @staticmethod
    def update_e1rm_kalman(
        e1rm_prev: float,
        p_prev: float,
        weight_lifted: float,
        reps_completed: int,
        logged_rir: float,
        vbt_velocity: Optional[float] = None,
        q_process_var: float = 1.0,
        r_meas_var: float = 4.0,
    ) -> Tuple[float, float, float]:
        """
        Updates athlete's estimated 1RM (e1RM) using Epley-RIR and optional VBT linear mapping
        via a 1D Kalman filter to isolate true strength progression from daily readiness noise.
        Formula: z_t = W * (1 + 0.0333 * (reps + rir))
        Returns: (updated_e1rm, updated_variance, single_session_measurement)
        """
        total_reps_capacity = float(reps_completed + logged_rir)
        z_meas = weight_lifted * (1.0 + 0.0333 * total_reps_capacity)

        # Time update (prediction)
        x_pred = e1rm_prev
        p_pred = p_prev + q_process_var

        # Measurement update (correction)
        k_gain = p_pred / (p_pred + r_meas_var)
        x_updated = x_pred + k_gain * (z_meas - x_pred)
        p_updated = (1.0 - k_gain) * p_pred

        return float(round(x_updated, 2)), float(round(p_updated, 3)), float(round(z_meas, 2))

    @staticmethod
    def evaluate_cp_retest_triggers(
        sport: str,
        current_week: int,
        last_test_week: int,
        rolling_decoupling_pct: Optional[float] = None,
        rolling_ef_change_pct: Optional[float] = None,
        consecutive_breakthroughs: int = 0,
        is_reentry_phase: bool = False,
    ) -> Dict[str, Any]:
        """Closed-Loop Critical Power Retesting & Recalibration Engine (ADR-011).

        Dual-trigger evaluation:
        1. Mesocycle synchronization (every 6-8 weeks during recovery deloads).
        2. Real-data telemetry triggers:
           - Aerobic decoupling (Pw:HR or Pa:HR) < 3.0% over steady Z2.
           - Efficiency Factor (EF) improvement >= 5.0% over 28 days.
           - Consecutive breakthrough completions (>=3) at low RPE.
        """
        sport = sport.lower().strip()
        weeks_since_test = current_week - last_test_week

        # 1. Re-entry Orthopedic & Biomechanical Safety Guard
        if is_reentry_phase:
            if sport in ("bike", "cycling") and current_week <= 4:
                return {
                    "retest_recommended": False,
                    "action": "REENTRY_GUARD_LOCKED",
                    "reason": (
                        f"Re-entry week {current_week} <= 4 for cycling. High risk of patellar/lumbar strain. "
                        f"Lock baseline CP (150W) and prioritize neuromuscular/cadence efficiency."
                    ),
                    "suggested_protocol": None,
                }
            if sport in ("run", "running") and current_week <= 6:
                return {
                    "retest_recommended": False,
                    "action": "REENTRY_GUARD_LOCKED",
                    "reason": (
                        f"Re-entry week {current_week} <= 6 for running. Tendon/bone matrix (tau=120d) requires "
                        f"progressive loading. All-out 3m/12m test prohibited to prevent Achilles/bone stress injury."
                    ),
                    "suggested_protocol": None,
                }

        # 2. Telemetry-Driven Adaptation Triggers (Early upward shift)
        decoupling_low = rolling_decoupling_pct is not None and rolling_decoupling_pct < 3.0
        ef_up = rolling_ef_change_pct is not None and rolling_ef_change_pct >= 5.0
        intervals_easy = consecutive_breakthroughs >= 3

        adaptation_signals = sum([bool(decoupling_low), bool(ef_up), bool(intervals_easy)])

        if weeks_since_test >= 4 and adaptation_signals >= 2:
            reasons = []
            if decoupling_low:
                reasons.append(f"Cardiac decoupling suppressed ({rolling_decoupling_pct:.1f}% < 3.0%)")
            if ef_up:
                reasons.append(f"Efficiency Factor climbed +{rolling_ef_change_pct:.1f}%")
            if intervals_easy:
                reasons.append(f"{consecutive_breakthroughs} consecutive breakthrough sessions completed easily")

            return {
                "retest_recommended": True,
                "action": "DATA_DRIVEN_RETEST_TRIGGERED",
                "weeks_since_test": weeks_since_test,
                "reason": f"Telemetry indicates significant aerobic expansion: {'; '.join(reasons)}.",
                "suggested_protocol": "Two-Point CP Test (P3 180s + P12 720s) in upcoming recovery week",
                "auto_bump_candidate_watts": 5.0,
            }

        # 3. Scheduled Mesocycle Horizon Trigger (6 to 8 weeks)
        if weeks_since_test >= 8:
            return {
                "retest_recommended": True,
                "action": "CALENDAR_HORIZON_RETEST_DUE",
                "weeks_since_test": weeks_since_test,
                "reason": (
                    f"{weeks_since_test} weeks elapsed since last CP calibration. Mesocycle block complete; "
                    f"schedule Two-Point CP test during recovery deload week to update 80/20 zones."
                ),
                "suggested_protocol": "Two-Point CP Test (P3 180s + P12 720s)",
                "auto_bump_candidate_watts": None,
            }

        # 4. Nominal Status
        return {
            "retest_recommended": False,
            "action": "NOMINAL_ZONE_MAINTENANCE",
            "weeks_since_test": weeks_since_test,
            "reason": (
                f"{weeks_since_test} weeks since last test. Current zones are physiologically active and protective. "
                f"Continue 80/20 volume accumulation without premature retesting."
            ),
            "suggested_protocol": None,
        }

    @staticmethod
    def configure_hybrid_mesocycle_phase(
        phase_name: str,
        week_in_cycle: int = 1,
        base_tss: float = 610.0,
        progression_model: str = "texas_method",
    ) -> MesoCycleTargetsAndConstraints:
        """
        Tier 3 Mesocycle Wave Alignment (Synchronizing 3:1 Triathlon & Barbell Blocks):
        - Week 1 (Accumulation / Build 1): 100% Baseline TSS (610 TSS), Triples (1x3 @ 90%) or 5/3/1 Wk 1
        - Week 2 (Intensification / Build 2): 106% Baseline TSS (646.6 TSS), Doubles (2x2 @ 93%) or 5/3/1 Wk 2
        - Week 3 (Peak Overload / Build 3): 112% Baseline TSS (683.2 TSS), Singles (5x1 @ 96%) or 5/3/1 Wk 3
        - Week 4 (Synchronized Deload): 60% Baseline TSS (366 TSS), -40% endurance TSS, -50% strength sets,
          high intensity (>=85% 1RM) maintained at low reps to shed fatigue without detraining.
        """
        w = max(1, min(4, week_in_cycle))
        p = phase_name.lower().strip()

        if w == 1 or "build_1" in p:
            # Week 1: 100% baseline TSS
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=base_tss,
                nominal_weekly_duration=640.0,
                target_sport_tss={"Swim": 100.0, "Bike": 260.0, "Run": 198.0, "Strength": 52.0},
                target_movement_sets={"Anterior_Chain": 10.0, "Posterior_Chain": 10.0, "Push": 10.0, "Pull": 10.0},
                target_zone_pcts={1: 0.25, 2: 0.55, 3: 0.12, 4: 0.06, 5: 0.02},
                required_heavy_strength_sessions=2,
                min_barbell_set_share=0.60,
                max_daily_axial_load=120.0,
                max_weekly_axial_load=320.0,
                w_durability=20.0,
            )
        elif w == 2 or "build_2" in p:
            # Week 2: 106% baseline TSS (+6%)
            scaled_tss = round(base_tss * 1.06, 1)
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=scaled_tss,
                nominal_weekly_duration=675.0,
                target_sport_tss={"Swim": round(100.0 * 1.06, 1), "Bike": round(260.0 * 1.06, 1), "Run": round(198.0 * 1.06, 1), "Strength": 52.0},
                target_movement_sets={"Anterior_Chain": 10.0, "Posterior_Chain": 10.0, "Push": 10.0, "Pull": 10.0},
                target_zone_pcts={1: 0.22, 2: 0.54, 3: 0.15, 4: 0.07, 5: 0.02},
                required_heavy_strength_sessions=2,
                min_barbell_set_share=0.60,
                max_daily_axial_load=120.0,
                max_weekly_axial_load=320.0,
                w_durability=30.0,
            )
        elif w == 3 or "build_3" in p or "peak" in p:
            # Week 3: 112% baseline TSS (+12% peak)
            scaled_tss = round(base_tss * 1.12, 1)
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=scaled_tss,
                nominal_weekly_duration=710.0,
                target_sport_tss={"Swim": round(100.0 * 1.12, 1), "Bike": round(260.0 * 1.12, 1), "Run": round(198.0 * 1.12, 1), "Strength": 52.0},
                target_movement_sets={"Anterior_Chain": 10.0, "Posterior_Chain": 10.0, "Push": 10.0, "Pull": 10.0},
                target_zone_pcts={1: 0.20, 2: 0.54, 3: 0.17, 4: 0.07, 5: 0.02},
                required_heavy_strength_sessions=2,
                min_barbell_set_share=0.60,
                max_daily_axial_load=120.0,
                max_weekly_axial_load=320.0,
                w_durability=40.0,
            )
        else:
            # Week 4: Synchronized 3:1 Supercompensation Deload (60% baseline TSS, 50% strength sets)
            scaled_tss = round(base_tss * 0.60, 1)
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=scaled_tss,
                nominal_weekly_duration=400.0,
                target_sport_tss={"Swim": round(100.0 * 0.60, 1), "Bike": round(260.0 * 0.60, 1), "Run": round(198.0 * 0.60, 1), "Strength": 26.0},
                target_movement_sets={"Anterior_Chain": 5.0, "Posterior_Chain": 5.0, "Push": 5.0, "Pull": 5.0},
                target_zone_pcts={1: 0.40, 2: 0.55, 3: 0.03, 4: 0.02, 5: 0.0},
                required_heavy_strength_sessions=1,
                min_barbell_set_share=0.60,
                max_daily_axial_load=80.0,
                max_weekly_axial_load=180.0,
                w_durability=0.0,
            )

    # -------------------------------------------------------------------------
    # TIER 2: WEEKLY RECEDING-HORIZON UPDATE
    # -------------------------------------------------------------------------
    def process_weekly_execution_and_recalibrate(
        self,
        weekly_daily_records: List[Dict[str, float]],
        base_meso_config: MesoCycleTargetsAndConstraints
    ) -> MesoCycleTargetsAndConstraints:
        """
        Ingests realized daily logs across Mon..Sun, updates PMC, ODI, and ACWR,
        and dynamically tightens upcoming weekly run strain ceiling if decoupled.
        """
        for r in weekly_daily_records:
            self.state.step_day(
                daily_aerobic_tss=r.get("tss", 0.0),
                daily_run_mech=r.get("run_mech", 0.0),
                daily_bike_tss=r.get("bike_tss", 0.0),
                daily_run_tss=r.get("run_tss", 0.0),
                daily_swim_tss=r.get("swim_tss", 0.0),
            )

        updated_config = base_meso_config
        odi = self.state.orthopedic_decoupling_index
        acwr = self.state.running_mechanical_acwr

        # If chassis is decoupled (ODI > 1.35) or in Danger Zone (ACWR > 1.30):
        # Dynamically scale down weekly run mechanical strain ceiling
        if odi > 1.35 or acwr > 1.30:
            scale_factor = min(1.35 / odi, 1.30 / acwr)
            new_run_strain_cap = round(updated_config.max_weekly_run_mech_strain * scale_factor, 1)
            updated_config.max_weekly_run_mech_strain = max(new_run_strain_cap, 150.0)
            # Rebalance Bike-Run TSS split toward non-impact cycling
            updated_config.target_bike_run_tss_splits = {"Bike": 0.70, "Run": 0.30}

        return updated_config

    # -------------------------------------------------------------------------
    # TIER 3: MESOCYCLE PERIODIZATION PROGRESSION
    # -------------------------------------------------------------------------
    @staticmethod
    def configure_mesocycle_phase(
        phase_name: str,
        base_tss: float = 540.0,
        race_distance: str = "70.3"
    ) -> MesoCycleTargetsAndConstraints:
        """
        Generates phase-specific periodization targets:
        - Base: Polarized TID (80/20), 2 Heavy Strength sessions, low durability weight
        - Build 1 (Step Wk 1): Pyramidal TID, 1 SM Strength, high durability weight
        - Build 2 (Step Wk 2): Peak Pyramidal (+8% TSS), 1 SM Strength, max durability weight
        - Recovery (Step Wk 3): Restoration TID (-35% TSS), 0 Strength, 0 durability weight
        - Peak / Taper: Sharpening TID, reduced volume, race rehearsal, 0 Strength
        """
        p = phase_name.lower().strip()
        dist = race_distance.lower().strip()

        if "base" in p:
            # Base Phase: Polarized TID, 2 Heavy Strength
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=base_tss,
                nominal_weekly_duration=620.0,
                target_zone_pcts={1: 0.25, 2: 0.55, 3: 0.04, 4: 0.10, 5: 0.06},
                target_sport_duration_splits={"Swim": 0.15, "Bike": 0.45, "Run": 0.32, "Strength": 0.08},
                target_bike_run_tss_splits={"Bike": 0.60, "Run": 0.40},
                required_heavy_strength_sessions=2,
                w_durability=10.0,
            )
        elif "build_2" in p or "build 2" in p or "overload" in p:
            # Build 2: Peak Pyramidal (+8% TSS), 1 Strength, maximal durability weight
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=round(base_tss * 1.08, 0),
                nominal_weekly_duration=680.0,
                target_zone_pcts={1: 0.22, 2: 0.53, 3: 0.18, 4: 0.06, 5: 0.01},
                target_sport_duration_splits={"Swim": 0.15, "Bike": 0.47, "Run": 0.33, "Strength": 0.05},
                target_bike_run_tss_splits={"Bike": 0.62, "Run": 0.38},
                required_heavy_strength_sessions=1,
                w_durability=35.0,
            )
        elif "recovery" in p:
            # Recovery: -35% TSS, 0 Strength, low high-intensity
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=round(base_tss * 0.65, 0),
                nominal_weekly_duration=420.0,
                target_zone_pcts={1: 0.45, 2: 0.55, 3: 0.0, 4: 0.0, 5: 0.0},
                target_sport_duration_splits={"Swim": 0.18, "Bike": 0.45, "Run": 0.37, "Strength": 0.0},
                target_bike_run_tss_splits={"Bike": 0.60, "Run": 0.40},
                required_heavy_strength_sessions=0,
                sport_freq_bounds={"Swim": (2, 2), "Bike": (2, 3), "Run": (3, 4), "Strength": (0, 0)},
                w_durability=0.0,
            )
        elif "taper" in p or "peak" in p:
            # Fast-decay exponential taper: maintain intensity frequency, reduce volume
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=round(base_tss * 0.55, 0),
                nominal_weekly_duration=360.0,
                target_zone_pcts={1: 0.30, 2: 0.50, 3: 0.10, 4: 0.07, 5: 0.03},
                target_sport_duration_splits={"Swim": 0.20, "Bike": 0.45, "Run": 0.35, "Strength": 0.0},
                target_bike_run_tss_splits={"Bike": 0.60, "Run": 0.40},
                required_heavy_strength_sessions=0,
                sport_freq_bounds={"Swim": (2, 2), "Bike": (2, 3), "Run": (3, 3), "Strength": (0, 0)},
                w_durability=5.0,
            )
        else:
            # Build 1 (Default): Pyramidal TID, 1 SM Strength
            return MesoCycleTargetsAndConstraints(
                target_weekly_tss=base_tss,
                nominal_weekly_duration=640.0,
                target_zone_pcts={1: 0.25, 2: 0.55, 3: 0.14, 4: 0.05, 5: 0.01},
                target_sport_duration_splits={"Swim": 0.15, "Bike": 0.45, "Run": 0.33, "Strength": 0.07},
                target_bike_run_tss_splits={"Bike": 0.60, "Run": 0.40},
                required_heavy_strength_sessions=1,
                w_durability=25.0,
            )


# =============================================================================
# 5. HIGH-LEVEL PIPELINE EXECUTION & SEASON GENERATOR
# =============================================================================

def format_plan_to_dataframe(plan: Dict[str, Any], week_num: int = 1, phase_name: str = "Base") -> pd.DataFrame:
    """Formats a solved plan dictionary into a clean pandas DataFrame."""
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    rows = []
    schedule = plan["schedule"]
    for d, d_name in enumerate(day_names):
        wkts = schedule[d]
        if not wkts:
            rows.append({
                "Week": week_num,
                "Phase": phase_name,
                "Day": d_name,
                "Sport": "REST",
                "Code": "REST",
                "Name": "Scheduled Complete Rest Day",
                "Duration": 0.0,
                "TSS": 0.0,
                "MechStrain": 0.0,
                "ANSCost": 0.0,
                "Durability": 0.0,
                "Intensity": "REST",
            })
        else:
            for w in wkts:
                rows.append({
                    "Week": week_num,
                    "Phase": phase_name,
                    "Day": d_name,
                    "Sport": w.sport,
                    "Code": w.code,
                    "Name": w.name,
                    "Duration": w.duration,
                    "TSS": w.aerobic_tss,
                    "MechStrain": w.mechanical_strain,
                    "ANSCost": w.ans_cost,
                    "Durability": w.durability_score,
                    "Intensity": "HARD" if w.is_hard_endurance or w.is_heavy_strength else "EASY",
                })
    return pd.DataFrame(rows)


def generate_closed_loop_season_schedule(
    profile: Optional[Any] = None,
    distance: str = "olympic",
    base_tss: Optional[float] = None,
    library: Optional[List[CandidateWorkout]] = None,
    time_limit_per_week: float = 3.5,
    perturbations: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generates a full periodized multi-month triathlon season plan (Olympic or 70.3)
    driven by the 3-Tier Closed-Loop Model Predictive Control (MPC) engine.

    Tier 3: 3:1 step-cycles, volume/intensity progression, durability scaling, fast-decay taper.
    Tier 2: Receding-horizon adaptive feedback monitoring Banister PMC (CTL/ATL/TSB),
            connective tissue remodeling (M(t)/C(t) ODI), and running mechanical EWMA ACWR.
    Tier 1: Perturbation support (illness, pool closure, sudden travel).
    """
    if profile is None:
        try:
            from athlete_profile import CURRENT_ATHLETE
            profile = CURRENT_ATHLETE
        except ImportError:
            profile = None

    dist = distance.lower().strip()
    if base_tss is None:
        base_tss = profile.base_weekly_tss if profile else (420.0 if "70.3" in dist or "half" in dist else 380.0)

    lib = library or build_default_candidate_library()

    # Define periodization matrices
    if "sprint" in dist:
        season_matrix = [
            (1,  "General Foundation",  "Base Load",              1.00, 10.0, 2),
            (2,  "General Foundation",  "Build (+10%)",           1.10, 15.0, 2),
            (3,  "General Foundation",  "Recovery Deload (-20%)", 0.80,  0.0, 0),
            (4,  "Aerobic Capacity",    "Base Load",              1.12, 15.0, 2),
            (5,  "Aerobic Capacity",    "Overload (+12%)",        1.25, 20.0, 1),
            (6,  "Aerobic Capacity",    "Recovery Deload (-20%)", 0.88,  0.0, 0),
            (7,  "Specific Speed",      "Base Load",              1.20, 25.0, 1),
            (8,  "Specific Speed",      "Overload (+10%)",        1.32, 25.0, 1),
            (9,  "Specific Speed",      "Recovery Deload (-20%)", 0.90,  0.0, 0),
            (10, "Race Simulation",     "Race Pace Simulation",   1.25, 30.0, 0),
            (11, "Taper & Sharpen",     "Taper (-25%)",           0.80, 10.0, 0),
            (12, "Race Week",           "Race Week (-50%)",       0.50,  5.0, 0),
        ]
    elif "70.3" in dist or "half" in dist:
        season_matrix = [
            (1,  "Base 1 (Aerobic)",    "Base Load",              1.00, 15.0, 2),
            (2,  "Base 1 (Aerobic)",    "Build (+10%)",           1.10, 15.0, 2),
            (3,  "Base 1 (Aerobic)",    "Recovery Deload (-20%)", 0.80,  0.0, 0),
            (4,  "Base 2 (Foundation)", "Base Load",              1.12, 20.0, 2),
            (5,  "Base 2 (Foundation)", "Overload (+12%)",        1.24, 25.0, 1),
            (6,  "Base 2 (Foundation)", "Recovery Deload (-20%)", 0.88,  0.0, 0),
            (7,  "Muscular Endurance",  "Base Load",              1.25, 25.0, 1),
            (8,  "Muscular Endurance",  "Overload (+10%)",        1.38, 30.0, 1),
            (9,  "Muscular Endurance",  "Recovery Deload (-20%)", 0.95,  0.0, 0),
            (10, "Specific Threshold",  "Base Load",              1.35, 30.0, 1),
            (11, "Specific Threshold",  "Overload (+10%)",        1.48, 35.0, 1),
            (12, "Specific Threshold",  "Recovery Deload (-22%)", 1.02,  0.0, 0),
            (13, "Durability / Bricks", "Base Load",              1.45, 40.0, 1),
            (14, "Durability / Bricks", "Peak Overload (+10%)",   1.58, 45.0, 1),
            (15, "Durability / Bricks", "Recovery Deload (-25%)", 1.05,  0.0, 0),
            (16, "Race Simulation",     "Race Simulation",        1.45, 45.0, 0),
            (17, "Taper Phase",         "Taper 1 (-30%)",         0.85, 15.0, 0),
            (18, "Race Week",           "Race Week (-55%)",       0.50,  5.0, 0),
        ]
    elif "ironman" in dist or "140.6" in dist:
        season_matrix = [
            (1,  "Base 1 (Foundation)", "Base Load",              1.00, 15.0, 2),
            (2,  "Base 1 (Foundation)", "Build (+10%)",           1.10, 15.0, 2),
            (3,  "Base 1 (Foundation)", "Recovery Deload (-20%)", 0.80,  0.0, 0),
            (4,  "Base 2 (Aerobic)",    "Base Load",              1.12, 20.0, 2),
            (5,  "Base 2 (Aerobic)",    "Overload (+12%)",        1.25, 25.0, 1),
            (6,  "Base 2 (Aerobic)",    "Recovery Deload (-20%)", 0.88,  0.0, 0),
            (7,  "Muscular Endurance",  "Base Load",              1.25, 25.0, 1),
            (8,  "Muscular Endurance",  "Overload (+10%)",        1.38, 30.0, 1),
            (9,  "Muscular Endurance",  "Recovery Deload (-20%)", 0.95,  0.0, 0),
            (10, "Tempo & Threshold",   "Base Load",              1.35, 30.0, 1),
            (11, "Tempo & Threshold",   "Overload (+10%)",        1.48, 35.0, 1),
            (12, "Tempo & Threshold",   "Recovery Deload (-22%)", 1.02,  0.0, 0),
            (13, "Durability Block",    "Base Load",              1.45, 45.0, 1),
            (14, "Durability Block",    "Overload (+10%)",        1.55, 50.0, 1),
            (15, "Durability Block",    "Recovery Deload (-25%)", 1.05,  0.0, 0),
            (16, "Peak Ironman Volume", "Peak Volume 1",          1.55, 50.0, 1),
            (17, "Peak Ironman Volume", "Peak Overload 2",        1.62, 55.0, 1),
            (18, "Peak Ironman Volume", "Recovery Deload (-25%)", 1.08,  0.0, 0),
            (19, "Race Simulation",     "Big Day Rehearsal",      1.45, 45.0, 0),
            (20, "Taper Phase",         "Taper 1 (-35%)",         0.80, 15.0, 0),
            (21, "Race Week",           "Race Week (-60%)",       0.45,  5.0, 0),
        ]
    else: # Default 16-Week Olympic
        season_matrix = [
            (1,  "General Foundation",  "Base Load",              1.00, 10.0, 2),
            (2,  "General Foundation",  "Build (+10%)",           1.10, 15.0, 2),
            (3,  "General Foundation",  "Recovery Deload (-20%)", 0.80,  0.0, 0),
            (4,  "Aerobic Capacity",    "Base Load",              1.12, 15.0, 2),
            (5,  "Aerobic Capacity",    "Overload (+12%)",        1.25, 20.0, 1),
            (6,  "Aerobic Capacity",    "Recovery Deload (-20%)", 0.88,  0.0, 0),
            (7,  "Specific Threshold",  "Base Load",              1.25, 25.0, 1),
            (8,  "Specific Threshold",  "Overload (+10%)",        1.38, 30.0, 1),
            (9,  "Specific Threshold",  "Recovery Deload (-20%)", 0.95,  0.0, 0),
            (10, "Peak Threshold",      "Base Load",              1.38, 30.0, 1),
            (11, "Peak Threshold",      "Peak Overload (+10%)",   1.50, 35.0, 1),
            (12, "Peak Threshold",      "Recovery Deload (-25%)", 0.98,  0.0, 0),
            (13, "Race Specific",       "Race Pace Simulation 1", 1.35, 40.0, 0),
            (14, "Race Specific",       "Race Pace Simulation 2", 1.30, 35.0, 0),
            (15, "Taper & Sharpen",     "Taper (-25%)",           0.85, 10.0, 0),
            (16, "Race Week",           "Race Week (-50%)",       0.55,  5.0, 0),
        ]

    # Initialize Controller and Physiological State
    athlete_state = profile.to_athlete_state() if profile else AthletePhysiologicalState()
    mpc = ClosedLoopMPCController(athlete_state)

    all_daily_rows = []
    season_summary_rows = []

    print(f"\n================================================================================")
    print(f"  LAUNCHING CLOSED-LOOP {dist.upper()} SEASON PLANNER ({len(season_matrix)} WEEKS)")
    print(f"  Starting CTL: {athlete_state.total_ctl:.1f} | ATL: {athlete_state.total_atl:.1f} | Initial ODI: {athlete_state.orthopedic_decoupling_index:.2f}")
    print(f"================================================================================")

    for week_num, phase_name, step_type, tss_factor, durability_w, req_strength in season_matrix:
        target_tss = round(base_tss * tss_factor, 0)

        # 1. Tier 3 Mesocycle Configuration
        if profile:
            cfg = profile.to_mesocycle_config(phase_name=phase_name, tss_target=target_tss)
        else:
            cfg = ClosedLoopMPCController.configure_mesocycle_phase(
                phase_name=phase_name,
                base_tss=target_tss,
                race_distance=dist,
            )
        cfg.w_durability = durability_w
        cfg.required_heavy_strength_sessions = req_strength
        if req_strength == 0:
            cfg.sport_freq_bounds["Strength"] = (0, 0)

        # 2. Tier 2 Receding-Horizon Feedback & Recalibration
        pre_odi = athlete_state.orthopedic_decoupling_index
        pre_acwr = athlete_state.running_mechanical_acwr
        recal_action = "NOMINAL"

        # Receding-Horizon Progression Guard:
        # Prevent acute mechanical & TSS shock (>15% in masters, >25% in regular) if previous week had lower volume
        solved_rows = [r for r in season_summary_rows if r.get("Solved_TSS", 0.0) > 0]
        if solved_rows:
            # Ramp guard references the last week that actually solved (an infeasible rest week is 0 TSS)
            last_solved_tss = solved_rows[-1]["Solved_TSS"]
            last_mech = solved_rows[-1]["Total_Mech_Strain"]
            max_ramp = 1.15 if athlete_state.masters_mode else 1.25
            if target_tss > last_solved_tss * max_ramp:
                target_tss = round(last_solved_tss * max_ramp, 0)
                cfg.target_weekly_tss = target_tss
            if cfg.max_weekly_run_mech_strain > last_mech * max_ramp:
                cfg.max_weekly_run_mech_strain = round(max(last_mech * max_ramp, 150.0), 1)

        # Check safety bounds: ODI > 1.35 (1.20 in masters mode) or ACWR > 1.30 (1.15 in masters mode)
        odi_limit = 1.20 if athlete_state.masters_mode else 1.35
        acwr_limit = 1.15 if athlete_state.masters_mode else 1.30

        if pre_odi > odi_limit or pre_acwr > acwr_limit:
            recal_action = "SAFETY_RECALIBRATION_APPLIED"
            # Throttling mechanical run strain ceiling and shifting TSS to bike
            scale_down = min(odi_limit / pre_odi, acwr_limit / pre_acwr)
            cfg.max_weekly_run_mech_strain = round(cfg.max_weekly_run_mech_strain * scale_down, 1)
            cfg.target_bike_run_tss_splits = {"Bike": 0.70, "Run": 0.30}

        # 3. Tier 1 Perturbation handling
        pert_info = None
        if perturbations and week_num in perturbations:
            pert = perturbations[week_num]
            pert_type = pert.get("type", "").lower()
            if pert_type == "sickness":
                pert_info = "ACUTE SICKNESS (-35% TSS, 0 Strength)"
                cfg.target_weekly_tss = round(cfg.target_weekly_tss * 0.65, 0)
                cfg.required_heavy_strength_sessions = 0
            elif pert_type == "pool_closure":
                pert_info = "POOL CLOSURE (Swim=0, Freq=(0,0))"
                cfg.facility_access["Swim"] = [False] * 7
                cfg.sport_freq_bounds["Swim"] = (0, 0)
            elif pert_type == "travel":
                pert_info = "TRAVEL WEEK (Daily Caps <=60m)"
                cfg.daily_time_caps = [60.0] * 7

        # 4. Solve Week MILP
        scheduler = TriathlonMILPScheduler(lib, cfg)
        plans = scheduler.solve_top_k_plans(k_plans=1, time_limit=time_limit_per_week)

        plan = plans[0] if plans else None
        if plan is None:
            # TASK-016: no profile-discarding fallback. An unsolvable week is reported with its plain-English
            # Tier-0 diagnostics and treated as a rest week; it is never silently replaced by another config.
            reasons = scheduler.last_diagnostics or [f"Solver status {scheduler.last_status}"]
            print(f"Week {week_num:02d} ({phase_name} - {step_type}): INFEASIBLE at TSS {target_tss} "
                  f"({scheduler.last_status}). Reasons: " + " | ".join(reasons))
            plan = {
                "status": scheduler.last_status or "INFEASIBLE", "mip_gap": None, "relaxations": [],
                "diagnostics": reasons, "schedule": {d: [] for d in range(7)}, "total_tss": 0.0,
                "total_mech_strain": 0.0, "total_ans_cost": 0.0, "max_single_run_ratio": 0.0,
            }
        if plan:
            # Advance physiological states day by day
            for d in range(7):
                wkts = plan["schedule"][d]
                d_aerobic = sum(w.aerobic_tss for w in wkts)
                d_mech = sum(w.mechanical_strain for w in wkts if w.sport == "Run")
                d_bike = sum(w.aerobic_tss for w in wkts if w.sport == "Bike")
                d_run = sum(w.aerobic_tss for w in wkts if w.sport == "Run")
                d_swim = sum(w.aerobic_tss for w in wkts if w.sport == "Swim")
                athlete_state.step_day(d_aerobic, d_mech, d_bike, d_run, d_swim)

            if any(plan["schedule"][d] for d in range(7)):
                df_week = format_plan_to_dataframe(plan, week_num=week_num, phase_name=phase_name)
                all_daily_rows.append(df_week)

            tot_ans = plan.get("total_ans_cost", 0.0)
            tot_durability = sum(w.durability_score for day_wkts in plan["schedule"].values() for w in day_wkts)

            season_summary_rows.append({
                "Week": week_num,
                "Phase": phase_name,
                "Step_Type": step_type,
                "Target_TSS": target_tss,
                "Solved_TSS": round(plan["total_tss"], 1),
                "Total_Mech_Strain": round(plan["total_mech_strain"], 1),
                "Total_ANS_Cost": round(tot_ans, 1),
                "Durability": round(tot_durability, 1),
                "Long_Run_Ratio": round(plan["max_single_run_ratio"], 3),
                "Ending_CTL": round(athlete_state.total_ctl, 1),
                "Ending_ATL": round(athlete_state.total_atl, 1),
                "TSB": round(athlete_state.total_tsb, 1),
                "Ending_ODI": round(athlete_state.orthopedic_decoupling_index, 3),
                "Ending_ACWR": round(athlete_state.running_mechanical_acwr, 3),
                "Recalibration": recal_action if not pert_info else f"{recal_action} + {pert_info}",
                "Solve_Status": plan.get("status"),
                "MIP_Gap": plan.get("mip_gap"),
                "Relaxations": "; ".join(r["message"] for r in plan.get("relaxations", [])),
                "Diagnostics": "; ".join(plan.get("diagnostics", [])),
            })

            print(f"Week {week_num:02d} [{phase_name:<20} | {step_type:<22}]: TSS={plan['total_tss']:>5.1f} | Mech={plan['total_mech_strain']:>5.1f} | ANS={tot_ans:>6.1f} | CTL={athlete_state.total_ctl:>4.1f} | TSB={athlete_state.total_tsb:>5.1f} | ODI={athlete_state.orthopedic_decoupling_index:.2f} ({recal_action})")

    daily_season_df = pd.concat(all_daily_rows, ignore_index=True) if all_daily_rows else pd.DataFrame()
    summary_season_df = pd.DataFrame(season_summary_rows)
    return daily_season_df, summary_season_df


# =============================================================================
# 6. DEMO EXECUTION
# =============================================================================


if __name__ == "__main__":
    print("Testing Closed-Loop Triathlon Training Optimizer...")
    lib = build_default_candidate_library()
    cfg = MesoCycleTargetsAndConstraints(
        daily_time_caps=[60.0, 105.0, 60.0, 105.0, 60.0, 240.0, 240.0],
        max_48h_ans_cost=700.0,
    )
    sched = TriathlonMILPScheduler(lib, cfg)
    plans = sched.solve_top_k_plans(k_plans=3)
    print(f"Generated {len(plans)} optimal plans successfully.")
    for p in plans:
        df_plan = format_plan_to_dataframe(p)
        print(f"\n--- Plan Rank {p['rank']} | Obj: {p['objective']:.2f} | TSS: {p['total_tss']:.1f} | MechStrain: {p['total_mech_strain']:.1f} ---")
        print(df_plan[['Day', 'Sport', 'Code', 'Duration', 'TSS', 'MechStrain', 'ANSCost', 'Intensity']].to_string(index=False))
