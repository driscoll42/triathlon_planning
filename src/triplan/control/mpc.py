"""Three-tier closed-loop MPC controller (Tier 1 readiness, Tier 2 recalibration, Tier 3 mesocycle)."""

from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import copy
import logging


from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.physiology.state import AthletePhysiologicalState

logger = logging.getLogger(__name__)


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

        Since TASK-018 (ADR-019) the planned running ACWR is enforced inside each week's MILP, so this is the
        backstop for REALIZED (actuals) data, where execution can deviate from a plan that satisfied the rows.
        """
        for r in weekly_daily_records:
            self.state.step_day(
                daily_aerobic_tss=r.get("tss", 0.0),
                daily_run_mech=r.get("run_mech", 0.0),
                daily_bike_tss=r.get("bike_tss", 0.0),
                daily_run_tss=r.get("run_tss", 0.0),
                daily_swim_tss=r.get("swim_tss", 0.0),
            )

        updated_config = copy.deepcopy(base_meso_config)  # never mutate the caller's config (TASK-013)
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

