import time
import multiprocessing as mp
from functools import partial
import pandas as pd

from triplan.model.classes import triVariables as triv
from triplan.library.util import calculate_plan_score, load_workouts, generate_random_workout_plan
from triplan.solver.highs_optimization import (
    optimize_weekly_workouts,
    optimize_weekly_workouts_multiseed,
    optimize_weekly_schedule,
    optimize_multi_week_step_cycle,
    generate_16_week_olympic_schedule,
    get_periodized_zone_ratios,
    evaluate_plan_with_original_score,
    prepare_workout_df,
    filter_viable_workouts,
)
from triplan.solver.scheduler import TriathlonMILPScheduler
from triplan.model.candidates import MesoCycleTargetsAndConstraints
from triplan.control.mpc import ClosedLoopMPCController
from triplan.physiology.state import AthletePhysiologicalState
from triplan.library.candidates import build_default_candidate_library, build_hybrid_candidate_library
from triplan.solver.hybrid import HybridAthleteMILPOptimizer
from triplan.control.season import format_plan_to_dataframe, generate_closed_loop_season_schedule
from triplan.physiology.athlete_profile import CURRENT_ATHLETE, AthleteProfile

pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)

# TODO: Allow for bricks
# TODO: Force bricks option
# TODO: Force only easy day /hard days
# TODO: Prefer Intense/Rest cycles
# TODO: Maximize time utilization
# TODO: Add Strength
# TODO: exponentially weight distanceNow
# TODO: Be able to force specific workouts (e.g. 5k run)
# TODO: Min/max number of run/bike/swim

# https://www.8020endurance.com/why-endurance-training-cant-be-reduced-to-a-formula/
# – Avoid moving workouts that result in 3 “hard” days in a row. “Hard” means intervals or long workouts.
# – Try to maintain reasonable distribution of each sport. For example, it’s best to cycle every other day than cycle 3 days in a row and not cycle for 4 days.
# – Due to the quick recovery, swim workouts are an exception to the “hard” rule and can be moved at your discretion, while still trying to maintain reasonable swim workout distribution.
# – When performing bike and run workouts back-to-back, it’s best to perform the workout with the most intensity first.
# – Try to keep the workouts all within the same week, as this maintains the precise 80/20 ratios that have been built into your plan.


def run_highs_multiseed(df, num_workers=15, threads_per_worker=2, top_k=5, time_limit_per_worker=4.0):
    """Runs the optimized 15 seeds × 2 threads HiGHS MILP multi-seed race with pre-filtering."""
    total_cores = num_workers * threads_per_worker
    print(f"\n=======================================================")
    print(f"  RUNNING HiGHS MILP MULTI-SEED RACE ({num_workers} seeds × {threads_per_worker} threads = {total_cores} cores, {time_limit_per_worker}s limit/worker)")
    print(f"=======================================================")
    t0 = time.time()
    highs_df = optimize_weekly_workouts_multiseed(
        df=df,
        target_tss=triv.target_tss,
        num_workouts=6,
        num_workers=num_workers,
        threads_per_worker=threads_per_worker,
        time_limit_per_worker=time_limit_per_worker,
        top_k_plans=top_k,
        prune_candidates=True,
    )
    dur = time.time() - t0

    if not highs_df.empty:
        highs_df['Orig Score'] = highs_df['Workouts'].apply(
            lambda w: evaluate_plan_with_original_score(w, df)
        )
        highs_df = highs_df.sort_values(by='Orig Score', ascending=False).reset_index(drop=True)

    print(f"HiGHS Multi-Seed Execution Time: {round(dur, 2)}s")
    cols = ['Workouts', 'Orig Score', 'Score', 'Total TSS', 'Total Duration', '% Bike Dur', '% Run Dur', '% High Dur']
    print(highs_df[cols])
    return highs_df, dur


