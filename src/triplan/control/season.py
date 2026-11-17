"""High-level season pipeline: plan formatting and closed-loop season schedule generation."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import copy
import logging


from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.control.mpc import ClosedLoopMPCController
from triplan.library.candidates import build_default_candidate_library
from triplan.solver.safety_constraints import run_minutes as run_minutes_of, run_strain
from triplan.physiology.state import AthletePhysiologicalState
from triplan.control.carry import initial_carry_in, next_week_carry_in
from triplan.control.periodization import (REENTRY_RELIEF_LEVELS, REENTRY_RELIEF_ROLES, STRUCTURE_OVERSHOOT_TOL,
                                            FLOOR_PROBE_TARGET, FLOOR_RAISE_ROLES, apply_deload_relief, apply_reentry_relief, raised_target, relax_deload_acwr_floor, relax_deload_anchors, drop_unhostable_long_sessions, build_season_matrix, filter_test_workouts, normalize_distance)
from triplan.control.feasibility import attribute_single_run_week, peak_48h, run_session_count, season_infeasibility_report
from triplan.solver.scheduler import TriathlonMILPScheduler

logger = logging.getLogger(__name__)


@dataclass
class WeekRecord:
    """Everything an independent checker needs about one solved week (TASK-027): the schedule, the limits the
    week was solved under, the carry-in it consumed and the solver's own relaxation / diagnostic reports."""
    week_num: int
    phase: str
    step_type: str
    role: str
    block: str
    target_tss: float
    status: Optional[str]
    solved: bool
    schedule: Dict[int, List[Any]]
    limits: Any = None          # SafetyLimits the week was solved under
    cfg: Any = None             # normalized MesoCycleTargetsAndConstraints actually solved
    carry: Any = None           # WeekCarryIn consumed by this week's MILP
    relaxations: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)


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


def _schedulable_heavy_lifts(lib: List[CandidateWorkout], cfg: MesoCycleTargetsAndConstraints) -> int:
    """Heavy-strength candidates that individually fit the Tier-0 daily axial / neuromuscular caps."""
    return sum(1 for w in lib if w.is_heavy_strength
               and w.axial_load <= cfg.max_daily_axial_load and w.neuromuscular_load <= cfg.max_daily_neuro_load)


