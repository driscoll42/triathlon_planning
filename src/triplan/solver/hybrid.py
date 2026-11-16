"""Dual-objective hybrid athlete MILP optimizer (HAOS)."""

from typing import Dict, List, Optional, Any
import numpy as np
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

logger = logging.getLogger(__name__)


class HybridAthleteMILPOptimizer:
    """
    Dual-Objective Hybrid Athlete Optimizer (HAOS) - Production MILP Scheduler.
    Solver Engine: HiGHS (highspy).

    Solves the rolling 7-day multi-sport triathlon (Swim, Bike, Run) and maximal
    barbell/dumbbell strength scheduling problem with exact ratio linearizations,
    biomechanical/axial conflict exclusion rules, and Canonical No-Good Integer Cuts.
    Supports both Upper/Lower and Full-Body Texas/HLM strength split architectures.
    """

    def __init__(
        self,
        library: List[Any],
        config: Any
    ):
        self.library = library
        self.N = len(library)
        self.config = config
        self.days = list(range(7))
        self.day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.sports = ["Swim", "Bike", "Run", "Strength"]
        self.muscles = ["Posterior_Chain", "Anterior_Chain", "Push", "Pull"]
        self.zones = [1, 2, 3, 4, 5]
        self.limits = SafetyLimits.from_config(self.config)
        self.sets = classify_workouts(self.library)
        self.last_diagnostics: List[str] = []
        self.last_status: Optional[str] = None

    def _get_cfg_val(self, key: str, default: Any) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _allowed(self, i: int, d: int) -> bool:
        """Unary Tier-0 gating: daily time cap, pool days, outdoor-ride days, optional facility access."""
        w = self.library[i]
        caps = self._get_cfg_val("daily_duration_caps", self._get_cfg_val("daily_time_caps", [120, 120, 75, 120, 120, 220, 180]))
        if float(getattr(w, "duration", 0.0)) > float(caps[d]) + 1e-9:
            return False
        flg = getattr(w, "flags", {}) or {}
        if flg.get("pool", 0) == 1 and d not in set(self._get_cfg_val("pool_open_days", [0, 2, 4])):
            return False
        if flg.get("outdoor_bike", 0) == 1 and d not in set(self._get_cfg_val("outdoor_bike_days", [5, 6])):
            return False
        access = self._get_cfg_val("facility_access", None)
        if access and not access.get(w.sport, [True] * 7)[d]:
            return False
        return True

    def explain_feasibility(self) -> List[str]:
        return explain_feasibility(self.library, self.config, self.limits, self._allowed, self.sets)

    def _get_zone_minutes(self, w: Any) -> Dict[int, float]:
        zm = getattr(w, "zone_minutes", None)
        if zm:
            return zm
        zd = getattr(w, "zone_durations", None)
        if zd and len(zd) >= 5:
            return {z: float(zd[z - 1]) for z in range(1, 6)}
        dur = float(getattr(w, "duration", 60.0))
        return {1: dur * 0.35, 2: dur * 0.45, 3: dur * 0.15, 4: dur * 0.05, 5: 0.0}

    def solve_top_k_plans(self, k_solutions: int = 2, time_limit: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Builds the MILP in HiGHS and iteratively applies Canonical No-Good
        Integer Cuts and structural split cuts to extract the Top-K optimal plans.
        """
        idx = 0
        x_idx = {(i, d): idx + i * 7 + d for i in range(self.N) for d in self.days}
        idx += self.N * 7

        y_idx = {(s, d): idx + s_i * 7 + d for s_i, s in enumerate(self.sports) for d in self.days}
        idx += len(self.sports) * 7

        u_idx = {(m, d): idx + m_i * 7 + d for m_i, m in enumerate(self.muscles) for d in self.days}
        idx += len(self.muscles) * 7

        w_idx = {d: idx + d for d in self.days}; idx += 7
        r_idx = {d: idx + d for d in self.days}; idx += 7
        b_idx = {d: idx + d for d in self.days}; idx += 7
        z_split = idx; idx += 1
        num_binary_vars = idx

        # Continuous Goal Programming Slack/Surplus Variables (>= 0)
        e_tss_pos = {s: idx + i for i, s in enumerate(self.sports)}; idx += 4
        e_tss_neg = {s: idx + i for i, s in enumerate(self.sports)}; idx += 4
        e_sets_pos = {m: idx + i for i, m in enumerate(self.muscles)}; idx += 4
        e_sets_neg = {m: idx + i for i, m in enumerate(self.muscles)}; idx += 4
        e_tiz_low_neg = idx; idx += 1
        e_tiz_hi_pos = idx; idx += 1
        total_vars = idx

        col_cost = np.zeros(total_vars, dtype=np.float64)
        col_lower = np.zeros(total_vars, dtype=np.float64)
        col_upper = np.ones(total_vars, dtype=np.float64)
        col_upper[num_binary_vars:] = 1e4

        weights = self._get_cfg_val("weights", {
            "w_tss_neg": 150.0, "w_tss_pos": 100.0,
            "w_sets_neg": 150.0, "w_sets_pos": 100.0,
            "w_tiz": 1.0, "w_ans": 0.20, "w_durability": 0.35,
            "w_double": 5.0, "w_rest": 18.0, "w_brick": 4.0,
        })
        target_tss = self._get_cfg_val("target_tss", self._get_cfg_val("target_sport_tss", {
            "Swim": 100.0, "Bike": 260.0, "Run": 198.0, "Strength": 52.0
        }))
        target_sets = self._get_cfg_val("target_sets", self._get_cfg_val("target_movement_sets", {
            "Anterior_Chain": 10.0, "Posterior_Chain": 10.0, "Push": 10.0, "Pull": 10.0
        }))

        for i, w in enumerate(self.library):
            ans = float(getattr(w, "ans_cost", 20.0))
            durab = float(getattr(w, "durability_score", 2.0))
            for d in self.days:
                col_cost[x_idx[(i, d)]] = (
                    weights.get("w_ans", 0.20) * ans
                    - weights.get("w_durability", 0.35) * durab
                )

        for d in self.days:
            col_cost[w_idx[d]] = weights.get("w_double", 5.0)
            col_cost[r_idx[d]] = -weights.get("w_rest", 18.0)
            col_cost[b_idx[d]] = -weights.get("w_brick", 4.0)

        for s in self.sports:
            t_s = float(target_tss.get(s, 100.0))
            col_cost[e_tss_pos[s]] = weights.get("w_tss_pos", 100.0) / max(1.0, t_s)
            col_cost[e_tss_neg[s]] = weights.get("w_tss_neg", 150.0) / max(1.0, t_s)

        for m in self.muscles:
            t_m = float(target_sets.get(m, 10.0))
            col_cost[e_sets_pos[m]] = weights.get("w_sets_pos", 100.0) / max(1.0, t_m)
            col_cost[e_sets_neg[m]] = weights.get("w_sets_neg", 150.0) / max(1.0, t_m)

        col_cost[e_tiz_low_neg] = weights.get("w_tiz", 1.0)
        col_cost[e_tiz_hi_pos] = weights.get("w_tiz", 1.0)

        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        if time_limit is not None and time_limit > 0:
            h.setOptionValue("time_limit", float(time_limit))
        inf = highspy.kHighsInf

        h.addCols(
            total_vars,
            col_cost,
            col_lower,
            col_upper,
            0,
            np.array([0] * total_vars, dtype=np.int32),
            np.array([], dtype=np.int32),
            np.array([], dtype=np.float64),
        )

        integrality = np.array(
            [highspy.HighsVarType.kInteger] * num_binary_vars
            + [highspy.HighsVarType.kContinuous] * (total_vars - num_binary_vars)
        )
        h.changeColsIntegrality(
            total_vars,
            np.arange(total_vars, dtype=np.int32),
            integrality,
        )

        def add_sparse_row(coeff_map: Dict[int, float], lb_val: float, ub_val: float):
            nz_cols = [c for c, v in coeff_map.items() if abs(v) > 1e-9]
            nz_vals = [float(coeff_map[c]) for c in nz_cols]
            h.addRow(
                float(lb_val),
                float(ub_val),
                len(nz_cols),
                np.array(nz_cols, dtype=np.int32),
                np.array(nz_vals, dtype=np.float64),
            )

        # C1: Template uniqueness
        for i in range(self.N):
            add_sparse_row({x_idx[(i, d)]: 1.0 for d in self.days}, 0.0, 1.0)

        # C2: Coherent Strength Split Architecture (z_split=1 -> UL, z_split=0 -> HLM)
        for i, w in enumerate(self.library):
            grp = getattr(w, "split_group", "ANY")
            if grp == "UL":
                row = {x_idx[(i, d)]: 1.0 for d in self.days}
                row[z_split] = -1.0
                add_sparse_row(row, -inf, 0.0)
            elif grp == "HLM":
                row = {x_idx[(i, d)]: 1.0 for d in self.days}
                row[z_split] = 1.0
                add_sparse_row(row, 0.0, 1.0)

        # C3: Daily Duration Ceilings, Max 2 Workouts/Day, Max 1 Workout/Sport/Day
        daily_caps = self._get_cfg_val("daily_duration_caps", self._get_cfg_val("daily_time_caps", [120, 120, 75, 120, 120, 220, 180]))

        for d in self.days:
            add_sparse_row(
                {x_idx[(i, d)]: float(getattr(self.library[i], "duration", 60.0)) for i in range(self.N)},
                0.0,
                float(daily_caps[d]),
            )
            add_sparse_row({x_idx[(i, d)]: 1.0 for i in range(self.N)}, 0.0, 2.0)

            for s in self.sports:
                add_sparse_row(
                    {x_idx[(i, d)]: 1.0 for i in range(self.N) if getattr(self.library[i], "sport", "") == s},
                    0.0,
                    1.0,
                )

        # C5: Auxiliary Variable Linking (y_{s,d}, u_{m,d}, w_d, r_d, b_d)
        for d in self.days:
            row_w = {x_idx[(i, d)]: 1.0 for i in range(self.N)}
            row_w[w_idx[d]] = -1.0
            add_sparse_row(row_w, -inf, 1.0)

            for i in range(self.N):
                add_sparse_row({r_idx[d]: 1.0, x_idx[(i, d)]: 1.0}, 0.0, 1.0)

            for s in self.sports:
                s_ids = [i for i in range(self.N) if getattr(self.library[i], "sport", "") == s]
                for i in s_ids:
                    add_sparse_row({x_idx[(i, d)]: 1.0, y_idx[(s, d)]: -1.0}, -inf, 0.0)
                row_ys = {x_idx[(i, d)]: -1.0 for i in s_ids}
                row_ys[y_idx[(s, d)]] = 1.0
                add_sparse_row(row_ys, -inf, 0.0)

            for m in self.muscles:
                m_ids = [i for i in range(self.N) if (getattr(self.library[i], "sets_by_muscle", {}) or {}).get(m, 0) > 0]
                for i in m_ids:
                    add_sparse_row({x_idx[(i, d)]: 1.0, u_idx[(m, d)]: -1.0}, -inf, 0.0)

            add_sparse_row({b_idx[d]: 1.0, y_idx[("Bike", d)]: -1.0}, -inf, 0.0)
            add_sparse_row({b_idx[d]: 1.0, y_idx[("Run", d)]: -1.0}, -inf, 0.0)

        # Rest day is exact: r_d = 1 iff no workout on day d (r_d + x_id <= 1 above; here r_d + sum_i x_id >= 1)
        for d in self.days:
            row_rest = {x_idx[(i, d)]: 1.0 for i in range(self.N)}
            row_rest[r_idx[d]] = 1.0
            add_sparse_row(row_rest, 1.0, inf)

        str_ids = [i for i, w in enumerate(self.library) if getattr(w, "sport", "") == "Strength"]

        # C6/C7a: Shared Tier-0 safety layer (TASK-017): unary gating, 48h TSS/ANS/strain, daily+weekly
        # axial & neuromuscular caps, hard/easy adjacency, 48h heavy-run separation, deadlift +-24h vs
        # threshold AND long runs, antagonism matrix, non-consecutive strength, single-run share on ALL runs.
        def hy_add_var(lb: float, ub: float, integer: bool) -> int:
            h.addVar(float(lb), float(ub))
            col = h.getNumCol() - 1
            if integer:
                h.changeColIntegrality(col, highspy.HighsVarType.kInteger)
            return col

        hooks = ModelHooks(x=lambda i, d: x_idx[(i, d)], add_row=add_sparse_row, add_var=hy_add_var,
                           allowed=self._allowed)
        add_tier0_safety(hooks, self.library, self.limits, self.sets)

        # Tier-1: sport minimum frequencies and rest days (named, reported slacks)
        reg = Tier1Registry(hooks, self._get_cfg_val("tier1_relaxation_order", None))
        add_tier1_targets(reg, self.library, self.config, self.sets, x=lambda i, d: x_idx[(i, d)],
                          sport_day=lambda s_, d: y_idx.get((s_, d)), rest_day=lambda d: r_idx[d],
                          rest_bounds_default=(1, 2))  # original hybrid rest-day window

        # 7b: Barbell Main Lifts Share (>= 60% of total weekly strength sets)
        beta_bb = float(self._get_cfg_val("min_barbell_set_share", 0.60))
        row_bb = {}
        for i in str_ids:
            w = self.library[i]
            bb_s = int(getattr(w, "bb_sets", 0))
            db_s = int(getattr(w, "db_sets", 0))
            tot_w_sets = bb_s + db_s
            coeff = float(bb_s) - beta_bb * float(tot_w_sets)
            for d in self.days:
                row_bb[x_idx[(i, d)]] = coeff
        if row_bb:
            add_sparse_row(row_bb, 0.0, inf)

        # 7c: Polarized / Pyramidal Time-in-Zone Distribution (Z1+Z2 >= 78%, Z4+Z5 <= 20%)
        endur_ids = [i for i, w in enumerate(self.library) if getattr(w, "sport", "") in ["Swim", "Bike", "Run"]]
        p_low_min = float(self._get_cfg_val("p_low_min", 0.78))
        p_high_max = float(self._get_cfg_val("p_high_max", 0.20))
        row_tiz_low = {e_tiz_low_neg: 1.0}
        row_tiz_hi = {e_tiz_hi_pos: -1.0}
        for i in endur_ids:
            w = self.library[i]
            zm = self._get_zone_minutes(w)
            z12 = zm.get(1, 0.0) + zm.get(2, 0.0)
            z45 = zm.get(4, 0.0) + zm.get(5, 0.0)
            tot_z = sum(zm.values())
            for d in self.days:
                row_tiz_low[x_idx[(i, d)]] = z12 - p_low_min * tot_z
                row_tiz_hi[x_idx[(i, d)]] = z45 - p_high_max * tot_z
        add_sparse_row(row_tiz_low, 0.0, inf)
        add_sparse_row(row_tiz_hi, -inf, 0.0)

        # C8: Goal Programming Target Equations
        for s in self.sports:
            row_tss = {e_tss_neg[s]: 1.0, e_tss_pos[s]: -1.0}
            t_s = float(target_tss.get(s, 100.0))
            for i in [idx_w for idx_w, w in enumerate(self.library) if getattr(w, "sport", "") == s]:
                for d in self.days:
                    row_tss[x_idx[(i, d)]] = float(getattr(self.library[i], "aerobic_tss", 0.0))
            add_sparse_row(row_tss, t_s, t_s)

        for m in self.muscles:
            row_m = {e_sets_neg[m]: 1.0, e_sets_pos[m]: -1.0}
            t_m = float(target_sets.get(m, 10.0))
            for i, w in enumerate(self.library):
                sets_m = (getattr(w, "sets_by_muscle", {}) or {}).get(m, 0)
                if sets_m > 0:
                    for d in self.days:
                        row_m[x_idx[(i, d)]] = float(sets_m)
            add_sparse_row(row_m, t_m, t_m)

        apply_threads(h)
        h.setOptionValue("mip_rel_gap", 0.01)
        diagnostics = self.explain_feasibility()
        self.last_diagnostics = diagnostics
        lex = LexState()
        solutions = []
        for k in range(k_solutions):
            res = lexicographic_solve(h, reg.costs(), lex)
            if k == 0:
                self.last_status = res.status
            if not res.has_solution:
                if k == 0:
                    logger.warning("HybridAthleteMILPOptimizer: no plan (%s). %s", res.status, " | ".join(diagnostics))
                break

            sol = res.col_value

            selected_pairs = []
            schedule_by_day: Dict[int, List[Any]] = {d: [] for d in self.days}
            for i in range(self.N):
                for d in self.days:
                    if sol[x_idx[(i, d)]] > 0.5:
                        selected_pairs.append((i, d))
                        schedule_by_day[d].append(self.library[i])

            split_val = round(sol[z_split])
            split_label = (
                "Upper/Lower Barbell + DB Split"
                if split_val == 1
                else "Full-Body HLM / Texas Method Split"
            )

            total_tss_solved = sum(float(getattr(w, "aerobic_tss", 0.0)) for d in self.days for w in schedule_by_day[d])
            total_axial_solved = sum(float(getattr(w, "axial_load", 0.0)) for d in self.days for w in schedule_by_day[d])
            total_dur_solved = sum(float(getattr(w, "duration", 0.0)) for d in self.days for w in schedule_by_day[d])

            solutions.append({
                "option": chr(65 + k),
                "rank": k + 1,
                "split_architecture": split_label,
                "status": res.status,
                "mip_gap": res.mip_gap,
                "objective_value": res.objective,
                "tier1_penalty": lex.tier1_penalty,
                "relaxations": reg.report(sol),
                "diagnostics": list(diagnostics),
                "run_share": run_share_report(schedule_by_day, self.limits),
                "total_tss": total_tss_solved,
                "total_axial_load": total_axial_solved,
                "total_duration": total_dur_solved,
                "schedule": schedule_by_day,
                "selected_pairs": selected_pairs,
            })

            # Append Canonical No-Good Integer Cut
            cut_row = {
                x_idx[(i, d)]: (1.0 if (i, d) in selected_pairs else -1.0)
                for i in range(self.N)
                for d in self.days
            }
            add_sparse_row(cut_row, -inf, float(len(selected_pairs) - 1))
            add_sparse_row({z_split: 1.0}, float(1 - split_val), float(1 - split_val))

        return solutions


