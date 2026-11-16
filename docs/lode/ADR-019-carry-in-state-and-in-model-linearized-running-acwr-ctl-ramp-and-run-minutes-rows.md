---
id: ADR-019
type: adr
domain: mpc
title: "Carry-in state and in-model linearized running ACWR, CTL ramp and run-minutes rows"
status: ACTIVE
created_at: 2026-11-16
supersedes: []
validated_by: []
affects_services: ["solver", "control", "physiology"]
one_liner: "Week k+1 is solved with week k's boundary days and EWMA state as constants; running ACWR (A - rho*C <= eps), CTL ramp and re-entry run minutes are in-model rows."
---

# `ADR-019`: Carry-in state and in-model linearized running ACWR, CTL ramp and run-minutes rows

## 1. Context & Problem
Every weekly MILP was an isolated week: Monday ignored Sunday (hard/easy adjacency, 48h heavy-run separation, HEUR-004 deadlift +-24h, non-consecutive strength days and the rolling 48h TSS/ANS/strain caps all stopped at the week boundary), and the EWMA running ACWR (`ADR-012`: <= 1.20 while run CTL < 15, <= 1.25 after) was enforced only after the fact. The season loop shrank `max_weekly_run_mech_strain` when the state's ACWR was already over the limit, i.e. it reacted one week late and could not shape the next week's day pattern. `AthletePhysiologicalState` was also never seeded with the profile's run CTL (it kept the dataclass default 35), so the "run CTL < 15" rule never fired outside the re-entry block.

## 2. Considered Alternatives
1. **Option A (Rejected): keep the post-hoc strain-cap shrink.** A weekly total cannot express a daily EWMA ratio; two weeks with equal run strain have very different peak ACWR depending on which days carry them.
2. **Option B (Rejected): a rolling 35-day multi-week MILP.** Exact, but multiplies the model by about 5 and defeats the receding-horizon structure (`ADR-005`); previous weeks are fixed history, so they are constants.
3. **Option C (Rejected): nonlinear ratio `A_d / C_d <= rho`.** `C_d` is affine in `x`, so for `C_d > 0` the ratio bound is exactly the linear row `A_d - rho*C_d <= 0`; the only difficulty is `C_d -> 0`, handled by `eps`.
4. **Option D (ADOPTED): `WeekCarryIn` constants plus linear rows in a new builder, `engine/builders/load_dynamics.py`.**

