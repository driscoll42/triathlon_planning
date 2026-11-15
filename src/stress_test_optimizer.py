"""
Stress Testing & Scenario Analysis Suite for Closed-Loop Triathlon Training Optimizer.
Evaluates model robustness, edge cases, combinatorial scalability, and failure boundaries.
"""

import os
import sys

_src_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_src_dir)
for _p in [_src_dir, _root_dir]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import time
from dataclasses import replace
import pandas as pd
import numpy as np

from closed_loop_optimizer import (
    CandidateWorkout,
    MesoCycleTargetsAndConstraints,
    TriathlonMILPScheduler,
    ClosedLoopMPCController,
    AthletePhysiologicalState,
    build_default_candidate_library,
    build_hybrid_candidate_library,
    HybridAthleteMILPOptimizer,
    enrich_workouts_from_df,
    format_plan_to_dataframe,
)
from util import load_workouts
from classes import triVariables as triv


def print_header(title: str):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def run_scenario(name: str, library: list, cfg: MesoCycleTargetsAndConstraints, k_plans: int = 1, time_limit: float = 10.0):
    print(f"\n--- Running Scenario: {name} ---")
    t0 = time.time()
    sched = TriathlonMILPScheduler(library, cfg)
    try:
        plans = sched.solve_top_k_plans(k_plans=k_plans, time_limit=time_limit)
        dur = time.time() - t0
        if not plans:
            print(f"RESULT: FAILED / INFEASIBLE (No plans found in {dur:.2f}s)")
            return {"status": "INFEASIBLE", "duration": dur, "plans": []}
        else:
            p = plans[0]
            slack_info = p.get('soft_slack_violations', {})
            slack_str = f" | Soft Slack Violations: {slack_info}" if slack_info else " | Strict Constraints Met"
            print(f"RESULT: SUCCESS (Solved in {dur:.2f}s | Obj: {p['objective']:.2f} | TSS: {p['total_tss']:.1f} | MechStrain: {p['total_mech_strain']:.1f} | LongRun: {p['max_single_run_ratio']*100:.1f}%{slack_str})")
            return {"status": "OPTIMAL", "duration": dur, "plans": plans}
    except Exception as e:
        dur = time.time() - t0
        print(f"RESULT: ERROR ({e}) in {dur:.2f}s")
        return {"status": "ERROR", "error": str(e), "duration": dur, "plans": []}


