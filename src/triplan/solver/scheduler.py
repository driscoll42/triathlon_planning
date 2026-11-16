"""
Closed-Loop Triathlon Training Optimizer (Production Implementation)
Based on: 'Closed-Loop Triathlon Training Optimizer: Physiological & Mathematical System Specification'
Authors/Sources: Joe Friel, Jim Vance, Patrick Hagerman, Matt Fitzgerald, Ed Maunder, Stephen Seiler.

Key Architecture:
- Dual-load modeling: Aerobic TSS vs Mechanical Strain
- Exponential ANS Costing: e^(0.65 * (z - 1))
- Asynchronous tissue remodeling tracking (Cardio tau=35d, Tendon/Bone tau=120d, ODI <= 1.35)
- EWMA Mechanical ACWR (Acute tau=7d, Chronic tau=28d)
- Exact algebraic linearizations (Single run <= 32% weekly run volume, TiZ ratios, Sport splits)
- Keep-Hard-Days-Hard same-day strength stacking (AM endurance + PM strength)
- Adjacency constraints: No consecutive hard days, no consecutive heavy runs (>=48h), no heavy run after strength
- 48h rolling glycogen/ANS/mechanical strain caps with robust infeasibility recovery
- Sequential Top-K distinct plan generation via Canonical No-Good Integer Cuts
- 3-Tier Closed-Loop Model Predictive Control (MPC):
    * Tier 1: Daily Morning Readiness Scalar (HRV RMSSD, sleep, soreness)
    * Tier 2: Weekly Receding-Horizon Update (Banister PMC, EWMA ACWR, ODI)
    * Tier 3: Mesocycle 3:1 Step-Cycles & Fast-Decay Taper
"""
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import copy
import logging

import highspy

from triplan.solver.highs_runtime import apply_threads
from triplan.solver.safety_constraints import (
    LexState,
    ModelHooks,
    SafetyLimits,
    Tier1Registry,
    add_tier0_safety,
    add_tier1_targets,
    classify_workouts,
    explain_feasibility,
    lexicographic_solve,
    run_share_report,
)
from triplan.model.candidates import CandidateWorkout, MesoCycleTargetsAndConstraints

logger = logging.getLogger(__name__)


# =============================================================================
# 3. MILP OPTIMIZATION ENGINE (HiGHS)
# =============================================================================

