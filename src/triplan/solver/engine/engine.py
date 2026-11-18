"""``UnifiedWeekEngine``: one HiGHS MILP engine for the training week (TASK-015, ADR-017).

Pipeline: normalize inputs (``WorkoutTable`` / ``WeekConfig``) -> create columns (calendar) -> declare
goal deviations -> run the constraint builders in a fixed order -> bulk-build HiGHS (one ``addCols``,
one ``addRows``) -> lexicographic solve (Tier-1 slack first, then Tier-2) -> Top-K extraction with
no-good cuts -> plan dictionaries.

The three historical engines are option presets (see ``engine.config`` for every option):

* ``meso_options()``   -> ``TriathlonMILPScheduler``  (partition + brick, meso goals, ``selection`` cuts)
* ``hybrid_options()`` -> ``HybridAthleteMILPOptimizer`` (<=2/day, <=1/sport/day, split architecture,
  barbell share, TiZ band, hybrid goals, ``selection`` cuts)
* ``legacy_options()`` -> ``optimize_weekly_schedule`` (one workout/day, exact count, day scheduler
  goals, single solve)

Builder seams: TASK-018 (ADR-019) added ``builders.load_dynamics`` (carry-in constants, in-model running ACWR, CTL
ramp, run-minutes guard; ``carry_in=`` constructor argument); TASK-019 (session structure, 80/20 TiZ) extends
``intensity_structure``. TASK-020 (ADR-018) added selection/layout cuts (``_add_cut``), dominance
pruning before ``WorkoutTable`` construction and the week-reversal symmetry row (``builders.symmetry``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import highspy
import numpy as np

from triplan.library.compound import make_lift_easy_combos
from triplan.solver.engine import session_quality as sq
from triplan.solver.engine.builders import (calendar, goals, intensity_structure, load_dynamics, orthopedic,
                                            session_structure, strength, symmetry)
from triplan.solver.engine.config import EngineOptions, WEEKENDS, WeekConfig, WorkoutTable
from triplan.solver.engine.context import BuildContext
from triplan.solver.engine.model import INF, WeekModel, add_row_to_highs
from triplan.solver.highs_runtime import apply_threads
from triplan.solver.pruning import (DominanceReport, filter_feasible_candidates, prune_8020_candidates,
                                  prune_dominated_candidates)
from triplan.solver.safety_constraints import (
    LexState,
    SafetyLimits,
    classify_workouts,
    explain_feasibility,
    lexicographic_solve,
    resolve_long_run_cap,
    run_share_report,
)

logger = logging.getLogger(__name__)

_LOG_NAME = {"meso": "TriathlonMILPScheduler", "hybrid": "HybridAthleteMILPOptimizer", "raw": "UnifiedWeekEngine"}


class UnifiedWeekEngine:
    """Build and solve one training week for any option preset."""

    def __init__(self, workouts: Sequence[Any], config: Any, options: EngineOptions,
                 notes: Optional[List[str]] = None, carry_in: Optional[Any] = None) -> None:
        self.config = config
        self.carry_in = carry_in           # WeekCarryIn (TASK-018): previous-week constants; None = isolated week
        self.opt = options
        self.dominance: Optional[DominanceReport] = None
        workouts = list(workouts)
        self.cfg = WeekConfig(config)
        # TASK-019 pre-filtering invariant: grey-zone drop -> q_i hard classification -> lossless feasibility filter ->
        # stratified 80/20 pool pruning -> DECISION-021 combos -> dominance pruning, all before WorkoutTable / model.
        self.quality_params = sq.SessionQualityParams.from_config(self.cfg)
        self.grey_zone_dropped: List[str] = []
        if options.prefilter_grey_zone:
            workouts, self.grey_zone_dropped = sq.prefilter_grey_zone(workouts, self.quality_params)
        if options.quality_classification:
            workouts = sq.reclassify_hard(workouts, self.quality_params)
        self.limits = SafetyLimits.from_config(config)
        self.limits.long_run_abs_cap_min = resolve_long_run_cap(self.limits, carry_in)   # ADR-022
        self._diag_workouts = list(workouts)
        self._diag_sets = classify_workouts(self._diag_workouts)
        self._diag_allowed = calendar.make_allowed(WorkoutTable(self._diag_workouts), self.cfg, options)
        if options.safety or options.allowed_rule != "none":
            workouts = filter_feasible_candidates(workouts, self._diag_allowed, self.limits)
        if options.prune_8020_pool:
            workouts = prune_8020_candidates(workouts, options.max_8020_per_bucket)
        self.combo_codes: List[str] = []
        if options.lift_easy_combo and bool(self.cfg.get("allow_lift_cardio_combo", default=True)):
            combos = make_lift_easy_combos(
                workouts,
                max_minutes=float(self.cfg.get("combo_cardio_max_minutes", default=20.0)),
                sports_after_heavy_lower=list(self.cfg.get("combo_cardio_sports_after_heavy_lower", default=["Bike"])),
                sports_after_other_lift=list(self.cfg.get("combo_cardio_sports_after_other_lift",
                                                          default=["Bike", "Run"])))
            self.combo_codes = [c.code for c in combos]
            workouts = workouts + combos
        if options.prune_dominated:
            workouts, self.dominance = prune_dominated_candidates(
                workouts, WeekConfig(config).get("sport_freq_bounds", default=None) if options.tier1 else None)
        self.workouts = workouts
        self.table = WorkoutTable(self.workouts)
        self.key = sq.key_mask(self.workouts, self.quality_params)
        self.sets = classify_workouts(self.workouts)
        self.allowed = calendar.make_allowed(self.table, self.cfg, options)
        self.notes: List[str] = list(notes or [])
        self.last_diagnostics: List[str] = []
        self.last_status: Optional[str] = None
        self.last_context: Optional[BuildContext] = None
        self.symmetry_row = False

    # ------------------------------------------------------------------ feasibility explainer
    def explain_feasibility(self) -> List[str]:
        if not self.opt.safety:
            return list(self.notes)
        return self.notes + explain_feasibility(
            self._diag_workouts, self.config, self.limits, self._diag_allowed, self._diag_sets)

    # ------------------------------------------------------------------ build
    def build_model(self) -> BuildContext:
        """Run every builder into a fresh ``WeekModel`` (nothing is sent to HiGHS yet)."""
        opt = self.opt
        model = WeekModel(empty_row_policy=opt.empty_row_policy)
        ctx = BuildContext(model=model, table=self.table, cfg=self.cfg, opt=opt, limits=self.limits,
                           sets=self.sets, allowed=self.allowed, key=self.key, carry_in=self.carry_in)
        calendar.add_variables(ctx, goals.cost_terms(ctx))
        goals.declare_goals(ctx)
        if opt.safety or opt.tier1:
            orthopedic.make_hooks(ctx)

        calendar.add_uniqueness(ctx)
        if opt.session_model == "partition_brick":
            calendar.add_duration_caps(ctx)
            calendar.add_session_partition(ctx)
            if opt.meso_adjacency_rows:
                orthopedic.add_meso_adjacency(ctx)
        elif opt.session_model == "per_sport_cap":
            if opt.split_coherence:
                strength.add_split_coherence(ctx)
            calendar.add_day_counts(ctx)
            calendar.add_session_links(ctx)
            strength.add_muscle_links(ctx)
        elif opt.session_model == "single_per_day":
            calendar.add_day_counts(ctx)
            calendar.add_exact_count(ctx, opt.legacy.num_workouts)
            calendar.add_duration_caps(ctx, skip_zero_cap=True)
        else:
            raise ValueError(f"unknown session_model {opt.session_model!r}")

        if opt.safety:
            orthopedic.add_safety(ctx)
        if opt.weekly_run_strain_cap:
            orthopedic.add_weekly_run_strain_cap(ctx)
        if opt.tier1:
            orthopedic.add_tier1(ctx)
        load_dynamics.add_carry_in(ctx)       # TASK-018: carry-in constants, in-model ACWR / CTL ramp / run minutes
        if opt.barbell_share:
            strength.add_barbell_share(ctx)
        session_structure.add_session_structure(ctx)
        if opt.tiz_band:
            intensity_structure.add_tiz_band(ctx)
        if opt.canonical_8020:
            intensity_structure.add_canonical_8020(ctx)
        if opt.legacy is not None:
            intensity_structure.add_legacy_structure(ctx)
        goals.add_goal_rows(ctx)
        self.symmetry_row = False
        if opt.symmetry_breaking and opt.cut_mode != "layout":
            self.symmetry_row = symmetry.add_reversal_symmetry(ctx)
        self.last_context = ctx
        return ctx

    def _new_solver(self, ctx: BuildContext, time_limit: Optional[float]) -> "highspy.Highs":
        opt = self.opt
        h = highspy.Highs()
        h.setOptionValue(opt.quiet_option, False)
        if time_limit is not None and time_limit > 0:
            h.setOptionValue("time_limit", float(time_limit))
        h.changeObjectiveSense(highspy.ObjSense.kMinimize)
        ctx.model.build(h)
        spec = opt.legacy
        apply_threads(h, spec.threads if spec else None)
        if spec is not None and spec.random_seed is not None:
            h.setOptionValue("random_seed", int(spec.random_seed))
        if opt.mip_rel_gap is not None:
            h.setOptionValue("mip_rel_gap", float(opt.mip_rel_gap))
        if opt.mip_abs_gap is not None:
            h.setOptionValue("mip_abs_gap", float(opt.mip_abs_gap))
        if opt.mip_heuristic_effort is not None:
            h.setOptionValue("mip_heuristic_effort", float(opt.mip_heuristic_effort))
        return h

    # ------------------------------------------------------------------ solve
    def solve(self, k: int = 1, time_limit: Optional[float] = None) -> List[Dict[str, Any]]:
        """Lexicographic solve and Top-K extraction. Returns ``[]`` when no plan exists; see
        ``last_diagnostics`` for the plain-English reason."""
        opt, tab = self.opt, self.table
        diagnostics = self.explain_feasibility()
        self.last_diagnostics = diagnostics
        ctx = self.build_model()
        h = self._new_solver(ctx, time_limit)
        reg = ctx.reg
        tier1_costs = reg.costs() if reg is not None else {}
        lex = LexState()
        plans: List[Dict[str, Any]] = []
        name = _LOG_NAME[opt.plan_format]

        for rank in range(k):
            res = lexicographic_solve(h, tier1_costs, lex)
            if rank == 0:
                self.last_status = res.status
            if not res.has_solution:
                if rank == 0:
                    if opt.infeasible_hint and res.status == "INFEASIBLE" and not diagnostics:
                        self.last_diagnostics = diagnostics + [
                            "Tier-0 safety caps cannot all be satisfied together (no single obvious cause found pre-solve; "
                            "check 48h TSS/ANS/strain, axial/neuro caps and day caps against the library)."]
                    logger.warning("%s: no plan (%s). %s", name, res.status, " | ".join(self.last_diagnostics))
                break

            col_vals = np.asarray(res.col_value)
            chosen = col_vals[ctx.X] > 0.5                        # (N, 7)
            pairs = [(int(i), int(d)) for i, d in zip(*np.nonzero(chosen))]  # workout-major, like the legacy loops
            schedule: Dict[int, List[Any]] = {d: [] for d in ctx.days}
            for i, d in pairs:
                schedule[d].append(self.workouts[i])
            plans.append(self._format_plan(rank, res, lex, reg, col_vals, ctx, pairs, schedule, diagnostics))
            if opt.cut_mode == "none":
                break
            if not self._add_cut(h, ctx, col_vals, pairs):
                break
        return plans

    # ------------------------------------------------------------------ Top-K cuts (TASK-020, ADR-018)
    def _add_cut(self, h: "highspy.Highs", ctx: BuildContext, col_vals: np.ndarray, pairs) -> bool:
        """Exclude the plan just found. Returns False when no further distinct plan can exist
        (the plan is too small for the requested difference), which ends the Top-K loop."""
        mode, m = self.opt.cut_mode, max(1, int(self.opt.cut_min_diff))
        if mode == "layout":
            # sum_{(i,d) in plan} x[i,d] <= |plan| - 1: any change of the (workout, day) layout is distinct.
            cols = [ctx.x(i, d) for i, d in pairs]
            rhs = len(cols) - 1
        elif mode == "selection":
            # s_i = sum_d x[i,d] (<= 1 by uniqueness); sum_{i in S} s_i <= |S| - m: the next plan must
            # drop at least m of the workouts chosen here, so plans differ in selection, not layout.
            chosen = sorted({i for i, _ in pairs})
            cols = [ctx.x(i, d) for i in chosen for d in ctx.days]
            rhs = len(chosen) - m
        else:
            raise ValueError(f"unknown cut_mode {mode!r}")
        if not cols or rhs < 0:
            return False
        add_row_to_highs(h, {c: 1.0 for c in cols}, -INF, float(rhs))
        return True

    # ------------------------------------------------------------------ AM/PM ordering (TASK-019)
    def _order_schedule(self, schedule, col_vals, ctx: BuildContext) -> Dict[int, List[Dict[str, Any]]]:
        """Reorder every day of ``schedule`` in place into deterministic AM -> PM order and return the slot
        labels (``AM`` / ``PM`` / ``WINDOW``, with the minimum gap between windows)."""
        gap = self.cfg.am_pm_min_gap_h
        slots: Dict[int, List[Dict[str, Any]]] = {}
        for d in ctx.days:
            ordered = session_structure.order_day(schedule[d])
            schedule[d] = ordered
            # Only the scheduler's weekend brick is ONE window (sum x - double - brick == 1); a weekday Bike +
            # Run pair is a double (two windows) even though the ``brick`` indicator column is raised.
            brick = (self.opt.session_model == "partition_brick" and d in WEEKENDS
                     and bool(col_vals[ctx.brick[d]] > 0.5))
            slots[d] = session_structure.day_slots(ordered, gap, brick) if ordered else []
        return slots

    # ------------------------------------------------------------------ plan dictionaries
    def _format_plan(self, rank, res, lex, reg, col_vals, ctx, pairs, schedule, diagnostics) -> Dict[str, Any]:
        tab, fmt = self.table, self.opt.plan_format
        by_day = [(i, d) for d in ctx.days for i, dd in pairs if dd == d]  # day-major, like the legacy sums

        def total(arr) -> float:
            return float(sum(float(arr[i]) for i, _ in by_day))

        base = {"rank": rank + 1, "status": res.status, "mip_gap": res.mip_gap}
        if fmt != "raw":
            base["am_pm"] = self._order_schedule(schedule, col_vals, ctx)
        if fmt == "raw":
            base.update({"objective": res.objective, "col_value": col_vals, "selected_pairs": pairs,
                         "schedule": schedule})
            return base
        relaxations = reg.report(col_vals) if reg is not None else []
        run_share = run_share_report(schedule, self.limits)
        if fmt == "meso":
            base.update({
                "objective": res.objective,
                "tier1_penalty": lex.tier1_penalty,
                "relaxations": relaxations,
                "diagnostics": list(diagnostics),
                "run_share": run_share,
                "total_tss": total(tab.aerobic_tss),
                "total_mech_strain": total(tab.mechanical_strain),
                "total_duration": total(tab.duration),
                "total_ans_cost": total(tab.ans_cost),
                "max_single_run_ratio": run_share["max_single_run_share"],
                "schedule": schedule,
                "soft_slack_violations": {},  # Tier-0 caps are hard since TASK-016; kept for compatibility
            })
            return base
        split_val = round(col_vals[ctx.z_split])
        split_label = ("Upper/Lower Barbell + DB Split" if split_val == 1
                       else "Full-Body HLM / Texas Method Split")
        out = {"option": chr(65 + rank), "rank": rank + 1, "split_architecture": split_label,
               "status": res.status, "mip_gap": res.mip_gap, "objective_value": res.objective,
               "tier1_penalty": lex.tier1_penalty, "relaxations": relaxations,
               "diagnostics": list(diagnostics), "run_share": run_share,
               "total_tss": total(tab.aerobic_tss), "total_axial_load": total(tab.axial_load),
               "total_duration": total(tab.duration), "schedule": schedule, "selected_pairs": pairs,
               "am_pm": base["am_pm"]}
        return out


def solve_week(workouts: Sequence[Any], config: Any, options: EngineOptions, k: int = 1,
               time_limit: Optional[float] = None, notes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Convenience wrapper: build, solve and return plan dictionaries."""
    return UnifiedWeekEngine(workouts, config, options, notes).solve(k, time_limit)
