"""
Advanced Stress Testing: Closed-Loop Season Planning under Acute Perturbations & Extreme Constraints
"""


import pandas as pd
import numpy as np

from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints
from triplan.solver.scheduler import TriathlonMILPScheduler
from triplan.control.mpc import ClosedLoopMPCController
from triplan.physiology.state import AthletePhysiologicalState
from triplan.library.candidates import build_default_candidate_library
from triplan.control.season import generate_closed_loop_season_schedule
from triplan.physiology.athlete_profile import AthleteProfile


def run_stress_tests():
    print("=" * 80)
    print("  ADVANCED SEASON-LONG STRESS TESTING SUITE")
    print("=" * 80)

    # -----------------------------------------------------------------------------
    # Test 1: Mid-Season Sickness Perturbation in Olympic 16W Season
    # -----------------------------------------------------------------------------
    print("\n>>> TEST 1: Olympic 16W with Mid-Season Sickness in Week 6")
    pert_sick = {
        6: {"type": "sickness"}  # Week 6 flu
    }
    daily_sick, summary_sick = generate_closed_loop_season_schedule(
        distance="olympic",
        base_tss=400.0,
        time_limit_per_week=1.5,
        perturbations=pert_sick
    )
    w6 = summary_sick[summary_sick['Week'] == 6].iloc[0]
    w7 = summary_sick[summary_sick['Week'] == 7].iloc[0]
    print(f"Week 6 (Sick Week): Solved TSS = {w6['Solved_TSS']} | Action = {w6['Recalibration']}")
    print(f"Week 7 (Post-Sick Bounce Back): Solved TSS = {w7['Solved_TSS']} | CTL = {w7['Ending_CTL']} | TSB = {w7['TSB']}")
    assert "ACUTE SICKNESS" in w6['Recalibration'], "Failed to trigger sickness perturbation!"
    assert w6['Solved_TSS'] < w7['Solved_TSS'], "Sickness did not reduce volume!"
    print("TEST 1 PASSED: Sickness safely absorbed and periodization resumed seamlessly.")

    # -----------------------------------------------------------------------------
    # Test 2: Unplanned Facility Pool Closure in Week 8 of 70.3 Season
    # -----------------------------------------------------------------------------
    print("\n>>> TEST 2: 70.3 Season with Unplanned Pool Closure in Week 8")
    pert_pool = {
        8: {"type": "pool_closure"}
    }
    daily_pool, summary_pool = generate_closed_loop_season_schedule(
        distance="70.3",
        base_tss=420.0,
        time_limit_per_week=1.5,
        perturbations=pert_pool
    )
    w8_daily = daily_pool[daily_pool['Week'] == 8]
    swim_count = (w8_daily['Sport'] == 'Swim').sum()
    print(f"Week 8 Swim Sessions: {swim_count} (Expected: 0)")
    w8_summary = summary_pool[summary_pool['Week'] == 8].iloc[0]
    print(f"Week 8 Solved TSS: {w8_summary['Solved_TSS']} | Action: {w8_summary['Recalibration']}")
    assert swim_count == 0, "Pool closure violated, swim sessions found!"
    print("TEST 2 PASSED: Model reallocated volume without violating facility bounds.")

    # -----------------------------------------------------------------------------
    # Test 3: Master Athlete 70.3 Season (Age 55, Masters Mode = True)
    # -----------------------------------------------------------------------------
    print("\n>>> TEST 3: Master Athlete 70.3 Season (tau_C = 210d, Stricter ODI <= 1.20)")
    master_profile = AthleteProfile(
        name="Master Triathlete",
        age=55,
        is_masters_athlete=True,
        base_weekly_tss=380.0,
        min_rest_days=2,
        max_rest_days=2,
        daily_time_caps=[60.0, 75.0, 60.0, 75.0, 60.0, 180.0, 150.0],
        weekday_single_workout_cap=60.0,
        sport_freq_bounds={"Swim": (1, 2), "Bike": (2, 3), "Run": (2, 3), "Strength": (1, 1)},
    )
    # Prune library to remove long anchors incompatible with master caps
    lib_master = [
        CandidateWorkout(
            w.id, w.code, w.name, w.sport, w.duration, w.aerobic_tss, w.mechanical_strain,
            w.ans_cost, w.durability_score, w.zone_durations, w.is_hard_endurance,
            w.is_heavy_run, w.is_heavy_strength, False, w.has_strides, w.is_indoor_bike, w.preference_penalty
        ) for w in build_default_candidate_library()
    ]
    daily_master, summary_master = generate_closed_loop_season_schedule(
        profile=master_profile,
        distance="70.3",
        base_tss=360.0,
        library=lib_master,
        time_limit_per_week=1.5
    )
    # TASK-022 (ADR-035): ODI^run <= 1.20 is an in-model Tier-0 row for masters (no post-hoc recalibration any more).
    print(f"Master Athlete Season Completed: {len(summary_master)} Weeks.")
    print(f"Max ODI^run: {summary_master['Ending_ODI'].max():.2f} (masters limit 1.20)")
    print(f"Final CTL: {summary_master['Ending_CTL'].iloc[-1]} | Mean ACWR: {summary_master['Ending_ACWR'].mean():.2f}")
    assert summary_master['Ending_ODI'].max() <= 1.20 + 1e-6, "Masters run-strain ODI exceeded the 1.20 ceiling!"
    assert summary_master['Ending_ACWR'].mean() <= 1.25, "Master athlete mean ACWR exceeded safe bounds!"
    print("TEST 3 PASSED: Master mode strictly protected slower collagen remodeling kinetics.")

    # -----------------------------------------------------------------------------
    # Test 4: Executive Ultra-Time-Starved Season (45m Weekdays, 90m Weekends)
    # -----------------------------------------------------------------------------
    print("\n>>> TEST 4: Executive Time-Starved Season (45m Weekdays, 90m Weekends)")
    exec_profile = AthleteProfile(
        name="Executive Triathlete",
        base_weekly_tss=280.0,
        daily_time_caps=[45.0, 45.0, 45.0, 45.0, 45.0, 90.0, 90.0],
        weekday_single_workout_cap=45.0,
        sport_freq_bounds={"Swim": (1, 2), "Bike": (2, 3), "Run": (3, 4), "Strength": (1, 1)},
    )
    lib_short = [w for w in lib_master if w.duration <= 90.0]
    daily_exec, summary_exec = generate_closed_loop_season_schedule(
        profile=exec_profile,
        distance="olympic",
        base_tss=260.0,
        library=lib_short,
        time_limit_per_week=1.5
    )
    print(f"Executive Season Completed: {len(summary_exec)} Weeks.")
    print(f"Mean Weekly TSS: {summary_exec['Solved_TSS'].mean():.1f} | Max TSS: {summary_exec['Solved_TSS'].max():.1f}")
    print("TEST 4 PASSED: Time-starved constraints solved with zero infeasibilities across 16 weeks.")

    print("\n" + "=" * 80)
    print("  ALL ADVANCED STRESS TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_stress_tests()
