"""Multi-week workout variety penalty (TASK-023, ADR-036).

Repeating the same key session week after week trains one narrow stimulus and wears one set of tissues. The
carry-in keeps the key workout codes of the last ``VARIETY_MEMORY_WEEKS`` solved weeks (newest first); a candidate
whose code was selected ``k`` weeks ago (``k = 0`` = last week) is charged ``w_variety * (N - k) / N`` with ``N`` the
configured window, summed over the weeks it appeared in. The penalty is a per-workout constant in the objective
(linear, no new columns), so it composes with every row and with dominance pruning (``prune_dominated_candidates``
ranks equal-structure workouts by it). Mandatory anchors are never charged (they cannot be varied) and only key
sessions (hard / long / anchor per ``session_quality.is_key``) are, so easy volume repeats freely.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

import numpy as np

from triplan.solver.engine import session_quality as sq


def recency_weights(carry_in: Optional[Any], window: int) -> Dict[str, float]:
    """``code -> sum over the last ``window`` weeks it was a key session of (window - k) / window`` (k = weeks ago - 1)."""
    codes = getattr(carry_in, "recent_key_codes", ()) or ()
    window = max(0, min(int(window), len(codes)))
    out: Dict[str, float] = {}
    for k, week in enumerate(codes[:window]):
        for c in week:
            out[c] = out.get(c, 0.0) + (window - k) / window
    return out


def variety_vector(workouts: Sequence[Any], recency: Dict[str, float], params: sq.SessionQualityParams) -> np.ndarray:
    """Per-workout recency weight (0 for non-key sessions and anchors); multiply by ``w_variety`` for the cost."""
    return np.array([recency.get(str(getattr(w, "code", "")), 0.0)
                     if (sq.is_key(w, params) and not getattr(w, "is_anchor", False)) else 0.0
                     for w in workouts], dtype=np.float64)
