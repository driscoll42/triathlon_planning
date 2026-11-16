"""Week-to-week hand-off for the closed-loop season (TASK-018, ADR-019).

``next_week_carry_in`` turns the week that was just solved plus the advanced ``AthletePhysiologicalState``
into the ``WeekCarryIn`` consumed by the next week's MILP: boundary day loads (Sunday/Saturday), the EWMA
running-ACWR and CTL state as constants, and this week's limits (ACWR ceiling, ramp, re-entry minutes).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from triplan.physiology.state import ALPHA_ACUTE, ALPHA_CHRONIC, AthletePhysiologicalState
from triplan.solver.engine.carry_in import DayLoad, LoadState, WeekCarryIn
from triplan.solver.safety_constraints import run_minutes as run_minutes_of

GLOBAL_ACWR_MAX = 1.30      # INVARIANTS: mechanical ACWR window [0.80, 1.30]
GLOBAL_ACWR_MIN = 0.80
MASTERS_ACWR_MAX = 1.15
MASTERS_CTL_RAMP = 5.0


def acwr_eps_floor(library: Iterable[Any], rho: float) -> float:
    """epsilon_floor (ADR-019): the value of ``A_d - rho * C_d`` after ONE smallest library run from a fully
    detrained state (A = C = 0), ``(ALPHA_ACUTE - rho * ALPHA_CHRONIC) * min_run_strain``. The ratio is undefined
    near zero chronic load, so a single floor-sized session must always be admissible (ADR-012: the 60 min/week
    absolute floor applies regardless of ACWR); above that the ceiling is the ratio ``rho``."""
    strains = [float(w.mechanical_strain) for w in library
               if getattr(w, "sport", "") == "Run" and float(w.mechanical_strain) > 0]
    return max(ALPHA_ACUTE - rho * ALPHA_CHRONIC, 0.0) * min(strains) if strains else 0.0


def load_state_for_week(state: AthletePhysiologicalState, profile: Optional[Any], prev_run_minutes: float,
                        library: Iterable[Any]) -> LoadState:
    """The EWMA constants and this week's limits (ADR-012: 1.20 while run CTL < 15, else 1.25)."""
    reentry = profile is not None and state.run_ctl < profile.reentry_run_ctl_exit
    rho = GLOBAL_ACWR_MAX
    ramp: Optional[float] = None
    run_cap: Optional[float] = None
    if profile is not None:
        rho = profile.max_running_acwr_reentry if reentry else profile.max_running_acwr
        ramp = float(profile.max_weekly_ctl_ramp)
        if reentry:
            run_cap = max(float(profile.reentry_run_minutes_floor),
                          prev_run_minutes + float(profile.max_weekly_run_minutes_increase))
    if state.masters_mode:
        rho = min(rho, MASTERS_ACWR_MAX)
        ramp = MASTERS_CTL_RAMP if ramp is None else min(ramp, MASTERS_CTL_RAMP)
    return LoadState(run_acute=state.running_acute_mech, run_chronic=state.running_chronic_mech,
                     total_ctl=state.total_ctl, run_ctl=state.run_ctl, acwr_max=rho, acwr_min=GLOBAL_ACWR_MIN,
                     acwr_eps=acwr_eps_floor(library, rho), ctl_ramp_max=ramp, run_minutes_cap=run_cap,
                     reentry=bool(reentry))


def initial_carry_in(state: AthletePhysiologicalState, profile: Optional[Any], library: Iterable[Any],
                     last_test_week: Optional[Dict[str, int]] = None) -> WeekCarryIn:
    """Week-1 carry-in: no boundary loads, EWMA state seeded from the profile (incl. run CTL)."""
    return WeekCarryIn(load=load_state_for_week(state, profile, 0.0, library),
                       last_test_week=dict(last_test_week or {}))


def next_week_carry_in(prev: WeekCarryIn, schedule: Dict[int, list], state: AthletePhysiologicalState,
                       profile: Optional[Any], library: Iterable[Any], week_tss: float) -> WeekCarryIn:
    """Carry-in for week k+1 from week k's solved ``schedule`` ({0..6: [workouts]}) and the already advanced state."""
    run_minutes = float(sum(run_minutes_of(w) for d in range(7) for w in schedule.get(d, [])))  # incl. combo run legs
    longest = max((run_minutes_of(w) for d in range(7) for w in schedule.get(d, [])), default=0.0)
    return WeekCarryIn(
        prev_longest_run_min=float(longest) if longest > 0 else prev.prev_longest_run_min,
        day_m1=DayLoad.from_workouts(schedule.get(6, [])),
        day_m2=DayLoad.from_workouts(schedule.get(5, [])),
        load=load_state_for_week(state, profile, run_minutes, library),
        prev_run_minutes=run_minutes, prev_week_tss=float(week_tss),
        last_test_week=prev.last_test_week,
    )