def stress_test_suite():
    results = {}
    default_lib = build_default_candidate_library()

    print_header("CLOSED-LOOP TRIATHLON TRAINING OPTIMIZER STRESS TEST SUITE")

    # -------------------------------------------------------------------------
    # Scenario 1: Baseline Nominal
    # -------------------------------------------------------------------------
    cfg_base = MesoCycleTargetsAndConstraints(
        target_weekly_tss=540.0,
        max_48h_ans_cost=700.0,
    )
    res1 = run_scenario("1. Baseline Nominal (540 TSS, Balanced 7-day)", default_lib, cfg_base, k_plans=3)
    results["1_baseline"] = res1

    # -------------------------------------------------------------------------
    # Scenario 2: The 650 vs 700 ANS Boundary & Soft Slack Behavior
    # -------------------------------------------------------------------------
    cfg_strict_650 = MesoCycleTargetsAndConstraints(
        target_weekly_tss=540.0,
        max_48h_ans_cost=650.0,
        enable_soft_48h_slacks=False, # STRICT HARD BOUND
    )
    res2_hard = run_scenario("2a. Strict Hard Cap 650 ANS (No Slacks)", default_lib, cfg_strict_650)
    results["2a_strict_650"] = res2_hard

    cfg_soft_650 = MesoCycleTargetsAndConstraints(
        target_weekly_tss=540.0,
        max_48h_ans_cost=650.0,
        enable_soft_48h_slacks=True, # SOFT PENALIZED SLACKS
    )
    res2_soft = run_scenario("2b. Soft Cap 650 ANS (With High-Penalty Slacks)", default_lib, cfg_soft_650)
    results["2b_soft_650"] = res2_soft

    # -------------------------------------------------------------------------
    # Scenario 3: Severe Time Starvation (Executive Schedule)
    # Weekdays: 45m cap. Weekend: 90m cap.
    # Note: Default anchor BIKE_DUR_OUT is 180m, which exceeds 90m!
    # -------------------------------------------------------------------------
    # Let's see what happens if anchor duration exceeds weekend cap:
    cfg_time_starved = MesoCycleTargetsAndConstraints(
        daily_time_caps=[45.0, 45.0, 45.0, 45.0, 45.0, 90.0, 90.0],
        weekday_single_workout_cap=45.0,
        target_weekly_tss=300.0,
        nominal_weekly_duration=360.0,
        max_48h_ans_cost=700.0,
    )
    res3 = run_scenario("3. Severe Time Starvation with 180m Anchor in Library", default_lib, cfg_time_starved)
    results["3_time_starvation_anchor"] = res3

    # Now remove the 180m anchor and test realistic time-starved library:
    lib_time_starved = [w for w in default_lib if w.duration <= 90]
    # Remove anchor tag on 75m run so it's not strictly forced if volume doesn't permit
    lib_time_starved_no_anchors = [
        CandidateWorkout(
            w.id, w.code, w.name, w.sport, w.duration, w.aerobic_tss, w.mechanical_strain,
            w.ans_cost, w.durability_score, w.zone_durations, w.is_hard_endurance,
            w.is_heavy_run, w.is_heavy_strength, False, w.has_strides, w.is_indoor_bike, w.preference_penalty
        ) for w in lib_time_starved
    ]
    cfg_time_starved_realistic = MesoCycleTargetsAndConstraints(
        daily_time_caps=[45.0, 45.0, 45.0, 45.0, 45.0, 90.0, 90.0],
        weekday_single_workout_cap=45.0,
        target_weekly_tss=280.0,
        nominal_weekly_duration=320.0,
        max_48h_ans_cost=700.0,
        sport_freq_bounds={"Swim": (1, 2), "Bike": (2, 3), "Run": (3, 4), "Strength": (1, 1)},
    )
    res3_real = run_scenario("3b. Realistic Time Starvation (Capped <=90m workouts)", lib_time_starved_no_anchors, cfg_time_starved_realistic)
    results["3b_time_starvation_realistic"] = res3_real

    # -------------------------------------------------------------------------
    # Scenario 4: High-Volume Ironman Athlete
    # TSS 800, weekend windows up to 360m (6 hours)
    # -------------------------------------------------------------------------
    cfg_ironman = MesoCycleTargetsAndConstraints(
        daily_time_caps=[75.0, 120.0, 75.0, 120.0, 75.0, 360.0, 300.0],
        weekday_single_workout_cap=75.0,
        target_weekly_tss=750.0,
        nominal_weekly_duration=900.0,
        max_48h_aerobic_tss=420.0,
        max_48h_ans_cost=950.0,
        max_48h_mech_strain=380.0,
        max_weekly_run_mech_strain=420.0,
        sport_freq_bounds={"Swim": (2, 3), "Bike": (3, 5), "Run": (4, 5), "Strength": (1, 2)},
    )
    res4 = run_scenario("4. High-Volume Ironman (750 TSS, 15+ Hours)", default_lib, cfg_ironman)
    results["4_ironman"] = res4

    # -------------------------------------------------------------------------
    # Scenario 5: Orthopedically Compromised Runner (Achilles / Bone Stress Guard)
    # Tight run strain cap = 160.0, single run cap = 28%
    # -------------------------------------------------------------------------
    cfg_rehab = MesoCycleTargetsAndConstraints(
        target_weekly_tss=480.0,
        max_weekly_run_mech_strain=160.0,   # Very low mechanical ceiling for run
        max_single_run_vol_ratio=0.28,
        target_sport_duration_splits={"Swim": 0.22, "Bike": 0.55, "Run": 0.18, "Strength": 0.05},
        target_bike_run_tss_splits={"Bike": 0.75, "Run": 0.25},
        sport_freq_bounds={"Swim": (2, 3), "Bike": (4, 5), "Run": (2, 3), "Strength": (1, 1)},
    )
    # Remove long run anchor so rehab runner isn't forced to do 75m long run
    lib_rehab = [
        CandidateWorkout(
            w.id, w.code, w.name, w.sport, w.duration, w.aerobic_tss, w.mechanical_strain,
            w.ans_cost, w.durability_score, w.zone_durations, w.is_hard_endurance,
            w.is_heavy_run, w.is_heavy_strength, (w.code == "BIKE_DUR_OUT"), w.has_strides, w.is_indoor_bike, w.preference_penalty
        ) for w in default_lib
    ]
    res5 = run_scenario("5. Orthopedic Rehab Runner (Run Mech <= 160, 75% Bike)", lib_rehab, cfg_rehab)
    results["5_orthopedic_rehab"] = res5

    # -------------------------------------------------------------------------
    # Scenario 6: Facility Lockdown (Pool Closed All Week)
    # -------------------------------------------------------------------------
    cfg_lockdown = MesoCycleTargetsAndConstraints(
        facility_access={
            "Swim": [False]*7,
            "Bike": [True]*7,
            "Run": [True]*7,
            "Strength": [True]*7,
        },
        sport_freq_bounds={"Swim": (2, 2), "Bike": (3, 4), "Run": (4, 5), "Strength": (1, 2)},
    )
    res6_contradiction = run_scenario("6a. Pool Lockdown with Conflicting Freq=(2,2)", default_lib, cfg_lockdown)
    results["6a_pool_lockdown_conflict"] = res6_contradiction

    cfg_lockdown_adapted = MesoCycleTargetsAndConstraints(
        facility_access={
            "Swim": [False]*7,
            "Bike": [True]*7,
            "Run": [True]*7,
            "Strength": [True]*7,
        },
        sport_freq_bounds={"Swim": (0, 0), "Bike": (4, 5), "Run": (4, 5), "Strength": (1, 2)},
        target_sport_duration_splits={"Swim": 0.0, "Bike": 0.60, "Run": 0.33, "Strength": 0.07},
    )
    res6_adapted = run_scenario("6b. Pool Lockdown with Adapted Freq=(0,0)", default_lib, cfg_lockdown_adapted)
    results["6b_pool_lockdown_adapted"] = res6_adapted

    # -------------------------------------------------------------------------
    # Scenario 7: Master Athlete / Slower Recovery
    # 2 Complete Rest Days, Zero Double Sessions
    # -------------------------------------------------------------------------
    cfg_master = MesoCycleTargetsAndConstraints(
        daily_time_caps=[60.0, 60.0, 60.0, 60.0, 60.0, 180.0, 120.0],
        weekday_single_workout_cap=60.0,
        min_rest_days=2,
        max_rest_days=2,
        sport_freq_bounds={"Swim": (1, 2), "Bike": (2, 3), "Run": (2, 3), "Strength": (1, 1)},
        target_weekly_tss=420.0,
        nominal_weekly_duration=480.0,
    )
    # Remove mandatory 180m bike and 75m run anchors to fit master schedule
    lib_master = [replace(w, is_anchor=False) for w in default_lib]
    res7 = run_scenario("7. Master Athlete (2 Rest Days, Single Sessions Only)", lib_master, cfg_master)
    results["7_master_athlete"] = res7

    # -------------------------------------------------------------------------
    # Scenario 8: Large Library Stress Test (Hundreds of Workouts)
    # Ingests Bike.xlsx, Run.xlsx, Swim.xlsx, Strength, and enriches them
    # -------------------------------------------------------------------------
    print_header("SCENARIO 8: LARGE WORKOUT LIBRARY SCALE TEST")
    try:
        triv.set_distance_profile('olympic')
        we_df, wd_df = load_workouts(triv.run_file, triv.bike_file, triv.swim_file)
        combined_raw = pd.concat([we_df, wd_df], ignore_index=True).drop_duplicates(subset=['Code'])
        enriched_large_lib = enrich_workouts_from_df(combined_raw)
        print(f"Successfully loaded and enriched {len(enriched_large_lib)} real-world workouts.")

        from closed_loop_optimizer import prune_candidate_library
        no_anchor_lib = [replace(w, is_anchor=False, is_indoor_bike=True) for w in enriched_large_lib]
        scaled_lib = prune_candidate_library(no_anchor_lib, target_tss=520.0, max_per_sport=25)
        print(f"Pruned large library to {len(scaled_lib)} high-quality representative workouts.")

        cfg_large = MesoCycleTargetsAndConstraints(
            target_weekly_tss=520.0,
            facility_access={
                "Swim": [True]*7,
                "Bike": [True]*7,
                "Run": [True]*7,
                "Strength": [True]*7,
            },
            sport_freq_bounds={"Swim": (2, 3), "Bike": (3, 4), "Run": (4, 5), "Strength": (0, 0)},
            required_heavy_strength_sessions=0,
            min_stride_sessions=0,
            max_48h_ans_cost=800.0,
        )
        res8 = run_scenario("8. Large Library MILP Scaling (Pruned to 60 workouts)", scaled_lib, cfg_large, k_plans=3, time_limit=10.0)
        results["8_large_library"] = res8
    except Exception as e:
        print(f"Scenario 8 Error loading library: {e}")
        results["8_large_library"] = {"status": "ERROR", "error": str(e)}

    # -------------------------------------------------------------------------
    # Scenario 9: Closed-Loop Tier 1 Daily Micro-Adjustment (Mid-Week Sickness)
    # -------------------------------------------------------------------------
    print_header("SCENARIO 9: CLOSED-LOOP TIER 1 DAILY SICKNESS PERTURBATION")
    mpc = ClosedLoopMPCController()
    wkt_hard = default_lib[6] # BIKE_ME3_OU (60m, 68 TSS, 245 ANS, Hard)
    
    # Athlete wakes up with acute illness: RMSSD drops to 18 (baseline 60), sleep 4.5h, soreness 7/10
    s_sick = mpc.calculate_morning_readiness(18.0, 60.0, 4.5, 7.0)
    tier1_sick = mpc.evaluate_tier1_daily_action(s_sick, wkt_hard)
    print(f"Sick Athlete Readiness: {s_sick:.2f} -> Action: {tier1_sick['status']}")
    print(f"Reason: {tier1_sick['reason']}")
    print(f"Adjusted: Dur={tier1_sick['adjusted_duration']}m, TSS={tier1_sick['adjusted_tss']}, ANS={tier1_sick['adjusted_ans_cost']}")

    # Fresh athlete supercompensation: RMSSD 85 (baseline 60), sleep 9.0h, soreness 1/10
    s_fresh = mpc.calculate_morning_readiness(85.0, 60.0, 9.0, 1.0)
    tier1_fresh = mpc.evaluate_tier1_daily_action(s_fresh, wkt_hard)
    print(f"\nFresh Athlete Readiness: {s_fresh:.2f} -> Action: {tier1_fresh['status']}")
    print(f"Reason: {tier1_fresh['reason']}")
    print(f"Adjusted: Dur={tier1_fresh['adjusted_duration']}m, TSS={tier1_fresh['adjusted_tss']}, ANS={tier1_fresh['adjusted_ans_cost']}")
    results["9_tier1_readiness"] = {"sick": tier1_sick, "fresh": tier1_fresh}

    # -------------------------------------------------------------------------
    # Scenario 10: Closed-Loop Tier 2 Intensity Creep & Receding-Horizon Recalibration
    # Athlete ran Zone 4 on easy recovery day, causing ACWR & ODI spike
    # -------------------------------------------------------------------------
    print_header("SCENARIO 10: CLOSED-LOOP TIER 2 INTENSITY CREEP RECALIBRATION")
    # Simulate an athlete where metabolic load outpaces orthopedic chassis:
    decoupled_state = AthletePhysiologicalState(
        metabolic_fitness_state=78.0,
        connective_tissue_state=48.0,    # ODI = 78/48 = 1.625 > 1.35!
        running_acute_mech=85.0,
        running_chronic_mech=52.0,       # ACWR = 85/52 = 1.635 > 1.30!
    )
    mpc_decoupled = ClosedLoopMPCController(decoupled_state)
    print(f"Pre-Recalibration ODI:  {decoupled_state.orthopedic_decoupling_index:.3f} (Ceiling: 1.35)")
    print(f"Pre-Recalibration ACWR: {decoupled_state.running_mechanical_acwr:.3f} (Ceiling: 1.30)")

    mock_weekly_records = [
        {"tss": 85.0, "run_mech": 90.0, "bike_tss": 30.0, "run_tss": 55.0, "swim_tss": 0.0},
        {"tss": 90.0, "run_mech": 85.0, "bike_tss": 40.0, "run_tss": 50.0, "swim_tss": 0.0},
    ]
    base_cfg = MesoCycleTargetsAndConstraints(max_weekly_run_mech_strain=350.0)
    recal_cfg = mpc_decoupled.process_weekly_execution_and_recalibrate(mock_weekly_records, base_cfg)
    print(f"Post-Recalibration Weekly Run Mechanical Strain Ceiling: {recal_cfg.max_weekly_run_mech_strain:.1f} (Reduced from 350.0)")
    print(f"Rebalanced Bike-Run TSS Split Target: {recal_cfg.target_bike_run_tss_splits}")
    results["10_tier2_recalibration"] = {
        "pre_odi": decoupled_state.orthopedic_decoupling_index,
        "pre_acwr": decoupled_state.running_mechanical_acwr,
        "recal_run_strain": recal_cfg.max_weekly_run_mech_strain,
    }

    # =========================================================================
    # SCENARIO 11: DUAL-OBJECTIVE HYBRID ATHLETE OPTIMIZATION (SWIM, BIKE, RUN, LIFT)
    # =========================================================================
    print_header("SCENARIO 11: DUAL-OBJECTIVE HYBRID ATHLETE BALANCED OPTIMIZATION")
    hybrid_lib = build_hybrid_candidate_library()
    hybrid_cfg = {
        "daily_duration_caps": [120, 120, 75, 120, 120, 220, 180],
        "pool_open_days": [0, 2, 4],
        "outdoor_bike_days": [5, 6],
        "target_tss": {"Swim": 100.0, "Bike": 260.0, "Run": 198.0, "Strength": 52.0},
        "target_sets": {"Anterior_Chain": 10.0, "Posterior_Chain": 10.0, "Push": 10.0, "Pull": 10.0},
        "long_run_cap_ratio": 0.32,
        "min_barbell_set_share": 0.60,
        "p_low_min": 0.78,
        "p_high_max": 0.20,
        "max_weekly_axial": 320.0,
        "max_daily_axial": 120.0,
        "max_daily_neuro": 140.0,
        "readiness_alpha": [1.0] * 7,
        "weights": {
            "w_tss_neg": 150.0, "w_tss_pos": 100.0,
            "w_sets_neg": 150.0, "w_sets_pos": 100.0,
            "w_tiz": 1.0, "w_ans": 0.20, "w_durability": 0.35,
            "w_double": 5.0, "w_rest": 18.0, "w_brick": 4.0,
        },
    }

    t0 = time.time()
    hybrid_opt = HybridAthleteMILPOptimizer(hybrid_lib, hybrid_cfg)
    hybrid_plans = hybrid_opt.solve_top_k_plans(k_solutions=2, time_limit=5.0)
    dur_h = time.time() - t0

    if len(hybrid_plans) >= 2:
        print(f"RESULT: SUCCESS (Solved {len(hybrid_plans)} distinct hybrid plans in {dur_h:.2f}s | Obj: {hybrid_plans[0]['objective_value']:.2f})")
        print(f"  Option A: {hybrid_plans[0]['split_architecture']}")
        print(f"  Option B: {hybrid_plans[1]['split_architecture']}")
        results["11_hybrid_athlete"] = {"status": "OPTIMAL", "duration": dur_h, "plans": len(hybrid_plans)}
    else:
        print(f"RESULT: FAILED (Found {len(hybrid_plans)} plans in {dur_h:.2f}s)")
        results["11_hybrid_athlete"] = {"status": "FAILED", "duration": dur_h, "plans": len(hybrid_plans)}

    print_header("STRESS TEST SUMMARY")
    for k, v in results.items():
        st = v.get("status", "N/A")
        dur = v.get("duration", 0.0)
        print(f"  {k:<32}: Status={st:<12} (Time: {dur:.2f}s)")

    return results


if __name__ == "__main__":
    stress_test_suite()
