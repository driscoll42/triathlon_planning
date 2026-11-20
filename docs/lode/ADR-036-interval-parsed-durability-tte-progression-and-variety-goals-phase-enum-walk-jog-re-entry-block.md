---
id: ADR-036
type: adr
domain: mpc
title: "Interval-parsed durability, TTE progression and variety goals, Phase enum, walk-jog re-entry block"
status: ACTIVE # PROPOSED | ACTIVE | SUPERSEDED | DEPRECATED
created_at: 2026-11-19
supersedes: []        # e.g., ["ADR-001", "EXP-002"] -> Automatically marks those nodes SUPERSEDED in SQLite!
validated_by: []      # e.g., ["EXP-005"]
affects_services: ["library", "control", "solver", "physiology"]
one_liner: "Durability = f(parsed intervals); week phase is a Phase enum, never a substring."
---

# `ADR-036`: Interval-parsed durability, TTE progression and variety goals, Phase enum, walk-jog re-entry block

## 1. Context & Problem
`TASK-023`. Four defects shared one root, "a number or a label typed in by hand and then matched loosely":
1. **Dual-scale durability.** `durability_score` was `0..10` for generated rows and `0..85` for hybrid hand-coded rows (`BK_LONG_DUR` 85, `RN_LONG_Z2` 65). The meso objective normalizes by the maximum, so one 85 row compressed every other row's reward; the hybrid objective used the raw values. Nothing tied a score to what the session actually does.
2. **No progression target.** The weekly objective had TSS, zone and split goals but nothing asking for more (or less, in a recovery week) time at threshold, the stimulus the durability literature (`SPEC.md`, Maunder 2021) cares about, and nothing preventing the same key session from being chosen every week.
3. **Substring phase matching.** `configure_mesocycle_phase` used `"base" in phase_name`, which missed `"General Foundation"` (a base block) and sent `"Race Week"` to the Build 1 defaults; `ROLE_CONFIG_NAME` worked around it with neutral keywords.
4. **No re-entry run progression.** A detrained athlete (run CTL < 15) could only be given 30+ min continuous runs; `ADR-019` named walk-jog as the structural fix for the `eps` / single-run-week problem but there was no impact model or ladder to build it on.

## 2. Considered Alternatives
1. **Rescale the hand-typed values onto one scale (Rejected):** fixes the units, not the cause: the next hand-coded row picks a new number. The score must be a function of the session.
2. **A hard weekly TTE row `T_w >= (1+g) T_{w-1}` (Rejected):** it is a Tier-2 aspiration, not a safety rule; as a hard row it would be infeasible whenever ACWR / ODI / 80/20 bind (they must win), and it would have to be relaxed through the Tier-1 registry. A penalized one-sided slack gives the same pull with no feasibility cost.
3. **Fuzzy phase classification (`"foundation"` -> base) (Rejected):** substring rules are the defect. An explicit alias table that raises on unknown labels cannot misclassify silently.
4. **Reduce walk-jog impact by a fitted factor now (Rejected):** no athlete data exists (`EXP-006`); an optimistic number would loosen the in-model ACWR / strain / ODI rows (charter §2). The model is derived from contact count and peak force and inflated by a safety factor instead.
5. **Parsed intervals + one derived score, soft TTE / variety goals, `Phase` enum, conservative impact model (ADOPTED).**

