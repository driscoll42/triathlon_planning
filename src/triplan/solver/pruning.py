"""Input-driven candidate-library pruning (pre-filtering invariant before building the HiGHS matrix)."""

from typing import Dict, List
import logging


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


