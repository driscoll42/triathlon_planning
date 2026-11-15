---
id: HEUR-007
type: heuristic
domain: integrations
title: "Pool Swim Active Length Parsing and Multi-Device Deduplication Window"
status: ACTIVE
created_at: 2026-11-10
supersedes: []
validated_by: []
one_liner: "Sum pool swim time strictly across active lengths (exclude drills from SWOLF) and deduplicate multi-device FIT files within 300s window."
---

# `HEUR-007`: Pool Swim Active Length Parsing and Multi-Device Deduplication Window

- **Target Subsystem / Path:** `src/clean_file.py`, `src/sync_8020_to_tp.py`, pool swim and multi-device ride ingestion.
- **Symptom / Failure Mode:**
  1. Pool swim duration and sTSS are severely inflated (by 20% to 50%), distorting weekly aerobic load and failing 80/20 intensity ratios.
  2. Weekly TSS is doubled when an athlete records a ride simultaneously on both a Garmin Edge 530 bike computer and a Garmin Enduro 3 watch.
  3. SWOLF calculations and average stroke rate collapse or yield divide-by-zero errors when swim drill logs are present.
- **Root Physics / Mechanism:**
  1. Garmin Swim 2 and Enduro 3 pool swim files store individual laps as `length` messages. With Auto-Rest enabled, `session.total_elapsed_time` includes all rest seconds at the lane wall.
  2. Drills (`swim_stroke == 'drill'` or `event == 'drill'`) record zero stroke counts (`total_strokes == None/0`), corrupting stroke efficiency metrics if not filtered out.
  3. Athletes frequently record rides on an Edge 530 (for handlebar cockpit viewing) and an Enduro 3 (for Garmin Unified Training Status). Syncing both creates duplicate activities in TrainingPeaks.
- **Permanent Operational Rule:**
  1. In pool swim `.fit` parsing, compute active swim duration strictly by summing `length` messages where `length_type == 'active'`.
  2. Filter out lengths where `swim_stroke == 'drill'` from SWOLF and stroke rate averages, but retain their duration in low-intensity aerobic swim time.
  3. Ingest only ONE `.fit` file per activity when start timestamps match within 300 seconds (`abs(start_time_A - start_time_B) < 300s`), prioritizing Edge 530 for cycling data quality.
- **Verification Check:**
  `python -c "from src.clean_file import deduplicate_fit_uploads, parse_pool_swim; assert deduplicate_fit_uploads(files) and parse_pool_swim('swim.fit')['active_only']"`
