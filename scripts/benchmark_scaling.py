import time


import numpy as np
import highspy
from triplan.library.candidates import build_default_candidate_library
from triplan.model.candidates import MesoCycleTargetsAndConstraints, CandidateWorkout
from triplan.solver.scheduler import TriathlonMILPScheduler

base_lib = build_default_candidate_library()

# Let's clone base_lib to simulate 50, 100, 200, 300 workouts
def generate_synthetic_library(target_count=200):
    lib = []
    idx = 0
    multiplier = (target_count // len(base_lib)) + 1
    for m in range(multiplier):
        for w in base_lib:
            if len(lib) >= target_count:
                break
            # Add slight jitter to avoid identical numbers
            dur_jitter = max(20.0, w.duration + (m % 5 - 2) * 5.0)
            tss_jitter = max(15.0, w.aerobic_tss + (m % 5 - 2) * 3.0)
            ans_jitter = max(20.0, w.ans_cost + (m % 5 - 2) * 10.0)
            mech_jitter = max(2.0, w.mechanical_strain + (m % 5 - 2) * 2.0)
            new_w = CandidateWorkout(
                id=idx,
                code=f"{w.code}_{m}",
                name=f"{w.name} (Variant {m})",
                sport=w.sport,
                duration=dur_jitter,
                aerobic_tss=tss_jitter,
                mechanical_strain=mech_jitter,
                ans_cost=ans_jitter,
                durability_score=w.durability_score,
                zone_durations=w.zone_durations,
                is_hard_endurance=w.is_hard_endurance,
                is_heavy_run=w.is_heavy_run,
                is_heavy_strength=w.is_heavy_strength,
                is_anchor=(w.is_anchor if m == 0 else False), # Only original anchors
                has_strides=w.has_strides,
                is_indoor_bike=w.is_indoor_bike,
                preference_penalty=w.preference_penalty
            )
            lib.append(new_w)
            idx += 1
    return lib

def run_benchmark():
    for count in [17, 50, 100, 200]:
        lib = generate_synthetic_library(count)
        meso_config = MesoCycleTargetsAndConstraints(
            daily_time_caps=[60.0, 105.0, 60.0, 105.0, 60.0, 240.0, 240.0],
            weekday_single_workout_cap=60.0,
            facility_access={
                "Swim":     [False, False, False, False, False, True, True],
                "Bike":     [True,  True,  True,  True,  True,  True, True],
                "Run":      [True,  True,  True,  True,  True,  True, True],
                "Strength": [True,  True,  True,  True,  True,  False, False],
            },
            target_weekly_tss=540.0,
            nominal_weekly_duration=640.0,
            target_zone_pcts={1: 0.25, 2: 0.55, 3: 0.14, 4: 0.05, 5: 0.01},
            target_sport_duration_splits={"Swim": 0.15, "Bike": 0.45, "Run": 0.33, "Strength": 0.07},
            target_bike_run_tss_splits={"Bike": 0.60, "Run": 0.40},
            max_48h_ans_cost=750.0,
        )
        t0 = time.time()
        scheduler = TriathlonMILPScheduler(lib, meso_config)
        plans = scheduler.solve_top_k_plans(k_plans=3)
        elapsed = time.time() - t0
        print(f"Library Size = {count:3d} | Plans Found: {len(plans)} | Solve Time: {elapsed:.3f}s")


if __name__ == '__main__':
    run_benchmark()
