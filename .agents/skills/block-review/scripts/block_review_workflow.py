"""Dynamic run_workflow script for parallel 4-sport weekly/4-week block reviews (PRD-003).

Invoke via `run_workflow(ScriptPath="<repo>/.agents/skills/block-review/scripts/block_review_workflow.py", Args={"days": 7})`
(or `days=28` for a 4-week mesocycle audit).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

_SPECIALIST_SCHEMA = {
    "type": "object",
    "properties": {
        "sport": {"type": "string"},
        "status": {"type": "string", "enum": ["nominal", "watch", "action_required", "no_data"]},
        "key_metrics": {"type": "array", "items": {"type": "string"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sport", "status", "key_metrics", "findings", "recommendations"],
}

_SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "window": {"type": "string"},
        "overall_status": {"type": "string", "enum": ["nominal", "watch", "action_required"]},
        "polarity_80_20_verdict": {"type": "string"},
        "guardrail_alerts": {"type": "array", "items": {"type": "string"}},
        "cp_retest_verdict": {"type": "string"},
        "next_week_adjustments": {"type": "array", "items": {"type": "string"}},
        "executive_summary": {"type": "string"},
    },
    "required": [
        "window",
        "overall_status",
        "polarity_80_20_verdict",
        "guardrail_alerts",
        "cp_retest_verdict",
        "next_week_adjustments",
        "executive_summary",
    ],
}

repo_root = Path(WORKSPACES[0]) if WORKSPACES else Path.cwd()
cfg = globals().get("args", {}) or {}
days = int(cfg.get("days", 7))
target_date = cfg.get("date")
start_date = cfg.get("start")
end_date = cfg.get("end")
current_week = int(cfg.get("current_week", 1))
last_test_week = int(cfg.get("last_test_week", 0))
record_flag = bool(cfg.get("record", False))

phase("Extract Telemetry Brief")
py_exe = shutil.which("python") or sys.executable
cmd = [
    py_exe,
    "-m",
    "triplan.io.workout_review",
    "--days",
    str(days),
    "--current-week",
    str(current_week),
    "--last-test-week",
    str(last_test_week),
    "--format",
    "json",
]
if target_date:
    cmd.extend(["--date", str(target_date)])
if start_date and end_date:
    cmd.extend(["--start", str(start_date), "--end", str(end_date)])
if cfg.get("fit_dir"):
    cmd.extend(["--fit-dir", str(cfg["fit_dir"])])
if cfg.get("lifting_json"):
    cmd.extend(["--lifting-json", str(cfg["lifting_json"])])
if cfg.get("wellness_dir"):
    cmd.extend(["--wellness-dir", str(cfg["wellness_dir"])])
if record_flag:
    cmd.append("--record")

proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
if proc.returncode != 0:
    raise RuntimeError(f"workout_review failed ({proc.returncode}): {proc.stderr.strip()}")
brief = json.loads(proc.stdout)
log(
    f"Extracted brief for {brief['window']['start_date']}..{brief['window']['end_date']} | "
    f"activities={brief['ingestion']['activities_in_window']} | "
    f"lifting_sessions={brief['sports']['Strength']['session_count']}"
)

agents_dir = repo_root / ".agents" / "skills" / "block-review" / "agents"
specialists = [
    {
        "sport": "Run",
        "role": "Run Block Analyst",
        "prompt_file": agents_dir / "run_analyst.md",
        "payload": {
            "window": brief["window"],
            "run_cp": brief["thresholds"]["run_cp"],
            "run_telemetry": brief["sports"]["Run"],
            "cp_retest_run": brief["cross_sport_summary"]["cp_retest_evaluation"]["Run"],
            "deadlift_run_collisions": brief["cross_sport_summary"]["deadlift_run_collisions"],
        },
    },
    {
        "sport": "Bike",
        "role": "Bike Block Analyst",
        "prompt_file": agents_dir / "bike_analyst.md",
        "payload": {
            "window": brief["window"],
            "bike_cp": brief["thresholds"]["bike_cp"],
            "bike_telemetry": brief["sports"]["Bike"],
            "cp_retest_bike": brief["cross_sport_summary"]["cp_retest_evaluation"]["Bike"],
        },
    },
    {
        "sport": "Swim",
        "role": "Swim Block Analyst",
        "prompt_file": agents_dir / "swim_analyst.md",
        "payload": {
            "window": brief["window"],
            "swim_css": brief["thresholds"]["swim_css"],
            "swim_telemetry": brief["sports"]["Swim"],
        },
    },
    {
        "sport": "Strength",
        "role": "Strength Block Analyst",
        "prompt_file": agents_dir / "strength_analyst.md",
        "payload": {
            "window": brief["window"],
            "strength_telemetry": brief["sports"]["Strength"],
            "deadlift_run_collisions": brief["cross_sport_summary"]["deadlift_run_collisions"],
        },
    },
]

for spec in specialists:
    rubric = spec["prompt_file"].read_text(encoding="utf-8")
    spec["full_prompt"] = (
        f"{rubric}\n\n"
        f"## Input Telemetry JSON\n```json\n{json.dumps(spec['payload'], indent=2)}\n```\n"
    )

phase("Parallel Sport-Specialist Audits")
specialist_reports = await parallel(
    specialists,
    lambda s: agent(s["full_prompt"], schema=_SPECIALIST_SCHEMA, role=s["role"]),
)
for rep in specialist_reports:
    log(f"[{rep['sport']}] status={rep['status']} | findings={len(rep['findings'])}")

phase("Cross-Sport MPC Synthesis")
synth_prompt = (
    "You are the Cross-Sport MPC & Orthopedic Safety Synthesizer for a hybrid triathlete.\n"
    "Synthesize the 4 sport specialist reports and the cross-sport summary below.\n"
    "Verify: (1) 80/20 intensity partitioning (>=78% Z1/Z2, <=20% mod+high, low Zone X), "
    "(2) 48h rolling TSS/strain/ANS caps vs charter & athlete limits, "
    "(3) HEUR-004 deadlift-run 24h lockouts and barbell >=60% share, "
    "(4) Garmin readiness alpha_d and ADR-011 CP retest verdicts.\n"
    "Only cite numbers present in the inputs.\n\n"
    f"## Window & Ingestion\n```json\n{json.dumps({'window': brief['window'], 'ingestion': brief['ingestion']}, indent=2)}\n```\n\n"
    f"## Cross-Sport Summary\n```json\n{json.dumps(brief['cross_sport_summary'], indent=2)}\n```\n\n"
    f"## Specialist Reports\n```json\n{json.dumps(specialist_reports, indent=2)}\n```\n"
)
synthesis = await agent(synth_prompt, schema=_SYNTHESIS_SCHEMA, role="Cross-Sport MPC Synthesizer")

result = {
    "window": brief["window"],
    "ingestion": brief["ingestion"],
    "specialists": specialist_reports,
    "synthesis": synthesis,
}
log(f"Synthesis complete: overall_status={synthesis['overall_status']}")
print(json.dumps(result, indent=2))
