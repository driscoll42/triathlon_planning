"""Shared pytest fixtures (TASK-008).

The modules in src/ use flat imports (``from classes import ...``), so src/ goes on sys.path.
"""
import contextlib
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for p in (SRC, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="session")
def stress_results():
    """Run the 11-scenario stress suite once per session (~60s) with stdout suppressed."""
    import stress_test_optimizer as st

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        results = st.stress_test_suite()
    return results


@pytest.fixture(scope="session")
def default_library():
    from closed_loop_optimizer import build_default_candidate_library

    return build_default_candidate_library()
