"""Training-week phase enum (TASK-023, ADR-036).

Replaces substring matching on free-text phase names (``"base" in phase_name``), which silently misclassified
``"General Foundation"`` (and ``"Race Week"``) as Build 1. A week's phase is one of six explicit values; text
only enters through :meth:`Phase.parse`, which resolves an exact, normalized alias and RAISES on anything unknown,
so a new display name can never silently fall back to a default.

``Phase`` is a ``StrEnum``: members compare and hash equal to their string values (``Phase.RECOVERY == "recovery"``),
so the season matrix, tests and CSV summaries keep their plain-string roles.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Optional, Union


class Phase(StrEnum):
    BASE = "base"
    BUILD = "build"
    OVERLOAD = "overload"
    RECOVERY = "recovery"
    TAPER = "taper"
    RACE = "race"

    @classmethod
    def parse(cls, value: Union["Phase", str]) -> "Phase":
        """Resolve a phase from a ``Phase``, a canonical value, a known alias or a display / step-type label."""
        if isinstance(value, cls):
            return value
        key = _normalize(str(value))
        try:
            return _ALIASES[key]
        except KeyError:
            raise ValueError(f"unknown phase {value!r} (normalized {key!r}); add it to triplan.model.phase.PHASE_ALIASES "
                             f"or use one of {[p.value for p in cls]}") from None

    @classmethod
    def try_parse(cls, value: Union["Phase", str, None]) -> Optional["Phase"]:
        """``parse`` that returns ``None`` for ``None`` / unknown text (callers that accept free-form labels)."""
        if value is None:
            return None
        try:
            return cls.parse(value)
        except ValueError:
            return None

    @property
    def is_deload(self) -> bool:
        """Recovery, taper and race weeks (``ADR-023`` deload roles)."""
        return self in (Phase.RECOVERY, Phase.TAPER, Phase.RACE)


def _normalize(text: str) -> str:
    """Lower-case, drop parenthetical qualifiers (``"Build (+10%)"`` -> ``"build"``), unify separators."""
    t = re.sub(r"\([^)]*\)", " ", text.lower())
    t = re.sub(r"[_\-/&]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _table(**groups: tuple) -> dict:
    return {_normalize(name): Phase[phase.upper()] for phase, names in groups.items() for name in names}


# Display names of the generated season matrix (control.periodization) and the step-type labels, mapped to the week
# phase they occupy. Mesocycle display names ("General Foundation", "Specific Threshold", ...) label a stretch of
# weeks, so the mapping gives the phase of the stretch's TRAINING character; the per-week role (build / overload /
# recovery) comes from the matrix, never from the name.
PHASE_ALIASES = _table(
    base=("base", "base load", "base 1 (aerobic)", "base 2 (foundation)", "base 1 (foundation)", "base 2 (aerobic)",
          "general foundation", "foundation", "re-entry foundation", "re-entry aerobic base", "re-entry general prep",
          "general prep", "aerobic base"),
    build=("build", "build 1", "build_1", "build (+10%)", "aerobic capacity", "specific speed", "race simulation",
           "specific threshold", "race specific", "muscular endurance", "tempo & threshold", "durability / bricks",
           "durability block", "peak ironman volume", "mesocycle build"),
    overload=("overload", "overload (+10%)", "build 2", "build 2 overload", "build_2"),
    recovery=("recovery", "recovery deload", "deload", "recovery deload (-30%)"),
    taper=("taper", "taper 1", "taper 2", "taper & sharpen", "taper sharpen", "sharpen", "peak"),
    race=("race", "race week"),
)
_ALIASES = dict(PHASE_ALIASES)
