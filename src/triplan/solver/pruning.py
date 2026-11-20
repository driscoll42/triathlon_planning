"""Input-driven candidate-library pruning (pre-filtering invariant before building the HiGHS matrix)."""

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


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
_IDENTITY_FIELDS = ("id", "code", "name", "provenance")
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
                               max_copies_default: int = 7,
                               extra_cost: Optional[Callable[[Any], float]] = None) -> Tuple[List[Any], DominanceReport]:
    """Remove workouts dominated by ``>= cap`` structurally identical workouts (see module notes).

    ``extra_cost`` (lower is better) is a third cost-only attribute, e.g. the multi-week variety penalty of a code
    (TASK-023): a workout is dropped only if it is no better than every kept one on it as well.

    Returns ``(kept_workouts_in_original_order, report)``. Never prunes protected workouts or sports
    outside Swim/Bike/Run/Strength (their per-day multiplicity is not bounded by a sport link).
    """
    bounds = sport_freq_bounds or {}
    groups: Dict[Tuple, List[int]] = {}
    for idx, w in enumerate(workouts):
        if _is_protected(w) or getattr(w, "sport", "") not in _STANDARD_SPORTS:
            continue
        groups.setdefault((w.sport, _signature(w)), []).append(idx)

    xc = extra_cost or (lambda _w: 0.0)
    drop: set = set()
    for (sport, _), idxs in groups.items():
        cap = max(1, min(max_copies_default, int(bounds.get(sport, (0, max_copies_default))[1])))
        if len(idxs) <= cap:
            continue
        ranked = sorted(
            idxs,
            key=lambda i: (
                float(getattr(workouts[i], "preference_penalty", 0.1)),
                float(xc(workouts[i])),
                -float(getattr(workouts[i], "durability_score", 2.0)),
                getattr(workouts[i], "provenance", "") == "8020_library",
                i,
            ),
        )
        # keep the ``cap`` best: lowest penalty, then highest durability. Everything after them has
        # penalty >= and durability <= every kept one only when sorted that way, so check explicitly.
        kept = ranked[:cap]
        worst_pen = max(float(getattr(workouts[i], "preference_penalty", 0.1)) for i in kept)
        best_dur = min(float(getattr(workouts[i], "durability_score", 2.0)) for i in kept)
        worst_xc = max(float(xc(workouts[i])) for i in kept)
        for i in ranked[cap:]:
            pen, dur = float(getattr(workouts[i], "preference_penalty", 0.1)), float(getattr(workouts[i], "durability_score", 2.0))
            if pen >= worst_pen and dur <= best_dur and float(xc(workouts[i])) >= worst_xc:
                drop.add(i)

    kept_list = [w for i, w in enumerate(workouts) if i not in drop]
    report = DominanceReport(total=len(workouts), kept=len(kept_list),
                             pruned_codes=[str(getattr(workouts[i], "code", i)) for i in sorted(drop)])
    if drop:
        logger.info("dominance pruning removed %d of %d workouts: %s", len(drop), len(workouts), report.pruned_codes)
    return kept_list, report


# =============================================================================
# Athlete-feasible pre-filter & stratified 80/20 pruning
# =============================================================================

DEFAULT_8020_BUCKET_CAPS: Dict[str, int] = {"easy": 6, "tempo": 4, "hi": 4}


def _is_heavy_strength(w: Any) -> bool:
    flg = getattr(w, "flags", None) or {}
    return bool(getattr(w, "is_heavy_strength", False) or flg.get("heavy_dl", 0) == 1 or flg.get("heavy_sq", 0) == 1)


def filter_feasible_candidates(
    workouts: Sequence[Any],
    allowed_fn,
    limits: Any,
    req_heavy_strength: Optional[int] = None,
) -> List[Any]:
    """Losslessly drop non-anchor, non-required-test workouts with zero schedulable days under Tier-0 unary gates."""
    drop_heavy = req_heavy_strength is not None and int(req_heavy_strength) == 0
    kept: List[Any] = []
    for i, w in enumerate(workouts):
        flg = getattr(w, "flags", None) or {}
        if bool(getattr(w, "is_anchor", False) or flg.get("required_test", 0)):
            kept.append(w)
            continue
        if drop_heavy and _is_heavy_strength(w):
            continue
        ax = float(getattr(w, "axial_load", 0.0) or 0.0)
        if ax > limits.max_daily_axial + 1e-9:
            continue
        nu = float(getattr(w, "neuromuscular_load", 0.0) or 0.0)
        dur = float(getattr(w, "duration", 0.0) or 0.0)
        wcap = limits.weekday_single_workout_cap
        ok = False
        for d in range(7):
            if d < 5 and wcap is not None and dur > wcap + 1e-9:
                continue
            if nu > limits.max_daily_neuro * float(limits.readiness_alpha[d]) + 1e-9:
                continue
            if allowed_fn(i, d):
                ok = True
                break
        if ok:
            kept.append(w)
    return kept


