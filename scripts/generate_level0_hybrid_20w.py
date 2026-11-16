"""
Refined Generator for 20-Week Level 0 Hybrid Athlete Training Plan (Duathlon + Strength)
Athlete: 270 lbs, 6'1", 3 months detrained, prior 70.3 + barbell experience, no swim.
"""

import math
import pandas as pd
import numpy as np

def build_20_week_plan():
    # Initial Banister and Tissue baselines
    ctl = 15.0
    atl = 12.0
    metabolic_state = 20.0     # tau=35d
    connective_state = 22.0    # tau=120d
    acute_mech = 10.0          # tau=7d
    chronic_mech = 12.0        # tau=28d

    daily_records = []
    weekly_summary = []

    # 20-week schedule parameters
    week_configs = [
        # Mesocycle 1: Anatomical Adaptation
        (1, "Mesocycle 1 (Adaptation)", "Base Load", 1.00, 1.00, 1.00, "Walk/Jog (1m jog/1m walk), Base Spin 90 RPM, Full-Body Structural 50% 1RM"),
        (2, "Mesocycle 1 (Adaptation)", "Build 1", 1.10, 1.10, 1.05, "Walk/Jog (90s jog/90s walk), Spin + Cadence 90 RPM, Full-Body Linear Touch"),
        (3, "Mesocycle 1 (Adaptation)", "Overload", 1.22, 1.20, 1.10, "Walk/Jog (2m jog/1m walk), Weekend 90m Spin, Full-Body Structural sets"),
        (4, "Mesocycle 1 (Adaptation)", "Recovery Deload", 0.78, 0.70, 0.60, "Mandatory Deload: Easy 1m jog/2m walk, Easy 45m spins, Mobility Flow STR_DELOAD"),

        # Mesocycle 2: Aerobic Foundation
        (5, "Mesocycle 2 (Foundation)", "Base Load", 1.25, 1.25, 1.15, "Continuous Easy Run 18-20m Z2, 90m Weekend Ride, Linear Progression 3x5"),
        (6, "Mesocycle 2 (Foundation)", "Build 1", 1.35, 1.35, 1.20, "Continuous Easy Run 20-22m Z2, 100m Weekend Ride, LP Squat +10lb, Bench +5lb"),
        (7, "Mesocycle 2 (Foundation)", "Overload", 1.48, 1.45, 1.25, "Continuous Easy Run 22-25m Z2, 105m Weekend Ride, LP Working sets @ RPE 7"),
        (8, "Mesocycle 2 (Foundation)", "Recovery Deload", 0.95, 0.85, 0.70, "Deload & Connective Recovery: 16m easy runs, 60m ride, 60% bar speed sets"),

        # Mesocycle 3: Engine Expansion & HLM
        (9, "Mesocycle 3 (Expansion)", "Base Load", 1.45, 1.45, 1.25, "Continuous Run 22-28m w/ Strides, 110m Ride, HLM Split: Mon Heavy / Fri Medium"),
        (10, "Mesocycle 3 (Expansion)", "Build 1", 1.58, 1.55, 1.30, "Continuous Run + Strides, 115m Ride (Sweet Spot 3x8m), HLM Working Sets @ 75%"),
        (11, "Mesocycle 3 (Expansion)", "Overload", 1.70, 1.65, 1.35, "Run 25-32m Z2, 120m (2hr) Endurance Ride (Sweet Spot 3x10m), Friday Heavy DL 1x5"),
        (12, "Mesocycle 3 (Expansion)", "Recovery Deload", 1.05, 0.95, 0.75, "Mid-Season Deload: Volume -25%, Mid-point body composition & metric check"),

        # Mesocycle 4: Sub-Threshold & Power
        (13, "Mesocycle 4 (Sub-Threshold)", "Base Load", 1.65, 1.65, 1.35, "Aerobic Tempo Run 30m (2x8m Z3), 130m Ride (Sweet Spot 2x15m), Heavy Barbell"),
        (14, "Mesocycle 4 (Sub-Threshold)", "Build 1", 1.78, 1.75, 1.40, "Aerobic Tempo Run 32m (3x7m Z3), 140m Ride (Sweet Spot 3x12m), Texas Method"),
        (15, "Mesocycle 4 (Sub-Threshold)", "Peak Overload", 1.90, 1.85, 1.45, "Aerobic Cruise Run 35m (2x12m Z3), 150m (2.5hr) Ride, Peak Strength sets"),
        (16, "Mesocycle 4 (Sub-Threshold)", "Recovery Deload", 1.15, 1.05, 0.75, "Pre-Peak Deload: Easy 20m runs, 80m spin, Joint deload, Neuromuscular rest"),

        # Mesocycle 5: Peak Durability & Testing
        (17, "Mesocycle 5 (Peak & Test)", "Peak Volume 1", 1.95, 1.90, 1.40, "Peak Hybrid Volume: 155m Ride, 38m Aerobic Run, Barbell Heavy Singles/Triples"),
        (18, "Mesocycle 5 (Peak & Test)", "Peak Volume 2", 2.05, 1.95, 1.40, "Peak Overload: 165m (2h45m) Ride, 40m Long Run, Explosive Barbell triples"),
        (19, "Mesocycle 5 (Peak & Test)", "Benchmark Testing", 1.10, 1.10, 0.90, "BENCHMARK WEEK: 20m FTP Test (Bike), 5k TT (Run), 5RM Squat/Bench/Deadlift"),
        (20, "Mesocycle 5 (Peak & Test)", "Graduation Deload", 0.70, 0.60, 0.50, "Graduation & Transition: Active recovery, Body comp check, Prep for 70.3 Build"),
    ]

    for wk, mesocycle, step_type, b_scale, r_scale, s_scale, notes in week_configs:
        is_deload = "Deload" in step_type or "Graduation" in step_type
        is_test_week = "Benchmark" in step_type

        # Base durations
        tue_bike_dur = round(35.0 + 20.0 * (b_scale - 1.0) if not is_deload else 35.0, 0)
        tue_bike_dur = max(30.0, min(75.0, tue_bike_dur))
        tue_bike_tss = round(tue_bike_dur * 0.65, 1)

        tue_str_dur = 45.0 if not is_deload else 30.0
        tue_str_tss = 30.0 if not is_deload else 15.0
        tue_str_ax = 45.0 if not is_deload else 20.0
        tue_str_neuro = 50.0 if not is_deload else 25.0
        tue_bb_sets = 6 if not is_deload else 3
        tue_db_sets = 4 if not is_deload else 2

        # Running micro-dosing for 270 lb runner across 3 touches:
        # Run 1 (Wed): 16-26m
        # Run 2 (Thu shakeout/strides): 14-24m
        # Run 3 (Sun long run): 18-36m (well controlled relative to weekly total)
        run1_dur = round(16.0 + 7.0 * (r_scale - 1.0) if not is_deload else 14.0, 0)
        run2_dur = round(15.0 + 6.0 * (r_scale - 1.0) if not is_deload else 12.0, 0)
        run3_dur = round(18.0 + 10.0 * (r_scale - 1.0) if not is_deload else 16.0, 0)

        run1_tss = round(run1_dur * 0.80, 1)
        run1_strain = round(run1_dur * 1.00, 1)
        run2_tss = round(run2_dur * 0.80, 1)
        run2_strain = round(run2_dur * 0.95, 1)
        run3_tss = round(run3_dur * 0.85, 1)
        run3_strain = round(run3_dur * 1.08, 1)

        thu_bike_dur = round(40.0 + 22.0 * (b_scale - 1.0) if not is_deload else 35.0, 0)
        thu_bike_dur = max(35.0, min(80.0, thu_bike_dur))
        thu_if = 0.70 if wk <= 8 else (0.80 if wk <= 16 else 0.75)
        thu_bike_tss = round(thu_bike_dur * (thu_if ** 2) * 1.1, 1)

        fri_str_dur = 45.0 if not is_deload else 30.0
        fri_str_tss = 32.0 if not is_deload else 15.0
        fri_str_ax = 55.0 if not is_deload else 20.0
        fri_str_neuro = 55.0 if not is_deload else 25.0
        fri_bb_sets = 7 if not is_deload else 3
        fri_db_sets = 4 if not is_deload else 2

        sat_bike_dur = round(60.0 + 65.0 * (b_scale - 1.0) if not is_deload else 50.0 + 20.0 * (b_scale - 0.7), 0)
        sat_bike_dur = max(50.0, min(165.0, sat_bike_dur))
        sat_bike_tss = round(sat_bike_dur * 0.68, 1)

        if is_test_week:
            tue_bike_dur = 60.0
            tue_bike_tss = 80.0
            run1_dur = 0.0
            run1_tss = 0.0
            run1_strain = 0.0
            run2_dur = 0.0
            run2_tss = 0.0
            run2_strain = 0.0
            thu_bike_dur = 45.0
            thu_bike_tss = 22.0
            fri_str_dur = 45.0
            fri_str_tss = 28.0
            fri_str_ax = 60.0
            fri_str_neuro = 65.0
            sat_bike_dur = 40.0
            sat_bike_tss = 18.0
            run3_dur = 45.0  # 5k TT
            run3_tss = 50.0
            run3_strain = 55.0

        # Detailed workout descriptions
        if wk <= 4:
            r1_desc = f"Walk/Jog Intervals ({int(run1_dur)}m): 5m walk, 5x (1m jog / 1m walk), 5m walk cooldown"
            r2_desc = f"Walk/Jog Intervals ({int(run2_dur)}m): 5m walk, 4x (1m jog / 1m walk), 5m walk cooldown"
            r3_desc = f"Walk/Jog Long Touch ({int(run3_dur)}m): 5m walk, 6x (1m jog / 1m walk), 5m walk cooldown"
            str_a_desc = "Full-Body Strength A: Back Box Squat (2x5 @ 50% 1RM), Flat Bench Press (3x5 @ 50%), DB Rows (3x8 w/ 40lb DBs), Pallof Press (3x30s)"
            str_b_desc = "Full-Body Strength B: Barbell RDL (2x5 @ 50% 1RM), DB Standing OHP (3x8 w/ 30lb DBs), DB Split Squats (2x6/leg), Lat Pulldown (3x8), Bird-Dogs"
        elif wk <= 8:
            r1_desc = f"Easy Aerobic Run ({int(run1_dur)}m): Continuous Zone 2 conversational jog on flat/treadmill"
            r2_desc = f"Easy Aerobic Run ({int(run2_dur)}m): Continuous Zone 2 jog + 3x 15s gentle cadence strides"
            r3_desc = f"Long Aerobic Run ({int(run3_dur)}m): Zone 2 aerobic running, strictly controlled heart rate"
            str_a_desc = "Linear Progression A: Barbell Back Squat (3x5 @ 65% 1RM), Barbell Bench Press (3x5 @ 65%), DB Rows (3x8 w/ 50lb DBs), Ab Wheel (3x8)"
            str_b_desc = "Linear Progression B: Barbell Deadlift (1x5 @ 70% 1RM), Barbell OHP (3x5 @ 65%), Bulgarian Split Squats (3x6/leg w/ 35lb DBs), Neutral Pull-Ups"
        elif wk <= 12:
            r1_desc = f"Aerobic Base Run ({int(run1_dur)}m): Zone 2 steady jog, 160-165 SPM cadence"
            r2_desc = f"Aerobic Run + Strides ({int(run2_dur)}m): 20m Z2 + 4x 20s relaxed strides (Zone 4) w/ 60s walk rec"
            r3_desc = f"Weekend Long Run ({int(run3_dur)}m): Steady Zone 2 aerobic durability, 40h after Fri Deadlift"
            str_a_desc = "HLM Heavy Day: Barbell Back Squat (3x5 @ 75% 1RM), Barbell Bench (3x5 @ 75%), Barbell Bent Row (3x6), Hanging Knee Raises (3x10)"
            str_b_desc = "HLM Intensity Day: Barbell Deadlift (1x5 @ 80% 1RM), Barbell OHP (3x5 @ 75%), DB Bulgarian Split Squats (3x6/leg w/ 50lb DBs), Chins (3x6)"
        elif wk <= 16:
            r1_desc = f"Aerobic Base Run ({int(run1_dur)}m): Zone 2 steady jog"
            r2_desc = f"Sub-Threshold Tempo Run ({int(run2_dur)}m): 10m warmup, 2x 8m Zone 3 Tempo w/ 2m jog rec, 5m cooldown"
            r3_desc = f"Weekend Long Run ({int(run3_dur)}m): Zone 2 durability, strictly capped duration"
            str_a_desc = "Texas Volume Day: Barbell Back Squat (4x5 @ 75% 1RM), Barbell Bench (4x5 @ 75%), DB Row (3x8 w/ 65lb DB), Core Rollouts"
            str_b_desc = "Texas Intensity Day: Barbell Deadlift (1x3 @ 85% 1RM), Barbell OHP (3x3 @ 82.5%), Single-Leg DB RDL (3x6/leg w/ 55lb DBs), Weighted Chins"
        else:
            if is_test_week:
                r1_desc = "Rest / Recovery Walk"
                r2_desc = "Rest / Recovery Walk"
                r3_desc = "5K RUN BENCHMARK TIME TRIAL: 15m warmup jog, 5.0 km Continuous Best-Effort Pacing, 10m walk cooldown"
                str_a_desc = "BENCHMARK STRENGTH TEST 1: Barbell Back Squat 5RM Ramp Test + Barbell Bench Press 5RM Ramp Test"
                str_b_desc = "BENCHMARK STRENGTH TEST 2: Barbell Deadlift 5RM Ramp Test + Barbell Overhead Press 5RM Ramp Test"
            elif is_deload:
                r1_desc = f"Active Recovery Jog ({int(run1_dur)}m): Zone 1/2 very light"
                r2_desc = f"Active Recovery Jog ({int(run2_dur)}m): Zone 1/2 light spin/jog"
                r3_desc = f"Aerobic Shakeout ({int(run3_dur)}m): Gentle Zone 1/2 run"
                str_a_desc = "Regeneration Strength: Bodyweight Mobility Flow, 90/90 Hips, Light Goblet Squats (2x8 w/ 35lb DB), Prone Y-T-W"
                str_b_desc = "Regeneration Mobility: Foam Rolling, Thoracic Extensions, Light DB RDL (2x8 w/ 35lb DBs), Bird-Dogs, Planks"
            else:
                r1_desc = f"Aerobic Base Run ({int(run1_dur)}m): Zone 2 steady"
                r2_desc = f"Aerobic Tempo Run ({int(run2_dur)}m): 10m warmup, 2x 10m Z3 Tempo w/ 2m jog rec, 5m cooldown"
                r3_desc = f"Peak Weekend Long Run ({int(run3_dur)}m): Zone 2 steady aerobic benchmark"
                str_a_desc = "Peak Volume Day: Barbell Back Squat (3x5 @ 77.5% 1RM), Barbell Bench (3x5 @ 77.5%), Heavy DB Row (3x6 w/ 75lb DBs)"
                str_b_desc = "Peak Intensity Day: Barbell Deadlift (1x3 @ 87.5% 1RM), Barbell OHP (3x3 @ 85%), DB Split Squat (2x6/leg), Chins"

        # Construct daily records
        week_days = [
            # Mon: Rest
            {'Day': 'Mon', 'Sport': 'Rest', 'Code': 'REST', 'Desc': 'Full Rest & Regeneration Mobility (Couch stretch, 90/90 hip flow, thoracic rolling)', 'Duration': 0.0, 'TSS': 0.0, 'Mech_Strain': 0.0, 'Axial_Load': 0.0, 'Neuro_Load': 0.0, 'BB_Sets': 0, 'DB_Sets': 0, 'Z1_Dur': 0.0, 'Z2_Dur': 0.0, 'Z3_Dur': 0.0, 'Z45_Dur': 0.0},

            # Tue: Bike + PM Strength A
            {'Day': 'Tue', 'Sport': 'Bike', 'Code': f'BK_AERO_{int(tue_bike_dur)}', 'Desc': f'Aerobic Base Spin ({int(tue_bike_dur)}m Z2, 90 RPM cadence focus)', 'Duration': tue_bike_dur, 'TSS': tue_bike_tss, 'Mech_Strain': 0.0, 'Axial_Load': 0.0, 'Neuro_Load': 15.0, 'BB_Sets': 0, 'DB_Sets': 0, 'Z1_Dur': 10.0, 'Z2_Dur': tue_bike_dur - 10.0, 'Z3_Dur': 0.0, 'Z45_Dur': 0.0},
            {'Day': 'Tue', 'Sport': 'Strength', 'Code': 'STR_SES_A', 'Desc': str_a_desc, 'Duration': tue_str_dur, 'TSS': tue_str_tss, 'Mech_Strain': 0.0, 'Axial_Load': tue_str_ax, 'Neuro_Load': tue_str_neuro, 'BB_Sets': tue_bb_sets, 'DB_Sets': tue_db_sets, 'Z1_Dur': tue_str_dur, 'Z2_Dur': 0.0, 'Z3_Dur': 0.0, 'Z45_Dur': 0.0},

            # Wed: Run 1
            {'Day': 'Wed', 'Sport': 'Run' if run1_dur > 0 else 'Rest', 'Code': f'RN_SES_1_{int(run1_dur)}' if run1_dur > 0 else 'REST', 'Desc': r1_desc, 'Duration': run1_dur, 'TSS': run1_tss, 'Mech_Strain': run1_strain, 'Axial_Load': 0.0, 'Neuro_Load': 20.0 if run1_dur > 0 else 0.0, 'BB_Sets': 0, 'DB_Sets': 0, 'Z1_Dur': 5.0 if run1_dur > 0 else 0.0, 'Z2_Dur': max(0.0, run1_dur - 5.0), 'Z3_Dur': 0.0, 'Z45_Dur': 0.0},

            # Thu: Quality Bike + Run 2
            {'Day': 'Thu', 'Sport': 'Bike', 'Code': f'BK_QUAL_{int(thu_bike_dur)}', 'Desc': f'Quality/Tempo Bike ({int(thu_bike_dur)}m)' if wk >= 9 and not is_deload and not is_test_week else f'Aerobic Endurance Spin ({int(thu_bike_dur)}m Z2)', 'Duration': thu_bike_dur, 'TSS': thu_bike_tss, 'Mech_Strain': 0.0, 'Axial_Load': 0.0, 'Neuro_Load': 25.0, 'BB_Sets': 0, 'DB_Sets': 0, 'Z1_Dur': 10.0, 'Z2_Dur': thu_bike_dur - (25.0 if wk >= 9 and not is_deload else 10.0), 'Z3_Dur': 15.0 if wk >= 9 and not is_deload else 0.0, 'Z45_Dur': 10.0 if wk >= 9 and not is_deload else 0.0},
        ]

        if run2_dur > 0:
            week_days.append({
                'Day': 'Thu', 'Sport': 'Run', 'Code': f'RN_SES_2_{int(run2_dur)}', 'Desc': r2_desc, 'Duration': run2_dur, 'TSS': run2_tss, 'Mech_Strain': run2_strain, 'Axial_Load': 0.0, 'Neuro_Load': 20.0, 'BB_Sets': 0, 'DB_Sets': 0, 'Z1_Dur': 5.0, 'Z2_Dur': max(0.0, run2_dur - 5.0), 'Z3_Dur': 0.0, 'Z45_Dur': 0.0
            })

        # Fri: PM Strength B (Deadlift - NO RUNNING!)
        week_days.append({
            'Day': 'Fri', 'Sport': 'Strength', 'Code': 'STR_SES_B', 'Desc': str_b_desc, 'Duration': fri_str_dur, 'TSS': fri_str_tss, 'Mech_Strain': 0.0, 'Axial_Load': fri_str_ax, 'Neuro_Load': fri_str_neuro, 'BB_Sets': fri_bb_sets, 'DB_Sets': fri_db_sets, 'Z1_Dur': fri_str_dur, 'Z2_Dur': 0.0, 'Z3_Dur': 0.0, 'Z45_Dur': 0.0
        })

        # Sat: Long Bike (Zero Impact)
        week_days.append({
            'Day': 'Sat', 'Sport': 'Bike', 'Code': f'BK_LONG_{int(sat_bike_dur)}', 'Desc': f'Weekend Long Endurance Ride ({int(sat_bike_dur)}m Z2 Non-Impact Base)', 'Duration': sat_bike_dur, 'TSS': sat_bike_tss, 'Mech_Strain': 0.0, 'Axial_Load': 0.0, 'Neuro_Load': 20.0, 'BB_Sets': 0, 'DB_Sets': 0, 'Z1_Dur': 15.0, 'Z2_Dur': sat_bike_dur - 15.0, 'Z3_Dur': 0.0, 'Z45_Dur': 0.0
        })

        # Sun: Weekend Long Aerobic Run (40-48h after Friday Deadlift!)
        week_days.append({
            'Day': 'Sun', 'Sport': 'Run', 'Code': f'RN_LONG_{int(run3_dur)}', 'Desc': r3_desc, 'Duration': run3_dur, 'TSS': run3_tss, 'Mech_Strain': run3_strain, 'Axial_Load': 0.0, 'Neuro_Load': 25.0, 'BB_Sets': 0, 'DB_Sets': 0, 'Z1_Dur': 5.0, 'Z2_Dur': max(0.0, run3_dur - 5.0), 'Z3_Dur': 0.0, 'Z45_Dur': 0.0
        })

        # Totals and metrics
        tot_wk_dur = sum(d['Duration'] for d in week_days)
        tot_wk_tss = sum(d['TSS'] for d in week_days)
        tot_wk_mech = sum(d['Mech_Strain'] for d in week_days)
        tot_wk_ax = sum(d['Axial_Load'] for d in week_days)
        tot_wk_neuro = sum(d['Neuro_Load'] for d in week_days)
        tot_bb = sum(d['BB_Sets'] for d in week_days)
        tot_db = sum(d['DB_Sets'] for d in week_days)
        bb_share = (tot_bb / (tot_bb + tot_db)) if (tot_bb + tot_db) > 0 else 0.0

        run_durs = [d['Duration'] for d in week_days if d['Sport'] == 'Run']
        tot_run_dur = sum(run_durs)
        max_run_dur = max(run_durs) if run_durs else 0.0
        long_run_ratio = (max_run_dur / tot_run_dur) if tot_run_dur > 0 else 0.0

        tot_z1 = sum(d['Z1_Dur'] for d in week_days)
        tot_z2 = sum(d['Z2_Dur'] for d in week_days)
        tot_z3 = sum(d['Z3_Dur'] for d in week_days)
        tot_z45 = sum(d['Z45_Dur'] for d in week_days)
        low_pct = ((tot_z1 + tot_z2) / tot_wk_dur) * 100.0 if tot_wk_dur > 0 else 100.0
        high_pct = (tot_z45 / tot_wk_dur) * 100.0 if tot_wk_dur > 0 else 0.0

        daily_tss = tot_wk_tss / 7.0
        daily_mech = tot_wk_mech / 7.0
        for _ in range(7):
            ctl = ctl + (daily_tss - ctl) / 42.0
            atl = atl + (daily_tss - atl) / 7.0
            metabolic_state = metabolic_state + (daily_tss - metabolic_state) / 35.0
            connective_state = connective_state + (daily_tss - connective_state) / 120.0
            acute_mech = acute_mech + (daily_mech - acute_mech) / 7.0
            chronic_mech = chronic_mech + (daily_mech - chronic_mech) / 28.0

        tsb = ctl - atl
        odi = metabolic_state / connective_state if connective_state > 0 else 1.0
        acwr = acute_mech / chronic_mech if chronic_mech > 0 else 1.0

        for d in week_days:
            daily_records.append({
                'Week': wk,
                'Mesocycle': mesocycle,
                'Step_Type': step_type,
                'Day': d['Day'],
                'Sport': d['Sport'],
                'Code': d['Code'],
                'Description': d['Desc'],
                'Duration_min': d['Duration'],
                'TSS': d['TSS'],
                'Mechanical_Strain': d['Mech_Strain'],
                'Axial_Load': d['Axial_Load'],
                'Neuromuscular_Load': d['Neuro_Load'],
                'BB_Sets': d['BB_Sets'],
                'DB_Sets': d['DB_Sets']
            })

        weekly_summary.append({
            'Week': wk,
            'Mesocycle': mesocycle,
            'Step_Type': step_type,
            'Solved_TSS': round(tot_wk_tss, 1),
            'Total_Duration_min': round(tot_wk_dur, 0),
            'Total_Duration_hours': f"{int(tot_wk_dur//60)}:{int(tot_wk_dur%60):02d}",
            'Total_Mech_Strain': round(tot_wk_mech, 1),
            'Total_Axial_Load': round(tot_wk_ax, 1),
            'Total_Neuro_Load': round(tot_wk_neuro, 1),
            'Ending_CTL': round(ctl, 1),
            'Ending_ATL': round(atl, 1),
            'TSB': round(tsb, 1),
            'Ending_ODI': round(odi, 2),
            'Ending_ACWR': round(acwr, 2),
            'BB_Set_Share_pct': round(bb_share * 100.0, 1),
            'Long_Run_Ratio_pct': round(long_run_ratio * 100.0, 1),
            'Low_Intensity_pct': round(low_pct, 1),
            'High_Intensity_pct': round(high_pct, 1),
            'Focus_Notes': notes
        })

    daily_df = pd.DataFrame(daily_records)
    summary_df = pd.DataFrame(weekly_summary)

    daily_df.to_csv('training_plans/level0_hybrid_20w_daily.csv', index=False)
    summary_df.to_csv('training_plans/level0_hybrid_20w_summary.csv', index=False)

    print("Successfully generated and saved:")
    print(" - training_plans/level0_hybrid_20w_daily.csv (rows:", len(daily_df), ")")
    print(" - training_plans/level0_hybrid_20w_summary.csv (rows:", len(summary_df), ")")
    print("\nSummary Preview:")
    cols = ['Week', 'Mesocycle', 'Step_Type', 'Solved_TSS', 'Total_Duration_hours', 'Ending_CTL', 'TSB', 'Ending_ODI', 'Ending_ACWR', 'BB_Set_Share_pct', 'Long_Run_Ratio_pct']
    print(summary_df[cols].to_string(index=False))

if __name__ == '__main__':
    build_20_week_plan()
