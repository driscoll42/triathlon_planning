"""Default/hybrid candidate-library builders backed by the canonical library (TASK-011)."""

from typing import List

from triplan.model.candidates import CandidateWorkout


def build_default_candidate_library() -> List[CandidateWorkout]:
    """Default triathlon candidate set (lib_default) from the canonical library (TASK-011)."""
    from triplan.library.loader import load_candidates
    return load_candidates(library="default")


def build_hybrid_candidate_library() -> List[CandidateWorkout]:
    """Hybrid-athlete candidate set (lib_hybrid) from the canonical library (TASK-011)."""
    from triplan.library.loader import load_candidates
    return load_candidates(library="hybrid")

