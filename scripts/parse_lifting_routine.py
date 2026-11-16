"""
Parse and Clean Lifting Routine Excel File
=========================================
Parses 'PPL Routine.xlsx', blanks out all italicized cells (which denote planned
but unexecuted sets), stores the cleaned workbook and raw archive in 'data/lifting/',
and produces a structured JSON dataset of the athlete's actual strength training history.
"""

import os
import shutil
import re
import json
from datetime import datetime
from collections import defaultdict
import openpyxl

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LIFTING_DIR = os.path.join(DATA_DIR, "lifting")
RAW_INPUT_FILE = os.path.join(DATA_DIR, "PPL Routine.xlsx")

# Movement pattern and equipment classifiers
EXERCISE_TAXONOMY = {
    # Anterior Chain
    "squat": ("Anterior_Chain", "Barbell", 1.0),
    "front squat": ("Anterior_Chain", "Barbell", 0.9),
    "db lunges": ("Anterior_Chain", "Dumbbell", 0.4),
    "good girls": ("Anterior_Chain", "Machine", 0.0), # Adductor
    "bad girls": ("Anterior_Chain", "Machine", 0.0),  # Abductor
    "leg press": ("Anterior_Chain", "Machine", 0.2),
    "calf press": ("Anterior_Chain", "Machine", 0.0),

    # Posterior Chain
    "deadlift": ("Posterior_Chain", "Barbell", 1.0),
    "romanian deadlift": ("Posterior_Chain", "Barbell", 0.8),
    "rdl": ("Posterior_Chain", "Barbell", 0.8),
    "lying leg curl": ("Posterior_Chain", "Machine", 0.0),
    "back extensions": ("Posterior_Chain", "Bodyweight", 0.3),
    "farmer's walk": ("Posterior_Chain", "Dumbbell", 0.7),

    # Push
    "flat bench": ("Push", "Barbell", 0.2),
    "bench press": ("Push", "Barbell", 0.2),
    "overhead press": ("Push", "Barbell", 0.8),
    "ohp": ("Push", "Barbell", 0.8),
    "incline bench": ("Push", "Barbell", 0.3),
    "incline db press": ("Push", "Dumbbell", 0.2),
    "decline bench press": ("Push", "Barbell", 0.1),
    "machine flys": ("Push", "Machine", 0.0),
    "seated lateral raises": ("Push", "Dumbbell", 0.1),
    "chest press (cable)": ("Push", "Cable", 0.0),
    "tricep pushdowns": ("Push", "Cable", 0.0),
    "dips": ("Push", "Bodyweight", 0.2),

    # Pull
    "pull ups": ("Pull", "Bodyweight", 0.1),
    "pull-ups": ("Pull", "Bodyweight", 0.1),
    "weighted pull-ups": ("Pull", "Bodyweight", 0.2),
    "chin ups": ("Pull", "Bodyweight", 0.1),
    "chin-ups": ("Pull", "Bodyweight", 0.1),
    "weighted chin-ups": ("Pull", "Bodyweight", 0.2),
    "db row": ("Pull", "Dumbbell", 0.5),
    "bent over db row": ("Pull", "Dumbbell", 0.6),
    "seated cable rows": ("Pull", "Cable", 0.3),
    "wide-grip pulldowns": ("Pull", "Cable", 0.1),
    "one arm hammer rows": ("Pull", "Dumbbell", 0.4),
    "shrugs": ("Pull", "Barbell", 0.8),
    "wide grip bar curls": ("Pull", "Barbell", 0.1),
    "narrow grip bar curls": ("Pull", "Barbell", 0.1),
    "hammer curls": ("Pull", "Dumbbell", 0.0),
    "preacher curl machine": ("Pull", "Machine", 0.0),
    "machine rear delts": ("Pull", "Machine", 0.0),
    "bb wrist curl": ("Pull", "Barbell", 0.0),
}


def classify_exercise(ex_name: str):
    clean = ex_name.lower().strip()
    # Strip common prefixes
    for prefix in ["sw:", "a - ", "b - ", "power ", "hypertrophy "]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()
    clean = clean.split(" - ")[0].strip()

    for key, (pattern, equip, axial_ratio) in EXERCISE_TAXONOMY.items():
        if key in clean:
            return pattern, equip, axial_ratio
    
    # Generic fallback based on keywords
    if any(k in clean for k in ["press", "bench", "push", "fly", "lateral", "tricep"]):
        return "Push", "Dumbbell" if "db" in clean else "Barbell", 0.2
    if any(k in clean for k in ["pull", "row", "chin", "curl", "lat"]):
        return "Pull", "Dumbbell" if "db" in clean else "Barbell", 0.3
    if any(k in clean for k in ["squat", "lunge", "leg"]):
        return "Anterior_Chain", "Barbell", 0.8
    if any(k in clean for k in ["dead", "hinge", "hamstring"]):
        return "Posterior_Chain", "Barbell", 0.9

    return "Other", "Unknown", 0.0


