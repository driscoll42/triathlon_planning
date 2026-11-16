"""Legacy DataFrame-based HiGHS day scheduling.

TASK-015 / ADR-017: ``optimize_weekly_schedule`` is a thin adapter over the unified week engine
(``triplan.solver.engine``): DataFrame in, same DataFrame-shaped schedule out.

TASK-020 / ADR-018 retired the selection-only ``optimize_weekly_workouts``, the multi-seed race
(``optimize_weekly_workouts_multiseed``, ``_solve_seed_worker``) and the parallel 16-week generator
(``_solve_olympic_week_worker``, ``generate_16_week_olympic_schedule``): a single deterministic solve
through the unified engine, reported with its ``mip_gap``, replaces them.
"""
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Tuple

import pandas as pd

from triplan.solver.engine.config import LegacySpec, legacy_options
from triplan.solver.engine.engine import UnifiedWeekEngine

logger = logging.getLogger(__name__)

DAYS_OF_WEEK =['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


@dataclass(frozen=True)
class DayWorkout:
    """Minimal workout record the unified engine reads for the DataFrame day scheduler."""
    code: str
    sport: str
    duration: float
    aerobic_tss: float
    zone_durations: Tuple[float, float, float, float, float]  # (Z1+Z2 collapsed into slot 1, 0, Z3, Z4, Z5)


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

    # Unified engine (TASK-015, ADR-017): ``single_per_day`` session model, exact workout count, hard/easy
    # and long-run adjacency, sport-count bounds, TSS guardrails, dimensionless soft goals, one solve.
    workouts = [
        DayWorkout(code=str(df.loc[i, 'code']), sport=str(df.loc[i, 'sport']), duration=float(dur[i]),
                   aerobic_tss=float(tss[i]),
                   zone_durations=(float(df.loc[i, 'z1_z2_dur']), 0.0, float(z3[i]), float(z4[i]), float(z5[i])))
        for i in range(n)
    ]
    spec = LegacySpec(
        num_workouts=num_workouts, target_tss=target_tss, tss_tolerance=tss_tolerance,
        max_high_ratio=max_high_ratio, bike_dur_ratio=bike_dur_ratio, run_dur_ratio=run_dur_ratio,
        swim_dur_ratio=swim_dur_ratio, high_ratio_per_sport=high_ratio_per_sport,
        z3_high_ratio=z3_high_ratio, z4_high_ratio=z4_high_ratio, z5_high_ratio=z5_high_ratio,
        day_max=list(max_dur), hard_bike_run=is_hard_bike_run > 0.5, long_run=is_long_run > 0.5,
        forbid_consecutive_hard=forbid_consecutive_hard, forbid_consecutive_long_runs=forbid_consecutive_long_runs,
        threads=threads, random_seed=random_seed,
    )
    engine = UnifiedWeekEngine(workouts, {"daily_time_caps": list(max_dur)}, legacy_options(spec))
    solved = engine.solve(k=1, time_limit=time_limit)
    plan = solved[0] if solved else None
    res = SimpleNamespace(has_solution=plan is not None, status=engine.last_status,
                          mip_gap=plan["mip_gap"] if plan else None)
    if not res.has_solution:
        logger.warning("optimize_weekly_schedule: no feasible schedule (status=%s, target_tss=%s)",
                       res.status, target_tss)
    chosen = {d: i for i, d in plan["selected_pairs"]} if plan else {}  # single_per_day: <=1 workout per day

    schedule = []
    for d, day_name in enumerate(days):
        day_item = None
        if d in chosen:
            i = chosen[d]
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


def evaluate_plan_with_original_score(wkt_codes: list, raw_df: pd.DataFrame, target_tss: float = None) -> float:
    """
    Evaluates a plan (list of workout codes) using the original calculate_plan_score
    function and triVariables from classes.py.
    """
    from triplan.model.classes import triVariables as triv, workout
    from triplan.library.util import calculate_plan_score

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

    calculate_plan_score(wkt, verbose=False, target_tss=target_tss)
    return round(wkt.score, 2)