def run_day_by_day_schedule(df):
    """Generates a fully scheduled Mon-Sun calendar adhering to Matt Fitzgerald's Hard/Easy rule."""
    print(f"\n=======================================================")
    print(f"  DAY-BY-DAY SCHEDULER: MATT FITZGERALD HARD/EASY RULE & TIME CAPS")
    print(f"=======================================================")
    t0 = time.time()
    schedule_df = optimize_weekly_schedule(
        df=df,
        target_tss=triv.target_tss,
        num_workouts=6,
        day_max_durations={
            'Mon': 0.0,    # Designated Rest Day
            'Tue': 75.0,   # Weekday morning budget
            'Wed': 75.0,
            'Thu': 75.0,
            'Fri': 75.0,
            'Sat': 150.0,  # Weekend long ride
            'Sun': 120.0,  # Weekend long run
        },
        forbid_consecutive_hard=True,
        forbid_consecutive_long_runs=True,
        time_limit=4.0,
        threads=2,
    )
    dur = time.time() - t0

    cols = ['Day', 'Code', 'Sport', 'Duration', 'TSS', 'High_Dur', 'Low_Dur', 'Intensity']
    print(f"Schedule solved in {round(dur, 2)}s:")
    print(schedule_df[cols].to_string(index=False))

    assigned_codes = [c for c in schedule_df['Code'] if c != 'REST']
    score = evaluate_plan_with_original_score(assigned_codes, df)
    print(f"Scheduled Plan Overall Quality Score: {score} / 100")
    return schedule_df


def run_3_week_step_cycle(df, weeks_to_race=10, race_distance='70.3'):
    """Generates a complete 3-Week Step Cycle (Base, Overload, Recovery) with periodized zones."""
    print(f"\n=======================================================")
    print(f"  3-WEEK STEP CYCLE PERIODIZATION ({weeks_to_race} Weeks to {race_distance} Race)")
    print(f"=======================================================")
    t0 = time.time()
    cycle_df = optimize_multi_week_step_cycle(
        df=df,
        base_tss=triv.target_tss,
        step_scale=(1.00, 1.08, 0.85),
        weeks_to_race=weeks_to_race,
        race_distance=race_distance,
        num_workouts=6,
        time_limit_per_week=3.5,
        threads=2,
    )
    dur = time.time() - t0

    cols = ['Week_Label', 'Target_TSS', 'Day', 'Code', 'Sport', 'Duration', 'TSS', 'Intensity']
    print(f"3-Week Step Cycle generated in {round(dur, 2)}s:")
    print(cycle_df[cols].to_string(index=False))
    return cycle_df


def run_random_method(wkend_wts_df, wkday_wts_df, total_to_run=10000, num_cores=30):
    """Runs the original Monte Carlo random workout plan generator across CPU cores."""
    print(f"\n=======================================================")
    print(f"  RUNNING ORIGINAL RANDOM METHOD ({total_to_run:,} iterations across {num_cores} cores)")
    print(f"=======================================================")
    t0 = time.time()
    num_runs = [int(total_to_run / num_cores)] * num_cores
    with mp.Pool(num_cores) as pool:
        func = partial(generate_random_workout_plan, wkend_wts_df, wkday_wts_df)
        dfs = pool.map(func, num_runs)
    random_df = pd.concat(dfs, ignore_index=True)
    dur = time.time() - t0

    random_df = random_df.sort_values(by='Score', ascending=False).reset_index(drop=True)
    count_90 = (random_df['Score'] >= 90).sum() if not random_df.empty else 0
    pct_90 = round(100 * count_90 / total_to_run, 4)

    print(f"Random Method Execution Time: {round(dur, 2)}s")
    print(f"Valid Plans: {len(random_df):,} / {total_to_run:,} ({round(100*len(random_df)/total_to_run, 2)}%)")
    print(f"Plans with Score >= 90: {count_90:,} ({pct_90}%)")
    cols = ['Workouts', 'Score', 'Total TSS', 'Total Duration', '% Bike Dur', '% Run Dur', '% High Dur']
    print(random_df.head(5)[cols])
    return random_df, dur