def parse_set_str(val):
    """Parses cell text into (reps, weight_lbs). Returns None if empty or invalid."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ["none", "n/a", "na", "-", "0"]:
        return None
    m = re.match(r"^(\d+)\s*@\s*([\d\.]+)$", s)
    if m:
        return int(m.group(1)), float(m.group(2))
    try:
        f = float(s)
        if f > 0:
            return int(f), 0.0 # Reps only (bodyweight)
    except ValueError:
        pass
    return None


def calculate_warmup_tonnage(working_wt: float, working_reps: int, equipment: str):
    """
    Calculates estimated warm-up sets and tonnage for barbell/heavy compound lifts.
    User note: 'Before the liftings I did warm up in sets of incremental loading until I hit the weight listed'
    Standard ramp: Bar (45x5x2) -> 50%x5 -> 70%x3 -> 85%x2 -> Work sets.
    """
    if equipment != "Barbell" or working_wt <= 45.0:
        return 0, 0.0

    warmup_sets = []
    # Bar only warm up
    warmup_sets.append((5, 45.0))
    warmup_sets.append((5, 45.0))

    if working_wt >= 135.0:
        warmup_sets.append((5, round(working_wt * 0.50 / 5) * 5))
    if working_wt >= 185.0:
        warmup_sets.append((3, round(working_wt * 0.70 / 5) * 5))
    if working_wt >= 225.0:
        warmup_sets.append((2, round(working_wt * 0.85 / 5) * 5))

    tonnage = sum(reps * wt for reps, wt in warmup_sets)
    return len(warmup_sets), tonnage


def process_workbook(input_path: str, output_cleaned_path: str):
    """
    Loads input workbook, zeroes out all italic cells, saves cleaned workbook,
    and returns parsed workout data.
    """
    print(f"Loading workbook: {input_path}")
    wb = openpyxl.load_workbook(input_path, data_only=False)
    wb_raw_val = openpyxl.load_workbook(input_path, data_only=True)
    
    # 1. Blank out italicized cells and ensure formula dates in row 1 are populated
    italic_blanked_count = 0
    total_cells = 0
    for sname in wb.sheetnames:
        ws = wb[sname]
        ws_raw_val = wb_raw_val[sname]
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                cell = ws.cell(row=r, column=c)
                total_cells += 1
                if cell.font and cell.font.italic and cell.value is not None:
                    cell.value = None
                    italic_blanked_count += 1
                elif r == 1 and cell.value and str(cell.value).startswith("="):
                    # Cache the evaluated date value into cleaned workbook
                    cached_val = ws_raw_val.cell(row=r, column=c).value
                    if cached_val is not None:
                        cell.value = cached_val

    print(f"Blanked out {italic_blanked_count} italicized cells across {len(wb.sheetnames)} sheets.")
    os.makedirs(os.path.dirname(output_cleaned_path), exist_ok=True)
    wb.save(output_cleaned_path)
    print(f"Saved cleaned workbook: {output_cleaned_path}")

    # 2. Re-read for evaluation with formulas evaluated
    wb_val = openpyxl.load_workbook(output_cleaned_path, data_only=True)
    
    sheet_configs = [
        # (sheet_name, start_col, ex_col)
        ("Push", 5, 2),
        ("Pull", 5, 2),
        ("Leg", 5, 2),
        ("PPush", 4, 1),
        ("PPull", 4, 1),
        ("PLegs", 4, 1),
        ("HPush", 4, 1),
        ("HPull", 4, 1),
        ("HLegs", 4, 1),
        ("2026 Routine", 2, 1),
    ]

    all_workouts_by_date = defaultdict(lambda: {
        "date": "",
        "sheets": set(),
        "exercises": defaultdict(lambda: {"sets": [], "pattern": "", "equipment": "", "axial_ratio": 0.0}),
    })

    for sname, start_col, ex_col in sheet_configs:
        if sname not in wb_val.sheetnames:
            continue
        ws = wb_val[sname]

        for c in range(start_col, ws.max_column + 1):
            dt_raw = ws.cell(row=1, column=c).value
            if dt_raw is None:
                continue

            # Parse date
            if isinstance(dt_raw, datetime):
                dt_str = dt_raw.strftime("%Y-%m-%d")
            else:
                dt_str = str(dt_raw)[:10]
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", dt_str):
                    continue

            for r in range(2, ws.max_row + 1):
                val = ws.cell(row=r, column=c).value
                if val is None:
                    continue

                parsed = parse_set_str(val)
                if not parsed:
                    continue

                reps, wt = parsed
                ex_name_raw = str(ws.cell(row=r, column=ex_col).value or "").strip()
                if not ex_name_raw or ex_name_raw.lower().startswith("reps") or ex_name_raw.lower().startswith("sets"):
                    continue

                clean_ex = ex_name_raw.split(" - ")[0].strip()
                if clean_ex.startswith("SW:"):
                    clean_ex = clean_ex[3:].strip()

                pattern, equip, axial_ratio = classify_exercise(clean_ex)
                e1rm = wt * (1.0 + reps / 30.0) if wt > 0 else 0.0

                w_entry = all_workouts_by_date[dt_str]
                w_entry["date"] = dt_str
                w_entry["sheets"].add(sname)

                ex_dict = w_entry["exercises"][clean_ex]
                ex_dict["pattern"] = pattern
                ex_dict["equipment"] = equip
                ex_dict["axial_ratio"] = axial_ratio
                ex_dict["sets"].append({
                    "reps": reps,
                    "weight_lbs": wt,
                    "e1rm_lbs": round(e1rm, 1),
                    "volume_lbs": round(reps * wt, 1),
                })

    # Transform into clean list of sessions
    structured_sessions = []
    for dt_str in sorted(all_workouts_by_date.keys()):
        day_data = all_workouts_by_date[dt_str]
        exercises_list = []
        total_working_tonnage = 0.0
        total_warmup_tonnage = 0.0
        total_working_sets = 0
        total_axial_units = 0.0
        pattern_sets = defaultdict(int)

        for ex_name, ex_info in day_data["exercises"].items():
            sets = ex_info["sets"]
            if not sets:
                continue

            working_tonnage = sum(s["volume_lbs"] for s in sets)
            top_wt = max(s["weight_lbs"] for s in sets)
            top_reps = max(s["reps"] for s in sets if s["weight_lbs"] == top_wt)
            best_e1rm = max(s["e1rm_lbs"] for s in sets)

            # Warm-up calculation
            warmup_count, warmup_tonnage = calculate_warmup_tonnage(top_wt, top_reps, ex_info["equipment"])

            # Axial compressive load estimate
            axial_units = len(sets) * (top_wt / 100.0) * ex_info["axial_ratio"] * 10.0

            total_working_tonnage += working_tonnage
            total_warmup_tonnage += warmup_tonnage
            total_working_sets += len(sets)
            total_axial_units += axial_units
            pattern_sets[ex_info["pattern"]] += len(sets)

            exercises_list.append({
                "exercise": ex_name,
                "movement_pattern": ex_info["pattern"],
                "equipment": ex_info["equipment"],
                "sets_completed": len(sets),
                "top_set": f"{top_reps} @ {top_wt:.1f} lbs",
                "top_weight_lbs": top_wt,
                "best_e1rm_lbs": best_e1rm,
                "working_tonnage_lbs": round(working_tonnage, 1),
                "warmup_sets_estimated": warmup_count,
                "warmup_tonnage_lbs": round(warmup_tonnage, 1),
                "detailed_sets": sets,
            })

        structured_sessions.append({
            "date": dt_str,
            "sheets_referenced": list(day_data["sheets"]),
            "exercise_count": len(exercises_list),
            "total_working_sets": total_working_sets,
            "total_working_tonnage_lbs": round(total_working_tonnage, 1),
            "total_warmup_tonnage_lbs": round(total_warmup_tonnage, 1),
            "total_session_tonnage_lbs": round(total_working_tonnage + total_warmup_tonnage, 1),
            "estimated_axial_load": round(total_axial_units, 1),
            "movement_pattern_sets": dict(pattern_sets),
            "exercises": exercises_list,
        })

    return structured_sessions, italic_blanked_count


def compute_summary_analytics(sessions):
    """Computes all-time PRs, phase statistics, and movement pattern balance."""
    all_time_prs = {}
    era_stats = {
        "2015_2016_PPL": {"sessions": 0, "sets": 0, "tonnage": 0.0},
        "2017_2020_PushPullLeg": {"sessions": 0, "sets": 0, "tonnage": 0.0},
        "2026_Restart_LP": {"sessions": 0, "sets": 0, "tonnage": 0.0},
    }
    pattern_totals = defaultdict(int)

    for sess in sessions:
        dt = sess["date"]
        year = int(dt[:4])
        if year <= 2016:
            era = "2015_2016_PPL"
        elif year <= 2020:
            era = "2017_2020_PushPullLeg"
        else:
            era = "2026_Restart_LP"

        era_stats[era]["sessions"] += 1
        era_stats[era]["sets"] += sess["total_working_sets"]
        era_stats[era]["tonnage"] += sess["total_session_tonnage_lbs"]

        for pat, cnt in sess["movement_pattern_sets"].items():
            pattern_totals[pat] += cnt

        for ex in sess["exercises"]:
            name = ex["exercise"]
            wt = ex["top_weight_lbs"]
            e1rm = ex["best_e1rm_lbs"]
            if name not in all_time_prs or e1rm > all_time_prs[name]["best_e1rm_lbs"]:
                all_time_prs[name] = {
                    "exercise": name,
                    "movement_pattern": ex["movement_pattern"],
                    "equipment": ex["equipment"],
                    "date": dt,
                    "top_set": ex["top_set"],
                    "top_weight_lbs": wt,
                    "best_e1rm_lbs": e1rm,
                }

    # Big 4 Compound Focus
    big_4 = {}
    for target in ["Squat", "Flat Bench", "Deadlift", "Overhead Press"]:
        match = None
        for name, pr in all_time_prs.items():
            if target.lower() in name.lower():
                if match is None or pr["best_e1rm_lbs"] > match["best_e1rm_lbs"]:
                    match = pr
        if match:
            big_4[target] = match

    return {
        "total_sessions_logged": len(sessions),
        "big_4_compound_prs": big_4,
        "era_breakdown": era_stats,
        "movement_pattern_distribution": dict(pattern_totals),
        "top_exercises_by_e1rm": sorted(all_time_prs.values(), key=lambda x: x["best_e1rm_lbs"], reverse=True)[:25],
    }


def main():
    source_file = RAW_INPUT_FILE
    if not os.path.exists(source_file):
        source_file = os.path.join(LIFTING_DIR, "PPL Routine.xlsx")
    if not os.path.exists(source_file):
        source_file = os.path.join(LIFTING_DIR, "PPL Routine_raw.xlsx")
    if not os.path.exists(source_file):
        print(f"Error: Could not find PPL Routine.xlsx in data/ or data/lifting/.")
        return

    os.makedirs(LIFTING_DIR, exist_ok=True)
    raw_archive_path = os.path.join(LIFTING_DIR, "PPL Routine_raw.xlsx")
    cleaned_path = os.path.join(LIFTING_DIR, "PPL Routine_cleaned.xlsx")
    parsed_json_path = os.path.join(LIFTING_DIR, "parsed_lifting_history.json")
    summary_json_path = os.path.join(LIFTING_DIR, "lifting_summary.json")

    # 1. Copy raw archive if not already identical
    if not os.path.exists(raw_archive_path) or os.path.abspath(source_file) != os.path.abspath(raw_archive_path):
        shutil.copy2(source_file, raw_archive_path)
        print(f"Archived raw file to: {raw_archive_path}")

    # 2. Process, blank italics, and parse
    sessions, italic_count = process_workbook(raw_archive_path, cleaned_path)

    # 3. Compute analytics
    summary = compute_summary_analytics(sessions)

    # 4. Save JSON files
    with open(parsed_json_path, "w") as f:
        json.dump(sessions, f, indent=2)
    print(f"Saved parsed lifting history: {parsed_json_path} ({len(sessions)} sessions)")

    with open(summary_json_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved lifting summary: {summary_json_path}")

    # 5. Move original PPL Routine.xlsx into data/lifting/PPL Routine.xlsx if still in data/
    target_original = os.path.join(LIFTING_DIR, "PPL Routine.xlsx")
    if os.path.exists(RAW_INPUT_FILE) and os.path.abspath(RAW_INPUT_FILE) != os.path.abspath(target_original):
        if os.path.exists(target_original):
            os.remove(target_original)
        shutil.move(RAW_INPUT_FILE, target_original)
        print(f"Moved original from {RAW_INPUT_FILE} to {target_original}")

    print("\nProcessing complete!")


if __name__ == "__main__":
    main()
