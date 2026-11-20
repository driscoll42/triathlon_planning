"""Interval-parsed durability features and the unified durability score (TASK-023, ADR-036).

The library used to carry a hand-typed ``durability_score`` on two incompatible scales (``0..10`` for generated
rows, ``0..85`` for hybrid hand-coded rows). It is now DERIVED, deterministically, from the ordered interval
description of each endurance session and its zone minutes:

* ``t_thr_min``  (``T^thr``)   total threshold-or-harder minutes: Zone 3, Zone Y, Zone 4, Zone 5 (the 80/20
  "moderate + high" set). Zone X is sub-threshold and excluded. Alactic strides (Zone 5 reps of <= 20 s) are not
  threshold work and excluded, as in ``session_quality.raw_quality``.
* ``t_cont_min`` (``T^cont``)  longest CONTINUOUS threshold block: adjacent threshold segments merge (``10' Z3 + 5' Z4``
  is one 15 min block); any Zone 1/2/X segment or a rest between them splits blocks (``4 x (7' Z3 / 2' Z1)`` is 7).
* ``late_min``   (``late``)    threshold minutes that fall after 60% of the session's elapsed time (fatigue-resistance
  stimulus: quality delivered on tired legs). Partial overlap with the 60% mark counts pro rata.
* ``brick``                    a Bike -> Run session (``sport == "Brick"``): the run leg is by definition late and tired.

Parsing grammar (the 80/20 library plus the agent-authored rows): ``<q> <unit>? Zone <z>`` segments (``minutes``,
``min``, ``seconds``, ``sec``, ``mile(s)``, or a bare pool distance), ``N x (a / b)`` repeat groups (``->`` and ``,``
also separate items inside a group), ``<s>" rest`` rests (swim; they split blocks but are not a zone). Distance rows
are converted to minutes per zone from the row's own ``z{k}_min / z{k}_dist``. Every parse is VALIDATED against the
row's stored zone minutes (or zone distances); when the description is free-form or the parse disagrees, the features
fall back to the zone minutes (``method == "zones"``): ``t_thr`` exact from the zone columns, ``t_cont`` from an
explicit ``N x M m`` hint (or the largest ``M m Zk`` token) else the whole threshold volume, ``late`` from an explicit
"late / finish" cue or, for a Brick, the run-leg minutes. Nothing is guessed from the library code.

``durability_score`` is one 0..10 scale:

    D = min(10, 1.5 + 5.0 sat((T - 30)/210) + 2.5 sat(T^cont/30) + 1.0 sat(T^thr/45) + 1.5 sat(late/20) + 1.5 brick)

with ``sat(x) = clip(x, 0, 1)`` and ``T`` the session minutes. The weights are calibration priors (``EXP-006``), not
fitted values; strength rows keep ``2.0`` and carry no threshold features.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

THR_ZONES = (3, 4, 5)          # Zone Y / Zone X do not occur in descriptions (5-zone scrape); columns handled below
LATE_FRACTION = 0.60           # "late" = after this share of the session's elapsed time
ALACTIC_MAX_MIN = 20.0 / 60.0  # Zone 5 reps up to 20 s are strides (SPEC.md): no threshold stimulus
STRENGTH_DURABILITY = 2.0
DURABILITY_MAX = 10.0
DURABILITY_FLOOR = 1.5
W_DURATION, W_CONT, W_THR, W_LATE, W_BRICK = 5.0, 2.5, 1.0, 1.5, 1.5
DURATION_START_MIN, DURATION_SPAN_MIN = 30.0, 210.0
CONT_SAT_MIN, THR_SAT_MIN, LATE_SAT_MIN = 30.0, 45.0, 20.0

_ZONE_MIN = ("z1_min", "z2_min", "z3_min", "z4_min", "z5_min")
_ZONE_DIST = ("z1_dist", "z2_dist", "z3_dist", "z4_dist", "z5_dist")

_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"(?:minutes?|mins?|seconds?|secs?|miles?|m(?=\s+(?:Zone|Z)\s*\d))"
_REPEAT = re.compile(rf"(?P<n>\d+)\s*(?:x|X|×|�)\s*\((?P<inner>[^()]*)\)")
_NXM = re.compile(r"(\d+)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*(?:m\b|min)")
_ZTOKEN = re.compile(rf"(?P<q>{_NUM})\s*(?:m\b|min(?:ute)?s?\b)[^,;/()]*?\bZ(?:one)?\s*(?P<z>[3-5])\b", re.IGNORECASE)
_LATE_CUE = re.compile(r"\b(late|finish|final)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Segment:
    """One parsed block of a session in elapsed order. ``zone`` 0 = rest (no zone minutes)."""
    zone: int
    minutes: float
    alactic: bool = False


@dataclass(frozen=True)
class IntervalFeatures:
    t_thr_min: float
    t_cont_min: float
    late_min: float
    brick: bool
    method: str                      # "parsed" (ordered intervals) | "zones" (zone-minute fallback) | "none"


def _clean(desc: str) -> str:
    s = re.sub(r"(\d)(minutes?)", r"\1 \2", desc, flags=re.IGNORECASE)   # "40minutes" -> "40 minutes"
    return s.replace("→", "->")


# One tokenizer for both segments ("10 minutes Zone 3", "500 Zone 1", "20 sec Z5") and swim rests (20" rest).
_SEG_OR_REST = re.compile(
    rf"(?P<q>{_NUM})\s*(?P<u>{_UNIT})?\s*(?:in\s+)?(?:Zone|Z)\s*(?P<z>[1-5])\b(?!\d)"
    rf'|(?P<rq>{_NUM})\s*(?:"|”|\'\')\s*rest', re.IGNORECASE)


def _items(text: str) -> List[Tuple[str, float, Optional[str], int]]:
    out: List[Tuple[str, float, Optional[str], int]] = []
    for m in _SEG_OR_REST.finditer(text):
        if m.group("z") is not None:
            out.append(("seg", float(m.group("q")), (m.group("u") or "").lower() or None, int(m.group("z"))))
        else:
            out.append(("rest", float(m.group("rq")), None, 0))
    return out


def _expand(desc: str) -> List[Tuple[str, float, Optional[str], int]]:
    """Ordered items of ``desc`` with every ``N x (...)`` group unrolled in place."""
    out: List[Tuple[str, float, Optional[str], int]] = []
    pos = 0
    for m in _REPEAT.finditer(desc):
        out += _items(desc[pos:m.start()])
        out += _items(m.group("inner")) * int(m.group("n"))
        pos = m.end()
    out += _items(desc[pos:])
    return out


def _unit_kind(u: Optional[str]) -> str:
    if u is None:
        return "dist"            # bare pool distance ("500 Zone 1")
    if u.startswith("mile"):
        return "dist"
    if u.startswith("sec"):
        return "sec"
    return "min"                 # minutes / min / m(+Zone)


def parse_segments(row: Mapping[str, Any]) -> Optional[List[Segment]]:
    """Ordered, validated segments of one library row, or ``None`` when the description does not parse cleanly."""
    sport = str(row.get("sport", ""))
    desc = _clean(str(row.get("description") or ""))
    if sport == "Strength" or not desc:
        return None
    if sport == "Brick" and re.search(r"\bswim\b", desc, re.IGNORECASE):
        return None                                         # swim-leg bricks: "m" means metres; use the zone fallback
    items = _expand(desc)
    segs = [it for it in items if it[0] == "seg"]
    if not segs:
        return None
    prescription = str(row.get("prescription") or "time")
    zmin = [float(row.get(c) or 0.0) for c in _ZONE_MIN]
    zdist = [float(row.get(c) or 0.0) for c in _ZONE_DIST]

    parsed_native = [0.0] * 5                                # parsed quantity per zone in the row's native unit
    out: List[Segment] = []
    for kind, q, unit, z in items:
        if kind == "rest":
            out.append(Segment(0, q / 60.0))
            continue
        uk = _unit_kind(unit)
        if prescription == "time":
            if uk == "dist":
                return None
            minutes = q / 60.0 if uk == "sec" else q
            parsed_native[z - 1] += minutes
        else:
            if uk != "dist":
                return None                                  # a time unit inside a distance-prescribed row
            per_unit = zmin[z - 1] / zdist[z - 1] if zdist[z - 1] > 0 else 0.0
            minutes = q * per_unit
            parsed_native[z - 1] += q
        out.append(Segment(z, minutes, alactic=(z == 5 and minutes <= ALACTIC_MAX_MIN + 1e-9 and prescription == "time")))
    ref = zmin if prescription == "time" else zdist
    for k in range(5):                                       # parse must reproduce the stored zone volumes
        if abs(parsed_native[k] - ref[k]) > max(1.0, 0.06 * ref[k]):
            return None
    return out


def _features_from_segments(segs: Sequence[Segment]) -> Tuple[float, float, float]:
    total = sum(s.minutes for s in segs)
    if total <= 0:
        return 0.0, 0.0, 0.0
    t_thr = t_cont = late = block = elapsed = 0.0
    cut = LATE_FRACTION * total
    for s in segs:
        if s.zone in THR_ZONES and not s.alactic:
            t_thr += s.minutes
            block += s.minutes
            t_cont = max(t_cont, block)
            late += max(0.0, (elapsed + s.minutes) - max(elapsed, cut))
        else:
            block = 0.0
        elapsed += s.minutes
    return t_thr, t_cont, late


def _fallback_features(row: Mapping[str, Any]) -> Tuple[float, float, float]:
    """Zone-minute features for free-form / unparsable descriptions (see module docstring)."""
    desc = str(row.get("description") or "")
    z = {k: float(row.get(k) or 0.0) for k in ("z3_min", "zy_min", "z4_min", "z5_min")}
    if bool(row.get("has_strides")):
        z["z5_min"] = 0.0
    t_thr = sum(z.values())
    if t_thr <= 0:
        return 0.0, 0.0, 0.0
    cont = t_thr
    nxm = _NXM.search(desc)
    toks = [float(m.group("q")) for m in _ZTOKEN.finditer(desc)]
    if nxm:
        cont = min(cont, float(nxm.group(2)))
    elif toks:
        cont = min(cont, max(toks))
    late = 0.0
    if str(row.get("sport")) == "Brick":
        late = min(t_thr, float(row.get("brick_run_min") or 0.0))
    elif _LATE_CUE.search(desc):
        late = t_thr
    return t_thr, cont, late


def interval_features(row: Mapping[str, Any]) -> IntervalFeatures:
    """``T^thr``, ``T^cont``, ``late`` and ``brick`` of one library row (a mapping with the canonical columns)."""
    sport = str(row.get("sport", ""))
    brick = sport == "Brick"
    if sport == "Strength" or bool(row.get("is_test")):
        return IntervalFeatures(0.0, 0.0, 0.0, False, "none")
    segs = parse_segments(row)
    if segs is not None:
        t_thr, t_cont, late = _features_from_segments(segs)
        # exact thr volume from the stored zone minutes (parse validated to the same totals; this removes rounding)
        method = "parsed"
    else:
        t_thr, t_cont, late = _fallback_features(row)
        method = "zones"
    t_cont = min(t_cont, t_thr)
    late = min(late, t_thr)
    return IntervalFeatures(round(t_thr, 3), round(t_cont, 3), round(late, 3), brick, method)


def _sat(x: float) -> float:
    return 0.0 if x <= 0 else 1.0 if x >= 1 else x


def durability_from_features(sport: str, duration_min: float, f: IntervalFeatures) -> float:
    """The unified 0..10 durability score (module docstring); strength is a constant ``2.0``."""
    if sport == "Strength":
        return STRENGTH_DURABILITY
    d = (DURABILITY_FLOOR
         + W_DURATION * _sat((float(duration_min) - DURATION_START_MIN) / DURATION_SPAN_MIN)
         + W_CONT * _sat(f.t_cont_min / CONT_SAT_MIN)
         + W_THR * _sat(f.t_thr_min / THR_SAT_MIN)
         + W_LATE * _sat(f.late_min / LATE_SAT_MIN)
         + (W_BRICK if f.brick else 0.0))
    return round(min(DURABILITY_MAX, d), 2)


def apply_interval_features(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute ``t_thr_min``, ``t_cont_min``, ``late_min`` and ``durability_score`` of every row (copy)."""
    out = df.copy()
    for col in ("t_thr_min", "t_cont_min", "late_min"):
        if col not in out.columns:
            out[col] = 0.0
    for i, r in out.iterrows():
        f = interval_features(r)
        out.at[i, "t_thr_min"], out.at[i, "t_cont_min"], out.at[i, "late_min"] = f.t_thr_min, f.t_cont_min, f.late_min
        out.at[i, "durability_score"] = durability_from_features(str(r["sport"]), float(r["duration_min"]), f)
    return out