class TriathlonMILPScheduler:
    """
    Production Mixed-Integer Linear Programming (MILP) 7-Day Triathlon Scheduler.

    Constraint hierarchy (ADR-013, TASK-016/017):
    - Tier 0 (hard, never relaxed): daily time caps, facility/modality gating, workout uniqueness,
      rolling 48h TSS/ANS/strain caps, axial/neuromuscular caps, single-run share cap (32% with
      >= 4 runs, explicit pigeonhole cap below), hard/easy adjacency, 48h heavy-run separation,
      heavy strength +-24h vs threshold/long runs, biomechanical antagonism matrix.
      Shared with HybridAthleteMILPOptimizer via ``safety_constraints``.
    - Tier 1 (named, reported slacks, lexicographic by relaxation order): anchors, min sport
      frequencies, strides, heavy-strength count (floor 1), rest days (floor 1).
    - Tier 2 (goal programming): TSS, zones, sport splits, bike/run TSS split, durability.
    Top-K distinct plans via canonical No-Good cuts. The caller's config is never mutated.
    """

    def __init__(
        self,
        workouts: List[CandidateWorkout],
        config: MesoCycleTargetsAndConstraints
    ) -> None:
        self.workouts = list(workouts)
        self.cfg = copy.deepcopy(config)  # never mutate the caller's config (TASK-013)
        self.num_workouts = len(self.workouts)
        self.days = list(range(7))
        self.weekdays = [0, 1, 2, 3, 4]
        self.weekends = [5, 6]
        self.sports = ["Swim", "Bike", "Run", "Strength"]
        self.zones = [1, 2, 3, 4, 5]
        self.notes: List[str] = []
        self.last_diagnostics: List[str] = []
        self.last_status: Optional[str] = None

        if self.cfg.sport_freq_bounds is None:
            self.cfg.sport_freq_bounds = {"Swim": (2, 2), "Bike": (3, 4), "Run": (4, 5), "Strength": (1, 2)}

        # Tier-2 only: a sport with no facility access gets a 0% duration-split target, redistributed
        # proportionally across the other sports. Frequency minimums are NOT touched; an unmeetable
        # minimum becomes a reported Tier-1 relaxation.
        for s in self.sports:
            if sum(self.cfg.facility_access.get(s, [True] * 7)) == 0 and \
                    self.cfg.target_sport_duration_splits.get(s, 0.0) > 0:
                zero_split = self.cfg.target_sport_duration_splits[s]
                self.cfg.target_sport_duration_splits[s] = 0.0
                rem_sum = sum(v for k, v in self.cfg.target_sport_duration_splits.items() if k != s)
                if rem_sum > 0:
                    for k in self.cfg.target_sport_duration_splits:
                        if k != s:
                            self.cfg.target_sport_duration_splits[k] += zero_split * (
                                self.cfg.target_sport_duration_splits[k] / rem_sum)
                self.notes.append(f"No {s} access this week: its {zero_split:.0%} duration-split target was "
                                  f"redistributed to the other sports.")

        self.limits = SafetyLimits.from_config(self.cfg)
        self.sets = classify_workouts(self.workouts)

    def _allowed(self, i: int, d: int) -> bool:
        """Unary Tier-0 gating: time cap, facility access, outdoor-bike weekend rule."""
        w = self.workouts[i]
        if w.duration > self.cfg.daily_time_caps[d] + 1e-9:
            return False
        sports = ["Bike", "Run"] if w.sport == "Brick" else [w.sport]
        if not all(self.cfg.facility_access.get(s, [True] * 7)[d] for s in sports):
            return False
        if w.sport == "Bike" and d in self.weekdays and not w.is_indoor_bike:
            return False
        return True

    def explain_feasibility(self) -> List[str]:
        return self.notes + explain_feasibility(self.workouts, self.cfg, self.limits, self._allowed, self.sets)

    @staticmethod
    def _add_clean_row(
        h: highspy.Highs,
        col_coeff_map: Dict[int, float],
        lower_bound: float,
        upper_bound: float,
        tol: float = 1e-9
    ) -> None:
        """Consolidates duplicate column indices, sorts, and adds clean row to HiGHS."""
        filtered = [(col, val) for col, val in sorted(col_coeff_map.items()) if abs(val) > tol]
        if not filtered:
            if lower_bound > tol or upper_bound < -tol:
                raise ValueError(f"Empty row with infeasible bounds [{lower_bound}, {upper_bound}]")
            return
        indices = np.array([col for col, _ in filtered], dtype=np.int32)
        values = np.array([val for _, val in filtered], dtype=np.double)
        h.addRow(float(lower_bound), float(upper_bound), len(indices), indices, values)

    def solve_top_k_plans(self, k_plans: int = 3, time_limit: float = 10.0) -> List[Dict[str, Any]]:
        """
        Builds the 3-tier MILP and solves it lexicographically (Tier-1 slack first, then Tier-2 goals),
        applying canonical No-Good Integer Cuts to extract the top K distinct weekly schedules.
        Returns [] when Tier 0 is infeasible; see ``self.last_diagnostics`` for the plain-English reason.
        """
        inf = highspy.kHighsInf
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        if time_limit is not None and time_limit > 0:
            h.setOptionValue("time_limit", float(time_limit))
        h.changeObjectiveSense(highspy.ObjSense.kMinimize)

        diagnostics = self.explain_feasibility()
        self.last_diagnostics = diagnostics

        col_counter = 0
        def add_var(lb: float, ub: float, cost: float, is_integer: bool) -> int:
            nonlocal col_counter
            idx = col_counter
            h.addVar(float(lb), float(ub))
            h.changeColCost(idx, float(cost))
            if is_integer:
                h.changeColIntegrality(idx, highspy.HighsVarType.kInteger)
            col_counter += 1
            return idx

        def add_row(coeff_map: Dict[int, float], lb: float, ub: float) -> None:
            self._add_clean_row(h, coeff_map, lb, ub)

        # ---------------------------------------------------------------------
        # 1. DECISION VARIABLES
        # ---------------------------------------------------------------------
        max_durab = max((w.durability_score for w in self.workouts), default=1.0)

        # Primary binary assignment variables: x[i, d] in {0, 1}
        x_vars: Dict[Tuple[int, int], int] = {}
        for i, w in enumerate(self.workouts):
            for d in self.days:
                net_cost = (
                    -self.cfg.w_durability * (w.durability_score / max(max_durab, 1e-9))
                    + self.cfg.w_friction * w.preference_penalty
                )
                x_vars[(i, d)] = add_var(0.0, 1.0, net_cost, is_integer=True)

        # Auxiliary binary variables: sport occurrence y[s, d] in {0, 1}
        y_vars: Dict[Tuple[str, int], int] = {}
        for s in self.sports:
            for d in self.days:
                y_vars[(s, d)] = add_var(0.0, 1.0, 0.0, is_integer=True)

        # Double session w_double[d], rest day u_rest[d], brick indicator b_brick[d]
        w_double: Dict[int, int] = {}
        u_rest: Dict[int, int] = {}
        b_brick: Dict[int, int] = {}
        for d in self.days:
            w_double[d] = add_var(0.0, 1.0, self.cfg.w_double_session, is_integer=True)
            u_rest[d] = add_var(0.0, 1.0, 0.0, is_integer=True)
            b_brick[d] = add_var(0.0, 1.0, 0.0, is_integer=True)

        # Tier-2 goal-programming deviations
        tss_scale = self.cfg.w_tss / max(self.cfg.target_weekly_tss, 1.0)
        e_tss_pos = add_var(0.0, inf, tss_scale, is_integer=False)
        e_tss_neg = add_var(0.0, inf, tss_scale, is_integer=False)

        zone_scale = self.cfg.w_zone / max(self.cfg.nominal_weekly_duration, 1.0)
        e_zone_pos: Dict[int, int] = {}
        e_zone_neg: Dict[int, int] = {}
        for z in self.zones:
            e_zone_pos[z] = add_var(0.0, inf, zone_scale, is_integer=False)
            e_zone_neg[z] = add_var(0.0, inf, zone_scale, is_integer=False)

        sdur_scale = self.cfg.w_sport_dur / max(self.cfg.nominal_weekly_duration, 1.0)
        e_sdur_pos: Dict[str, int] = {}
        e_sdur_neg: Dict[str, int] = {}
        for s in self.sports:
            e_sdur_pos[s] = add_var(0.0, inf, sdur_scale, is_integer=False)
            e_sdur_neg[s] = add_var(0.0, inf, sdur_scale, is_integer=False)

        stss_scale = self.cfg.w_sport_tss / max(self.cfg.target_weekly_tss, 1.0)
        e_stss_pos: Dict[str, int] = {}
        e_stss_neg: Dict[str, int] = {}
        for s in ["Bike", "Run"]:
            e_stss_pos[s] = add_var(0.0, inf, stss_scale, is_integer=False)
            e_stss_neg[s] = add_var(0.0, inf, stss_scale, is_integer=False)

        hooks = ModelHooks(
            x=lambda i, d: x_vars[(i, d)],
            add_row=add_row,
            add_var=lambda lb, ub, integer: add_var(lb, ub, 0.0, integer),
            allowed=self._allowed,
        )

        # ---------------------------------------------------------------------
        # 2. TIER 0 — STRUCTURAL & ORTHOPEDIC HARD CONSTRAINTS
        # ---------------------------------------------------------------------
        # Workout library uniqueness (<= 1 per week; anchors handled in Tier 1)
        for i in range(self.num_workouts):
            add_row({x_vars[(i, d)]: 1.0 for d in self.days}, 0.0, 1.0)

        # Daily duration ceilings
        for d in self.days:
            add_row({x_vars[(i, d)]: self.workouts[i].duration for i in range(self.num_workouts)},
                    0.0, self.cfg.daily_time_caps[d])

        # Auxiliary linking, session partition (<= 2 sessions/day; weekend brick may add a third leg)
        for d in self.days:
            for s in self.sports:
                s_map = {x_vars[(i, d)]: 1.0 for i, w in enumerate(self.workouts) if w.sport == s}
                s_map[y_vars[(s, d)]] = -1.0
                add_row(s_map, 0.0, 0.0)

            part_map = {x_vars[(i, d)]: 1.0 for i in range(self.num_workouts)}
            part_map[u_rest[d]] = 1.0
            part_map[w_double[d]] = -1.0
            if d in self.weekends:
                part_map[b_brick[d]] = -1.0
            add_row(part_map, 1.0, 1.0)
            add_row({u_rest[d]: 1.0, w_double[d]: 1.0}, 0.0, 1.0)

            add_row({y_vars[("Bike", d)]: 1.0, y_vars[("Run", d)]: 1.0, b_brick[d]: -1.0}, -inf, 1.0)
            add_row({b_brick[d]: 1.0, y_vars[("Bike", d)]: -1.0}, -inf, 0.0)
            add_row({b_brick[d]: 1.0, y_vars[("Run", d)]: -1.0}, -inf, 0.0)

        S = self.sets
        for d in self.days:
            # At most 1 hard endurance workout per day
            add_row({x_vars[(i, d)]: 1.0 for i, w in enumerate(self.workouts) if w.is_hard_endurance}, 0.0, 1.0)
            # Heavy strength (any heavy lower lift) never shares a day with a threshold/long run
            add_row({x_vars[(i, d)]: 1.0 for i in S.heavy_strength + S.heavy_or_long_runs}, 0.0, 1.0)
        for d in range(6):
            # Conservative +-24h buffer between ANY heavy strength and threshold/long runs (superset of HEUR-004)
            r = {x_vars[(i, d)]: 1.0 for i in S.heavy_strength}
            r.update({x_vars[(j, d + 1)]: 1.0 for j in S.heavy_or_long_runs})
            add_row(r, 0.0, 1.0)
            r = {x_vars[(i, d)]: 1.0 for i in S.heavy_or_long_runs}
            r.update({x_vars[(j, d + 1)]: 1.0 for j in S.heavy_strength})
            add_row(r, 0.0, 1.0)

        # Shared Tier-0 safety layer: gating, 48h caps, axial/neuro, hard/easy, HEUR-004, antagonism, run share
        add_tier0_safety(hooks, self.workouts, self.limits, S)

        # Weekly running mechanical strain ceiling (ACWR & ODI guardrail)
        add_row({x_vars[(i, d)]: self.workouts[i].mechanical_strain for i in S.runs for d in self.days},
                0.0, self.cfg.max_weekly_run_mech_strain)

        # ---------------------------------------------------------------------
        # 3. TIER 1 — NAMED, REPORTED SLACKS (lexicographic by relaxation order)
        # ---------------------------------------------------------------------
        reg = Tier1Registry(hooks, getattr(self.cfg, "tier1_relaxation_order", None))
        add_tier1_targets(
            reg, self.workouts, self.cfg, S,
            x=lambda i, d: x_vars[(i, d)],
            sport_day=lambda s, d: y_vars[(s, d)] if s in self.sports else None,
            rest_day=lambda d: u_rest[d],
        )

        # ---------------------------------------------------------------------
        # 4. TIER 2 — GOAL PROGRAMMING
        # ---------------------------------------------------------------------
        tss_goal_map = {x_vars[(i, d)]: self.workouts[i].aerobic_tss for i in range(self.num_workouts) for d in self.days}
        tss_goal_map[e_tss_neg] = 1.0
        tss_goal_map[e_tss_pos] = -1.0
        add_row(tss_goal_map, self.cfg.target_weekly_tss, self.cfg.target_weekly_tss)

        for z in self.zones:
            p_z = self.cfg.target_zone_pcts.get(z, 0.0)
            z_map: Dict[int, float] = {}
            for i, w in enumerate(self.workouts):
                net_z_minutes = w.zone_durations[z - 1] - p_z * w.duration
                for d in self.days:
                    z_map[x_vars[(i, d)]] = net_z_minutes
            z_map[e_zone_neg[z]] = 1.0
            z_map[e_zone_pos[z]] = -1.0
            add_row(z_map, 0.0, 0.0)

        for s in self.sports:
            r_s = self.cfg.target_sport_duration_splits.get(s, 0.0)
            sd_map: Dict[int, float] = {}
            for i, w in enumerate(self.workouts):
                indicator = 1.0 if w.sport == s else 0.0
                net_s_dur = (indicator - r_s) * w.duration
                for d in self.days:
                    sd_map[x_vars[(i, d)]] = net_s_dur
            sd_map[e_sdur_neg[s]] = 1.0
            sd_map[e_sdur_pos[s]] = -1.0
            add_row(sd_map, 0.0, 0.0)

        for s in ["Bike", "Run"]:
            r_tss = self.cfg.target_bike_run_tss_splits.get(s, 0.5)
            st_map: Dict[int, float] = {}
            for i, w in enumerate(self.workouts):
                if w.sport in ("Bike", "Run"):
                    indicator = 1.0 if w.sport == s else 0.0
                    net_s_tss = (indicator - r_tss) * w.aerobic_tss
                    for d in self.days:
                        st_map[x_vars[(i, d)]] = net_s_tss
            st_map[e_stss_neg[s]] = 1.0
            st_map[e_stss_pos[s]] = -1.0
            add_row(st_map, 0.0, 0.0)

        # ---------------------------------------------------------------------
        # 5. LEXICOGRAPHIC SOLVE WITH CANONICAL NO-GOOD INTEGER CUTS
        # ---------------------------------------------------------------------
        h.setOptionValue("mip_rel_gap", 0.01)
        h.setOptionValue("mip_heuristic_effort", 0.20)
        apply_threads(h)

        plans: List[Dict[str, Any]] = []
        lex = LexState()

        for k in range(k_plans):
            res = lexicographic_solve(h, reg.costs(), lex)
            if k == 0:
                self.last_status = res.status
            if not res.has_solution:
                if k == 0:
                    if res.status == "INFEASIBLE" and not diagnostics:
                        self.last_diagnostics = diagnostics + [
                            "Tier-0 safety caps cannot all be satisfied together (no single obvious cause found pre-solve; "
                            "check 48h TSS/ANS/strain, axial/neuro caps and day caps against the library)."]
                    logger.warning("TriathlonMILPScheduler: no plan (%s). %s", res.status, " | ".join(self.last_diagnostics))
                break

            col_vals = res.col_value
            active_cols: List[int] = []
            schedule_by_day: Dict[int, List[CandidateWorkout]] = {d: [] for d in self.days}
            for (i, d), col_idx in x_vars.items():
                if col_vals[col_idx] > 0.5:
                    active_cols.append(col_idx)
                    schedule_by_day[d].append(self.workouts[i])

            total_tss = sum(w.aerobic_tss for d in self.days for w in schedule_by_day[d])
            total_mech = sum(w.mechanical_strain for d in self.days for w in schedule_by_day[d])
            total_dur = sum(w.duration for d in self.days for w in schedule_by_day[d])
            total_ans = sum(w.ans_cost for d in self.days for w in schedule_by_day[d])
            run_share = run_share_report(schedule_by_day, self.limits)

            plans.append({
                "rank": k + 1,
                "status": res.status,
                "mip_gap": res.mip_gap,
                "objective": res.objective,
                "tier1_penalty": lex.tier1_penalty,
                "relaxations": reg.report(col_vals),
                "diagnostics": list(diagnostics),
                "run_share": run_share,
                "total_tss": total_tss,
                "total_mech_strain": total_mech,
                "total_duration": total_dur,
                "total_ans_cost": total_ans,
                "max_single_run_ratio": run_share["max_single_run_share"],
                "schedule": schedule_by_day,
                "soft_slack_violations": {},  # Tier-0 caps are hard since TASK-016; kept for compatibility
            })

            # Sparse Canonical No-Good Integer Cut: sum_{col in Active} x_col <= |Active| - 1
            add_row({col: 1.0 for col in active_cols}, -inf, float(len(active_cols) - 1))

        return plans