def _intensity_bucket(w: Any) -> str:
    if not getattr(w, "is_hard_endurance", False):
        return "easy"
    z7 = getattr(w, "zone7", None)
    if z7 and len(z7) >= 7:
        z3, z45 = float(z7[3]), float(z7[4] + z7[5] + z7[6])
    else:
        zd = getattr(w, "zone_durations", (0.0, 0.0, 0.0, 0.0, 0.0))
        z3, z45 = float(zd[2]), float(zd[3] + zd[4])
    return "tempo" if z3 >= z45 else "hi"


def _has_canonical_long_runs(workouts: Sequence[Any]) -> bool:
    return any(
        getattr(w, "code", "") == "RUN_AE2_LONG" or str(getattr(w, "code", "")).startswith("RUN_LC_Z2_")
        for w in workouts
    )


def prune_8020_candidates(workouts: Sequence[Any],
                          bucket_caps: Optional[Dict[str, int]] = None) -> List[Any]:
    """Stratified representative pruning of ``8020_library`` workouts across duration and intensity buckets.

    Curated core rows (non-``8020_library``), anchors, tests, and stride-bearing sessions are never pruned.
    Near-duplicate ``8020_library`` rows within (duration <= 2.0 min, aerobic_tss <= 3.0 TSS) of an existing
    core row in the same ``(sport, intensity_bucket, is_indoor_bike)`` are dropped before bucket stratification.
    When canonical long-course runs (``RUN_AE2_LONG``, ``RUN_LC_Z2_*``) are present, ``8020_library`` easy runs
    with ``duration > 70.0`` are excluded from the ``easy`` Run bucket so slots stratify across ``<= 70.0 min``
    support runs (retaining the lowest-strain ~60 min Z1 support run such as ``RRe9``).
    """
    caps = bucket_caps or DEFAULT_8020_BUCKET_CAPS
    has_canonical_long_runs = _has_canonical_long_runs(workouts)
    core_by_key: Dict[Tuple[str, str, bool], List[Any]] = {}
    for w in workouts:
        if getattr(w, "provenance", "") != "8020_library":
            key = (getattr(w, "sport", ""), _intensity_bucket(w), bool(getattr(w, "is_indoor_bike", False)))
            core_by_key.setdefault(key, []).append(w)

    groups: Dict[Tuple[str, str], List[int]] = {}
    for idx, w in enumerate(workouts):
        if getattr(w, "provenance", "") != "8020_library" or _is_protected(w) or getattr(w, "has_strides", False):
            continue
        sport = getattr(w, "sport", "")
        bucket = _intensity_bucket(w)
        w_dur = float(getattr(w, "duration", 0.0) or 0.0)
        if sport == "Run" and bucket == "easy" and has_canonical_long_runs and w_dur > 70.0 + 1e-9:
            continue
        core_matches = core_by_key.get((sport, bucket, bool(getattr(w, "is_indoor_bike", False))), ())
        w_tss = float(getattr(w, "aerobic_tss", 0.0) or 0.0)
        if any(abs(w_dur - float(getattr(c, "duration", 0.0) or 0.0)) <= 2.0 + 1e-9
               and abs(w_tss - float(getattr(c, "aerobic_tss", 0.0) or 0.0)) <= 3.0 + 1e-9
               for c in core_matches):
            continue
        groups.setdefault((sport, bucket), []).append(idx)

    keep_8020: set = set()
    for (sport, bucket), idxs in groups.items():
        cap = int(caps.get(bucket, 4))
        if len(idxs) <= cap:
            keep_8020.update(idxs)
            continue
        ordered = sorted(idxs, key=lambda i: (round(float(workouts[i].duration), 1),
                                              round(float(workouts[i].aerobic_tss), 1),
                                              str(getattr(workouts[i], "code", ""))))
        if cap == 1:
            keep_8020.add(ordered[0])
        elif sport == "Run" and bucket == "easy":
            support_60 = [i for i in ordered if 59.0 <= float(workouts[i].duration) <= 65.0]
            if support_60:
                best_support = min(
                    support_60,
                    key=lambda i: (float(workouts[i].mechanical_strain), -float(workouts[i].duration),
                                   str(getattr(workouts[i], "code", ""))),
                )
                keep_8020.add(best_support)
                rest = [i for i in ordered if i != best_support]
                sub_cap = cap - 1
                if sub_cap == 1:
                    keep_8020.add(rest[0])
                else:
                    picks = {rest[int(round(k * (len(rest) - 1) / (sub_cap - 1)))] for k in range(sub_cap)}
                    keep_8020.update(picks)
            else:
                picks = {ordered[int(round(k * (len(ordered) - 1) / (cap - 1)))] for k in range(cap)}
                keep_8020.update(picks)
        else:
            picks = {ordered[int(round(k * (len(ordered) - 1) / (cap - 1)))] for k in range(cap)}
            keep_8020.update(picks)

    return [w for i, w in enumerate(workouts)
            if getattr(w, "provenance", "") != "8020_library"
            or _is_protected(w)
            or getattr(w, "has_strides", False)
            or i in keep_8020]