def print_comparison(highs_df, highs_time, random_df, random_time, total_random_runs, num_cores, candidate_df=None):
    """Prints a clear side-by-side comparison between HiGHS and Random.

    All figures are measured from this run (TASK-009); no hardcoded benchmark claims.
    """
    print(f"\n=======================================================")
    print(f"  SIDE-BY-SIDE COMPARISON (Hardware: {mp.cpu_count()} CPUs, using {num_cores} cores)")
    print(f"=======================================================")

    rnd_top = random_df.loc[0, 'Score'] if not random_df.empty else 0.0
    rnd_mean = random_df['Score'].mean() if not random_df.empty else 0.0
    rnd_ge90 = (random_df['Score'] >= 90).sum() if not random_df.empty else 0

    highs_top = highs_df.loc[0, 'Orig Score'] if not highs_df.empty else 0.0
    highs_mean = highs_df['Orig Score'].mean() if not highs_df.empty else 0.0
    highs_ge90 = (highs_df['Orig Score'] >= 90).sum() if not highs_df.empty else 0
    highs_total = len(highs_df)

    print(f"{'Metric':<30} | {'Random Method':<22} | {'HiGHS (15 seeds × 2 thr)':<25}")
    print("-" * 83)
    print(f"{'Cores Used':<30} | {num_cores:<22} | {num_cores:<25}")
    print(f"{'Total Execution Time':<30} | {f'{random_time:.2f}s':<22} | {f'{highs_time:.2f}s':<25}")
    print(f"{'Top Original Score':<30} | {f'{rnd_top:.2f}':<22} | {f'{highs_top:.2f}':<25}")
    print(f"{'Mean Score':<30} | {f'{rnd_mean:.2f}':<22} | {f'{highs_mean:.2f}':<25}")
    print(f"{'Plans with Score >= 90':<30} | {f'{rnd_ge90:,} / {total_random_runs:,} ({100*rnd_ge90/total_random_runs:.2f}%)':<22} | {f'{highs_ge90} / {highs_total} ({100*highs_ge90/highs_total:.1f}%)':<25}")
    rnd_valid_pct = 100 * len(random_df) / total_random_runs if total_random_runs else 0.0
    if not highs_df.empty and 'Status' in highs_df.columns:
        status_counts = highs_df['Status'].value_counts()
        highs_status = ", ".join(f"{n} {s}" for s, n in status_counts.items())
    else:
        highs_status = "no plans"
    print(f"{'Feasibility / Solve Status':<30} | {f'Trial & Error ({rnd_valid_pct:.0f}% valid)':<22} | {highs_status:<25}")
    if not highs_df.empty and 'MIP Gap' in highs_df.columns and highs_df['MIP Gap'].notna().any():
        worst_gap = f"{100 * highs_df['MIP Gap'].max():.1f}%"
        print(f"{'Worst MIP Gap':<30} | {'n/a':<22} | {worst_gap:<25}")
    if candidate_df is not None:
        prepared = prepare_workout_df(candidate_df)
        kept = filter_viable_workouts(prepared, target_tss=triv.target_tss, num_workouts=6)
        n_all, n_pruned = len(prepared), len(prepared) - len(kept)
        print(f"{'Candidate Pre-Filtering':<30} | {f'None (draws all {n_all})':<22} | {f'Pruned {n_pruned} unviable items':<25}")
    print(f"{'Day-by-Day Scheduling':<30} | {'None (unordered bag)':<22} | {'Mon-Sun + Hard/Easy Rule':<25}")
    print(f"{'Multi-Week Step Cycles':<30} | {'Manual':<22} | {'Automatic (Load/Deload)':<25}")
    print("-" * 83)


def run_16_week_olympic_schedule(df):
    """
    Generates and evaluates a full 16-Week Olympic Distance Schedule (Level 0 Equivalent)
    incorporating empirical 85/15 intensity, 3-week step cycles, dynamic periodization,
    the Hard/Easy Rule (with Swim exemption), and day-specific duration caps.
    """
    print(f"\n=======================================================")
    print(f"  16-WEEK OLYMPIC DISTANCE SCHEDULE GENERATION (LEVEL 0 EQUIVALENT)")
    print(f"  Incorporating 85/15 Default, 3-Week Step Cycles, Hard/Easy Rule, & Swim Exemption")
    print(f"=======================================================")
    t0 = time.time()
    triv.set_distance_profile('olympic')
    daily_df, summary_df = generate_16_week_olympic_schedule(df, num_workers=16, time_limit_per_week=3.5)
    dur = time.time() - t0

    print(f"Entire 16-Week Olympic Schedule generated in {round(dur, 2)}s across CPU cores!")
    print("\nWEEKLY PERIODIZATION SUMMARY:")
    print(summary_df.to_string(index=False))

    daily_df.to_csv('training_plans/optimized_olympic_16w_daily.csv', index=False)
    summary_df.to_csv('training_plans/optimized_olympic_16w_summary.csv', index=False)
    print("\nSaved full daily schedule to 'training_plans/optimized_olympic_16w_daily.csv'")
    return daily_df, summary_df


