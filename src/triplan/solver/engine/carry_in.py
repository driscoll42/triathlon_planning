"""Cross-week carry-in state for the unified week engine (TASK-018, ADR-019).

A training week is not an island: day 0 (Monday) is adjacent to the previous Sunday, the rolling 48h
caps straddle the week boundary, and the EWMA running ACWR / Banister CTL are *states* that the previous
weeks produced. ``WeekCarryIn`` is the hand-off object: week ``k`` produces it from its solved schedule
and the athlete state (``control.season``), week ``k+1`` consumes it as **constants** of its MILP
(``engine.builders.load_dynamics``). An empty ``WeekCarryIn()`` adds nothing: the model is exactly the
isolated-week model.

Day loads are stored for days -1 (Sunday before) and -2 (Saturday before). The existing rows only reach
back one day (48h windows over ``(d, d+1)``, adjacency over ``(d, d+1)``); day -2 is kept so a future
72h rule needs no new plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

from triplan.solver.safety_constraints import classify_workouts


@dataclass(frozen=True)
class DayLoad:
    """What one (previous-week) day contributed, in the terms the Tier-0 rows consume."""
    hard: bool = False               # hard endurance or heavy strength (hard/easy adjacency)
    heavy_run: bool = False          # threshold / interval / long run (48h separation, HEUR-004)
    deadlift: bool = False           # heavy deadlift-class lift (HEUR-004 +-24h vs heavy/long runs)
    heavy_strength: bool = False     # any heavy strength (scheduler's lift vs heavy-run buffer)
    strength: bool = False           # any strength session (non-consecutive strength days)
    aerobic_tss: float = 0.0
    mech_strain: float = 0.0
    ans_cost: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not (self.hard or self.heavy_run or self.deadlift or self.heavy_strength or self.strength
                    or self.aerobic_tss or self.mech_strain or self.ans_cost)

    @classmethod
    def from_workouts(cls, workouts: Sequence[Any]) -> "DayLoad":
        """Summarize the workouts actually scheduled on one day (same classification as the safety layer)."""
        ws = list(workouts)
        if not ws:
            return cls()
        S = classify_workouts(ws)
        return cls(
            hard=bool(S.hard), heavy_run=bool(S.heavy_or_long_runs), deadlift=bool(S.deadlifts),
            heavy_strength=bool(S.heavy_strength), strength=bool(S.strength),
            aerobic_tss=float(sum(float(getattr(w, "aerobic_tss", 0.0)) for w in ws)),
            mech_strain=float(sum(float(getattr(w, "mechanical_strain", 0.0)) for w in ws)),
            ans_cost=float(sum(float(getattr(w, "ans_cost", 0.0)) for w in ws)),
        )


@dataclass(frozen=True)
class LoadState:
    """EWMA / PMC state at the START of the week plus the limits that apply this week (all constants)."""
    run_acute: float                 # running mechanical acute EWMA (tau 7d) after day -1
    run_chronic: float               # running mechanical chronic EWMA (tau 28d) after day -1
    total_ctl: float                 # Banister CTL of bike+run+swim TSS after day -1
    run_ctl: float = 0.0             # decides which ACWR ceiling applies (ADR-012)
    acwr_max: float = 1.30           # rho_max: running ACWR ceiling this week (Tier-0)
    acwr_min: float = 0.80           # undertraining floor (Tier-1 named slack)
    acwr_eps: float = 0.0            # epsilon_floor in running-strain units (ADR-019)
    ctl_ramp_max: Optional[float] = None          # max weekly CTL gain (points); None = no row
    run_minutes_cap: Optional[float] = None       # absolute weekly run minutes cap (re-entry guard); None = off


@dataclass
class WeekCarryIn:
    """Hand-off from week k to week k+1: boundary days (constants) and EWMA state (constants)."""
    day_m1: DayLoad = field(default_factory=DayLoad)    # Sunday before
    day_m2: DayLoad = field(default_factory=DayLoad)    # Saturday before
    load: Optional[LoadState] = None
    prev_run_minutes: float = 0.0
    prev_week_tss: float = 0.0
    last_test_week: Dict[str, int] = field(default_factory=dict)   # ADR-011 CP/CSS test hand-off (TASK-026)

    @property
    def is_active(self) -> bool:
        """True when the carry-in changes the model (boundary loads or in-model load dynamics)."""
        return (not self.day_m1.is_empty) or (not self.day_m2.is_empty) or self.load is not None

    @classmethod
    def empty(cls) -> "WeekCarryIn":
        return cls()
