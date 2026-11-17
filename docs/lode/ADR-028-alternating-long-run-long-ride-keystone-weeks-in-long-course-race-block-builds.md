---
id: ADR-028
type: adr
domain: mpc
title: "Alternating long-run / long-ride keystone weeks in long-course race-block builds"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: 2026-11-16
supersedes: []        # e.g., ["ADR-001", "EXP-002"] -> Automatically marks those nodes SUPERSEDED in SQLite!
validated_by: ["EXP-007"]      # e.g., ["EXP-005"]
affects_services: ["control"]  # e.g., ["core", "db"]
one_liner: "70.3/IM race-block build/overload weeks alternate a long-run and a long-ride mandatory anchor; deload roles keep ADR-023; Tier-0 never moves."
---

# `ADR-028`: Alternating long-run / long-ride keystone weeks in long-course race-block builds

## 1. Context & Problem
`TASK-049` (`EXP-007` section 10) showed the reference 70.3 / Ironman athlete never schedules a run >= 75 min: with >= 1 heavy lift the two weekend anchors (`BIKE_DUR_OUT_DEF` 180 min ride, `RUN_AE2_LONG` 75 min run) cannot coexist in one calendar (heavy-lift +-24h / 48h separation), and a 90 min run needs >= 225 (3 runs, 40% share) / 281 (4 runs, 32% share) run min/week. Both anchors were mandatory (Tier-1 slack `shorten_or_drop_anchor`) every week, so the solver paid for the cheaper one (the run) and dropped it.

## 2. Considered Alternatives
1. **Fewer heavy lifts in long-course build weeks (Rejected by owner):** heavy lifting stays as is (ADR-012).
2. **One distance-aware weekend keystone (Rejected):** loses the weekly long ride or long run altogether.
3. **Alternate keystones (ADOPTED, owner decision):** long-run week then long-ride week.

## 3. Decision & Production Invariant
- **Target Component / Path:** `control/periodization.py` (`keystone_kind`, `keystone_run_minutes`, `keystone_run_rungs`, `apply_keystone_week`, `shift_run_split`), applied in `control/season.py` before each week solve.
- **Rule:** in race-block `build` / `overload` weeks of a 70.3 / Ironman season, week k (0-based count of such weeks) is a long-run week when k is even, a long-ride week when odd. Long-run week: only the keystone long run is `is_anchor` (the ride anchor becomes optional) (a Tier-2 split shift toward running was tried and rejected, see section 4). Long-ride week: only `BIKE_DUR_OUT_DEF` is an anchor (run anchor optional). Recovery / taper / race / base weeks and sprint / olympic / re-entry blocks are untouched (ADR-023 anchors optional in deload roles).
- **Distance-aware keystone duration:** ladder per distance (70.3: 75, 90, 90, 105, 105, 120; Ironman: 75, 90, 105, 120, 120, 150) clamped to the hostable `long_run` library rungs and to one library rung above the last long run actually scheduled (progression, never a jump). An unhostable keystone is retried one rung lower down to the shortest; the first rung keeping its anchor wins. The solver may still drop an anchor as the existing Tier-1 slack (reported, never silent).
- **Never loosened:** ADR-022 long-run cap, ADR-003 32% ratio, running ACWR, 48h caps, heavy-lift separation. Only the Tier-1 anchor obligation moves, plus soft Tier-2 goals.
- **Enforcement:** `tests/test_alternating_keystones.py`.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** `EXP-007` section 11 (before / after, reference 70.3 / Ironman at 8 s / week, 0 fallback, 0 violations; `CURRENT_ATHLETE` olympic byte-identical). 75 min keystone hosted in 2 of 7 long-run weeks (w2, w7); `shorten_or_drop_anchor:Run` build weeks 12 -> 4 (70.3), 15 -> 6 (Ironman). A 60/40 Run TSS split + 8-point duration shift made no extra keystone feasible and pulled Ironman build weeks to ~345 TSS vs 590-690 targets; dropped.
- **Supersedes:** None.

## 5. Consequences & Trade-Offs
- **Positive:** a 75 min long run is now scheduled where the Tier-0 rows can host it (w2, w7 of the reference block); the 180 min long ride is kept in every long-ride week.
- **Negative / Operational Cost:** 90+ min runs are never hosted and 75 min only in 2 of 7 long-run weeks: the receding-horizon run-strain guard (`TASK-061`) binds after low-run weeks and the 90 min row stack is unattributed (`TASK-062`); the ride keystone stays 180 min (`TASK-063`). Extra solves in failing long-run weeks. Ironman build weeks stay bound by the 48h ANS cap (`TASK-050` / `TASK-021`).
- **When to Revisit:** after `TASK-021` (ANS re-weighting) or if the owner relaxes the run-load progression policy.
