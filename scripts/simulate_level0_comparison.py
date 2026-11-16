"""
Simulate and Compare End-Race Readiness Metrics:
All Level 0 Plans (Sprint, Olympic, Half Ironman, Ironman) vs. Closed-Loop Optimizer
Starting from 0 (CTL=0, ATL=0, M(0)=0, C(0)=0).
"""


import pandas as pd
import numpy as np

from triplan.physiology.athlete_profile import AthleteProfile
from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.solver.scheduler import TriathlonMILPScheduler
from triplan.control.mpc import ClosedLoopMPCController
from triplan.physiology.state import AthletePhysiologicalState
from triplan.library.candidates import build_default_candidate_library
from triplan.control.season import generate_closed_loop_season_schedule


def compute_book_mech(row):
    dur = row['duration']
    intensity = row['if']
    sport = row['sport_cat']
    if sport == 'Run':
        return 1.2 * dur * (intensity ** 2)
    elif sport == 'Bike':
        return 0.1 * dur * (intensity ** 2)
    elif sport == 'Swim':
        return 0.05 * dur * (intensity ** 2)
    elif sport == 'Strength':
        return 65.0
    return 0.5 * dur * (intensity ** 2)


def compute_book_durability(row):
    dur = row['duration']
    intensity = row['if']
    if dur <= 45:
        return 1.0
    elif dur <= 75:
        return 3.5 if intensity < 0.8 else 6.0
    elif dur <= 120:
        return 7.0
    else:
        return 9.5


def simulate_daily_stream(daily_records):
    """
    Simulates Banister PMC, tissue remodeling M(t)/C(t), and EWMA ACWR
    starting from absolute ZERO fitness/fatigue, with sedentary biological baseline (M0=20, C0=20).
    """
    alpha_atl = 2.0 / (7.0 + 1.0)
    alpha_ctl = 2.0 / (42.0 + 1.0)
    lambda_m = 1.0 / 35.0
    lambda_c = 1.0 / 120.0
    alpha_acute_mech = 2.0 / (7.0 + 1.0)
    alpha_chronic_mech = 2.0 / (28.0 + 1.0)

    ctl, atl = 0.0, 0.0
    m_t, c_t = 20.0, 20.0
    acute_mech, chronic_mech = 10.0, 10.0
    history = []
    peak_ctl = 0.0

    for rec in daily_records:
        day_tss = rec['tss']
        day_run_mech = rec['run_mech']
        ctl = ctl + alpha_ctl * (day_tss - ctl)
        atl = atl + alpha_atl * (day_tss - atl)
        tsb = ctl - atl
        if ctl > peak_ctl:
            peak_ctl = ctl

        delta_m = (day_tss / 10.0) - lambda_m * m_t
        delta_c = (day_run_mech / 12.0) - lambda_c * c_t
        m_t = max(10.0, m_t + delta_m)
        c_t = max(10.0, c_t + delta_c)
        odi = m_t / c_t if c_t > 0 else 1.0

        acute_mech = acute_mech + alpha_acute_mech * (day_run_mech - acute_mech)
        chronic_mech = chronic_mech + alpha_chronic_mech * (day_run_mech - chronic_mech)
        acwr = acute_mech / chronic_mech if chronic_mech > 0 else 1.0

        history.append({
            'ctl': ctl, 'atl': atl, 'tsb': tsb,
            'odi': odi, 'acwr': acwr, 'peak_ctl': peak_ctl
        })

    final = history[-1]
    return {
        'final_ctl': round(final['ctl'], 1),
        'peak_ctl': round(final['peak_ctl'], 1),
        'final_atl': round(final['atl'], 1),
        'final_tsb': round(final['tsb'], 1),
        'final_odi': round(final['odi'], 2),
        'final_acwr': round(final['acwr'], 2),
        'history': history,
    }


