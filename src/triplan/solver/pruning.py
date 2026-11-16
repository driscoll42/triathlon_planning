"""Input-driven candidate-library pruning (pre-filtering invariant before building the HiGHS matrix)."""

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


from triplan.model.candidates import CandidateWorkout

logger = logging.getLogger(__name__)


def prune_candidate_library(workouts: List[CandidateWorkout], target_tss: float = 500.0, max_per_sport: int = 25) -> List[CandidateWorkout]:
    """
    Input-driven candidate pruning: filters duplicates and selects top representative workouts
    per sport covering low-endurance, high-intensity, strides, anchors, and varying durations.
    Drastically accelerates MILP solving on libraries with 100+ items.
    """
    if len(workouts) <= 40:
        return workouts

    pruned: List[CandidateWorkout] = []
    by_sport: Dict[str, List[CandidateWorkout]] = {}
    for w in workouts:
        by_sport.setdefault(w.sport, []).append(w)

    for s, w_list in by_sport.items():
        must_keep = [w for w in w_list if w.is_anchor or w.has_strides or w.is_heavy_strength]
        remaining = [w for w in w_list if w not in must_keep]
        remaining.sort(key=lambda x: (x.duration, x.aerobic_tss))
        slots_left = max(6, max_per_sport - len(must_keep))
        if len(remaining) > slots_left:
            step = len(remaining) / slots_left
            selected = [remaining[int(i * step)] for i in range(slots_left)]
        else:
            selected = remaining
        pruned.extend(must_keep + selected)

    # Re-index unique workouts
    seen_codes = set()
    unique_pruned = []
    for w in pruned:
        if w.code not in seen_codes:
            seen_codes.add(w.code)
            unique_pruned.append(w)
    return unique_pruned




# =============================================================================
# Dominance pruning (TASK-020, ADR-018)
# =============================================================================
#
# Soundness argument. A workout j may be dropped only if EVERY feasible plan using j maps to a feasible
# plan of no greater objective that uses a kept workout instead. The model has two-sided goals (TSS,
# zone and sport-split targets) and hard caps, so "smaller duration/TSS/ANS/strain" is NOT a safe
# dominance order: a lighter workout can move a goal deviation the wrong way. The only attributes
# that enter the model purely as "less/more is never worse" are the Tier-2 costs
# ``preference_penalty`` (lower is better) and ``durability_score`` (higher is better). So j is
# dominated by i iff they are IDENTICAL in every other attribute (sport, anchor/hard/heavy/strides
# structure, flags, split group, movement-pattern sets, equipment sets, all load and zone
# dimensions) and i is no worse on those two costs. Because each workout can be used once per week
# but identical workouts can fill several slots, we keep up to ``cap`` dominators where ``cap`` is
# the most identical workouts of that sport any plan can legally use (the sport frequency maximum,
# at most one per sport per day for the standard sports). Anchors and required-test workouts are
# never pruned. ``durability_score`` max is preserved (the dominator has >= durability), so the
# max-normalised durability cost is unchanged.

_COST_ONLY_FIELDS = ("preference_penalty", "durability_score")
_IDENTITY_FIELDS = ("id", "code", "name")
_STANDARD_SPORTS = ("Swim", "Bike", "Run", "Strength")


@dataclass
class DominanceReport:
    total: int
    kept: int
    pruned_codes: List[str] = field(default_factory=list)

    @property
    def pruned(self) -> int:
        return self.total - self.kept


def _freeze(v: Any) -> Any:
    if isinstance(v, dict):
        return tuple(sorted((str(k), _freeze(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple, set, frozenset)):
        return tuple(_freeze(x) for x in v)
    return v


def _signature(w: Any) -> Tuple:
    names = ([f.name for f in dataclasses.fields(w)] if dataclasses.is_dataclass(w) else sorted(vars(w)))
    return tuple((n, _freeze(getattr(w, n))) for n in sorted(names)
                 if n not in _COST_ONLY_FIELDS and n not in _IDENTITY_FIELDS)


def _is_protected(w: Any) -> bool:
    flg = getattr(w, "flags", None) or {}
    return bool(getattr(w, "is_anchor", False) or flg.get("test", 0) or flg.get("required_test", 0))


def prune_dominated_candidates(workouts: Sequence[Any], sport_freq_bounds: Optional[Dict[str, Tuple[int, int]]] = None,
                               max_copies_default: int = 7) -> Tuple[List[Any], DominanceReport]:
    """Remove workouts dominated by ``>= cap`` structurally identical workouts (see module notes).

    Returns ``(kept_workouts_in_original_order, report)``. Never prunes protected workouts or sports
    outside Swim/Bike/Run/Strength (their per-day multiplicity is not bounded by a sport link).
    """
    bounds = sport_freq_bounds or {}
    groups: Dict[Tuple, List[int]] = {}
    for idx, w in enumerate(workouts):
        if _is_protected(w) or getattr(w, "sport", "") not in _STANDARD_SPORTS:
            continue
        groups.setdefault((w.sport, _signature(w)), []).append(idx)

    drop: set = set()
    for (sport, _), idxs in groups.items():
        cap = max(1, min(max_copies_default, int(bounds.get(sport, (0, max_copies_default))[1])))
        if len(idxs) <= cap:
            continue
        ranked = sorted(idxs, key=lambda i: (float(getattr(workouts[i], "preference_penalty", 0.1)),
                                             -float(getattr(workouts[i], "durability_score", 2.0)), i))
        # keep the ``cap`` best: lowest penalty, then highest durability. Everything after them has
        # penalty >= and durability <= every kept one only when sorted that way, so check explicitly.
        kept = ranked[:cap]
        worst_pen = max(float(getattr(workouts[i], "preference_penalty", 0.1)) for i in kept)
        best_dur = min(float(getattr(workouts[i], "durability_score", 2.0)) for i in kept)
        for i in ranked[cap:]:
            pen, dur = float(getattr(workouts[i], "preference_penalty", 0.1)), float(getattr(workouts[i], "durability_score", 2.0))
            if pen >= worst_pen and dur <= best_dur:
                drop.add(i)

    kept_list = [w for i, w in enumerate(workouts) if i not in drop]
    report = DominanceReport(total=len(workouts), kept=len(kept_list),
                             pruned_codes=[str(getattr(workouts[i], "code", i)) for i in sorted(drop)])
    if drop:
        logger.info("dominance pruning removed %d of %d workouts: %s", len(drop), len(workouts), report.pruned_codes)
    return kept_list, report
