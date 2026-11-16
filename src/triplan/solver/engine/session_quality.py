"""Sport-specific intensity quality ``q_i``, hard/key classification and the grey-zone pre-filter (TASK-019).

Why not IF
----------
Intensity Factor is not comparable across sports: the swim IF scale starts at ~0.795 (zone 1 swim speed
is 79.5% of CSS), so a Zone-2-only swim has IF 0.86 and a raw ``IF >= 0.85`` rule marks easy swims "hard".
Zone **minutes** are already expressed in each sport's own threshold-relative zones (CSS for swims, CP
for rides, run CP for runs), so a minutes-based quality is sport-comparable by construction.

Definition (``q_i``, in "Z4-equivalent minutes")
------------------------------------------------
``q_i = sum_z w_z * minutes_{i,z}`` over the 7 zones (Z1, Z2, ZX, Z3, ZY, Z4, Z5) with the default weights
``w = (0, 0, 1/6, 1/3, 2/3, 1, 1)``. Derivation: the library's reviewed hard-session rule (TASK-011) is
"15 min in Z3+ or 5 min in Z4+". Setting the Z4/Z5 weight to 1 and the Z3 weight to 1/3 makes both pure
cases equal ``q = 5``, so the default threshold ``q_hard = 5`` reproduces that rule on pure sessions and
generalizes it smoothly to mixed ones (ZX sits between Z2 and Z3 -> 1/6; ZY between Z3 and Z4 -> 2/3).
Z5 minutes of a strides session are alactic (<= 20 s) and carry no quality (zero ANS penalty, SPEC.md), so
they are excluded when ``has_strides``.

Thresholds (per sport, config key ``session_quality``)
-------------------------------------------------------
* ``q_hard[sport]``  (default 5): ``q_i >= q_hard`` -> hard endurance session ("quality").
* Incidental allowance: a session whose ``q_i <= q_noise + q_noise_per_hour * hours`` is easy with
  incidental surges (a long Z2 ride with a few tempo minutes): its quality is treated as 0.
  Defaults ``q_noise = 2``, ``q_noise_per_hour = 1`` (a 165 min ride may hold ~4.75 Z4-eq minutes).
* Grey zone: ``0 < q_i < q_hard`` after the allowance. Such "medium" sessions are neither clean easy
  volume nor a real key session, so they are removed before the model is built (pre-filtering
  invariant). Anchors and test sessions are never removed.
* ``long_min[sport]`` (Run 70, Bike 120 min): a long easy session is a *key* session (long run / long
  ride). Swims have no long-key threshold.

``key_i`` = endurance session that is hard, an anchor, or long. Strength is never "key" (it has its own
heavy-strength rules). Override any value with ``session_quality = {"q_hard": {"Swim": 6}, ...}``.
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

ZONES7 = ("Z1", "Z2", "ZX", "Z3", "ZY", "Z4", "Z5")
ENDURANCE_SPORTS = ("Swim", "Bike", "Run", "Brick")
DEFAULT_ZONE_WEIGHTS = (0.0, 0.0, 1.0 / 6.0, 1.0 / 3.0, 2.0 / 3.0, 1.0, 1.0)


def _per_sport(default: float, **over: float) -> Dict[str, float]:
    d = {s: default for s in ENDURANCE_SPORTS}
    d.update(over)
    return d


@dataclass
class SessionQualityParams:
    zone_weights: Tuple[float, ...] = DEFAULT_ZONE_WEIGHTS
    q_hard: Dict[str, float] = field(default_factory=lambda: _per_sport(5.0))
    q_noise: Dict[str, float] = field(default_factory=lambda: _per_sport(2.0))
    q_noise_per_hour: Dict[str, float] = field(default_factory=lambda: _per_sport(1.0))
    long_min: Dict[str, float] = field(default_factory=lambda: _per_sport(1e9, Run=70.0, Bike=120.0))

    @classmethod
    def from_config(cls, cfg: Any) -> "SessionQualityParams":
        """``cfg`` is a ``WeekConfig`` (or anything with ``.get``); key ``session_quality`` overrides."""
        p = cls()
        over = cfg.get("session_quality", default=None) if cfg is not None else None
        for k, v in (over or {}).items():
            if k == "zone_weights":
                if len(v) != 7:
                    raise ValueError("session_quality.zone_weights needs 7 entries (Z1,Z2,ZX,Z3,ZY,Z4,Z5)")
                p.zone_weights = tuple(float(x) for x in v)
            elif k in ("q_hard", "q_noise", "q_noise_per_hour", "long_min"):
                getattr(p, k).update({s: float(x) for s, x in v.items()})
            else:
                raise ValueError(f"unknown session_quality key {k!r}")
        return p


# ----------------------------------------------------------------------------- zone vectors

def zone7(w: Any) -> Tuple[float, ...]:
    """(Z1, Z2, ZX, Z3, ZY, Z4, Z5) minutes of ``w``; falls back to the 5-zone view with ZX = ZY = 0."""
    z7 = getattr(w, "zone7", None)
    if z7 is not None and len(z7) == 7:
        return tuple(float(x) for x in z7)
    zd = getattr(w, "zone_durations", None)
    if zd is not None and len(zd) >= 5:
        return (float(zd[0]), float(zd[1]), 0.0, float(zd[2]), 0.0, float(zd[3]), float(zd[4]))
    dur = float(getattr(w, "duration", 60.0))
    return (dur * 0.35, dur * 0.45, 0.0, dur * 0.15, 0.0, dur * 0.05, 0.0)


def is_endurance(w: Any) -> bool:
    return getattr(w, "sport", "") in ENDURANCE_SPORTS


def endurance_zone7(w: Any) -> Tuple[float, ...]:
    """Zone minutes that count toward the weekly 80/20 time-in-zone: the whole workout for endurance
    sports, only the cardio component of a lift+easy combo, nothing for plain strength."""
    if is_endurance(w):
        return zone7(w)
    if getattr(w, "cardio_sport", ""):
        z1, z2 = getattr(w, "cardio_zone_durations", (0.0, 0.0))
        return (float(z1), float(z2), 0.0, 0.0, 0.0, 0.0, 0.0)
    return (0.0,) * 7


# ----------------------------------------------------------------------------- quality

def raw_quality(w: Any, p: SessionQualityParams) -> float:
    z = list(zone7(w))
    if getattr(w, "has_strides", False):
        z[6] = 0.0  # alactic strides carry no quality
    return float(sum(wt * m for wt, m in zip(p.zone_weights, z)))


def quality(w: Any, p: SessionQualityParams) -> float:
    """``q_i`` after the incidental allowance (0 for non-endurance workouts and easy sessions)."""
    if not is_endurance(w):
        return 0.0
    sport = w.sport
    q = raw_quality(w, p)
    hours = float(getattr(w, "duration", 0.0)) / 60.0
    if q <= p.q_noise.get(sport, 2.0) + p.q_noise_per_hour.get(sport, 1.0) * hours:
        return 0.0
    return q


def is_hard(w: Any, p: SessionQualityParams) -> bool:
    return is_endurance(w) and quality(w, p) >= p.q_hard.get(w.sport, 5.0) - 1e-9


def is_grey_zone(w: Any, p: SessionQualityParams) -> bool:
    """0 < q_i < q_hard (after the allowance). Anchors and test sessions are never grey-zone."""
    if not is_endurance(w) or getattr(w, "is_anchor", False) or (getattr(w, "flags", None) or {}).get("test", 0):
        return False
    q = quality(w, p)
    return 0.0 < q < p.q_hard.get(w.sport, 5.0) - 1e-9


def is_key(w: Any, p: SessionQualityParams) -> bool:
    if not is_endurance(w):
        return False
    return (is_hard(w, p) or bool(getattr(w, "is_anchor", False))
            or float(getattr(w, "duration", 0.0)) >= p.long_min.get(w.sport, 1e9) - 1e-9)


# ----------------------------------------------------------------------------- list transforms

def _replace(w: Any, **changes: Any) -> Any:
    if dataclasses.is_dataclass(w):
        return dataclasses.replace(w, **changes)
    c = copy.copy(w)
    for k, v in changes.items():
        setattr(c, k, v)
    return c


def prefilter_grey_zone(workouts: Sequence[Any], p: SessionQualityParams) -> Tuple[List[Any], List[str]]:
    """Drop grey-zone workouts. Returns ``(kept, dropped_codes)``."""
    kept, dropped = [], []
    for w in workouts:
        if is_grey_zone(w, p):
            dropped.append(str(getattr(w, "code", "?")))
        else:
            kept.append(w)
    return kept, dropped


def reclassify_hard(workouts: Sequence[Any], p: SessionQualityParams) -> List[Any]:
    """Set ``is_hard_endurance`` of every endurance workout from ``q_i`` (never from a raw IF or a stale
    flag). Strength and non-endurance workouts are untouched. Returns new objects only where it changed."""
    out = []
    for w in workouts:
        if is_endurance(w):
            hard = is_hard(w, p)
            if bool(getattr(w, "is_hard_endurance", False)) != hard:
                w = _replace(w, is_hard_endurance=hard)
        out.append(w)
    return out


def key_mask(workouts: Sequence[Any], p: SessionQualityParams) -> np.ndarray:
    return np.array([is_key(w, p) for w in workouts], dtype=bool)


def quality_vector(workouts: Sequence[Any], p: SessionQualityParams) -> np.ndarray:
    return np.array([quality(w, p) for w in workouts], dtype=np.float64)
