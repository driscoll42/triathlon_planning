"""Shared pytest fixtures (TASK-008).

The ``triplan`` package is importable via ``pip install -e ".[test]"`` (or pytest ``pythonpath=["src"]``).
"""
import contextlib
import io
import os

import pytest

if os.environ.get("PYTEST_XDIST_WORKER"):
    os.environ.setdefault("TRI_HIGHS_THREADS", "1")


@pytest.fixture(scope="session")
def stress_results():
    """Run the 11-scenario stress suite once per session (~60s) with stdout suppressed."""
    from triplan.perf import stress as st

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        results = st.stress_test_suite()
    return results


@pytest.fixture(scope="session")
def default_library():
    from triplan.library.candidates import build_default_candidate_library

    return build_default_candidate_library()
