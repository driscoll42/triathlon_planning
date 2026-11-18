"""Default/hybrid candidate-library builders backed by the canonical library (TASK-011)."""

from typing import List

from triplan.model.candidates import CandidateWorkout


def build_default_candidate_library(include_8020: bool = True) -> List[CandidateWorkout]:
    """Default triathlon candidate set (lib_default + 8020_library options) from the canonical library."""
    from triplan.library.loader import load_candidates
    return load_candidates(library="default", include_8020=include_8020)


def build_hybrid_candidate_library(include_8020: bool = True) -> List[CandidateWorkout]:
    """Hybrid-athlete candidate set (lib_hybrid + 8020_library options) from the canonical library."""
    from triplan.library.loader import load_candidates
    return load_candidates(library="hybrid", include_8020=include_8020)

