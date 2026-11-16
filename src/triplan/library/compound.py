"""DECISION-021 lift + easy cardio combo: compound candidate generation (TASK-019).

One strength window followed immediately (lift first) by <= 20 min of strictly Z1/Z2 Bike or Run. After
heavy lower-body lifting only Bike is allowed (no impact on fatigued legs); after any other lift Bike or
Run. A compound is ONE session in ONE window: it adds one to the day's session count and its full
duration to the day's time cap, and counts toward the cardio sport's weekly frequency
(``cardio_sport`` / ``cardio_minutes``). Its ``sport`` stays ``"Strength"`` so the one-lift-per-day and
non-consecutive-strength-day rules apply, and so does the heavy-strength classification of the lift.

The cardio segment is synthesized from the athlete-independent zone intensity tables used to build the
library (``triplan.library.build``): half Z1 / half Z2 minutes, TSS from the NP-style 4th-power mean,
mechanical strain / ANS / neuromuscular / axial loads from the library's own endurance formulas. All loads
add to the lift's loads, so every Tier-0 cap sees the true combined load, and ``cardio_mech_strain`` /
``cardio_minutes`` let the run-share cap and the weekly run-strain cap see only the run component.
"""

from __future__ import annotations

import dataclasses
from typing import Any, List, Sequence

from triplan.library.build import BIKE_ZONE_IF, RUN_ZONE_IF, endurance_loads, power_tss

COMBO_SEPARATOR = "+EZ_"


def is_heavy_lower(w: Any) -> bool:
    f = getattr(w, "flags", None) or {}
    return bool(getattr(w, "is_heavy_strength", False) or f.get("heavy_sq", 0) == 1 or f.get("heavy_dl", 0) == 1)


def cardio_piece(sport: str, minutes: float) -> dict:
    """Loads of a strictly Z1/Z2 ``sport`` segment of ``minutes`` (50/50 Z1/Z2)."""
    z1 = z2 = minutes / 2.0
    zif = BIKE_ZONE_IF if sport == "Bike" else RUN_ZONE_IF
    _np, if_, tss = power_tss([z1, z2, 0.0, 0.0, 0.0], zif, 1.0)
    loads = endurance_loads(sport, tss, if_, minutes, [z1, z2, 0.0, 0.0, 0.0], False)
    return {"tss": tss, "zones": (z1, z2), **loads}


def make_lift_easy_combos(workouts: Sequence[Any], *, max_minutes: float = 20.0,
                          sports_after_heavy_lower: Sequence[str] = ("Bike",),
                          sports_after_other_lift: Sequence[str] = ("Bike", "Run"),
                          minutes: Sequence[float] = ()) -> List[Any]:
    """New compound candidates for every plain strength workout in ``workouts`` (originals not included).

    ``minutes`` defaults to ``(max_minutes,)``; every value must be in ``(0, max_minutes]``.
    """
    minutes = tuple(minutes) or (float(max_minutes),)
    if any(m <= 0 or m > max_minutes + 1e-9 for m in minutes):
        raise ValueError(f"combo cardio minutes must lie in (0, {max_minutes}]: {minutes}")
    next_id = 1 + max((int(getattr(w, "id", 0)) for w in workouts), default=0)
    out: List[Any] = []
    for w in workouts:
        if getattr(w, "sport", "") != "Strength" or getattr(w, "cardio_sport", ""):
            continue
        sports = sports_after_heavy_lower if is_heavy_lower(w) else sports_after_other_lift
        z7 = tuple(getattr(w, "zone7", ()) or ())
        for sport in sports:
            if sport not in ("Bike", "Run"):
                continue
            for m in minutes:
                c = cardio_piece(sport, float(m))
                z5 = list(w.zone_durations)
                z5[0] += c["zones"][0]
                z5[1] += c["zones"][1]
                flags = dict(getattr(w, "flags", None) or {})
                flags[f"combo_{sport.lower()}"] = 1
                changes = dict(
                    id=next_id, code=f"{w.code}{COMBO_SEPARATOR}{sport}{int(round(m))}",
                    name=f"{w.name} + {m:.0f} min easy {sport}",
                    duration=w.duration + m, aerobic_tss=w.aerobic_tss + c["tss"],
                    mechanical_strain=w.mechanical_strain + c["mechanical_strain"],
                    ans_cost=w.ans_cost + c["ans_cost"],
                    zone_durations=tuple(z5),
                    neuromuscular_load=w.neuromuscular_load + c["neuromuscular_load"],
                    axial_load=w.axial_load + c["axial_load"],
                    is_anchor=False, has_strides=False,
                    is_indoor_bike=bool(sport == "Bike") or w.is_indoor_bike,
                    preference_penalty=min(1.0, w.preference_penalty + 0.05),
                    flags=flags,
                    cardio_sport=sport, cardio_minutes=float(m),
                    cardio_mech_strain=float(c["mechanical_strain"]),
                    cardio_zone_durations=(float(c["zones"][0]), float(c["zones"][1])),
                )
                if len(z7) == 7:
                    z7c = list(z7)
                    z7c[0] += c["zones"][0]
                    z7c[1] += c["zones"][1]
                    changes["zone7"] = tuple(z7c)
                out.append(dataclasses.replace(w, **changes))
                next_id += 1
    return out