def run_simulation():
    enriched_daily = pd.read_csv(r'training_plans/enriched_daily_preview.csv')
    l0_df = enriched_daily[enriched_daily['level'] == 'Level 0'].copy()

    l0_df['mech_strain'] = l0_df.apply(compute_book_mech, axis=1)
    l0_df['durability'] = l0_df.apply(compute_book_durability, axis=1)

    book_race_results = {}
    for race in ['Sprint', 'Olympic', 'Half Ironman', 'Ironman']:
        race_df = l0_df[l0_df['race'] == race].sort_values(by=['week'])
        num_weeks = race_df['week'].max()

        daily_stream = []
        tot_durability = 0.0
        consec_hard = 0
        day_hard_prev = False

        for wk in range(1, num_weeks + 1):
            wk_sub = race_df[race_df['week'] == wk]
            for d_name in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']:
                day_rows = wk_sub[wk_sub['day'] == d_name]
                day_tss = day_rows['tss'].sum()
                run_rows = day_rows[day_rows['sport_cat'] == 'Run']
                day_run_mech = run_rows['mech_strain'].sum()
                day_is_hard = day_rows['is_hard'].any()
                tot_durability += day_rows['durability'].sum()

                if day_is_hard and day_hard_prev:
                    consec_hard += 1
                day_hard_prev = day_is_hard

                daily_stream.append({
                    'week': wk, 'day': d_name, 'tss': day_tss,
                    'run_mech': day_run_mech, 'is_hard': day_is_hard
                })

        sim = simulate_daily_stream(daily_stream)
        book_race_results[race] = {
            'weeks': num_weeks,
            'final_ctl': sim['final_ctl'],
            'peak_ctl': sim['peak_ctl'],
            'final_atl': sim['final_atl'],
            'final_tsb': sim['final_tsb'],
            'final_odi': sim['final_odi'],
            'final_acwr': sim['final_acwr'],
            'durability': round(tot_durability, 1),
            'consec_hard': consec_hard,
            'strength_sessions': len(race_df[race_df['sport_cat'] == 'Strength']),
        }

    cl_race_results = {}
    season_configs = [
        ('Sprint', 'sprint', 12, 280.0),
        ('Olympic', 'olympic', 16, 320.0),
        ('Half Ironman', '70.3', 20, 360.0),
        ('Ironman', 'ironman', 24, 420.0),
    ]

    for display_name, dist_code, n_weeks, base_tss in season_configs:
        print(f"Generating Closed-Loop Season for {display_name} ({n_weeks} weeks, base={base_tss})...")
        novice_profile = AthleteProfile(
            name=f"Novice {display_name} Athlete",
            starting_ctl=0.0,
            starting_atl=0.0,
            initial_metabolic_state=20.0,
            initial_connective_tissue_state=20.0,
            initial_running_acute_mech=10.0,
            initial_running_chronic_mech=10.0,
            base_weekly_tss=base_tss,
        )

        daily_df, summary_df = generate_closed_loop_season_schedule(
            profile=novice_profile,
            distance=dist_code,
            base_tss=base_tss,
            time_limit_per_week=1.5
        )

        cl_stream = []
        tot_durability = 0.0
        consec_hard = 0
        day_hard_prev = False

        for wk in range(1, len(summary_df) + 1):
            wk_sub = daily_df[daily_df['Week'] == wk]
            for d_name in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']:
                day_rows = wk_sub[wk_sub['Day'] == d_name]
                day_tss = day_rows['TSS'].sum()
                run_rows = day_rows[day_rows['Sport'] == 'Run']
                day_run_mech = run_rows['Mechanical_Strain'].sum()
                day_is_hard = day_rows['Is_Hard'].any()

                for _, r in day_rows.iterrows():
                    dur = r['Duration']
                    int_f = r['Intensity']
                    if dur <= 45:
                        tot_durability += 1.0
                    elif dur <= 75:
                        tot_durability += (3.5 if int_f < 0.8 else 6.0)
                    elif dur <= 120:
                        tot_durability += 7.0
                    else:
                        tot_durability += 9.5

                if day_is_hard and day_hard_prev:
                    consec_hard += 1
                day_hard_prev = day_is_hard

                cl_stream.append({
                    'week': wk, 'day': d_name, 'tss': day_tss,
                    'run_mech': day_run_mech, 'is_hard': day_is_hard
                })

        cl_sim = simulate_daily_stream(cl_stream)
        cl_race_results[display_name] = {
            'weeks': len(summary_df),
            'final_ctl': cl_sim['final_ctl'],
            'peak_ctl': cl_sim['peak_ctl'],
            'final_atl': cl_sim['final_atl'],
            'final_tsb': cl_sim['final_tsb'],
            'final_odi': cl_sim['final_odi'],
            'final_acwr': cl_sim['final_acwr'],
            'durability': round(tot_durability, 1),
            'consec_hard': consec_hard,
            'strength_sessions': len(daily_df[daily_df['Sport'] == 'Strength']),
        }

    comparison_rows = []
    for race in ['Sprint', 'Olympic', 'Half Ironman', 'Ironman']:
        b = book_race_results[race]
        cl = cl_race_results[race]

        comparison_rows.append({
            'Distance': race,
            'Plan': '80/20 Official Level 0',
            'Weeks': b['weeks'],
            'Final_CTL (Fitness)': b['final_ctl'],
            'Peak_CTL': b['peak_ctl'],
            'Final_ATL (Fatigue)': b['final_atl'],
            'Race_Day_TSB (Freshness)': b['final_tsb'],
            'Race_Day_ODI (Remodeling)': b['final_odi'],
            'Race_Day_ACWR': b['final_acwr'],
            'Total_Durability_Stimulus': b['durability'],
            'Strength_Workouts': b['strength_sessions'],
            'Consec_Hard_Days': b['consec_hard'],
        })

        comparison_rows.append({
            'Distance': race,
            'Plan': 'Closed-Loop Optimizer',
            'Weeks': cl['weeks'],
            'Final_CTL (Fitness)': cl['final_ctl'],
            'Peak_CTL': cl['peak_ctl'],
            'Final_ATL (Fatigue)': cl['final_atl'],
            'Race_Day_TSB (Freshness)': cl['final_tsb'],
            'Race_Day_ODI (Remodeling)': cl['final_odi'],
            'Race_Day_ACWR': cl['final_acwr'],
            'Total_Durability_Stimulus': cl['durability'],
            'Strength_Workouts': cl['strength_sessions'],
            'Consec_Hard_Days': cl['consec_hard'],
        })

    comp_df = pd.DataFrame(comparison_rows)

    print("\n" + "=" * 120)
    print("  RACE READINESS COMPARISON: LEVEL 0 PLANS VS CLOSED-LOOP OPTIMIZER (STARTING FROM 0)")
    print("=" * 120)
    print(comp_df.to_string(index=False))

    comp_df.to_csv(r'training_plans/race_readiness_level0_vs_closed_loop.csv', index=False)
    print("\nSaved full table to 'training_plans/race_readiness_level0_vs_closed_loop.csv'")


if __name__ == '__main__':
    run_simulation()