def run_closed_loop_optimizer_demo(df=None):
    """
    Demonstrates the Closed-Loop Triathlon Training Optimizer:
    Dual-load (Aerobic TSS vs Mechanical Strain), ANS exponential costing,
    keep-hard-days-hard strength stacking, and 3-Tier Closed-Loop MPC.
    """
    print(f"\n=======================================================")
    print(f"  CLOSED-LOOP TRIATHLON TRAINING OPTIMIZER DEMO")
    print(f"  Dual-Load Tracking (TSS vs Mech Strain), ANS Recovery, & 3-Tier MPC")
    print(f"=======================================================")
    t0 = time.time()

    # 1. Build library
    default_lib = build_default_candidate_library()

    # 2. Configure Mesocycle Constraints
    cfg = MesoCycleTargetsAndConstraints(
        target_weekly_tss=540.0,
        nominal_weekly_duration=640.0,
        max_48h_ans_cost=650.0,
        max_weekly_run_mech_strain=350.0,
        enable_soft_48h_slacks=True,
    )

    # 3. Solve MILP
    scheduler = TriathlonMILPScheduler(default_lib, cfg)
    plans = scheduler.solve_top_k_plans(k_plans=1, time_limit=5.0)
    dur = time.time() - t0

    if plans:
        plan = plans[0]
        schedule_df = format_plan_to_dataframe(plan)
        print(f"Closed-Loop Schedule Solved in {dur:.2f}s:")
        print(f"Objective Score: {plan['objective']:.2f} | Total TSS: {plan['total_tss']:.1f} | Mechanical Strain: {plan['total_mech_strain']:.1f}")
        print(f"Total ANS Cost: {plan['total_ans_cost']:.1f} | Long Run Share: {plan['max_single_run_ratio']*100:.1f}%")

        display_cols = ['Day', 'Sport', 'Code', 'Duration', 'TSS', 'MechStrain', 'ANSCost', 'Durability', 'Intensity']
        available_cols = [c for c in display_cols if c in schedule_df.columns]
        print(schedule_df[available_cols].to_string(index=False))

        # 4. Closed-Loop Tier 1 Daily Adjustment Simulation
        mpc = ClosedLoopMPCController()
        # Grab first non-rest workout
        first_wkt = next((w for day_wkts in plan['schedule'].values() for w in day_wkts if w.code != 'REST'), default_lib[0])
        s_morning = mpc.calculate_morning_readiness(
            rmssd_today=42.0,
            rmssd_7d_baseline=60.0,
            sleep_hours=6.0,
            muscle_soreness_1_to_10=5.0
        )
        t1_action = mpc.evaluate_tier1_daily_action(s_morning, first_wkt)
        print(f"\n[MPC Tier 1 Morning Adaptation] Readiness Scalar S={s_morning:.2f} -> Action: {t1_action['status']}")
        print(f"Detail: {t1_action['reason']}")
        return schedule_df
    else:
        print("Optimizer could not find a feasible schedule.")
        return None


def run_closed_loop_season_demo(distance: str = "olympic"):
    """
    Demonstrates full periodized multi-month season planning with 3-Tier Closed-Loop MPC.
    Supports distance='olympic' (16 weeks) or distance='70.3' (20 weeks).
    """
    print(f"\n=======================================================")
    print(f"  CLOSED-LOOP {distance.upper()} MULTI-MONTH SEASON GENERATOR")
    print(f"  3-Tier MPC: Periodization Step-Cycles + Receding-Horizon Recalibration")
    print(f"=======================================================")
    t0 = time.time()
    daily_df, summary_df = generate_closed_loop_season_schedule(
        profile=CURRENT_ATHLETE,
        distance=distance,
        time_limit_per_week=2.0
    )
    dur = time.time() - t0
    print(f"\nEntire {distance.upper()} Season solved in {dur:.2f}s!")
    print("\nSEASON PROGRESSION SUMMARY:")
    display_cols = ['Week', 'Phase', 'Step_Type', 'Solved_TSS', 'Total_Mech_Strain', 'Total_ANS_Cost', 'Ending_CTL', 'TSB', 'Ending_ODI', 'Ending_ACWR', 'Recalibration']
    print(summary_df[display_cols].to_string(index=False))

    save_path = f"training_plans/closed_loop_{distance.lower()}_season_summary.csv"
    summary_df.to_csv(save_path, index=False)
    print(f"\nSaved full season summary to '{save_path}'")
    return daily_df, summary_df