def generate_closed_loop_season_schedule(
    profile: Optional[Any] = None,
    distance: str = "olympic",
    base_tss: Optional[float] = None,
    library: Optional[List[CandidateWorkout]] = None,
    time_limit_per_week: float = 3.5,
    perturbations: Optional[Dict[int, Dict[str, Any]]] = None,
    weeks_to_race: Optional[int] = None,
    reentry_weeks: Optional[int] = None,
    collect: Optional[List["WeekRecord"]] = None,
    diagnose_single_run: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generates a full periodized multi-month triathlon season plan (Olympic or 70.3)
    driven by the 3-Tier Closed-Loop Model Predictive Control (MPC) engine.

    Tier 3: 3:1 step-cycles, volume/intensity progression, durability scaling, fast-decay taper.
    Tier 2: Receding-horizon adaptive feedback monitoring Banister PMC (CTL/ATL/TSB),
            connective tissue remodeling (M(t)/C(t) ODI), and running mechanical EWMA ACWR.
    Tier 1: Perturbation support (illness, pool closure, sudden travel).

    ``collect``: optional list that receives one ``WeekRecord`` per week (schedule, limits, carry-in) for
    independent validation (``triplan.perf.season_validation``). The season-level infeasibility diagnosis
    (TASK-027) is attached as ``summary.attrs["infeasibility"]`` and printed.
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

    # 3:1 periodization matrix (TASK-026): generated from the profile's mesocycle shape, the race
    # distance, weeks_to_race and the optional re-entry block (ADR-012 DECISION-010 / DECISION-023).
    build_w = getattr(profile, "mesocycle_build_weeks", 3)
    recovery_w = getattr(profile, "mesocycle_recovery_weeks", 1)
    if reentry_weeks is None:
        reentry_weeks = getattr(profile, "reentry_weeks", 0)
    if weeks_to_race is None and profile is not None and normalize_distance(profile.target_race_distance) == normalize_distance(dist):
        weeks_to_race = profile.weeks_to_race
    season_matrix = build_season_matrix(dist, weeks_to_race, build_w, recovery_w, reentry_weeks)
    maint_sessions = getattr(profile, "recovery_week_strength_sessions", 1)   # DECISION-009
    test_rules = dict(
        reentry_weeks=reentry_weeks,
        bike_lockout_weeks=getattr(profile, "reentry_bike_test_lockout_weeks", 4),
        run_lockout_weeks=getattr(profile, "reentry_run_test_lockout_weeks", 6),
        min_gap_weeks=getattr(profile, "cp_test_min_gap_weeks", 8),
    )

    # Initialize Controller and Physiological State (seeded from the profile incl. run CTL, TASK-018)
    athlete_state = profile.to_athlete_state() if profile else AthletePhysiologicalState()
    mpc = ClosedLoopMPCController(athlete_state)
    # Week-to-week hand-off (TASK-018, ADR-019): boundary day loads, EWMA ACWR / CTL state, ADR-011 test weeks.
    carry = initial_carry_in(athlete_state, profile, lib)

    all_daily_rows = []
    season_summary_rows = []

    print(f"\n================================================================================")
    print(f"  LAUNCHING CLOSED-LOOP {dist.upper()} SEASON PLANNER ({len(season_matrix)} WEEKS)")
    print(f"  Starting CTL: {athlete_state.total_ctl:.1f} | ATL: {athlete_state.total_atl:.1f} | Initial ODI: {athlete_state.orthopedic_decoupling_index:.2f}")
    print(f"================================================================================")

    for wk in season_matrix:
        week_num, phase_name, step_type, tss_factor, durability_w, req_strength = wk.as_tuple()
        in_reentry = wk.block == "reentry"
        target_tss = round(base_tss * tss_factor, 0)

        # 1. Tier 3 Mesocycle Configuration
        if profile:
            cfg = profile.to_mesocycle_config(phase_name=wk.config_phase, tss_target=target_tss)
        else:
            cfg = ClosedLoopMPCController.configure_mesocycle_phase(
                phase_name=wk.config_phase,
                base_tss=target_tss,
                race_distance=dist,
            )
        cfg.target_weekly_tss = target_tss   # the matrix is authoritative (phase defaults re-scale TSS)
        cfg.w_durability = durability_w
        # Strength: heavy lifts follow the phase, capped by the profile's re-entry rule (DECISION-023) while
        # in the re-entry block (or when no re-entry block is planned, the legacy always-capped behavior).
        if profile is not None and (in_reentry or reentry_weeks == 0):
            cfg.required_heavy_strength_sessions = profile.heavy_strength_sessions(req_strength)
        else:
            cfg.required_heavy_strength_sessions = req_strength
        # Tier-0 axial/neuromuscular caps are never relaxed: if the library has fewer heavy lifts under the
        # profile's daily caps than the phase asks for, request only what can be scheduled and say so.
        n_heavy_ok = _schedulable_heavy_lifts(lib, cfg)
        heavy_note = None
        if cfg.required_heavy_strength_sessions > n_heavy_ok:
            heavy_note = (f"HEAVY_STRENGTH_LIMITED ({cfg.required_heavy_strength_sessions}->{n_heavy_ok}: "
                          f"library heavy lifts exceed daily axial/neuro caps)")
            cfg.required_heavy_strength_sessions = n_heavy_ok
        if req_strength == 0:
            # Recovery / taper / race weeks keep maintenance (non-heavy) lifting, never 0 (DECISION-009).
            cfg.sport_freq_bounds["Strength"] = (maint_sessions, maint_sessions)
        # Deload / taper / race weeks scale the full-week structure minimums down (TASK-034); caps untouched.
        apply_deload_relief(cfg, wk.role)
        if in_reentry:
            # General prep: strength + bike + run only. No swim, even if the pool were open.
            cfg.sport_freq_bounds["Swim"] = (0, 0)
            cfg.facility_access["Swim"] = [False] * 7

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

        # Safety bound: ODI > 1.35 (1.20 in masters mode). The running-ACWR ceiling (1.20 during re-entry, 1.25
        # after; ADR-012) is no longer a post-hoc shrink of the strain cap: it is a per-day linear row inside the
        # week's MILP, fed by `carry` (TASK-018). ODI has no in-model form yet (TASK-022), so it stays here.
        odi_limit = 1.20 if athlete_state.masters_mode else 1.35
        acwr_cap, acwr_eps = carry.load.acwr_max, carry.load.acwr_eps

        if pre_odi > odi_limit:
            recal_action = "SAFETY_RECALIBRATION_APPLIED"
            # Throttling mechanical run strain ceiling and shifting TSS to bike
            scale_down = odi_limit / pre_odi
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
        # ADR-011 lockouts: forbidden CP/CSS tests are removed from the candidates BEFORE the solve.
        week_lib = filter_test_workouts(lib, wk, carry.last_test_week, **test_rules)
        week_lib = relax_deload_anchors(week_lib, wk.role)   # TASK-038: no keystone obligation in deload weeks
        week_lib = drop_unhostable_long_sessions(week_lib, cfg.daily_time_caps)   # TASK-040: nothing longer than any day cap
        solve_carry = relax_deload_acwr_floor(carry, wk.role)   # TASK-038
        scheduler = TriathlonMILPScheduler(week_lib, cfg, carry_in=solve_carry)
        plans = scheduler.solve_top_k_plans(k_plans=1, time_limit=time_limit_per_week)
        # TASK-037: a re-entry build week whose full-week structure floor overshoots its target by more than
        # STRUCTURE_OVERSHOOT_TOL is re-solved with the structure minimums scaled down (never the Tier-0 caps).
        structure_note = None
        if in_reentry and wk.role in REENTRY_RELIEF_ROLES:
            for level in REENTRY_RELIEF_LEVELS:
                if not plans or plans[0]["total_tss"] <= STRUCTURE_OVERSHOOT_TOL * cfg.target_weekly_tss:
                    break
                cfg_l = copy.deepcopy(cfg)
                note = apply_reentry_relief(cfg_l, level)
                sched_l = TriathlonMILPScheduler(week_lib, cfg_l, carry_in=solve_carry)
                plans_l = sched_l.solve_top_k_plans(k_plans=1, time_limit=time_limit_per_week)
                if plans_l and plans_l[0]["total_tss"] < plans[0]["total_tss"]:
                    scheduler, plans, structure_note = sched_l, plans_l, note

        # TASK-041 / ADR-025: the owner's weekly structure is the plan. A race-block base / build / overload week whose
        # first solve overshoots its target by more than STRUCTURE_OVERSHOOT_TOL has a structural floor above the matrix
        # target: probe the floor (cheapest plan meeting Tier-0 + the full structure, a ~0 TSS goal), raise the target
        # to it and re-solve there. No session is dropped; the hard CTL ramp / ACWR / 48h rows still bind in the solve.
        struct_floor, matrix_target = None, target_tss
        if (wk.block == "race" and wk.role in FLOOR_RAISE_ROLES and pert_info is None and plans
                and plans[0]["total_tss"] > STRUCTURE_OVERSHOOT_TOL * cfg.target_weekly_tss):
            cfg_f = copy.deepcopy(cfg)
            cfg_f.target_weekly_tss = FLOOR_PROBE_TARGET
            plans_f = TriathlonMILPScheduler(week_lib, cfg_f, carry_in=solve_carry).solve_top_k_plans(
                k_plans=1, time_limit=time_limit_per_week)
            struct_floor = round(min([plans[0]["total_tss"]] + [p_["total_tss"] for p_ in plans_f]), 1)
            new_target = raised_target(target_tss, struct_floor)
            if new_target > target_tss:
                cfg.target_weekly_tss = target_tss = new_target
                sched_r = TriathlonMILPScheduler(week_lib, cfg, carry_in=solve_carry)
                plans_r = sched_r.solve_top_k_plans(k_plans=1, time_limit=time_limit_per_week)
                if plans_r:
                    scheduler, plans = sched_r, plans_r
                structure_note = (f"TARGET_RAISED_TO_STRUCTURE ({matrix_target:.0f}->{target_tss:.0f}, "
                                  f"structural floor {struct_floor:.0f})")

        plan = plans[0] if plans else None
        solved_ok = plan is not None
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
        if collect is not None:
            collect.append(WeekRecord(
                week_num=week_num, phase=phase_name, step_type=step_type, role=wk.role, block=wk.block,
                target_tss=target_tss, status=plan.get("status"), solved=solved_ok,
                schedule={d: list(plan["schedule"][d]) for d in range(7)}, limits=scheduler.limits,
                cfg=scheduler.cfg, carry=carry, relaxations=list(plan.get("relaxations", [])),
                diagnostics=list(plan.get("diagnostics", []))))
        if plan:
            for day_wkts in plan["schedule"].values():
                for w in day_wkts:
                    if w.flags.get("test"):
                        carry.last_test_week[w.sport] = week_num
            # Advance physiological states day by day
            max_acwr, max_excess = 0.0, -1e9
            for d in range(7):
                wkts = plan["schedule"][d]
                d_aerobic = sum(w.aerobic_tss for w in wkts)
                d_mech = sum(run_strain(w) for w in wkts)   # run leg of a lift + run combo included
                d_bike = sum(w.aerobic_tss for w in wkts if w.sport == "Bike")
                d_run = sum(w.aerobic_tss for w in wkts if w.sport == "Run")
                d_swim = sum(w.aerobic_tss for w in wkts if w.sport == "Swim")
                athlete_state.step_day(d_aerobic, d_mech, d_bike, d_run, d_swim)
                max_acwr = max(max_acwr, athlete_state.running_mechanical_acwr)
                max_excess = max(max_excess, athlete_state.running_acute_mech - acwr_cap * athlete_state.running_chronic_mech)
            run_minutes = float(sum(run_minutes_of(w) for d in range(7) for w in plan["schedule"][d]))
            # TASK-046: a one-run week is attributed to its binding row(s) by relaxing each in a scratch model
            # (only these weeks are re-solved, ~1 s per candidate row).
            single_run_binding = ""
            if diagnose_single_run and solved_ok and run_session_count(plan["schedule"]) == 1:
                single_run_binding = attribute_single_run_week(week_lib, copy.deepcopy(scheduler.cfg), solve_carry)["label"]
            carry = next_week_carry_in(carry, plan["schedule"], athlete_state, profile, lib, plan["total_tss"])

            if any(plan["schedule"][d] for d in range(7)):
                df_week = format_plan_to_dataframe(plan, week_num=week_num, phase_name=phase_name)
                all_daily_rows.append(df_week)

            tot_ans = plan.get("total_ans_cost", 0.0)
            tot_durability = sum(w.durability_score for day_wkts in plan["schedule"].values() for w in day_wkts)

            season_summary_rows.append({
                "Week": week_num,
                "Phase": phase_name,
                "Block": wk.block,
                "Step_Type": step_type,
                "Target_TSS": target_tss,
                "Matrix_Target_TSS": matrix_target,      # TASK-041: before the structural-floor raise
                "StructFloor": struct_floor,             # TASK-041: cheapest plan meeting the full structure (None = not probed)
                "Solved_TSS": round(plan["total_tss"], 1),
                "Total_Mech_Strain": round(plan["total_mech_strain"], 1),
                "Total_ANS_Cost": round(tot_ans, 1),
                "Durability": round(tot_durability, 1),
                "Long_Run_Ratio": round(plan["max_single_run_ratio"], 3),
                **{f"Max_48h_{k}": round(v, 1) for k, v in peak_48h(plan["schedule"]).items()},   # TASK-040
                "Ending_CTL": round(athlete_state.total_ctl, 1),
                "Ending_ATL": round(athlete_state.total_atl, 1),
                "TSB": round(athlete_state.total_tsb, 1),
                "Ending_ODI": round(athlete_state.orthopedic_decoupling_index, 3),
                "Ending_ACWR": round(athlete_state.running_mechanical_acwr, 3),
                "Max_Run_ACWR": round(max_acwr, 3),
                "Run_ACWR_Cap": round(acwr_cap, 3),
                "Run_ACWR_Excess": round(max_excess, 3),     # max_d (A_d - cap * C_d); the in-model row bounds it by eps
                "Run_ACWR_Eps": round(acwr_eps, 3),
                "Run_Minutes": run_minutes,
                "Single_Run_Binding": single_run_binding,    # TASK-046: "" unless the week has exactly one run
                "Run_CTL": round(athlete_state.run_ctl, 1),
                "Recalibration": " + ".join(x for x in (recal_action, pert_info, heavy_note, structure_note) if x),
                "Solve_Status": plan.get("status"),
                "MIP_Gap": plan.get("mip_gap"),
                "Relaxations": "; ".join(r["message"] for r in plan.get("relaxations", [])),
                "Diagnostics": "; ".join(plan.get("diagnostics", [])),
            })

            print(f"Week {week_num:02d} [{phase_name:<20} | {step_type:<22}]: TSS={plan['total_tss']:>5.1f} | Mech={plan['total_mech_strain']:>5.1f} | ANS={tot_ans:>6.1f} | CTL={athlete_state.total_ctl:>4.1f} | TSB={athlete_state.total_tsb:>5.1f} | ODI={athlete_state.orthopedic_decoupling_index:.2f} ({recal_action})")

    daily_season_df = pd.concat(all_daily_rows, ignore_index=True) if all_daily_rows else pd.DataFrame()
    summary_season_df = pd.DataFrame(season_summary_rows)
    infeasibility = season_infeasibility_report(summary_season_df, profile, dist, lib) if profile is not None else []
    summary_season_df.attrs["infeasibility"] = infeasibility
    if infeasibility:
        print("\nSEASON INFEASIBILITY DIAGNOSIS:")
        for m in infeasibility:
            print(f"  - {m}")
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