## 3. Decision & Production Invariant
- **Target Component / Path:** `library/intervals.py`, `library/build.py` (`endurance_loads`), `library/schema.py`, `library/loader.py`, `scripts/migrations/m2026_11_19_interval_durability.py`; `model/phase.py`, `control/mpc.py`, `control/periodization.py`, `control/season.py`, `control/carry.py`, `physiology/athlete_profile.py`; `solver/engine/builders/goals.py`, `solver/engine/variety.py`, `solver/engine/carry_in.py`, `solver/pruning.py`; `physiology/load_model.py`.
- **Locked Parameters / Contract:**
  - *Features* (columns `t_thr_min`, `t_cont_min`, `late_min`): $T^{\text{thr}}$ = Z3 + ZY + Z4 + Z5 minutes (Zone X excluded; Zone 5 reps <= 20 s are alactic strides and excluded); $T^{\text{cont}}$ = longest run of adjacent threshold segments (Z1/Z2/X segments and rests split blocks); $\text{late}$ = threshold minutes after 60% of elapsed time (pro rata); $\text{brick}$ = `sport == "Brick"`. The parse must reproduce the row's stored zone minutes (distance rows: zone distances) within `max(1, 6%)` per zone or the row falls back to zone columns (`t_thr` exact, `t_cont` from an explicit `N x M m` hint or the largest `M m Zk` token, `late` from a "late / finish / final" cue or, for a Brick, the run-leg minutes). Strength rows and tests carry 0.
  - *Score:* one `0..10` scale, $D = \min(10,\ 1.5 + 5\,\text{sat}\tfrac{T-30}{210} + 2.5\,\text{sat}\tfrac{T^{\text{cont}}}{30} + 1\,\text{sat}\tfrac{T^{\text{thr}}}{45} + 1.5\,\text{sat}\tfrac{\text{late}}{20} + 1.5\,\text{brick})$, strength `2.0`; schema enforces `0 <= D <= 10`, `t_cont, late <= t_thr <= duration`. Weights are calibration priors (`EXP-006`).
  - *TTE goal:* Tier-2 one-sided soft goal on $T^{\text{thr}}_w = \sum t^{\text{thr}}_i x_{i,d}$. `progress` (base / build / overload): shortfall below $(1+g) T_{\text{ref}}$, $g = 0.05$, $T_{\text{ref}}$ = last non-deload solved week; `deload` (recovery): overshoot above $0.6\, T_{w-1}$; taper / race `off`. Weight `w_tte` = 30 per unit of $T_{\text{ref}}$. No carry reference (week 1, isolated week) = no goal.
  - *Variety:* linear cost `w_variety` (4.0) x $\sum_k (N-k)/N$ for a key, non-anchor code selected $k$ weeks ago (window $N$ = 3, memory 4 weeks); dominance pruning ranks twins by it.
  - *Phase:* `Phase` is the only week-role type; `Phase.parse` is an exact normalized alias lookup that raises on unknown text. `RACE` shares the TAPER targets.
  - *Walk-jog:* `run_walk_mechanical_strain` = run-zone strain + walk minutes x $2 \cdot \tfrac{110}{170}(\tfrac{1.2}{2.2})^3 \approx 0.21$ of a Z1 run minute; ladder 1:1 x 20, 2:1 x 25, 3:1 x 30, 4:1 x 35 min, one rung per build week, held in deload weeks, active only while the in-model re-entry flag holds.
- **Enforcement Mechanism:** `tests/test_interval_durability.py` (parser grammar, library durability == derived value, 0..10 scale, >= 97% of scraped rows parsed), `tests/test_phase_enum.py` (every generated display / step label resolves, unknown raises, `General Foundation` is base, `Race` = taper targets), `tests/test_tte_variety.py` (goal pulls / caps the right session, soft, inert without carry, variety swaps equal twins, pruning soundness, carry hand-off), `tests/test_walk_jog_block.py` (conservative, bounded impact model and ladder), 8-season matrix (`-m slow`, `perf/season_validation`).

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** unit tests above plus the 8-season matrix (0 Tier-0 violations; see `docs/archive/TASKS_COMPLETED.md`, `TASK-023`). The calibration constants (durability weights, `g`, `w_tte`, `w_variety`, walk dose) are priors to be fitted in `EXP-006` / `EXP-004`; nothing here is athlete-validated.
- **Supersedes:** None formally. It retires the dual-scale `durability_score` note in `ADR-014` and the `ROLE_CONFIG_NAME` keyword workaround; do not re-propose hand-typed durability or substring phase matching.

## 5. Consequences & Trade-Offs
- **Positive:** durability is reproducible from the description; a new row cannot carry a private scale; long easy sessions, late threshold work and bricks are rewarded for what they are; build weeks are pulled toward more threshold time and recovery weeks away from it; repeated key sessions cost something; an unknown phase label fails loudly; `TASK-058` has an impact model and ladder to build `RUN_RW_*` rows on.
- **Negative / Operational Cost:** `data/workout_library.csv` gained three columns (rerun `scripts/migrations/m2026_11_19_interval_durability.py`; new rows must use `endurance_loads(description=...)` or `apply_interval_features`); long easy sessions score lower than the old hand-typed 7.0-8.5 (the objective is normalized, so only relative order matters); `RACE` weeks now use taper (not Build 1) targets; the hybrid objective sees durability on `0..10` instead of `0..85`; the TTE goal competes with the TSS / zone goals and can nudge selection in build weeks.
- **When to Revisit:** after `EXP-006` (fit the durability weights, `g`, the walk dose factor against actuals), after `TASK-058` lands the run/walk rows (re-derive `acwr_eps_floor`), or if a season shows TTE shortfall dominating the TSS goal.
