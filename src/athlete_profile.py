"""
Athlete Profile and Preferences Configuration
=============================================
Update this file with your personal physiological thresholds, starting fitness (CTL/ATL),
weekly schedule availability, pool/gym access, and target race parameters.

The Closed-Loop Triathlon Training Optimizer and multi-month season planners read
directly from this configuration file.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import numpy as np


@dataclass
class AthleteProfile:
    # -------------------------------------------------------------------------
    # 1. PERSONAL THRESHOLDS & BIOMETRICS (Calibrated Nov 2026)
    # -------------------------------------------------------------------------
    name: str = "driscoll42"
    age: int = 37                            # Age >= 45 automatically activates Masters Mode
    is_masters_athlete: bool = False         # Slower collagen remodeling tau=210d, extra recovery
    menstrual_tracking_enabled: bool = False # Normalizes HRV baseline during mid-luteal phase
    current_menstrual_phase: Optional[str] = None # 'follicular', 'ovulatory', 'luteal', or None

    # Performance Thresholds & Lifting 1RM
    # Calibrated from Garmin telemetry (Z2: 135-149 bpm) and Summer 2026 lifting restart (5@195 SQ, 5@180 DL, 5@140 BP, 5@105 OHP)
    # Historical pre-COVID peaks: Squat 5@335 (e1RM 391), Deadlift 5@335 (e1RM 391), Bench 2@305 (e1RM 325), OHP 5@190 (e1RM 222)
    # Single Source of Truth: Locked manual CP integer across Stryd, TrainingPeaks, and config (ADR-009)
    # PROVISIONAL (ADR-012 / DECISION-005): no CP test in months. Prior values were bike 150W (aero, Neo 2T)
    # and run 215W. Bike history: best 20-min power 156W (2025), 171W (2024); no structured riding since
    # 2026-01. Discounted ~10% (bike) and ~7% (run) for detraining; erring low keeps Z2 below LT1.
    # Retest per ADR-011 lockouts: Bike Two-Point CP at start of week 5, Run at the week-8 deload.
    thresholds_verified: bool = False        # Flip to True once Two-Point CP tests are applied
    bike_ftp: float = 135.0                  # Bike FTP / Critical Power (Watts; locked integer; AERO position)
    bike_cp: float = 135.0                   # Alias for Bike Critical Power (Watts)
    run_ftp: float = 200.0                   # Running Stryd Critical Power (Watts; locked integer)
    run_cp: float = 200.0                    # Alias for Run Critical Power (Watts)
    anaerobic_work_capacity_w_prime: float = 18000.0 # W' (Joules); high capacity from concurrent heavy squats/deadlifts
    # PROVISIONAL CSS (DECISION-001): last swim 2024-08. 2024 pool steady pace ~2:18-2:27/100m (moving time),
    # implying CSS ~2:12/100m then; +~10s/100m for >2 years out of the water => 2:30/100m = 40.0 m/min.
    # Replaces conflicting 24.0 m/min (4:10/100m) here and 35.29 m/min in classes.py. Run a 400/200 CSS test
    # in the 2nd weekend after pool access resumes.
    swim_css: float = 40.0                   # Critical Swim Speed (meters / min; 40.0 = 2:30 / 100m)
    swim_pool_length: str = "25y"            # Default pool unit ('25y', '25m', '50m'); confirm when pool access resumes
    aero_position_watts_delta: float = 15.0  # Upright road-hoods wattage is 3%-7% (10-20W) higher than aero CP
    easy_run_pace_min_per_mile: float = 14.5 # DECISION-015: re-entry easy pace (2026 median 15:08/mi @ HR 143) for mile->duration conversion
    barbell_1rm: Dict[str, float] = field(default_factory=lambda: {
        "squat": 210.0, "deadlift": 205.0, "bench": 155.0, "ohp": 115.0
    })
    dumbbell_cap_lbs: float = 75.0           # Maximum dumbbell weight available per hand (lbs)
    dumbbell_increment_lbs: float = 5.0      # DECISION-008: dumbbells 5-75 lb in 5 lb steps
    barbell_max_load_lbs: float = 400.0      # DECISION-008: plates 2.5-45 lb, stackable to ~400 lb total
    barbell_increment_lbs: float = 5.0       # Smallest jump = 2 x 2.5 lb plates
    has_trap_bar: bool = False               # Library rows prescribing Trap Bar DL must substitute conventional DL
    # DECISION-022 equipment inventory
    has_pullup_bar: bool = True
    has_nordic_anchor: bool = True
    has_resistance_bands: bool = False       # Banded face pulls/pull-aparts -> DB rear-delt fly / prone Y-T-W
    has_treadmill: bool = True
    allow_squat_deadlift_same_session: bool = True  # DECISION-008: heavy SQ + DL may share a session (HLM_BB_INT)
    strength_split_preference: str = "AUTO"  # 'AUTO', 'UL' (Upper/Lower), or 'HLM' (Texas Method)
    strength_priority: str = "COEQUAL"       # DECISION-009: strength is co-equal with triathlon
    recovery_week_strength_sessions: int = 1 # DECISION-009: >=1 maintenance (non-heavy) session in recovery/taper/race weeks
    # DECISION-006: sport priority when time or safety forces a trade-off (highest first)
    sport_priority: List[str] = field(default_factory=lambda: ["Strength", "Bike", "Run", "Swim"])
    # DECISION-006: Tier-1 relaxation order (relaxed FIRST -> LAST). Tier-0 safety caps are never relaxed.
    tier1_relaxation_order: List[str] = field(default_factory=lambda: [
        "reduce_weekly_tss",        # TSS is a soft outcome under hard time caps
        "drop_stride_count",
        "shorten_or_drop_anchor:Swim",
        "reduce_frequency:Swim",
        "shorten_or_drop_anchor:Run",
        "reduce_frequency:Run",
        "shorten_or_drop_anchor:Bike",
        "reduce_frequency:Bike",
        "reduce_rest_days_to_min_1",
        "reduce_heavy_strength_count_to_min_1",
    ])

    # -------------------------------------------------------------------------
    # 2. STARTING FITNESS & REHABILITATION STATUS
    # -------------------------------------------------------------------------
    # Grounded in Garmin 90-day activity logs (5 walks, 0 runs/rides in last 90 days).
    # DECISION-003 confirmed: last run 2026-07-13, last ride 2026-06-20, last swim 2024-08; no injury history.
    starting_ctl: float = 5.0                # Chronic Training Load (Fitness, ~42-day EWMA)
    starting_atl: float = 3.0                # Acute Training Load (Fatigue, ~7-day EWMA)
    initial_metabolic_state: float = 15.0    # Cardio / Mitochondrial convolution state (tau=35d)
    initial_connective_tissue_state: float = 20.0 # Tendon / Bone matrix convolution state (tau=120d)
    initial_running_acute_mech: float = 5.0  # Acute mechanical running load (tau=7d)
    initial_running_chronic_mech: float = 8.0 # Chronic mechanical running load (tau=28d)

    # Injury guard: 'HEALTHY', 'ACHILLES_REHAB', 'PATELLAR_GUARD', or 'RESTRICTED_RUN'
    orthopedic_status: str = "HEALTHY"

    # DECISION-014: running mechanical ACWR ceiling, tightened from 1.30. The ratio is unstable when chronic
    # load is near zero, so re-entry is also governed by absolute minutes (floor + max weekly increase).
    # NOTE: closed_loop_optimizer.py still hardcodes 1.30; wiring these is part of TASK-018.
    max_running_acwr_reentry: float = 1.20   # Applies while run CTL < reentry_run_ctl_exit
    max_running_acwr: float = 1.25           # Applies after re-entry
    reentry_run_ctl_exit: float = 15.0
    reentry_run_minutes_floor: float = 60.0  # Always-allowed weekly run/walk minutes (e.g. 3 x 20 min) regardless of ACWR
    max_weekly_run_minutes_increase: float = 15.0  # Absolute week-over-week cap on running minutes during re-entry
    # DECISION-023 (2026-11-15): during re-entry, 0 heavy lifts/week are allowed and the conservative axial
    # (65) / neuro (75) caps stay. Flip reentry_phase to False to restore phase defaults (ADR-012).
    reentry_phase: bool = True
    reentry_heavy_strength_sessions: int = 0
    default_run_surface: str = "outdoor"     # DECISION-012: runs outdoors; treadmill (owned) for ice, dark, intervals, walk-run

    # -------------------------------------------------------------------------
    # 3. TARGET RACE & SEASON GOALS
    # -------------------------------------------------------------------------
    # Options: 'olympic', '70.3', 'half_ironman', 'sprint', 'ironman'
    # DECISION-002: Olympic is the next target (no date yet); validate the optimizer on all distances.
    target_race_distance: str = "olympic"
    target_race_date: Optional[str] = None   # Recommended: late Aug 2027, so the 16-wk block starts with pool access (~May 2027)
    weeks_to_race: int = 16                  # Length of the race-specific periodized block
    constraints_review_date: str = "2027-05-15"  # DECISION-004/007: revisit time budget + swim access
    # DECISION-010: 3 build weeks + 1 recovery week
    mesocycle_build_weeks: int = 3
    mesocycle_recovery_weeks: int = 1
    base_weekly_tss: float = 160.0           # Conservative Re-Entry target weekly TSS (scalable via 3:1 step cycle)
    peak_weekly_tss: float = 380.0           # Peak target weekly TSS during Build 2 / Peak

    # -------------------------------------------------------------------------
    # 4. WEEKLY TIME BUDGET & CALENDAR AVAILABILITY (Minutes)
    # -------------------------------------------------------------------------
    # Index: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    # DECISION-004: 45-75 min/day is a real ceiling for at least 6 months (family constraint), not just re-entry.
    daily_time_caps: List[float] = field(default_factory=lambda: [
        45.0,   # Monday (Recovery / Mobility / Off)
        60.0,   # Tuesday (Foundation Bike / Easy Aerobic)
        45.0,   # Wednesday (Light Resistance / Walk)
        60.0,   # Thursday (Mid-week Quality / Strength)
        45.0,   # Friday (Easy Pre-weekend / Rest)
        75.0,   # Saturday (Foundation Endurance Ride)
        60.0,   # Sunday (Walk-Jog / Pool Swim)
    ])
    weekday_single_workout_cap: float = 60.0 # Max duration for any single workout on weekdays (= largest weekday cap)
    # DECISION-004: no AM/PM split days (>=6h apart) assumed. Set True on days that allow two windows.
    two_session_windows: List[bool] = field(default_factory=lambda: [False] * 7)
    # DECISION-021: within one window, a lift may be followed immediately by short, strictly easy cardio.
    allow_lift_cardio_combo: bool = True
    combo_cardio_max_minutes: float = 20.0   # Z1/Z2 only; lift first
    combo_cardio_sports_after_heavy_lower: List[str] = field(default_factory=lambda: ["Bike"])
    combo_cardio_sports_after_other_lift: List[str] = field(default_factory=lambda: ["Bike", "Run"])

    # -------------------------------------------------------------------------
    # 5. FACILITY & EQUIPMENT ACCESS (Mon through Sun)
    # -------------------------------------------------------------------------
    # Set to False on days where pool, bike trainer, or gym are unavailable.
    # DECISION-007: no pool for ~6 months; afterwards weekend-only (set Swim to Sat/Sun True, pool_open_days=[5, 6]).
    # DECISION-008: home rack/barbell available every day. DECISION-012: all rides on the Tacx Neo 2T.
    facility_access: Dict[str, List[bool]] = field(default_factory=lambda: {
        "Swim":     [False, False, False, False, False, False, False], # No pool access until ~2027-05
        "Bike":     [True,  True,  True,  True,  True,  True, True],   # Indoor smart trainer only
        "Run":      [True,  True,  True,  True,  True,  True, True],   # Outdoor (treadmill optional)
        "Strength": [True,  True,  True,  True,  True,  True, True],   # Home rack, every day
    })

    # -------------------------------------------------------------------------
    # 6. PHYSIOLOGICAL SAFETY CEILINGS & DISCIPLINE SPLITS
    # -------------------------------------------------------------------------
    max_48h_ans_cost: float = 450.0          # Rolling 48h Autonomic Nervous System fatigue ceiling
    max_weekly_run_mech_strain: float = 75.0 # Conservative peripheral eccentric running ceiling during re-entry
    max_single_run_vol_ratio: float = 0.32   # Single run <= 32% of weekly running duration
    enable_soft_48h_slacks: bool = True      # High-penalty slacks to prevent infeasibility

    # Target discipline duration splits (Swim / Bike / Run / Strength)
    # DECISION-006/009: Strength > Bike > Run > Swim while there is no pool access.
    target_sport_splits: Dict[str, float] = field(default_factory=lambda: {
        "Swim": 0.0, "Bike": 0.40, "Run": 0.25, "Strength": 0.35
    })
    target_bike_run_tss_splits: Dict[str, float] = field(default_factory=lambda: {
        "Bike": 0.70, "Run": 0.30
    })

    # Weekly session frequency bounds: (min_sessions, max_sessions)
    # With one session per day (no split windows) and 1 rest day, 6 sessions/week are possible.
    sport_freq_bounds: Dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "Swim": (0, 0), "Bike": (2, 3), "Run": (2, 3), "Strength": (2, 3)
    })
    min_rest_days: int = 1
    max_rest_days: int = 2

    # DECISION-013: canonical 80/20 rule (Fitzgerald, 80/20 Triathlon; Seiler). Time-in-zone over
    # endurance minutes only (strength excluded from the denominator).
    intensity_low_min: float = 0.78          # Tier-0 floor: Z1+Z2 share of endurance time
    intensity_low_target: float = 0.80       # Tier-2 soft target (0.85-0.90 during re-entry/base)
    intensity_mod_high_max: float = 0.20     # Z3+ZY+Z4+Z5 (moderate + high) combined ceiling
    intensity_zone_x_max: float = 0.05       # Zone X grey-zone ceiling (soft-penalized toward 0)
    key_sessions_per_week: tuple[int, int] = (2, 3)  # Structure rule; 0-1 during first 4 re-entry weeks

    # -------------------------------------------------------------------------
    # 7. DUAL-OBJECTIVE HYBRID ATHLETE TARGETS & EQUIPMENT GUARDRAILS
    # -------------------------------------------------------------------------
    target_sport_tss: Dict[str, float] = field(default_factory=lambda: {
        "Swim": 0.0, "Bike": 80.0, "Run": 35.0, "Strength": 20.0
    })
    target_movement_sets: Dict[str, float] = field(default_factory=lambda: {
        "Anterior_Chain": 4.0, "Posterior_Chain": 4.0, "Push": 5.0, "Pull": 5.0
    })
    min_barbell_set_share: float = 0.60          # >= 60% of weekly strength sets via Olympic Barbell
    max_daily_axial_load: float = 65.0           # Conservative Lumbar L1-S1 daily compressive/shear cap
    max_weekly_axial_load: float = 160.0         # Conservative Lumbar L1-S1 weekly compressive/shear cap
    max_daily_neuro_load: float = 75.0           # Daily central nervous system load cap
    pool_open_days: List[int] = field(default_factory=list)     # DECISION-007: none until ~2027-05, then [5, 6]
    outdoor_bike_days: List[int] = field(default_factory=list)  # DECISION-012: all rides indoor on the trainer

    # -------------------------------------------------------------------------
    # 8. TELEMETRY & INTEGRATIONS (DECISION-017..020)
    # -------------------------------------------------------------------------
    # One device per sport, no dual-recording (DECISION-018). Swim 2 retired.
    recording_device: Dict[str, str] = field(default_factory=lambda: {
        "Bike": "Edge 530", "Run": "Enduro 3", "Swim": "Enduro 3", "Strength": "Enduro 3"
    })
    completed_activity_source: str = "garmin_connect"  # DECISION-019: authoritative for completed activities (raw FIT)
    planned_workout_target: str = "trainingpeaks"      # TrainingPeaks holds the plan only
    trainingpeaks_push_mode: str = "dry_run"           # DECISION-020: 'dry_run' | 'approve' | 'auto'

    def heavy_strength_sessions(self, phase_required: int) -> int:
        """Heavy-strength sessions/week for a phase, capped during re-entry (DECISION-023)."""
        if self.reentry_phase:
            return min(int(phase_required), int(self.reentry_heavy_strength_sessions))
        return int(phase_required)

    def to_athlete_state(self):
        """Builds an AthletePhysiologicalState initialized with this profile."""
        from closed_loop_optimizer import AthletePhysiologicalState
        is_masters = self.is_masters_athlete or (self.age >= 45)
        return AthletePhysiologicalState(
            day_index=0,
            metabolic_fitness_state=self.initial_metabolic_state,
            connective_tissue_state=self.initial_connective_tissue_state,
            running_acute_mech=self.initial_running_acute_mech,
            running_chronic_mech=self.initial_running_chronic_mech,
            masters_mode=is_masters,
            total_ctl=self.starting_ctl,
            total_atl=self.starting_atl,
        )

    def to_mesocycle_config(self, phase_name: str = "base", tss_target: Optional[float] = None):
        """Constructs a MesoCycleTargetsAndConstraints tailored to this profile."""
        from closed_loop_optimizer import MesoCycleTargetsAndConstraints, ClosedLoopMPCController
        target_tss = tss_target or self.base_weekly_tss
        base_cfg = ClosedLoopMPCController.configure_mesocycle_phase(
            phase_name=phase_name,
            base_tss=target_tss,
            race_distance=self.target_race_distance,
        )

        # Apply personalized availability, facilities, and ceilings
        base_cfg.daily_time_caps = list(self.daily_time_caps)
        base_cfg.weekday_single_workout_cap = self.weekday_single_workout_cap
        base_cfg.facility_access = {k: list(v) for k, v in self.facility_access.items()}
        base_cfg.max_48h_ans_cost = self.max_48h_ans_cost
        base_cfg.max_weekly_run_mech_strain = self.max_weekly_run_mech_strain
        base_cfg.max_single_run_vol_ratio = self.max_single_run_vol_ratio
        base_cfg.enable_soft_48h_slacks = self.enable_soft_48h_slacks
        base_cfg.sport_freq_bounds = dict(self.sport_freq_bounds)
        # Phase-specific override: in taper, recovery, or race week, drop heavy strength but keep
        # maintenance sessions (DECISION-009: strength is co-equal, >=1 maintenance session).
        is_taper_or_recovery = any(x in phase_name.lower() for x in ("taper", "recovery", "deload", "race"))
        if is_taper_or_recovery:
            n_maint = self.recovery_week_strength_sessions
            base_cfg.sport_freq_bounds["Strength"] = (n_maint, n_maint)
            base_cfg.required_heavy_strength_sessions = 0

        base_cfg.min_rest_days = self.min_rest_days
        base_cfg.max_rest_days = self.max_rest_days
        base_cfg.required_heavy_strength_sessions = self.heavy_strength_sessions(base_cfg.required_heavy_strength_sessions)

        # Tier-0 spinal / neuromuscular ceilings and Tier-1 relaxation order (TASK-016/017)
        base_cfg.max_daily_axial_load = self.max_daily_axial_load
        base_cfg.max_weekly_axial_load = self.max_weekly_axial_load
        base_cfg.max_daily_neuro_load = self.max_daily_neuro_load
        base_cfg.tier1_relaxation_order = list(self.tier1_relaxation_order)

        # Orthopedic rehab adjustment
        if self.orthopedic_status.upper() in ("ACHILLES_REHAB", "RESTRICTED_RUN"):
            base_cfg.max_weekly_run_mech_strain = min(base_cfg.max_weekly_run_mech_strain, 180.0)
            base_cfg.target_bike_run_tss_splits = {"Bike": 0.72, "Run": 0.28}
            base_cfg.sport_freq_bounds["Run"] = (2, 3)

        return base_cfg

    def to_hybrid_config(self) -> Dict[str, Any]:
        """Constructs an optimizer configuration dictionary for HybridAthleteMILPOptimizer."""
        return {
            "daily_duration_caps": list(self.daily_time_caps),
            "pool_open_days": list(self.pool_open_days),
            "outdoor_bike_days": list(self.outdoor_bike_days),
            "target_tss": dict(self.target_sport_tss),
            "target_sets": dict(self.target_movement_sets),
            "long_run_cap_ratio": self.max_single_run_vol_ratio,
            "min_barbell_set_share": self.min_barbell_set_share,
            "p_low_min": self.intensity_low_min,
            "p_high_max": self.intensity_mod_high_max,
            "max_weekly_axial": self.max_weekly_axial_load,
            "max_daily_axial": self.max_daily_axial_load,
            "max_daily_neuro": self.max_daily_neuro_load,
            # Tier-0 / Tier-1 inputs for the shared safety layer (TASK-016/017)
            "weekday_single_workout_cap": self.weekday_single_workout_cap,
            "max_48h_ans_cost": self.max_48h_ans_cost,
            "sport_freq_bounds": dict(self.sport_freq_bounds),
            "min_rest_days": self.min_rest_days,
            "max_rest_days": self.max_rest_days,
            "tier1_relaxation_order": list(self.tier1_relaxation_order),
            "readiness_alpha": [1.0] * 7,
            "weights": {
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
            },
        }


    def calculate_two_point_cp(self, p3: float, p12: float) -> Dict[str, float]:
        """Calculates Critical Power (CP, Watts) and Anaerobic Work Capacity (W', Joules)

        from a 3-minute (180s) and 12-minute (720s) all-out test (ADR-009 / SPEC.md).
        CP = (4 * P12 - P3) / 3
        W' = 180 * (P3 - CP)
        """
        cp = (4.0 * p12 - p3) / 3.0
        w_prime = 180.0 * (p3 - cp)
        return {"critical_power": cp, "w_prime_joules": w_prime}

    def calculate_css(self, t400_sec: float, t200_sec: float) -> Dict[str, float]:
        """Calculates Critical Swim Speed (CSS, m/s or yd/s) and pace per 100 from

        400m and 200m time trials (ADR-009 / SPEC.md).
        CSS = 200 / (t400 - t200)
        Pace100 = (t400 - t200) / 2
        """
        delta_t = t400_sec - t200_sec
        if delta_t <= 0:
            raise ValueError("t400_sec must be strictly greater than t200_sec")
        css = 200.0 / delta_t
        pace_100_sec = delta_t / 2.0
        return {"css_speed": css, "pace_100_sec": pace_100_sec, "css_m_per_min": css * 60.0}

    def apply_cp_retest_result(
        self,
        sport: str,
        p3: float,
        p12: float,
        auto_lock: bool = True,
        max_jump_pct: float = 0.10,
    ) -> Dict[str, Any]:
        """Validates and applies Two-Point Critical Power test results (ADR-009 / ADR-011).

        Guards against spurious jumps (>10%) and updates the canonical 80/20 7-zone scale.
        """
        sport = sport.lower().strip()
        if p3 <= p12 or p12 <= 0:
            raise ValueError(f"Invalid CP test inputs: P3 ({p3}W) must be strictly greater than P12 ({p12}W) > 0")

        res = self.calculate_two_point_cp(p3=p3, p12=p12)
        raw_cp = res["critical_power"]
        raw_w_prime = res["w_prime_joules"]

        prev_cp = self.run_ftp if sport in ("run", "running") else self.bike_ftp
        pct_change = (raw_cp - prev_cp) / prev_cp if prev_cp > 0 else 0.0

        # Guard: Runaway jump protection
        effective_cp = raw_cp
        warning = None
        if pct_change > max_jump_pct:
            effective_cp = round(prev_cp * (1.0 + max_jump_pct), 1)
            warning = (
                f"CP jump of +{pct_change*100:.1f}% exceeded safety ceiling (+{max_jump_pct*100:.1f}%). "
                f"Capped at {effective_cp:.1f}W to prevent overreaching in subsequent training blocks."
            )
        elif pct_change < -0.15:
            warning = f"Significant CP decrease of {pct_change*100:.1f}% detected ({prev_cp:.1f}W -> {raw_cp:.1f}W). Check recovery/fatigue."

        locked_int_cp = int(round(effective_cp))

        if auto_lock:
            if sport in ("run", "running"):
                self.run_ftp = float(locked_int_cp)
                self.run_cp = float(locked_int_cp)
            elif sport in ("bike", "cycling"):
                self.bike_ftp = float(locked_int_cp)
                self.bike_cp = float(locked_int_cp)
            else:
                raise ValueError(f"Unsupported sport: {sport}")
            self.anaerobic_work_capacity_w_prime = round(raw_w_prime, 0)

        updated_zones = self.get_8020_7zones(sport)

        return {
            "sport": sport,
            "raw_cp": round(raw_cp, 1),
            "effective_locked_cp": locked_int_cp,
            "prev_cp": prev_cp,
            "pct_change": round(pct_change * 100.0, 1),
            "w_prime_joules": round(raw_w_prime, 0),
            "warning": warning,
            "updated_zones": updated_zones,
        }

    def get_8020_7zones(self, sport: str = "run") -> Dict[str, Dict[str, Any]]:
        """Returns the canonical 80/20 7-zone power boundaries and ANS cost weights

        anchored to verified Critical Power (SPEC.md / ADR-009).
        """
        sport = sport.lower()
        if sport in ("run", "running"):
            cp = self.run_ftp
            pcts = {
                "Z1": (0.50, 0.76, "Active Recovery / Low Aerobic", 0.0),
                "Z2": (0.76, 0.88, "Aerobic Endurance (Ceiling = LT1)", 0.0),
                "ZX": (0.88, 0.94, "Moderate Drift Grey Zone (Penalized)", 0.7),
                "Z3": (0.94, 1.00, "Sub-Threshold / Tempo (Ceiling = LT2)", 1.0),
                "ZY": (1.00, 1.03, "Threshold Over-Reach Gap (Penalized)", 1.4),
                "Z4": (1.03, 1.20, "Supra-Threshold / VO2max", 1.8),
                "Z5": (1.20, 2.00, "Anaerobic / Repetition (W' Depletion)", 3.5),
            }
        elif sport in ("bike", "cycling"):
            cp = self.bike_ftp
            pcts = {
                "Z1": (0.50, 0.70, "Active Recovery / Low Aerobic", 0.0),
                "Z2": (0.70, 0.83, "Aerobic Endurance (Ceiling = LT1)", 0.0),
                "ZX": (0.83, 0.91, "Moderate Drift Grey Zone (Penalized)", 0.7),
                "Z3": (0.91, 1.00, "Sub-Threshold / Tempo (Ceiling = LT2)", 1.0),
                "ZY": (1.00, 1.02, "Threshold Over-Reach Gap (Penalized)", 1.4),
                "Z4": (1.02, 1.10, "Supra-Threshold / VO2max", 1.8),
                "Z5": (1.10, 2.00, "Anaerobic / Repetition (W' Depletion)", 3.5),
            }
        else:
            raise ValueError(f"Unsupported sport for 7-zone power: {sport}")

        zones = {}
        for z, (low_pct, high_pct, role, ans_cost) in pcts.items():
            zones[z] = {
                "role": role,
                "pct_low": low_pct,
                "pct_high": high_pct,
                "watts_low": round(cp * low_pct, 1),
                "watts_high": round(cp * high_pct, 1),
                "ans_cost_weight": ans_cost,
            }
        return zones


# Default active athlete instance
CURRENT_ATHLETE = AthleteProfile()
