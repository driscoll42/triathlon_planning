---
name: block-review
description: >-
  Run a multi-agent weekly (7-day) or 4-week (28-day) mesocycle training audit
  using 4 dedicated sport specialists (Run Analyst, Bike Analyst, Swim Analyst,
  Strength Analyst) in parallel followed by a Cross-Sport MPC Synthesizer. Use
  whenever the user asks for a weekly review, 4-week block review, mesocycle
  audit, or multi-sport trend analysis.
---

# Multi-Sport Block Review Skill (Weekly / 4-Week Mesocycle)

Orchestrates a parallel 4-specialist review (`Run`, `Bike`, `Swim`, `Strength`) over deterministic telemetry extracted by `triplan.io.workout_review`, followed by a cross-sport MPC & safety synthesis.

## How to Run

### Option A — Dynamic Workflow (Preferred)
Call `run_workflow` pointing at [scripts/block_review_workflow.py](./scripts/block_review_workflow.py):
- **Weekly review (7 days):**
  `ScriptPath`: `<repo_root>/.agents/skills/block-review/scripts/block_review_workflow.py`
  `Args`: `{"days": 7, "date": "<YYYY-MM-DD>"}` (optional: `"current_week"`, `"last_test_week"`, `"record": false`)
- **4-week mesocycle review (28 days):**
  `Args`: `{"days": 28, "date": "<YYYY-MM-DD>"}`

The workflow:
1. Runs `python -m triplan.io.workout_review` once to build the deduplicated block JSON brief.
2. Fans out 4 specialist agents concurrently (`parallel`) using the role briefs in `agents/`:
   - [Run Analyst](./agents/run_analyst.md)
   - [Bike Analyst](./agents/bike_analyst.md)
   - [Swim Analyst](./agents/swim_analyst.md)
   - [Strength Analyst](./agents/strength_analyst.md)
3. Synthesizes all 4 reports alongside `cross_sport_summary` (80/20 polarity, 48h peaks, `HEUR-004`, `ADR-011` CP retests, Garmin readiness).

### Option B — Direct Subagent Invocation (Fallback)
If launching specialists manually via `invoke_subagent`, first run:
```bash
python -m triplan.io.workout_review --days 7 --date <YYYY-MM-DD> --format json
```
Then pass each sport slice + the matching `agents/<sport>_analyst.md` prompt to an `invoke_subagent` (`research` or `self`) call, and synthesize the returned JSON findings against `docs/INVARIANTS.md`.