def run_hybrid_athlete_demo():
    """
    Demonstrates the Dual-Objective Hybrid Athlete Optimizer (HAOS):
    - Balances Running, Cycling, Swimming, and Heavy Barbell/Dumbbell Lifting
    - Equipment-aware: Barbell monopoly on compound lifts, <=75 lb DB unilateral assistance
    - Biomechanical & Neuromuscular Conflict Prevention Matrix
    - Coherent Strength Architecture: Solves for Option A (Texas/HLM) vs Option B (Upper/Lower)
    - 3-Tier Closed-Loop MPC: Morning HRV/RHR telemetry, Kalman e1RM, Mesocycle undulation
    """
    print(f"\n=======================================================")
    print(f"  DUAL-OBJECTIVE HYBRID ATHLETE OPTIMIZER (HAOS) DEMO")
    print(f"  Balancing Swim, Bike, Run, & Lifting with 75 lb DB Boundary")
    print(f"=======================================================")
    t0 = time.time()

    # 1. Load library and athlete configuration
    library = build_hybrid_candidate_library()
    config = CURRENT_ATHLETE.to_hybrid_config()

    # 2. Solve for Top-2 Globally Optimal Structurally Distinct Plans
    scheduler = HybridAthleteMILPOptimizer(library, config)
    plans = scheduler.solve_top_k_plans(k_solutions=2, time_limit=5.0)
    dur = time.time() - t0

    print(f"Solved Top-{len(plans)} Distinct Hybrid Schedules in {dur:.3f}s:\n")

    for plan in plans:
        print(f"----------------------------------------------------------------------")
        print(f"  OPTION {plan['option']}: {plan['split_architecture']}")
        print(f"  Objective: {plan['objective_value']:.2f} | Solved TSS: {plan['total_tss']:.1f} | Axial Load: {plan['total_axial_load']:.1f}")
        print(f"----------------------------------------------------------------------")

        # Track metrics
        tot_dur = 0.0
        tot_bb_sets = 0
        tot_db_sets = 0
        muscle_sets = {"Anterior_Chain": 0, "Posterior_Chain": 0, "Push": 0, "Pull": 0}
        sport_tss = {"Swim": 0.0, "Bike": 0.0, "Run": 0.0, "Strength": 0.0}
        z_durs = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
        run_durations = []

        for d in range(7):
            d_name = scheduler.day_names[d]
            wkts = plan["schedule"][d]
            if not wkts:
                print(f"  {d_name:3s}: [ FULL REST DAY — Autonomic & Myofibrillar Restoration ]")
            else:
                day_dur = sum(w.duration for w in wkts)
                day_ax = sum(w.axial_load for w in wkts)
                day_neuro = sum(w.neuromuscular_load for w in wkts)
                w_desc = "  +  [>=6h Recovery Gap]  ".join(
                    f"{w.code} ({int(w.duration)}m, {int(w.aerobic_tss)} TSS)" for w in wkts
                )
                print(f"  {d_name:3s} [{int(day_dur):3d}m | Axial {int(day_ax):2d} | Neuro {int(day_neuro):2d}]: {w_desc}")

                for w in wkts:
                    tot_dur += w.duration
                    tot_bb_sets += getattr(w, "bb_sets", 0)
                    tot_db_sets += getattr(w, "db_sets", 0)
                    for m, s_cnt in (getattr(w, "sets_by_muscle", {}) or {}).items():
                        muscle_sets[m] = muscle_sets.get(m, 0) + s_cnt
                    sport_tss[w.sport] = sport_tss.get(w.sport, 0.0) + w.aerobic_tss
                    zm = getattr(w, "zone_minutes", {}) or {}
                    if not zm and getattr(w, "zone_durations", None):
                        zm = {z: w.zone_durations[z-1] for z in range(1, 6)}
                    for z, mins in zm.items():
                        z_durs[z] = z_durs.get(z, 0.0) + mins
                    if w.sport == "Run":
                        run_durations.append(w.duration)

        tot_sets = tot_bb_sets + tot_db_sets
        bb_share = (tot_bb_sets / tot_sets * 100) if tot_sets > 0 else 0.0
        tot_run_dur = sum(run_durations)
        max_run = max(run_durations) if run_durations else 0.0
        long_run_ratio = (max_run / tot_run_dur * 100) if tot_run_dur > 0 else 0.0
        tot_endur_z = sum(z_durs.values())
        z12_pct = ((z_durs[1] + z_durs[2]) / tot_endur_z * 100) if tot_endur_z > 0 else 0.0
        z45_pct = ((z_durs[4] + z_durs[5]) / tot_endur_z * 100) if tot_endur_z > 0 else 0.0

        print(f"\n  Plan Balance Verification:")
        print(f"  * 4-Sport TSS: Swim {sport_tss.get('Swim',0):.1f} | Bike {sport_tss.get('Bike',0):.1f} | Run {sport_tss.get('Run',0):.1f} | Strength {sport_tss.get('Strength',0):.1f} (Target: 100/260/198/52)")
        print(f"  * Resistance Sets: Anterior {muscle_sets['Anterior_Chain']} | Posterior {muscle_sets['Posterior_Chain']} | Push {muscle_sets['Push']} | Pull {muscle_sets['Pull']} (Target: 10 each)")
        print(f"  * Barbell Share: {bb_share:.1f}% >= 60.0% min (Barbell: {tot_bb_sets} sets, Dumbbell: {tot_db_sets} sets)")
        print(f"  * Single Long Run: {max_run:.0f}m / {tot_run_dur:.0f}m = {long_run_ratio:.1f}% <= 32.0% cap")
        print(f"  * Polarized TID: Z1+Z2 = {z12_pct:.1f}% (>=78%), Z4+Z5 = {z45_pct:.1f}% (<=20%)\n")

    # 3. Closed-Loop Tier 1 Daily Auto-Regulation Demo
    mpc = ClosedLoopMPCController()
    print("----------------------------------------------------------------------")
    print("  3-TIER CLOSED-LOOP HYBRID MPC AUTO-REGULATION DEMO")
    print("----------------------------------------------------------------------")

    # Tier 1 Morning Telemetry Simulation (Garmin rMSSD Z-score + RHR + Sleep)
    alpha_green = mpc.calculate_garmin_morning_readiness(
        rmssd_today=65.0, rmssd_baseline_mean=60.0, rmssd_baseline_std=8.0,
        rhr_today=48.0, rhr_baseline_mean=50.0, sleep_score=88.0
    )
    alpha_red = mpc.calculate_garmin_morning_readiness(
        rmssd_today=36.0, rmssd_baseline_mean=60.0, rmssd_baseline_std=8.0,
        rhr_today=58.0, rhr_baseline_mean=50.0, sleep_score=52.0
    )

    sample_squat = library[0] # UL_BB_LOWER_SQ
    action_green = mpc.evaluate_tier1_hybrid_action(alpha_green, sample_squat)
    action_red = mpc.evaluate_tier1_hybrid_action(alpha_red, sample_squat)

    print(f"1. Tier 1 Daily Auto-Regulation:")
    print(f"   - Green Readiness (alpha={alpha_green:.2f}): Status={action_green['status']} -> {action_green['reason']}")
    print(f"   - Red Suppression (alpha={alpha_red:.2f}): Status={action_red['status']} -> {action_red['reason']}")

    # Tier 2 Kalman Filter e1RM Update Simulation
    e1rm_init = CURRENT_ATHLETE.barbell_1rm.get("squat", 315.0)
    p_init = 9.0
    # Athlete lifts 265 lb for 5 reps with 2 RIR
    e1rm_updated, p_updated, z_meas = mpc.update_e1rm_kalman(
        e1rm_prev=e1rm_init, p_prev=p_init,
        weight_lifted=265.0, reps_completed=5, logged_rir=2.0
    )
    print(f"\n2. Tier 2 Dynamic e1RM Kalman Filter (Squat):")
    print(f"   - Prior e1RM: {e1rm_init:.1f} lb (Var: {p_init:.1f})")
    print(f"   - Logged Session: 265 lb x 5 reps @ 2 RIR -> Raw Measurement: {z_meas:.1f} lb")
    print(f"   - Posterior e1RM: {e1rm_updated:.1f} lb (Updated Var: {p_updated:.2f})")

    # Tier 3 Mesocycle Wave Alignment ( Texas Method undulation with 3:1 step cycle)
    print(f"\n3. Tier 3 Mesocycle Wave Alignment (3:1 Triathlon & Undulating Barbell Progression):")
    for wk in range(1, 5):
        cfg_wk = mpc.configure_hybrid_mesocycle_phase(phase_name=f"Mesocycle Week {wk}", week_in_cycle=wk)
        wave_desc = (
            "Accumulation (Texas Triples 1x3 @ 90% | 100% TSS)" if wk == 1
            else "Intensification (Texas Doubles 2x2 @ 93% | 106% TSS)" if wk == 2
            else "Peak Overload (Texas Singles 5x1 @ 96% | 112% TSS)" if wk == 3
            else "Supercompensation Deload (-40% Endurance TSS, -50% Strength Sets, >=85% Intensity)"
        )
        print(f"   - Week {wk}: Target TSS = {cfg_wk.target_weekly_tss:.1f} | Durability Weight = {cfg_wk.w_durability:.1f} -> {wave_desc}")

    return plans