## 3. Decision & Production Invariant
- **Target Component / Path:** `solver/engine/carry_in.py` (`DayLoad`, `LoadState`, `WeekCarryIn`), `solver/engine/builders/load_dynamics.py`, `control/carry.py` (season hand-off), `UnifiedWeekEngine(..., carry_in=)`, `TriathlonMILPScheduler(..., carry_in=)`, EWMA constants `ALPHA_ACUTE/CHRONIC/ATL/CTL` in `physiology/state.py` (single source for `step_day` and the rows).
- **Locked Parameters / Contract:**
  - **Boundary (Tier-0 constants).** `DayLoad` (days -1, -2: hard, heavy/long run, deadlift, heavy strength, strength, aerobic TSS, mech strain, ANS) comes from `classify_workouts`, the same sets as the safety layer. Day -1 hard -> no hard workout on day 0; day -1 heavy/long run -> no heavy/long run or deadlift on day 0; day -1 deadlift -> no heavy/long run on day 0; day -1 strength -> no strength on day 0; scheduler adjacency (heavy lift vs heavy/long run +-24h); rolling 48h caps over the window (-1, 0): `sum_i v_i x[i,0] <= max(0, cap - v_{-1})`. An empty `WeekCarryIn()` adds no rows (the isolated-week model, row for row).
  - **Linearized running ACWR.** With `a = 2/8`, `c = 2/29` (as `step_day`), carried `A0, C0` after day -1 and run mechanical strain `m_i`: `A_d = (1-a)^(d+1) A0 + a * sum_{k<=d} (1-a)^(d-k) sum_i m_i x[i,k]`, same for `C_d` with `c`. **Ceiling (Tier-0), every day d:** `A_d - rho_max C_d <= eps`, with `rho_max` = `max_running_acwr_reentry` (1.20) while start-of-week run CTL < `reentry_run_ctl_exit` (15), else `max_running_acwr` (1.25); 1.30 (INVARIANTS) when no profile; min(rho, 1.15) in masters mode. The rhs is clamped at `max(eps - const_d, 0)`: an already-overshooting start state is not charged to the week (the plan may only not worsen it) instead of making the week infeasible.
  - **`eps` (`epsilon_floor`).** `eps = (a - rho_max*c) * min_run_strain` of the library: the value of `A - rho*C` after ONE smallest run from a fully detrained state (CURRENT_ATHLETE: 3.345 strain units). The ratio is undefined near zero chronic load and `ADR-012` allows the 60 min/week floor regardless of ACWR, so a single floor-sized session is always admissible; above that the ratio rules. Stated plainly: with small `C` the raw *ratio* can exceed `rho` by design (`A - rho*C <= eps` is a hybrid absolute/relative bound), and with the current library's shortest run (30 min) and at most 2 runs/week the EWMA ratio of two spaced runs is mathematically about 1.4, so the cap bites only on top of that floor. Walk-jog candidates (`TASK-023`) are the fix, not a looser `rho`.
  - **Lower window bound 0.80 is a named Tier-1 slack, not Tier-0.** `A_d - 0.8 C_d + M_d s >= 0`, one binary `s` per week, category `relax_acwr_floor`, weight 1 (cheaper than every ranked relaxation, `Tier1Registry.add_named`), reported in `relaxations`. Undertraining is not an injury risk and deload/taper/re-entry weeks must stay feasible. This is a NEW in-model rule (none existed before), not a loosening of an existing one; the upper bound (1.30 global, 1.20/1.25 running) is Tier-0.
  - **Weekly CTL ramp (Tier-0).** `CTL_7 - CTL_0 <= ramp` with `CTL_7 = (1-b)^7 CTL_0 + b sum_d (1-b)^(6-d) T_d`, `b = 2/43`, `T_d` the bike+run+swim TSS (what `total_ctl` integrates). `ramp` = `AthleteProfile.max_weekly_ctl_ramp` = 7 CTL points/week (upper end of the usual 5-8 guidance; ADR-012 sets no value), 5 for masters. New profile field: flag for review.
  - **Absolute run-minutes guard (Tier-0, ADR-012 `max_weekly_run_minutes_increase`).** While run CTL < 15: `sum run duration <= max(reentry_run_minutes_floor, prev_week_run_minutes + max_weekly_run_minutes_increase)` = max(60, prev + 15).
  - **MPC backstop.** The season loop no longer shrinks the run-strain cap for ACWR (the in-model rows replace it); it keeps the ODI trigger (no in-model ODI until `TASK-022`). `process_weekly_execution_and_recalibrate` stays as the backstop for ACTUAL data (a plan that held can still be executed worse; `TASK-025`) with its 1.30 global trigger.
  - `is_week_reversal_symmetric` returns False whenever the carry-in is active (boundary constants and EWMA rows are time-oriented).
  - `AthleteProfile.to_athlete_state` seeds `run_ctl/atl` (new `starting_run_ctl/atl`) and splits the remainder of `starting_ctl/atl` over bike and swim 55:25.
- **Enforcement Mechanism:** `tests/test_carry_in.py`: boundary bans, 48h cap counting day -1, empty carry-in equals the isolated model (rows/objective/plan), ACWR row binds in a constructed ramp, eps floor, Tier-1 floor waiver, CTL-ramp row, run-minutes row, symmetry off, profile seeding, and a 40-week season with `A_d - rho*C_d <= eps` on every day of every week plus the run-minutes guard and CTL ramp every week.

## 4. Empirical Evidence & Validation Lineage
- **Validated By:** CURRENT_ATHLETE Olympic season (24-week re-entry + 16-week race block): 40/40 OPTIMAL in 7.9 s (8.9 s without the rows); `A_d - rho*C_d <= eps` held in 40/40 weeks (without the rows: 3 weeks over eps, max excess 3.75 vs 3.33). Floor waiver used in 31/40 weeks (the library cannot hold 0.80 with 1-2 runs/week).
- **Supersedes:** None. Do not re-add a post-hoc ACWR strain-cap shrink; it cannot see the daily pattern.

## 5. Consequences & Trade-Offs
- **Positive:** Weeks are solved in context (no Sunday-hard plus Monday-hard, correct 48h caps across the boundary); ACWR is a plan-time guarantee, not a next-week reaction; run CTL is seeded so ADR-012's 1.20/1.25 switch is real.
- **Negative / Operational Cost:** The ACWR ceiling is `eps`-relative at low chronic load (above). The run-minutes guard (60 + 15/wk) plus 30-minute minimum runs keeps CURRENT_ATHLETE inside the re-entry rules for the whole season (run CTL 2.8-8.5, never 15) and leaves a Run frequency shortfall or dropped Run anchor in many weeks (14/40 report a `Run:` relaxation); `TASK-023` (walk-jog) is the structural fix. State and model must share EWMA constants (`physiology/state.py`); `TASK-021` changes them in one place.
- **When to Revisit:** After `TASK-021` (exact Banister decay) and `TASK-023` (walk-jog), re-derive `eps` and re-check whether the 0.80 floor can be Tier-0; once ODI is in-model (`TASK-022`), move that trigger out of the season loop too.
