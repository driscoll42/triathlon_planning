import os
from dataclasses import dataclass, field
from typing import List
from pandas import DataFrame
import numpy as np


def _resolve_data_path(filename: str) -> str:
    """Resolves data paths checking data/ directory first, then root/relative paths."""
    src_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(src_dir)
    candidates = [
        os.path.join('data', filename),
        os.path.join(root_dir, 'data', filename),
        filename,
        os.path.join(root_dir, filename),
        os.path.join(src_dir, 'data', filename),
        os.path.join(src_dir, filename),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return os.path.join('data', filename)



class triVariables:
    run_ftp = 295.0  # Your running FTP / Critical Power (Stryd)
    bike_ftp = 170.0  # Your bike FTP / Critical Power
    swim_css = 35.29  # Your Critical Swim Speed in meters/minute or yards/minute
    # If you currently have it in minutes / 100 yards (e.g. 2:50 / 100 yards)
    # The calculation to get this is 100 * 60 / # seconds

    # Canonical 80/20 7-Zone Power Boundaries (% of Critical Power) anchored to verified CP (ADR-009)
    run_7zone_bounds = {
        "Z1": (0.50, 0.76),   # Active Recovery / Low Aerobic
        "Z2": (0.76, 0.88),   # Aerobic Endurance (Ceiling = LT1)
        "ZX": (0.88, 0.94),   # Moderate Drift Grey Zone (Penalized)
        "Z3": (0.94, 1.00),   # Sub-Threshold / Tempo (Ceiling = LT2)
        "ZY": (1.00, 1.03),   # Threshold Over-Reach Gap (Penalized)
        "Z4": (1.03, 1.20),   # Supra-Threshold / VO2max
        "Z5": (1.20, 2.00),   # Anaerobic / Repetition (W' Depletion)
    }

    bike_7zone_bounds = {
        "Z1": (0.50, 0.70),   # Active Recovery / Low Aerobic
        "Z2": (0.70, 0.83),   # Aerobic Endurance (Ceiling = LT1)
        "ZX": (0.83, 0.91),   # Moderate Drift Grey Zone (Penalized)
        "Z3": (0.91, 1.00),   # Sub-Threshold / Tempo (Ceiling = LT2)
        "ZY": (1.00, 1.02),   # Threshold Over-Reach Gap (Penalized)
        "Z4": (1.02, 1.10),   # Supra-Threshold / VO2max
        "Z5": (1.10, 2.00),   # Anaerobic / Repetition (W' Depletion)
    }

    ans_cost_7zones = {
        "Z1": 0.0,
        "Z2": 0.0,
        "ZX": 0.7,
        "Z3": 1.0,
        "ZY": 1.4,
        "Z4": 1.8,
        "Z5": 3.5,
    }

    # Legacy 5-Zone operational midpoints for backward compatibility
    bike_zone_power_multiplier = np.array([0.6, 0.765, 0.955, 1.06, 1.30])
    run_zone_power_multiplier = np.array([0.63, 0.82, 0.97, 1.115, 1.35])
    swim_zone_css_multiplier = np.array([0.795, 0.875, 0.98, 1.04, 1.1])

    # Score weights
    tot_weights = 6.0
    tot_tss_weight = 1.0 / tot_weights  # Overall TSS score weight
    tgt_weight = 1.0 / tot_weights  # Overall 80/20 score weight
    # rbs = run, bike, swim
    rbs_dur_split_weight = 1.0 / tot_weights  # RBS Duration 2/3-1/3 score weight
    rbs_tss_split_weight = 1.0 / tot_weights  # RBS TSS 1/3-1/3 score weight
    rbs_dur_tgt_split_weight = 1.0 / tot_weights  # RBS Duration is 80/20 score weight (i.e. 80% of
    high_zone_split_weight = 1.0 / tot_weights # Distribution of the high zone intensities (
    dist_metric_exponential = 1  # Basically how much to punish values which are far away from the target, better to have everything off by 0.1 than everything spot on and one off by 0.08


    target_tss = 500
    low_tgt = 0.85
    high_tgt = 0.15

    # Within the high zone, empirical 80/20 standard: 80% Z3, 14% Z4, 6% Z5
    zone_3_tgt = 0.80
    zone_4_tgt = 0.14
    zone_5_tgt = 0.06

    # 3-Sport Default Distribution (Olympic Distance Preset)
    bike_tgt_percent = 0.40
    run_tgt_percent = 0.28
    swim_tgt_percent = 0.32

    @classmethod
    def set_distance_profile(cls, distance: str = 'olympic'):
        """Sets empirical 80/20 target splits by race distance."""
        d = distance.lower().strip()
        if d in ['sprint', 'olympic']:
            cls.bike_tgt_percent = 0.40
            cls.run_tgt_percent = 0.28
            cls.swim_tgt_percent = 0.32
        elif d in ['70.3', 'half ironman', 'half', 'ironman', '140.6']:
            cls.bike_tgt_percent = 0.44
            cls.run_tgt_percent = 0.28
            cls.swim_tgt_percent = 0.28
        elif d in ['duathlon', '2-sport', 'bike-run']:
            cls.bike_tgt_percent = 0.67
            cls.run_tgt_percent = 0.33
            cls.swim_tgt_percent = 0.0
        cls.low_tgt = 0.85
        cls.high_tgt = 0.15
        cls.zone_3_tgt = 0.80
        cls.zone_4_tgt = 0.14
        cls.zone_5_tgt = 0.06

    max_tss_weekday = 100
    max_time_weekday = 70
    max_tss_weekend = 200
    max_time_weekend = 140

    weekdays = ['Tues', 'Wed', 'Thurs', 'Fri']
    weekend = ['Sat', 'Sun']

    # Standard data file paths
    run_file = _resolve_data_path('Run.xlsx')
    bike_file = _resolve_data_path('Bike.xlsx')
    swim_file = _resolve_data_path('Swim.xlsx')
    strength_file = _resolve_data_path('strength_workouts.csv')
    brick_file = _resolve_data_path('brick_workouts.csv')

    # Column name mappings
    tss = 'TSS'
    duration = 'Duration'
    code = 'Code'
    type = 'Type'
    zone_1 = 'Zone 1'
    zone_2 = 'Zone 2'
    zone_3 = 'Zone 3'
    zone_4 = 'Zone 4'
    zone_5 = 'Zone 5'
    high_duration = 'High Duration'
    low_duration = 'Low Duration'
    # Column aliases referenced by util.py / clean_file.py (TASK-006)
    high_duration_head = 'High Duration'
    low_duration_head = 'Low Duration'
    intensity_factor = 'Intensity Factor'
    normalized_power = 'Normalized Power'


class HybridVariables:
    """
    Dual-Objective Hybrid Athlete Configuration (Swim, Bike, Run, Strength).
    Synthesizes Rippetoe, Helms, Zatsiorsky & Kraemer, Friel, and Viada.
    """
    # Primary Movement Patterns
    ANTERIOR_CHAIN = "Anterior_Chain"
    POSTERIOR_CHAIN = "Posterior_Chain"
    PUSH = "Push"
    PULL = "Pull"
    MUSCLE_GROUPS = [POSTERIOR_CHAIN, ANTERIOR_CHAIN, PUSH, PULL]

    # Sports
    SPORTS = ["Swim", "Bike", "Run", "Strength"]

    # Balanced Dual-Objective Build Default Targets (610 TSS Total)
    target_tss_sports = {
        "Swim": 100.0,
        "Bike": 260.0,
        "Run": 198.0,
        "Strength": 52.0,
    }

    # Weekly Effective Hard Sets Targets (RPE >= 7, RIR <= 3)
    target_movement_sets = {
        ANTERIOR_CHAIN: 10.0,
        POSTERIOR_CHAIN: 10.0,
        PUSH: 10.0,
        PULL: 10.0,
    }

    # Equipment Guardrails (Home Gym Power Rack + Capped 75 lb DBs)
    dumbbell_cap_lbs = 75.0
    min_barbell_set_share = 0.60  # >= 60% of weekly sets via Olympic Barbell

    # Axial Spinal (L1-S1) & Neuromuscular Load Ceilings
    max_daily_axial_load = 120.0
    max_weekly_axial_load = 320.0
    max_daily_neuro_load = 130.0  # ADR-008 / INVARIANTS.md (TASK-009)

    # Orthopedic Safety & Polarized Endurance Ratios
    max_single_run_vol_ratio = 0.32  # <= 32% of realized weekly run duration
    p_low_min = 0.78                 # >= 78% Zone 1-2
    p_high_max = 0.20                # <= 20% Zone 4-5

    # Real-World Availability & Facility Gating (Mon=0 .. Sun=6)
    daily_duration_caps = [120.0, 120.0, 75.0, 120.0, 120.0, 220.0, 180.0]
    pool_open_days = [0, 2, 4]      # Mon, Wed, Fri
    outdoor_bike_days = [5, 6]       # Sat, Sun

    # Multi-Objective Pareto Weights
    default_weights = {
        "w_tss_neg": 150.0,
        "w_tss_pos": 100.0,
        "w_sets_neg": 150.0,
        "w_sets_pos": 100.0,
        "w_tiz": 1.0,
        "w_ans": 0.20,
        "w_durability": 0.35,
        "w_double": 5.0,
        "w_rest": 18.0,
        "w_brick": 4.0,
    }

    run_file = _resolve_data_path('Run.xlsx')
    bike_file = _resolve_data_path('Bike.xlsx')
    swim_file = _resolve_data_path('Swim.xlsx')

    tss = 'TSS'
    duration = 'Duration'
    code = 'Code'
    type = 'Type'
    zone_1 = 'Zone 1'
    zone_2 = 'Zone 2'
    zone_3 = 'Zone 3'
    zone_4 = 'Zone 4'
    zone_5 = 'Zone 5'

    intensity_factor = 'Intensity Factor'
    normalized_power = 'Normalized Power'
    high_duration_head = 'High Duration'
    low_duration_head = 'Low Duration'


class workout:
    def __init__(self):
        self.plan = []
        self.total_tss = 0.0
        self.duration = 0.0
        self.high_dur = 0.0
        self.low_dur = 0.0
        self.high_pct = 0.0
        self.low_pct = 0.0

        # Outputs
        self.score = 0.0
        self.tss_score = 0.0
        self.tgt_score = 0.0
        self.high_dur_percent = 0.0
        self.low_dur_percent = 0.0
        self.run_dur_percent = 0.0
        self.bike_dur_percent = 0.0
        self.swim_dur_percent = 0.0
        self.run_tss_percent = 0.0
        self.bike_tss_percent = 0.0
        self.swim_tss_percent = 0.0
        self.run_high_dur_percent = 0.0
        self.run_low_dur_percent = 0.0
        self.bike_high_dur_percent = 0.0
        self.bike_low_dur_percent = 0.0
        self.swim_high_dur_percent = 0.0
        self.swim_low_dur_percent = 0.0
        self.dur_tgt_score = 0.0
        self.tss_tgt_score = 0.0
        self.dur_split_tgt_score = 0.0
        self.high_split_tgt_score = 0.0

        class _Sport:
            def __init__(self, name):
                self.name = name
                self.tgt_percent = 0.0
                self.dur = 0.0
                self.dur_percent = 0.0
                self.tss = 0.0
                self.tss_percent = 0.0
                self.high_dur = 0.0
                self.low_dur = 0.0
                self.high_dur_percent = 0.0
                self.low_dur_percent = 0.0
                self.zone_3 = 0.0
                self.zone_4 = 0.0
                self.zone_5 = 0.0
                self.zone_3_percent = 0.0
                self.zone_4_percent = 0.0
                self.zone_5_percent = 0.0

        self.bike = _Sport('Bike')
        self.run = _Sport('Run')
        self.swim = _Sport('Swim')

    plan = []
    total_tss = 0.0
    duration = 0.0
    high_dur = 0.0
    low_dur = 0.0
    high_pct = 0.0
    low_pct = 0.0

    score = 0.0
    tss_score = 0.0
    tgt_score = 0.0
    high_dur_percent, low_dur_percent = 0.0, 0.0
    run_dur_percent, bike_dur_percent, swim_dur_percent = 0.0, 0.0, 0.0
    run_tss_percent, bike_tss_percent, swim_tss_percent = 0.0, 0.0, 0.0
    run_high_dur_percent, run_low_dur_percent = 0.0, 0.0
    bike_high_dur_percent, bike_low_dur_percent = 0.0, 0.0
    swim_high_dur_percent, swim_low_dur_percent = 0.0, 0.0
    dur_tgt_score = 0.0
    tss_tgt_score = 0.0
    dur_split_tgt_score = 0.0
    high_split_tgt_score = 0.0

    class bike:
        name = 'Bike'
        tgt_percent = 0.0

        dur = 0.0
        dur_percent = 0.0

        tss = 0.0
        tss_percent = 0.0

        high_dur = 0.0
        low_dur = 0.0
        high_dur_percent = 0.0
        low_dur_percent = 0.0

        zone_3 = 0.0
        zone_4 = 0.0
        zone_5 = 0.0
        zone_3_percent = 0.0
        zone_4_percent = 0.0
        zone_5_percent = 0.0

    class run:
        name = 'Run'
        tgt_percent = 0.0

        dur = 0.0
        dur_percent = 0.0

        tss = 0.0
        tss_percent = 0.0

        high_dur = 0.0
        low_dur = 0.0
        high_dur_percent = 0.0
        low_dur_percent = 0.0

        zone_3 = 0.0
        zone_4 = 0.0
        zone_5 = 0.0
        zone_3_percent = 0.0
        zone_4_percent = 0.0
        zone_5_percent = 0.0

    class swim:
        name = 'Swim'
        tgt_percent = 0.0

        dur = 0.0
        dur_percent = 0.0

        tss = 0.0
        tss_percent = 0.0

        high_dur = 0.0
        low_dur = 0.0
        high_dur_percent = 0.0
        low_dur_percent = 0.0

        zone_3 = 0.0
        zone_4 = 0.0
        zone_5 = 0.0
        zone_3_percent = 0.0
        zone_4_percent = 0.0
        zone_5_percent = 0.0

