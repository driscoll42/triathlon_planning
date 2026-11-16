"""High-level season pipeline: plan formatting and closed-loop season schedule generation."""

from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import logging


from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.control.mpc import ClosedLoopMPCController
from triplan.library.candidates import build_default_candidate_library
from triplan.physiology.state import AthletePhysiologicalState
from triplan.solver.scheduler import TriathlonMILPScheduler

logger = logging.getLogger(__name__)


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
            from triplan.physiology.athlete_profile import CURRENT_ATHLETE
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
        if profile is not None:
            # Profile governs strength: re-entry cap (DECISION-023) and maintenance sessions (DECISION-009)
            cfg.required_heavy_strength_sessions = profile.heavy_strength_sessions(req_strength)
        else:
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
