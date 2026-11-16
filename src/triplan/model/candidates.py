"""Candidate workout and mesocycle constraint data models (split from closed_loop_optimizer, TASK-014)."""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple
import logging


from triplan.solver.safety_constraints import DEFAULT_RELAXATION_ORDER


logger = logging.getLogger(__name__)


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

