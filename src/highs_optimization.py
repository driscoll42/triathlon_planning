import os
import logging
import multiprocessing as mp
import highspy
import numpy as np
import pandas as pd
from functools import partial

from highs_runtime import apply_threads, run_and_check

logger = logging.getLogger(__name__)

DAYS_OF_WEEK =['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

def prepare_workout_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardizes column names for HiGHS optimization.
    Accepts DataFrames from load_workouts (Title Case) or pre-normalized schemas.
    """
    df = df.copy()
    col_mapping = {
        'Code': 'code',
        'Type': 'sport',
        'TSS': 'tss',
        'Duration': 'duration',
        'Zone 3': 'z3_dur',
        'Zone 4': 'z4_dur',
        'Zone 5': 'z5_dur',
        'Intensity Factor': 'intensity_factor',
        'Description': 'description',
    }
    for old_col, new_col in col_mapping.items():
        if old_col in df.columns and new_col not in df.columns:
            df[new_col] = df[old_col]

    if 'z1_z2_dur' not in df.columns:
        if 'Zone 1' in df.columns and 'Zone 2' in df.columns:
            df['z1_z2_dur'] = df['Zone 1'].astype(float) + df['Zone 2'].astype(float)
        elif 'Low Duration' in df.columns:
            df['z1_z2_dur'] = df['Low Duration'].astype(float)
        else:
            df['z1_z2_dur'] = 0.0

    for col in ['tss', 'duration', 'z1_z2_dur', 'z3_dur', 'z4_dur', 'z5_dur']:
        if col in df.columns:
            df[col] = df[col].astype(float)

    if 'intensity_factor' not in df.columns:
        high = df['z3_dur'] + df['z4_dur'] + df['z5_dur']
        df['intensity_factor'] = (high / df['duration'].clip(lower=1.0) * 1.5).clip(0.6, 1.2)

    return df.reset_index(drop=True)


def filter_viable_workouts(
    df: pd.DataFrame,
    target_tss: float = 500.0,
    num_workouts: int = 6,
    max_high_ratio: float = 0.20,
    min_duration: float = 20.0,
    max_high_intensity_pct: float = 0.50,
) -> pd.DataFrame:
    """
    Input-driven candidate pruning: Eliminates workouts that could mathematically
    never be part of a high-quality weekly plan given target TSS and zone ceilings:
    1. Eliminates micro-workouts with TSS < target_tss / (num_workouts * 3.5) or duration < 20 min.
    2. Eliminates extreme high-intensity outliers (high% > 50%), which would blow past the weekly 20% cap.
    3. Deduplicates identical numeric workout profiles.
    """
    min_viable_tss = target_tss / (num_workouts * 3.5)
    high_dur = df['z3_dur'] + df['z4_dur'] + df['z5_dur']
    high_pct = high_dur / df['duration'].clip(lower=1.0)

    mask = (
        (df['tss'] >= min_viable_tss) &
        (df['duration'] >= min_duration) &
        (high_pct <= max_high_intensity_pct)
    )
    pruned = df[mask].drop_duplicates(
        subset=['sport', 'tss', 'duration', 'z1_z2_dur', 'z3_dur', 'z4_dur', 'z5_dur']
    ).reset_index(drop=True)

    return pruned


def get_periodized_zone_ratios(weeks_to_race: int = 12, race_distance: str = 'olympic') -> dict:
    """
    Calculates dynamic pyramidal periodization zone ratios based on the empirical 85/15 target.
    
    - Base / General Phase (>= 12 weeks out): 85% Z1/2, 11.5% Z3, 2.3% Z4, 1.2% Z5 (~76% Z3, 16% Z4, 8% Z5 within High)
    - Specific Phase (6-11 weeks out): 85% Z1/2, 12.8% Z3, 1.5% Z4, 0.7% Z5 (~85% Z3, 10% Z4, 5% Z5 within High)
    - Race-Specific Phase (2-5 weeks out): 85% Z1/2, 13.5% Z3, 1.1% Z4, 0.4% Z5 (~90% Z3, 7% Z4, 3% Z5 within High)
    - Taper Phase (0-1 weeks out): 85% Z1/2, 12.0% Z3, 1.8% Z4, 1.2% Z5 (~80% Z3, 12% Z4, 8% Z5 within High)
    """
    if weeks_to_race <= 1:
        # Taper sharpening
        return {'z1_z2_dur': 0.85, 'z3_dur': 0.120, 'z4_dur': 0.018, 'z5_dur': 0.012}
    elif weeks_to_race <= 4:
        # Race-specific threshold / tempo
        return {'z1_z2_dur': 0.85, 'z3_dur': 0.135, 'z4_dur': 0.011, 'z5_dur': 0.004}
    elif weeks_to_race <= 9:
        # Specific build
        return {'z1_z2_dur': 0.85, 'z3_dur': 0.128, 'z4_dur': 0.015, 'z5_dur': 0.007}
    else:
        # Base / General foundation
        return {'z1_z2_dur': 0.85, 'z3_dur': 0.115, 'z4_dur': 0.023, 'z5_dur': 0.012}


def optimize_weekly_workouts(
    df: pd.DataFrame,
    target_tss: float = 500.0,
    num_workouts: int = 6,
    bike_dur_ratio: float = 0.40,
    run_dur_ratio: float = 0.28,
    swim_dur_ratio: float = 0.32,
    high_ratio_per_sport: float = 0.15,
    z3_high_ratio: float = 0.80,
    z4_high_ratio: float = 0.14,
    z5_high_ratio: float = 0.06,
    tss_tolerance: float = 0.05,
    max_high_ratio: float = 0.20,
    top_k_plans: int = 5,
    min_workout_diff: int = 2,
    time_limit: float = None,
    mip_abs_gap: float = 0.005,
    mip_rel_gap: float = None,
    threads: int = None,
    random_seed: int = None,
    prune_candidates: bool = True,
    mip_heuristic_effort: float = 0.20,
) -> pd.DataFrame:
    """
    Finds globally optimal workout plans using HiGHS MILP with tightened sport bounds
    and full pyramidal zone alignment (default 85/15 target).
    """
    df = prepare_workout_df(df)

    if prune_candidates:
        df = filter_viable_workouts(
            df=df,
            target_tss=target_tss,
            num_workouts=num_workouts,
            max_high_ratio=max_high_ratio,
        )

    n = len(df)
    is_bike = (df["sport"] == "Bike").astype(float).to_numpy()
    is_run = (df["sport"] == "Run").astype(float).to_numpy()
    is_swim = (df["sport"] == "Swim").astype(float).to_numpy()
    is_brick = (df["sport"] == "Brick").astype(float).to_numpy()
    tss = df["tss"].to_numpy()
    dur = df["duration"].to_numpy()
    high_dur = (df["z3_dur"] + df["z4_dur"] + df["z5_dur"]).to_numpy()
    z3 = df["z3_dur"].to_numpy()
    z4 = df["z4_dur"].to_numpy()
    z5 = df["z5_dur"].to_numpy()

    est_total_dur = target_tss * (df["duration"].sum() / df["tss"].sum())

    h = highspy.Highs()
    h.setOptionValue("log_to_console", False)

    if time_limit is not None:
        h.setOptionValue("time_limit", float(time_limit))
    if mip_abs_gap is not None:
        h.setOptionValue("mip_abs_gap", float(mip_abs_gap))
    if mip_rel_gap is not None:
        h.setOptionValue("mip_rel_gap", float(mip_rel_gap))
    apply_threads(h, threads)
    if random_seed is not None:
        h.setOptionValue("random_seed", int(random_seed))
    if mip_heuristic_effort is not None:
        h.setOptionValue("mip_heuristic_effort", float(mip_heuristic_effort))

    # 1. Binary Decision Variables
    x = [h.addBinary(name=f"x_{i}") for i in range(n)]

    obj_terms = []
    def add_soft_goal(expr, scale: float, weight: float, name: str):
        e_pos = h.addVariable(lb=0.0, name=f"{name}_pos")
        e_neg = h.addVariable(lb=0.0, name=f"{name}_neg")
        h.addConstr(expr == e_pos - e_neg)
        obj_terms.append((weight / scale) * (e_pos + e_neg))

    # 2. Hard Constraints
    total_tss_expr = h.qsum(tss[i] * x[i] for i in range(n))
    total_dur_expr = h.qsum(dur[i] * x[i] for i in range(n))
    total_high_expr = h.qsum(high_dur[i] * x[i] for i in range(n))

    # Exact number of workouts
    h.addConstr(h.qsum(x) == num_workouts)

    if is_swim.sum() > 0 and swim_dur_ratio > 0.05:
        exp_swims = int(round(num_workouts * swim_dur_ratio))
        min_swims = max(1, exp_swims - 1)
        max_swims = min(num_workouts - 2, exp_swims + 1)
        h.addConstr(h.qsum(is_swim[i] * x[i] for i in range(n)) >= min_swims)
        h.addConstr(h.qsum(is_swim[i] * x[i] for i in range(n)) <= max_swims)

        exp_bikes = int(round(num_workouts * bike_dur_ratio))
        min_bikes = max(1, exp_bikes - 1)
        max_bikes = min(num_workouts - 2, exp_bikes + 1)
        h.addConstr(h.qsum((is_bike[i] + is_brick[i]) * x[i] for i in range(n)) >= min_bikes)
        h.addConstr(h.qsum((is_bike[i] + is_brick[i]) * x[i] for i in range(n)) <= max_bikes)

        exp_runs = int(round(num_workouts * run_dur_ratio))
        min_runs = max(1, exp_runs - 1)
        max_runs = min(num_workouts - 2, exp_runs + 1)
        h.addConstr(h.qsum(is_run[i] * x[i] for i in range(n)) >= min_runs)
        h.addConstr(h.qsum(is_run[i] * x[i] for i in range(n)) <= max_runs)
    else:
        exp_bikes = int(round(num_workouts * bike_dur_ratio))
        min_bikes = max(1, exp_bikes - 1)
        max_bikes = min(num_workouts - 1, exp_bikes + 1)
        min_runs = max(1, num_workouts - max_bikes)
        max_runs = min(num_workouts - 1, num_workouts - min_bikes)
        h.addConstr(h.qsum((is_bike[i] + is_brick[i]) * x[i] for i in range(n)) >= min_bikes)
        h.addConstr(h.qsum((is_bike[i] + is_brick[i]) * x[i] for i in range(n)) <= max_bikes)
        h.addConstr(h.qsum(is_run[i] * x[i] for i in range(n)) >= min_runs)
        h.addConstr(h.qsum(is_run[i] * x[i] for i in range(n)) <= max_runs)

    # Hard Guardrails
    h.addConstr(total_tss_expr >= target_tss * (1.0 - tss_tolerance))
    h.addConstr(total_tss_expr <= target_tss * (1.0 + tss_tolerance))
    h.addConstr(total_high_expr - max_high_ratio * total_dur_expr <= 0)

    # 3. Soft Goal Constraints
    add_soft_goal(total_tss_expr - target_tss, scale=target_tss, weight=1.0, name="tss")
    bike_dur_diff = h.qsum((is_bike[i] + is_brick[i] - bike_dur_ratio) * dur[i] * x[i] for i in range(n))
    add_soft_goal(bike_dur_diff, scale=est_total_dur, weight=1.0, name="bike_dur_split")

    run_dur_diff = h.qsum((is_run[i] - run_dur_ratio) * dur[i] * x[i] for i in range(n))
    add_soft_goal(run_dur_diff, scale=est_total_dur, weight=1.0, name="run_dur_split")

    if is_swim.sum() > 0 and swim_dur_ratio > 0.05:
        swim_dur_diff = h.qsum((is_swim[i] - swim_dur_ratio) * dur[i] * x[i] for i in range(n))
        add_soft_goal(swim_dur_diff, scale=est_total_dur, weight=1.0, name="swim_dur_split")

    overall_high_diff = h.qsum((high_dur[i] - high_ratio_per_sport * dur[i]) * x[i] for i in range(n))
    add_soft_goal(overall_high_diff, scale=est_total_dur, weight=1.0, name="overall_high")

    bike_scale = est_total_dur * bike_dur_ratio
    run_scale = est_total_dur * run_dur_ratio
    bike_high_diff = h.qsum(is_bike[i] * (high_dur[i] - high_ratio_per_sport * dur[i]) * x[i] for i in range(n))
    run_high_diff = h.qsum(is_run[i] * (high_dur[i] - high_ratio_per_sport * dur[i]) * x[i] for i in range(n))
    add_soft_goal(bike_high_diff, scale=bike_scale, weight=1.0, name="bike_high")
    add_soft_goal(run_high_diff, scale=run_scale, weight=1.0, name="run_high")

    z3_tgt_dur = z3_high_ratio * high_ratio_per_sport
    z4_tgt_dur = z4_high_ratio * high_ratio_per_sport
    z5_tgt_dur = z5_high_ratio * high_ratio_per_sport

    add_soft_goal(h.qsum(is_bike[i] * (z3[i] - z3_tgt_dur * dur[i]) * x[i] for i in range(n)), bike_scale, 1.0, name="bike_z3")
    add_soft_goal(h.qsum(is_bike[i] * (z4[i] - z4_tgt_dur * dur[i]) * x[i] for i in range(n)), bike_scale, 1.0, name="bike_z4")
    add_soft_goal(h.qsum(is_bike[i] * (z5[i] - z5_tgt_dur * dur[i]) * x[i] for i in range(n)), bike_scale, 1.0, name="bike_z5")

    add_soft_goal(h.qsum(is_run[i] * (z3[i] - z3_tgt_dur * dur[i]) * x[i] for i in range(n)), run_scale, 1.0, name="run_z3")
    add_soft_goal(h.qsum(is_run[i] * (z4[i] - z4_tgt_dur * dur[i]) * x[i] for i in range(n)), run_scale, 1.0, name="run_z4")
    add_soft_goal(h.qsum(is_run[i] * (z5[i] - z5_tgt_dur * dur[i]) * x[i] for i in range(n)), run_scale, 1.0, name="run_z5")

    # 4. Minimize total weighted penalty
    h.minimize(h.qsum(obj_terms))

    # 5. Extract Top-K distinct plans
    plans = []
    for _ in range(top_k_plans):
        res = run_and_check(h)
        if not res.has_solution or len(res.col_value) < n:
            break

        chosen_idx = [i for i in range(n) if res.col_value[i] > 0.5]
        if len(chosen_idx) != num_workouts:
            break

        sub = df.iloc[chosen_idx]

        tot_dur = sub["duration"].sum()
        tot_tss = sub["tss"].sum()
        b_sub = sub[sub["sport"].isin(["Bike", "Brick"])]
        r_sub = sub[sub["sport"] == "Run"]
        s_sub = sub[sub["sport"] == "Swim"]

        b_dur, r_dur, s_dur = b_sub["duration"].sum(), r_sub["duration"].sum(), s_sub["duration"].sum()
        b_high = (b_sub["z3_dur"] + b_sub["z4_dur"] + b_sub["z5_dur"]).sum()
        r_high = (r_sub["z3_dur"] + r_sub["z4_dur"] + r_sub["z5_dur"]).sum()
        s_high = (s_sub["z3_dur"] + s_sub["z4_dur"] + s_sub["z5_dur"]).sum()
        tot_high = b_high + r_high + s_high

        penalty = res.objective
        score = max(0.0, 100.0 * (1.0 - penalty / 8.0))

        plans.append({
            "Workouts": sorted(list(sub["code"])),
            "Score": round(score, 2),
            "Status": res.status,
            "MIP Gap": res.mip_gap,
            "Total Duration": round(tot_dur, 1),
            "Total TSS": round(tot_tss, 2),
            "% Bike Dur": round(b_dur / tot_dur, 2) if tot_dur else 0.0,
            "% Run Dur": round(r_dur / tot_dur, 2) if tot_dur else 0.0,
            "% Swim Dur": round(s_dur / tot_dur, 2) if tot_dur else 0.0,
            "% High Dur": round(tot_high / tot_dur, 2) if tot_dur else 0.0,
            "% Low Dur": round(1.0 - (tot_high / tot_dur), 2) if tot_dur else 0.0,
            "% Run TSS": round(r_sub["tss"].sum() / tot_tss, 2) if tot_tss else 0.0,
            "% Bike TSS": round(b_sub["tss"].sum() / tot_tss, 2) if tot_tss else 0.0,
            "% Swim TSS": round(s_sub["tss"].sum() / tot_tss, 2) if tot_tss else 0.0,
            "% High Run Dur": round(r_high / r_dur, 2) if r_dur else 0.0,
            "% Low Run Dur": round(1.0 - (r_high / r_dur), 2) if r_dur else 0.0,
            "% High Bike Dur": round(b_high / b_dur, 2) if b_dur else 0.0,
            "% Low Bike Dur": round(1.0 - (b_high / b_dur), 2) if b_dur else 0.0,
        })

        h.addConstr(h.qsum(x[i] for i in chosen_idx) <= num_workouts - min_workout_diff)

    return pd.DataFrame(plans)


def optimize_weekly_schedule(
    df: pd.DataFrame,
    target_tss: float = 500.0,
    num_workouts: int = 6,
    day_max_durations: dict = None,
    forbid_consecutive_hard: bool = True,
    forbid_consecutive_long_runs: bool = True,
    hard_threshold_high_dur: float = 15.0,
    hard_threshold_if: float = 0.85,
    long_run_threshold_dur: float = 75.0,
    bike_dur_ratio: float = 0.40,
    run_dur_ratio: float = 0.28,
    swim_dur_ratio: float = 0.32,
    high_ratio_per_sport: float = 0.15,
    z3_high_ratio: float = 0.80,
    z4_high_ratio: float = 0.14,
    z5_high_ratio: float = 0.06,
    tss_tolerance: float = 0.05,
    max_high_ratio: float = 0.20,
    time_limit: float = 5.0,
    threads: int = 2,
    random_seed: int = None,
    prune_candidates: bool = True,
) -> pd.DataFrame:
    """
    Day-by-Day Scheduling MILP with Physiological Scheduling Rules:
    
    1. Indexed binary variables: x[i, d] in {0, 1} for each workout i and day d in Mon..Sun.
    2. At most 1 workout per day (or 0 for rest days).
    3. Day-specific duration caps: sum_i Dur_i * x[i, d] <= max_minutes_d.
    4. Hard/Easy Rule (Matt Fitzgerald): Forbids consecutive days with high intensity
       on Bike and Run (High Duration >= 15 min or IF >= 0.85). Swim is exempt as active recovery.
    5. Injury Prevention: Forbids consecutive long runs (Run duration >= 75 min).
    6. Weekly 85/15 low/high intensity & sport duration splits fully optimized across the calendar.
    """
    df = prepare_workout_df(df)
    if prune_candidates:
        df = filter_viable_workouts(
            df=df,
            target_tss=target_tss,
            num_workouts=num_workouts,
            max_high_ratio=max_high_ratio,
        )

    # Default day duration caps: Mon=Rest (0m), Tue-Fri=Weekday caps (75m), Sat=150m, Sun=120m
    if day_max_durations is None:
        day_max_durations = {
            'Mon': 0.0,
            'Tue': 75.0,
            'Wed': 75.0,
            'Thu': 75.0,
            'Fri': 75.0,
            'Sat': 150.0,
            'Sun': 120.0,
        }

    days = DAYS_OF_WEEK
    num_days = len(days)
    max_dur = [day_max_durations.get(d, 90.0) for d in days]

    n = len(df)
    is_bike = (df["sport"] == "Bike").astype(float).to_numpy()
    is_run = (df["sport"] == "Run").astype(float).to_numpy()
    is_swim = (df["sport"] == "Swim").astype(float).to_numpy()
    is_brick = (df["sport"] == "Brick").astype(float).to_numpy()
    tss = df["tss"].to_numpy()
    dur = df["duration"].to_numpy()
    high_dur = (df["z3_dur"] + df["z4_dur"] + df["z5_dur"]).to_numpy()
    z3 = df["z3_dur"].to_numpy()
    z4 = df["z4_dur"].to_numpy()
    z5 = df["z5_dur"].to_numpy()

    if_arr = df['intensity_factor'].to_numpy()
    is_hard = ((high_dur >= hard_threshold_high_dur) | (if_arr >= hard_threshold_if)).astype(float)
    # Lesson 1: Hard/Easy Rule forbids consecutive hard days on Bike, Run, Brick (Swim is active recovery exception)
    is_hard_bike_run = ((is_hard > 0.5) & ((is_bike > 0.5) | (is_run > 0.5) | (is_brick > 0.5))).astype(float)
    is_long_run = ((is_run > 0.5) & (dur >= long_run_threshold_dur)).astype(float)

    est_total_dur = target_tss * (df["duration"].sum() / df["tss"].sum())

    h = highspy.Highs()
    h.setOptionValue("log_to_console", False)
    if time_limit is not None:
        h.setOptionValue("time_limit", float(time_limit))
    apply_threads(h, threads)
    if random_seed is not None:
        h.setOptionValue("random_seed", int(random_seed))
    h.setOptionValue("mip_heuristic_effort", 0.20)
    h.setOptionValue("mip_abs_gap", 0.01)

    # 1. Binary Decision Variables x[i, d]
    x = {}
    for i in range(n):
        for d in range(num_days):
            x[i, d] = h.addBinary(name=f"x_{i}_{d}")

    # Each workout used at most once in the week
    for i in range(n):
        h.addConstr(h.qsum(x[i, d] for d in range(num_days)) <= 1)

    # At most 1 workout per day (or 0 if max duration is 0)
    for d in range(num_days):
        h.addConstr(h.qsum(x[i, d] for i in range(n)) <= (1 if max_dur[d] > 0 else 0))

    # Exact number of workouts in the week
    h.addConstr(h.qsum(x[i, d] for i in range(n) for d in range(num_days)) == num_workouts)

    # Day-Specific Duration Caps
    for d in range(num_days):
        if max_dur[d] > 0:
            h.addConstr(h.qsum(dur[i] * x[i, d] for i in range(n)) <= max_dur[d])

    # Hard/Easy Rule: No consecutive hard days on Bike, Run, Brick
    if forbid_consecutive_hard:
        for d in range(num_days - 1):
            h.addConstr(
                h.qsum(is_hard_bike_run[i] * x[i, d] for i in range(n)) +
                h.qsum(is_hard_bike_run[i] * x[i, d + 1] for i in range(n)) <= 1
            )

    # Injury Prevention: No consecutive long runs
    if forbid_consecutive_long_runs:
        for d in range(num_days - 1):
            h.addConstr(
                h.qsum(is_long_run[i] * x[i, d] for i in range(n)) +
                h.qsum(is_long_run[i] * x[i, d + 1] for i in range(n)) <= 1
            )

    # Tightened Sport Count Bounds
    if is_swim.sum() > 0 and swim_dur_ratio > 0.05:
        exp_swims = int(round(num_workouts * swim_dur_ratio))
        min_swims = max(1, exp_swims - 1)
        max_swims = min(num_workouts - 2, exp_swims + 1)
        h.addConstr(h.qsum(is_swim[i] * x[i, d] for i in range(n) for d in range(num_days)) >= min_swims)
        h.addConstr(h.qsum(is_swim[i] * x[i, d] for i in range(n) for d in range(num_days)) <= max_swims)

        exp_bikes = int(round(num_workouts * bike_dur_ratio))
        min_bikes = max(1, exp_bikes - 1)
        max_bikes = min(num_workouts - 2, exp_bikes + 1)
        h.addConstr(h.qsum((is_bike[i] + is_brick[i]) * x[i, d] for i in range(n) for d in range(num_days)) >= min_bikes)
        h.addConstr(h.qsum((is_bike[i] + is_brick[i]) * x[i, d] for i in range(n) for d in range(num_days)) <= max_bikes)

        exp_runs = int(round(num_workouts * run_dur_ratio))
        min_runs = max(1, exp_runs - 1)
        max_runs = min(num_workouts - 2, exp_runs + 1)
        h.addConstr(h.qsum(is_run[i] * x[i, d] for i in range(n) for d in range(num_days)) >= min_runs)
        h.addConstr(h.qsum(is_run[i] * x[i, d] for i in range(n) for d in range(num_days)) <= max_runs)
    else:
        exp_bikes = int(round(num_workouts * bike_dur_ratio))
        min_bikes = max(1, exp_bikes - 1)
        max_bikes = min(num_workouts - 1, exp_bikes + 1)
        min_runs = max(1, num_workouts - max_bikes)
        max_runs = min(num_workouts - 1, num_workouts - min_bikes)
        h.addConstr(h.qsum((is_bike[i] + is_brick[i]) * x[i, d] for i in range(n) for d in range(num_days)) >= min_bikes)
        h.addConstr(h.qsum((is_bike[i] + is_brick[i]) * x[i, d] for i in range(n) for d in range(num_days)) <= max_bikes)
        h.addConstr(h.qsum(is_run[i] * x[i, d] for i in range(n) for d in range(num_days)) >= min_runs)
        h.addConstr(h.qsum(is_run[i] * x[i, d] for i in range(n) for d in range(num_days)) <= max_runs)

    # Weekly TSS Guardrails
    total_tss_expr = h.qsum(tss[i] * x[i, d] for i in range(n) for d in range(num_days))
    total_dur_expr = h.qsum(dur[i] * x[i, d] for i in range(n) for d in range(num_days))
    total_high_expr = h.qsum(high_dur[i] * x[i, d] for i in range(n) for d in range(num_days))

    h.addConstr(total_tss_expr >= target_tss * (1.0 - tss_tolerance))
    h.addConstr(total_tss_expr <= target_tss * (1.0 + tss_tolerance))
    h.addConstr(total_high_expr - max_high_ratio * total_dur_expr <= 0)

    # Soft Goals
    obj_terms = []
    def add_soft_goal(expr, scale: float, weight: float, name: str):
        e_pos = h.addVariable(lb=0.0, name=f"{name}_pos")
        e_neg = h.addVariable(lb=0.0, name=f"{name}_neg")
        h.addConstr(expr == e_pos - e_neg)
        obj_terms.append((weight / scale) * (e_pos + e_neg))

    add_soft_goal(total_tss_expr - target_tss, scale=target_tss, weight=1.0, name="tss")
    bike_dur_diff = h.qsum((is_bike[i] + is_brick[i] - bike_dur_ratio) * dur[i] * x[i, d] for i in range(n) for d in range(num_days))
    add_soft_goal(bike_dur_diff, scale=est_total_dur, weight=1.0, name="bike_dur_split")

    run_dur_diff = h.qsum((is_run[i] - run_dur_ratio) * dur[i] * x[i, d] for i in range(n) for d in range(num_days))
    add_soft_goal(run_dur_diff, scale=est_total_dur, weight=1.0, name="run_dur_split")

    if is_swim.sum() > 0 and swim_dur_ratio > 0.05:
        swim_dur_diff = h.qsum((is_swim[i] - swim_dur_ratio) * dur[i] * x[i, d] for i in range(n) for d in range(num_days))
        add_soft_goal(swim_dur_diff, scale=est_total_dur, weight=1.0, name="swim_dur_split")

    overall_high_diff = h.qsum((high_dur[i] - high_ratio_per_sport * dur[i]) * x[i, d] for i in range(n) for d in range(num_days))
    add_soft_goal(overall_high_diff, scale=est_total_dur, weight=1.0, name="overall_high")

    bike_scale = est_total_dur * bike_dur_ratio
    run_scale = est_total_dur * run_dur_ratio
    bike_high_diff = h.qsum(is_bike[i] * (high_dur[i] - high_ratio_per_sport * dur[i]) * x[i, d] for i in range(n) for d in range(num_days))
    run_high_diff = h.qsum(is_run[i] * (high_dur[i] - high_ratio_per_sport * dur[i]) * x[i, d] for i in range(n) for d in range(num_days))
    add_soft_goal(bike_high_diff, scale=bike_scale, weight=1.0, name="bike_high")
    add_soft_goal(run_high_diff, scale=run_scale, weight=1.0, name="run_high")

    z3_tgt_dur = z3_high_ratio * high_ratio_per_sport
    z4_tgt_dur = z4_high_ratio * high_ratio_per_sport
    z5_tgt_dur = z5_high_ratio * high_ratio_per_sport

    add_soft_goal(h.qsum(is_bike[i] * (z3[i] - z3_tgt_dur * dur[i]) * x[i, d] for i in range(n) for d in range(num_days)), bike_scale, 1.0, name="bike_z3")
    add_soft_goal(h.qsum(is_bike[i] * (z4[i] - z4_tgt_dur * dur[i]) * x[i, d] for i in range(n) for d in range(num_days)), bike_scale, 1.0, name="bike_z4")
    add_soft_goal(h.qsum(is_bike[i] * (z5[i] - z5_tgt_dur * dur[i]) * x[i, d] for i in range(n) for d in range(num_days)), bike_scale, 1.0, name="bike_z5")

    add_soft_goal(h.qsum(is_run[i] * (z3[i] - z3_tgt_dur * dur[i]) * x[i, d] for i in range(n) for d in range(num_days)), run_scale, 1.0, name="run_z3")
    add_soft_goal(h.qsum(is_run[i] * (z4[i] - z4_tgt_dur * dur[i]) * x[i, d] for i in range(n) for d in range(num_days)), run_scale, 1.0, name="run_z4")
    add_soft_goal(h.qsum(is_run[i] * (z5[i] - z5_tgt_dur * dur[i]) * x[i, d] for i in range(n) for d in range(num_days)), run_scale, 1.0, name="run_z5")

    h.minimize(h.qsum(obj_terms))
    res = run_and_check(h)
    if not res.has_solution:
        logger.warning("optimize_weekly_schedule: no feasible schedule (status=%s, target_tss=%s)",
                       res.status, target_tss)

    schedule = []
    for d, day_name in enumerate(days):
        day_item = None
        for i in range(n):
            idx = x[i, d].index
            if res.has_solution and res.col_value[idx] > 0.5:
                day_item = {
                    'Day': day_name,
                    'Code': df.loc[i, 'code'],
                    'Sport': df.loc[i, 'sport'],
                    'Duration': round(df.loc[i, 'duration'], 1),
                    'TSS': round(df.loc[i, 'tss'], 1),
                    'High_Dur': round(high_dur[i], 1),
                    'Low_Dur': round(df.loc[i, 'z1_z2_dur'], 1),
                    'Intensity': 'HARD' if is_hard[i] else 'EASY',
                    'Description': df.loc[i, 'description'] if 'description' in df.columns else '',
                }
                break
        if day_item:
            schedule.append(day_item)
        else:
            schedule.append({
                'Day': day_name,
                'Code': 'REST',
                'Sport': '-',
                'Duration': 0.0,
                'TSS': 0.0,
                'High_Dur': 0.0,
                'Low_Dur': 0.0,
                'Intensity': 'REST',
                'Description': 'Rest / Active Recovery' if res.has_solution else f'NO SOLUTION ({res.status})',
            })

    out = pd.DataFrame(schedule)
    # Every row carries the solve status so an all-REST week can't be mistaken for a planned rest week.
    out['Solve_Status'] = res.status
    out['MIP_Gap'] = res.mip_gap
    out.attrs['status'] = res.status
    out.attrs['mip_gap'] = res.mip_gap
    return out


def optimize_multi_week_step_cycle(
    df: pd.DataFrame,
    base_tss: float = 500.0,
    step_scale: tuple = (1.00, 1.08, 0.85),
    weeks_to_race: int = 10,
    race_distance: str = '70.3',
    num_workouts: int = 6,
    day_max_durations: dict = None,
    time_limit_per_week: float = 4.0,
    threads: int = 2,
) -> pd.DataFrame:
    """
    Generates a full 3-Week Step Cycle (Matt Fitzgerald Principle) with day-by-day scheduling:
    
    - Week 1: Base Load (1.00 * T0)
    - Week 2: Overload (+8% progression)
    - Week 3: Recovery / Absorption (-15% deload)
    
    Periodized zone targets interpolate dynamically as weeks_to_race decreases.
    """
    all_weeks = []
    labels = ['Week 1 (Base Load)', 'Week 2 (Overload)', 'Week 3 (Recovery)']

    for w_idx, (scale, label) in enumerate(zip(step_scale, labels)):
        week_target_tss = round(base_tss * scale, 1)
        current_wtr = max(1, weeks_to_race - w_idx)
        z_ratios = get_periodized_zone_ratios(weeks_to_race=current_wtr, race_distance=race_distance)

        week_sched = optimize_weekly_schedule(
            df=df,
            target_tss=week_target_tss,
            num_workouts=num_workouts,
            day_max_durations=day_max_durations,
            time_limit=time_limit_per_week,
            threads=threads,
            random_seed=w_idx + 10,
            prune_candidates=True,
            high_ratio_per_sport=round(1.0 - z_ratios['z1_z2_dur'], 2),
            z3_high_ratio=round(z_ratios['z3_dur'] / (1.0 - z_ratios['z1_z2_dur']), 2),
            z4_high_ratio=round(z_ratios['z4_dur'] / (1.0 - z_ratios['z1_z2_dur']), 2),
            z5_high_ratio=round(z_ratios['z5_dur'] / (1.0 - z_ratios['z1_z2_dur']), 2),
        )

        week_sched.insert(0, 'Week_Label', label)
        week_sched.insert(1, 'Target_TSS', week_target_tss)
        all_weeks.append(week_sched)

    return pd.concat(all_weeks, ignore_index=True)


def _solve_seed_worker(args):
    """Worker function for parallel multi-seed search."""
    (df_dict, target_tss, num_workouts, bike_dur_ratio, run_dur_ratio, swim_dur_ratio,
     high_ratio_per_sport, z3_high_ratio, z4_high_ratio, z5_high_ratio,
     tss_tolerance, max_high_ratio, seed, time_limit, mip_abs_gap, threads,
     prune_candidates, mip_heuristic_effort) = args

    df = pd.DataFrame(df_dict)
    res_df = optimize_weekly_workouts(
        df=df,
        target_tss=target_tss,
        num_workouts=num_workouts,
        bike_dur_ratio=bike_dur_ratio,
        run_dur_ratio=run_dur_ratio,
        swim_dur_ratio=swim_dur_ratio,
        high_ratio_per_sport=high_ratio_per_sport,
        z3_high_ratio=z3_high_ratio,
        z4_high_ratio=z4_high_ratio,
        z5_high_ratio=z5_high_ratio,
        tss_tolerance=tss_tolerance,
        max_high_ratio=max_high_ratio,
        top_k_plans=1,
        time_limit=time_limit,
        mip_abs_gap=mip_abs_gap,
        random_seed=seed,
        threads=threads,
        prune_candidates=prune_candidates,
        mip_heuristic_effort=mip_heuristic_effort,
    )
    if not res_df.empty:
        plan_dict = res_df.iloc[0].to_dict()
        plan_dict["seed"] = seed
        return plan_dict
    return None


def optimize_weekly_workouts_multiseed(
    df: pd.DataFrame,
    target_tss: float = 500.0,
    num_workouts: int = 6,
    num_workers: int = 15,
    threads_per_worker: int = 2,
    time_limit_per_worker: float = 4.0,
    mip_abs_gap: float = 0.005,
    top_k_plans: int = 5,
    bike_dur_ratio: float = 0.40,
    run_dur_ratio: float = 0.28,
    swim_dur_ratio: float = 0.32,
    high_ratio_per_sport: float = 0.15,
    z3_high_ratio: float = 0.80,
    z4_high_ratio: float = 0.14,
    z5_high_ratio: float = 0.06,
    tss_tolerance: float = 0.05,
    max_high_ratio: float = 0.20,
    prune_candidates: bool = True,
    mip_heuristic_effort: float = 0.20,
) -> pd.DataFrame:
    """
    Races multiple HiGHS MILP instances in parallel across available CPU cores,
    each using a different random seed to explore distinct branching paths.
    
    Default configuration:
    - 15 seeds × 2 threads (perfect 30-core saturation with peak score performance)
    - 4.0s per worker limit
    - Automated input-driven candidate pruning enabled
    
    Returns deduplicated, top-ranked distinct plans.
    """
    df_prep = prepare_workout_df(df)
    if prune_candidates:
        df_prep = filter_viable_workouts(
            df=df_prep,
            target_tss=target_tss,
            num_workouts=num_workouts,
            max_high_ratio=max_high_ratio,
        )

    df_dict = df_prep.to_dict(orient="list")

    tasks = [
        (
            df_dict, target_tss, num_workouts, bike_dur_ratio, run_dur_ratio, swim_dur_ratio,
            high_ratio_per_sport, z3_high_ratio, z4_high_ratio, z5_high_ratio,
            tss_tolerance, max_high_ratio, seed, time_limit_per_worker,
            mip_abs_gap, threads_per_worker,
            False,
            mip_heuristic_effort
        )
        for seed in range(1, num_workers + 1)
    ]

    with mp.Pool(processes=num_workers) as pool:
        raw_results = pool.map(_solve_seed_worker, tasks)

    valid_plans = [r for r in raw_results if r is not None]
    valid_plans.sort(key=lambda x: x["Score"], reverse=True)

    seen = set()
    distinct_plans = []
    for p in valid_plans:
        key = tuple(p["Workouts"])
        if key not in seen:
            seen.add(key)
            distinct_plans.append(p)
            if len(distinct_plans) >= top_k_plans:
                break

    return pd.DataFrame(distinct_plans)


def evaluate_plan_with_original_score(wkt_codes: list, raw_df: pd.DataFrame, target_tss: float = None) -> float:
    """
    Evaluates a plan (list of workout codes) using the original calculate_plan_score
    function and triVariables from classes.py.
    """
    from classes import triVariables as triv, workout
    from util import calculate_plan_score

    chosen = raw_df[raw_df["Code"].isin(wkt_codes)]
    wkt = workout()
    wkt.plan = list(chosen["Code"])
    wkt.total_tss = chosen["TSS"].sum()
    wkt.duration = chosen["Duration"].sum()
    wkt.high_dur = chosen["High Duration"].sum()
    wkt.low_dur = chosen["Low Duration"].sum()
    wkt.high_pct = wkt.high_dur / wkt.duration if wkt.duration > 0 else 0.0

    if triv.run_tgt_percent > 0:
        run_wkts = chosen[chosen["Type"] == "Run"]
        wkt.run.tgt_percent = triv.run_tgt_percent
        wkt.run.dur = run_wkts["Duration"].sum()
        wkt.run.high_dur = run_wkts["High Duration"].sum()
        wkt.run.low_dur = run_wkts["Low Duration"].sum()
        wkt.run.zone_3 = run_wkts["Zone 3"].sum()
        wkt.run.zone_4 = run_wkts["Zone 4"].sum()
        wkt.run.zone_5 = run_wkts["Zone 5"].sum()
        wkt.run.tss = run_wkts["TSS"].sum()

    if triv.bike_tgt_percent > 0:
        bike_wkts = chosen[chosen["Type"].isin(["Bike", "Brick"])]
        wkt.bike.tgt_percent = triv.bike_tgt_percent
        wkt.bike.dur = bike_wkts["Duration"].sum()
        wkt.bike.high_dur = bike_wkts["High Duration"].sum()
        wkt.bike.low_dur = bike_wkts["Low Duration"].sum()
        wkt.bike.zone_3 = bike_wkts["Zone 3"].sum()
        wkt.bike.zone_4 = bike_wkts["Zone 4"].sum()
        wkt.bike.zone_5 = bike_wkts["Zone 5"].sum()
        wkt.bike.tss = bike_wkts["TSS"].sum()

    if triv.swim_tgt_percent > 0:
        swim_wkts = chosen[chosen["Type"] == "Swim"]
        wkt.swim.tgt_percent = triv.swim_tgt_percent
        wkt.swim.dur = swim_wkts["Duration"].sum()
        wkt.swim.high_dur = swim_wkts["High Duration"].sum()
        wkt.swim.low_dur = swim_wkts["Low Duration"].sum()
        wkt.swim.zone_3 = swim_wkts["Zone 3"].sum()
        wkt.swim.zone_4 = swim_wkts["Zone 4"].sum()
        wkt.swim.zone_5 = swim_wkts["Zone 5"].sum()
        wkt.swim.tss = swim_wkts["TSS"].sum()

    old_tss = triv.target_tss
    if target_tss is not None:
        triv.target_tss = target_tss

    calculate_plan_score(wkt, verbose=False)
    score = round(wkt.score, 2)
    triv.target_tss = old_tss
    return score


def _solve_olympic_week_worker(args):
    """
    Multiprocessing worker function to solve a single week of the 16-Week Olympic Schedule.
    """
    (w, phase, block_label, target_tss, wtr, df_dict, threads, time_limit) = args
    from classes import triVariables as triv
    triv.set_distance_profile('olympic')
    triv.target_tss = target_tss

    df = pd.DataFrame(df_dict)
    z_ratios = get_periodized_zone_ratios(weeks_to_race=wtr, race_distance='olympic')
    high_ratio = round(1.0 - z_ratios['z1_z2_dur'], 2)
    z3_ratio = round(z_ratios['z3_dur'] / high_ratio, 2)
    z4_ratio = round(z_ratios['z4_dur'] / high_ratio, 2)
    z5_ratio = round(z_ratios['z5_dur'] / high_ratio, 2)

    if w == 16:
        # Race week: light sessions with race tuneup
        day_caps = {'Mon': 0.0, 'Tue': 40.0, 'Wed': 40.0, 'Thu': 45.0, 'Fri': 30.0, 'Sat': 45.0, 'Sun': 60.0}
    elif 'Recovery' in block_label:
        day_caps = {'Mon': 0.0, 'Tue': 50.0, 'Wed': 50.0, 'Thu': 60.0, 'Fri': 45.0, 'Sat': 90.0, 'Sun': 75.0}
    else:
        day_caps = {'Mon': 0.0, 'Tue': 60.0, 'Wed': 60.0, 'Thu': 75.0, 'Fri': 50.0, 'Sat': 120.0, 'Sun': 90.0}

    sched = optimize_weekly_schedule(
        df=df,
        target_tss=target_tss,
        num_workouts=6 if w < 16 else 5,
        day_max_durations=day_caps,
        forbid_consecutive_hard=True,
        forbid_consecutive_long_runs=True,
        bike_dur_ratio=0.40,
        run_dur_ratio=0.28,
        swim_dur_ratio=0.32,
        high_ratio_per_sport=high_ratio,
        z3_high_ratio=z3_ratio,
        z4_high_ratio=z4_ratio,
        z5_high_ratio=z5_ratio,
        max_high_ratio=0.20,
        time_limit=time_limit,
        threads=threads,
        random_seed=w * 23,
        prune_candidates=True,
    )

    sched.insert(0, 'Week', w)
    sched.insert(1, 'Phase', phase)
    sched.insert(2, 'Block_Label', block_label)

    assigned = [c for c in sched['Code'] if c != 'REST']
    score = evaluate_plan_with_original_score(assigned, df, target_tss=target_tss)
    return sched, score


def generate_16_week_olympic_schedule(
    df: pd.DataFrame,
    num_workers: int = 16,
    time_limit_per_week: float = 3.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generates an optimized 16-Week Olympic Distance Schedule (Level 0 Equivalent)
    incorporating empirical 85/15 intensity, 3-week step cycles, dynamic periodization,
    the Hard/Easy Rule (with Swim exemption), and day-specific duration caps.
    """
    matrix = [
        (1, 'General Foundation', 'Base Load', 260.0, 15),
        (2, 'General Foundation', 'Build (+12%)', 290.0, 14),
        (3, 'General Foundation', 'Recovery Deload (-17%)', 240.0, 13),
        (4, 'Aerobic Capacity', 'Base Load', 320.0, 12),
        (5, 'Aerobic Capacity', 'Overload (+12%)', 360.0, 11),
        (6, 'Aerobic Capacity', 'Recovery Deload (-18%)', 295.0, 10),
        (7, 'Specific Threshold', 'Base Load', 390.0, 9),
        (8, 'Specific Threshold', 'Overload (+10%)', 430.0, 8),
        (9, 'Specific Threshold', 'Recovery Deload (-20%)', 345.0, 7),
        (10, 'Peak Threshold', 'Base Load', 450.0, 6),
        (11, 'Peak Threshold', 'Peak Overload (+11%)', 500.0, 5),
        (12, 'Peak Threshold', 'Recovery Deload (-24%)', 380.0, 4),
        (13, 'Race Specific', 'Race Pace 1 (Brick Focus)', 450.0, 3),
        (14, 'Race Specific', 'Race Pace 2 (Simulation)', 460.0, 2),
        (15, 'Taper & Sharpen', 'Taper 1 (-25%)', 340.0, 1),
        (16, 'Race Week', 'Race Week (-50%)', 200.0, 0),
    ]

    df_dict = prepare_workout_df(df).to_dict(orient='list')
    workers = min(num_workers, mp.cpu_count())
    tasks = [
        (m[0], m[1], m[2], m[3], m[4], df_dict, 1, time_limit_per_week)
        for m in matrix
    ]

    with mp.Pool(processes=workers) as pool:
        results = pool.map(_solve_olympic_week_worker, tasks)

    all_daily = []
    summary_rows = []
    for m, (sched, score) in zip(matrix, results):
        all_daily.append(sched)
        tot_dur = sched['Duration'].sum()
        tot_tss = sched['TSS'].sum()
        tot_high = sched['High_Dur'].sum()
        tot_low = sched['Low_Dur'].sum()

        b_dur = sched[sched['Sport'].isin(['Bike', 'Brick'])]['Duration'].sum()
        r_dur = sched[sched['Sport'] == 'Run']['Duration'].sum()
        s_dur = sched[sched['Sport'] == 'Swim']['Duration'].sum()

        summary_rows.append({
            'Week': m[0],
            'Phase': m[1],
            'Step_Type': m[2],
            'Target_TSS': m[3],
            'Actual_TSS': round(tot_tss, 1),
            'Hours': f"{tot_dur / 60.0:.1f}h",
            '% Low': f"{tot_low / tot_dur * 100:.1f}%" if tot_dur else "0%",
            '% High': f"{tot_high / tot_dur * 100:.1f}%" if tot_dur else "0%",
            'Bike%': f"{b_dur / tot_dur * 100:.0f}%" if tot_dur else "0%",
            'Run%': f"{r_dur / tot_dur * 100:.0f}%" if tot_dur else "0%",
            'Swim%': f"{s_dur / tot_dur * 100:.0f}%" if tot_dur else "0%",
            'Quality_Score': score,
        })

    daily_schedule_df = pd.concat(all_daily, ignore_index=True)
    summary_df = pd.DataFrame(summary_rows)
    return daily_schedule_df, summary_df