def main():
    """Run the full demo pipeline (random baseline, HiGHS multi-seed race, closed-loop MPC, hybrid athlete)."""
    # Initialize 3-sport Olympic profile with 85/15 default
    triv.set_distance_profile('olympic')
    wkend_wts_df, wkday_wts_df = load_workouts()

    # 1. 16-Week Olympic Schedule (Level 0 Equivalent) with 85/15 default & periodization
    olympic_daily_df, olympic_summary_df = run_16_week_olympic_schedule(df=wkend_wts_df)

    # 2. HiGHS Multi-Seed Race (15 seeds × 2 threads default)
    highs_df, highs_time = run_highs_multiseed(
        df=wkend_wts_df,
        num_workers=15,
        threads_per_worker=2,
        top_k=5,
        time_limit_per_worker=4.0
    )

    # 3. Day-by-Day Calendar Scheduling with Hard/Easy Rule & Daily Caps
    schedule_df = run_day_by_day_schedule(df=wkend_wts_df)

    # 4. 3-Week Step Cycle (Base Load -> Overload -> Recovery)
    cycle_df = run_3_week_step_cycle(df=wkend_wts_df, weeks_to_race=10, race_distance='olympic')

    # 4. Original Random Method (for benchmark comparison)
    total_random_runs = 10000
    random_df, random_time = run_random_method(
        wkend_wts_df=wkend_wts_df,
        wkday_wts_df=wkday_wts_df,
        total_to_run=total_random_runs,
        num_cores=30
    )

    # 5. Side-by-Side Comparison
    print_comparison(
        highs_df=highs_df,
        highs_time=highs_time,
        random_df=random_df,
        random_time=random_time,
        total_random_runs=total_random_runs,
        num_cores=30,
        candidate_df=wkend_wts_df,
    )

    # 6. Production Closed-Loop Triathlon Training Optimizer (Dual-Load & 3-Tier MPC)
    closed_loop_schedule = run_closed_loop_optimizer_demo(df=wkend_wts_df)

    # 7. Closed-Loop Multi-Month Season Generation (16-Week Olympic Periodization)
    season_daily, season_summary = run_closed_loop_season_demo(distance='olympic')

    # 8. Dual-Objective Hybrid Athlete Optimizer (Balancing Swim, Bike, Run, & Lifting)
    hybrid_plans = run_hybrid_athlete_demo()


if __name__ == '__main__':
    main()
