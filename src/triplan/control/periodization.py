"""Season periodization: 3:1 mesocycle matrices, re-entry block and CP/CSS test lockouts (TASK-026).

Pure functions, no solver imports. ``generate_closed_loop_season_schedule`` consumes the matrix
returned by :func:`build_season_matrix` one :class:`SeasonWeek` at a time.

Rules encoded here:
  * ADR-012 DECISION-010: mesocycles are ``build_weeks`` build + ``recovery_weeks`` recovery (3:1).
  * ADR-012 DECISION-009: recovery, taper and race weeks keep maintenance strength (``req_strength == 0``
    means "no HEAVY lifts"; the caller applies ``profile.recovery_week_strength_sessions``).
  * ADR-011: no all-out CP/CSS tests during initial re-entry (bike <= 4 weeks, run <= 6 weeks), none in
    week 1, none in taper/race weeks, tests otherwise only in recovery (deload) weeks, and at least
    ``min_gap_weeks`` between tests of the same sport. ADR-012 DECISION-005 adds the first bike test at
    the start of week 5 (the first week after the bike lockout).
"""

from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from triplan.model.phase import Phase

# Default race-block length (weeks) per distance, when the caller does not give ``weeks_to_race``.
DEFAULT_RACE_WEEKS = {"sprint": 12, "olympic": 16, "70.3": 20, "ironman": 24}
_TAPER_WEEKS = {"sprint": 1, "olympic": 1, "70.3": 2, "ironman": 2}
_PEAK_FACTOR = {"sprint": 1.32, "olympic": 1.50, "70.3": 1.58, "ironman": 1.62}
_PEAK_DURABILITY = {"sprint": 30.0, "olympic": 40.0, "70.3": 45.0, "ironman": 55.0}
_PHASE_NAMES = {
    "sprint": ["General Foundation", "Aerobic Capacity", "Specific Speed", "Race Simulation"],
    "olympic": ["General Foundation", "Aerobic Capacity", "Specific Threshold", "Race Specific"],
    "70.3": ["Base 1 (Aerobic)", "Base 2 (Foundation)", "Muscular Endurance", "Specific Threshold",
             "Durability / Bricks"],
    "ironman": ["Base 1 (Foundation)", "Base 2 (Aerobic)", "Muscular Endurance", "Tempo & Threshold",
                "Durability Block", "Peak Ironman Volume"],
}
_REENTRY_PHASES = ["Re-Entry Foundation", "Re-Entry Aerobic Base", "Re-Entry General Prep"]
REENTRY_START_FACTOR = 0.60   # of base_tss; re-entry ramps to 1.00 so the race block continues smoothly
RECOVERY_RATIO = 0.70         # recovery week TSS = 70% of the preceding build week

DELOAD_ROLES = (Phase.RECOVERY, Phase.TAPER, Phase.RACE)
DELOAD_MAX_REST_DAYS = 4      # a deload week may hold up to 4 rest days (>= 3 training days)


def apply_deload_relief(cfg, role: str) -> bool:
    """TASK-034: scale the full-week structure minimums down in recovery / taper / race weeks.

    The week structure minimums (>= 2 key sessions, >= 2 stride sessions, >= 2 Bike / Run sessions, at most 2
    rest days, i.e. >= 5 training days) were each a Tier-1 slack that costs more than the Tier-2 TSS goal, so
    together they set a ~160 TSS floor and a deload target of 72-112 TSS solved at 126-146 (the TSS goal is
    soft). In deload roles each minimum (strides, Bike / Run frequency) is halved (rounded up, never below 1), the
    key-session minimum is 0 (TASK-038) and the rest-day maximum is raised to :data:`DELOAD_MAX_REST_DAYS`. Tier-0 caps, maxima and the profile are untouched; the profile's
    own full-week values apply in every other role. Returns True when ``cfg`` was changed.
    """
    if role not in DELOAD_ROLES:
        return False
    klo, khi = cfg.key_sessions_per_week
    # TASK-038: no key-session minimum in any deload role (a recovery week is not built around a hard session; in
    # race week the race is the key session). A key minimum of even 1 forced ~160-170 TSS for a 72-141 target.
    cfg.key_sessions_per_week = (0, khi)
    cfg.min_stride_sessions = min(cfg.min_stride_sessions, max(1, -(-cfg.min_stride_sessions // 2)))
    # TASK-038: Swim joins Bike / Run (the reference profile's swim minimum of 2 held its race week 48 TSS over target).
    cfg.sport_freq_bounds = {sp: ((min(lo, max(1, -(-lo // 2))) if sp in ("Bike", "Run", "Swim") else lo), hi)
                             for sp, (lo, hi) in cfg.sport_freq_bounds.items()}
    cfg.max_rest_days = max(cfg.max_rest_days, DELOAD_MAX_REST_DAYS)
    return True


# TASK-037: re-entry weeks whose TSS target sits below the TSS floor of the full-week structure minimums.
REENTRY_RELIEF_ROLES = (Phase.BASE, Phase.BUILD, Phase.OVERLOAD)
STRUCTURE_OVERSHOOT_TOL = 1.15    # solved / target above this triggers the next relief level
REENTRY_RELIEF_LEVELS = (1, 2)


def apply_reentry_relief(cfg, level: int) -> str:
    """TASK-037: scale the full-week structure minimums down for one re-entry build week; returns a report.

    The structure minimums (>= 2 key, >= 2 stride sessions, >= 2 Bike / Run sessions, <= 2 rest days) are Tier-1
    slacks solved BEFORE the soft Tier-2 TSS goal, so together they set a TSS floor (~160-215 for
    CURRENT_ATHLETE) that a re-entry target of 96-150 can never reach. Level 1 is the TASK-034 deload relief
    (each minimum halved, rounded up, never below 1; rest days up to :data:`DELOAD_MAX_REST_DAYS`). Level 2
    additionally drops the key-session and stride minimums to 0, as the profile's own rule says ("0-1 key
    sessions during the first 4 re-entry weeks"). Maxima, Strength frequency, heavy-strength count and every
    Tier-0 cap are untouched. The caller escalates a level only while the solved week overshoots its target.
    """
    before = (cfg.key_sessions_per_week[0], cfg.min_stride_sessions,
              cfg.sport_freq_bounds.get("Bike", (0, 0))[0], cfg.sport_freq_bounds.get("Run", (0, 0))[0], cfg.max_rest_days)
    klo, khi = cfg.key_sessions_per_week
    cfg.key_sessions_per_week = (min(klo, max(1, -(-klo // 2))), khi)
    cfg.min_stride_sessions = min(cfg.min_stride_sessions, max(1, -(-cfg.min_stride_sessions // 2)))
    cfg.sport_freq_bounds = {sp: ((min(lo, max(1, -(-lo // 2))) if sp in ("Bike", "Run") else lo), hi)
                             for sp, (lo, hi) in cfg.sport_freq_bounds.items()}
    cfg.max_rest_days = max(cfg.max_rest_days, DELOAD_MAX_REST_DAYS)
    if level >= 2:
        cfg.key_sessions_per_week = (0, khi)
        cfg.min_stride_sessions = 0
    after = (cfg.key_sessions_per_week[0], cfg.min_stride_sessions,
             cfg.sport_freq_bounds.get("Bike", (0, 0))[0], cfg.sport_freq_bounds.get("Run", (0, 0))[0], cfg.max_rest_days)
    return (f"STRUCTURE_RELIEF_L{level} (key {before[0]}->{after[0]}, strides {before[1]}->{after[1]}, "
            f"Bike/Run min {before[2]}/{before[3]}->{after[2]}/{after[3]}, max rest days {before[4]}->{after[4]})")


# TASK-041 (ADR-025): race-block build weeks whose TSS target sits below the structural floor of the full-week structure.
FLOOR_RAISE_ROLES = (Phase.BASE, Phase.BUILD, Phase.OVERLOAD)
FLOOR_PROBE_TARGET = 1.0     # a ~0 TSS goal makes the lexicographic solve return the cheapest plan meeting Tier-0 + Tier-1


def raised_target(matrix_target: float, floor: Optional[float]) -> float:
    """TASK-041: the week's TSS target once the structural floor is honoured: ``max(matrix target, floor)``."""
    return float(matrix_target) if floor is None else float(max(matrix_target, round(floor)))


def relax_deload_acwr_floor(carry, role: str):
    """TASK-038: no running-ACWR *floor* in deload roles; returns the carry-in to solve the week with.

    ``load_dynamics`` models ``A_d >= 0.80 * C_d`` as a binary Tier-1 waiver of weight 1. Tier-1 is minimised
    before the Tier-2 TSS goal, so whenever any plan can hold the floor the solver pays for it in extra running
    even though a recovery / taper / race week is *defined* as acute load below chronic load (reference profile,
    recovery weeks: +89 TSS over target with the floor, on target without; ``nocarry`` / ``nofloor`` ablations).
    Only ``acwr_min`` is set to 0 (no floor row, no waiver); the ACWR ceiling, boundary rows, CTL ramp and run-minute
    guard are untouched, and the carry handed to the next week is the original one.
    """
    if role not in DELOAD_ROLES or carry is None or getattr(carry, "load", None) is None:
        return carry
    return replace(carry, load=replace(carry.load, acwr_min=0.0))


# Longest run / ride of the library before TASK-040 (the 75 min RUN_AE2_LONG anchor, the 180 min BIKE_DUR_OUT_DEF anchor).
# A session longer than this is a long-course session.
LEGACY_MAX_SESSION_MIN = {"Run": 75.0, "Bike": 180.0}


def drop_unhostable_long_sessions(library, daily_time_caps):
    """TASK-040: drop long-course sessions (run > 75 min, ride > 180 min) that no daily time cap can host.

    A session longer than the biggest day cap can never be scheduled (Tier-0 time gate), so it is dead weight in the
    MILP and only changes HiGHS tie-breaking: a profile with 45-75 min/day caps (``CURRENT_ATHLETE``) solves exactly
    the candidate set it solved before the long-course rows existed. Anchors are never dropped here (their Tier-1
    "dropped anchor" relaxation is the diagnosis the season report rolls up); shorter, long-standing candidates are
    untouched even when they exceed the caps.
    """
    cap = max((float(c) for c in daily_time_caps), default=0.0)
    return [w for w in library
            if getattr(w, "is_anchor", False) or float(w.duration) <= cap
            or float(w.duration) <= LEGACY_MAX_SESSION_MIN.get(w.sport, float("inf"))]


def relax_deload_anchors(library, role: str):
    """TASK-038: in deload roles the mandatory weekly keystone sessions (``is_anchor``: the long ride / long run)
    become ordinary optional candidates, so the Tier-1 "keep the anchor" slack no longer forces them.

    On a profile whose caps *can* host the anchors (the unconstrained reference profile) a 180 min ride and a 75 min
    run every week put the recovery / taper floor at 350-400 TSS against a 186-220 target. The workouts stay in
    the library (the solver may still pick them); only the obligation goes. Other roles get the library back as is.
    """
    if role not in DELOAD_ROLES:
        return library
    return [replace(w, is_anchor=False) if getattr(w, "is_anchor", False) else w for w in library]


# TASK-055 (ADR-028): alternating keystones in long-course race-block build / overload weeks.
# Heavy lifts + the 180 min ride anchor + the 75 min run anchor + the run-share caps cannot share one calendar
# (EXP-007 section 10), so the weekly keystone alternates: a long-run week (run anchor mandatory, ride anchor optional)
# then a long-ride week (ride anchor mandatory, run anchor optional). Deload roles keep ADR-023 (anchors optional).
KEYSTONE_DISTANCES = ("70.3", "ironman")
KEYSTONE_ROLES = (Phase.BUILD, Phase.OVERLOAD)
# Keystone long-run minutes per long-run week (index = how many run-keystone weeks came before); clamped to the last rung,
# to the library rungs the day caps can host and to one library rung above the last long run actually scheduled.
# A Tier-2 split shift toward running (Run TSS 40%, duration +8 points) was tried and dropped: it did not make a single
# extra keystone feasible and cost Ironman build weeks up to 240 TSS against target (ADR-028).
KEYSTONE_RUN_LADDER = {"70.3": (75.0, 90.0, 90.0, 105.0, 105.0, 120.0),
                       "ironman": (75.0, 90.0, 105.0, 120.0, 120.0, 150.0)}
KEYSTONE_RUN_CODES = frozenset({"RUN_AE2_LONG", "RUN_LC_Z2_90", "RUN_LC_Z2_105", "RUN_LC_Z2_120", "RUN_LC_Z2_150"})


def _is_keystone_run(w, has_canonical: bool) -> bool:
    if has_canonical:
        return getattr(w, "code", "") in KEYSTONE_RUN_CODES
    return w.sport == "Run" and bool((getattr(w, "flags", None) or {}).get("long_run", 0))


# TASK-063 (ADR-028): the long-ride keystone grows like the run one. Rungs are the TASK-040 long-course outdoor Z1/Z2
# rides (BIKE_LC_OUT_*); 180 min is the BIKE_DUR_OUT_DEF anchor (the rung every season starts on and the fallback when
# the day caps host no longer ride). Clamped to hostable rungs and one library rung above the last longest ride.
KEYSTONE_RIDE_BASE_MIN = 180.0
KEYSTONE_RUN_BASE_MIN = 75.0
KEYSTONE_RIDE_LADDER = {"70.3": (180.0, 210.0, 210.0, 240.0, 240.0, 300.0),
                        "ironman": (180.0, 210.0, 240.0, 300.0, 300.0, 360.0)}
KEYSTONE_RIDE_CODES = frozenset({"BIKE_DUR_OUT_DEF", "BIKE_LC_OUT_210", "BIKE_LC_OUT_240", "BIKE_LC_OUT_300",
                                 "BIKE_LC_OUT_360"})


def _is_keystone_ride(w, has_canonical: bool) -> bool:
    if has_canonical:
        return getattr(w, "code", "") in KEYSTONE_RIDE_CODES
    return w.sport == "Bike" and (
        bool(getattr(w, "is_anchor", False))
        or (bool((getattr(w, "flags", None) or {}).get("outdoor_bike", 0)) and float(w.duration) >= KEYSTONE_RIDE_BASE_MIN - 1e-6))


def keystone_kind(distance: str, role: str, block: str, ordinal: int) -> Optional[str]:
    """TASK-055: ``"run"`` / ``"ride"`` keystone for the ``ordinal``-th (0-based) race-block build / overload week of a
    70.3 / Ironman season, alternating run, ride, run, ...; ``None`` for every other week (sprint / olympic, re-entry
    block, base and deload roles are untouched)."""
    if block != "race" or role not in KEYSTONE_ROLES or normalize_distance(distance) not in KEYSTONE_DISTANCES:
        return None
    return "run" if ordinal % 2 == 0 else "ride"


def keystone_run_rungs(library, daily_time_caps) -> list:
    """TASK-055: the long-run durations (canonical keystone rows or ``long_run`` rows) the day caps can host, ascending."""
    lib_list = list(library)
    has_canonical = any(getattr(w, "code", "") in KEYSTONE_RUN_CODES for w in lib_list)
    cap = max((float(c) for c in daily_time_caps), default=0.0)
    return sorted({float(w.duration) for w in lib_list
                   if _is_keystone_run(w, has_canonical) and float(w.duration) <= cap})


def keystone_run_minutes(distance: str, run_ordinal: int, last_longest_run: float, library, daily_time_caps) -> float:
    """TASK-055: duration of the long-run keystone: the ladder rung for this distance and week, capped to the library's
    long-run rungs (``long_run`` flag) a day cap can host, and to at most one library rung above ``last_longest_run``
    (a progression in the spirit of the ADR-022 ``prev longest + 10`` cap, never a jump)."""
    rungs = keystone_run_rungs(library, daily_time_caps)
    if not rungs:
        return 0.0
    ladder = KEYSTONE_RUN_LADDER[normalize_distance(distance)]
    want = ladder[min(run_ordinal, len(ladder) - 1)]
    above = [r for r in rungs if r > last_longest_run]
    ceiling = above[0] if above else rungs[-1]
    ok = [r for r in rungs if r <= min(want, ceiling)]
    return ok[-1] if ok else rungs[0]


def keystone_ride_rungs(library, daily_time_caps) -> list:
    """TASK-063: the long-ride durations (canonical keystone rides or the Bike anchor / long outdoor rows) the day caps
    can host, ascending."""
    lib_list = list(library)
    has_canonical = any(getattr(w, "code", "") in KEYSTONE_RIDE_CODES for w in lib_list)
    cap = max((float(c) for c in daily_time_caps), default=0.0)
    return sorted({float(w.duration) for w in lib_list
                   if _is_keystone_ride(w, has_canonical) and float(w.duration) <= cap})


def keystone_ride_minutes(distance: str, ride_ordinal: int, last_longest_ride: float, library, daily_time_caps) -> float:
    """TASK-063: duration of the long-ride keystone: the ladder rung for this distance and ride-week index, clamped to the
    hostable library rungs and to at most one library rung above ``last_longest_ride`` (a progression, never a jump).
    When the day caps host no keystone ride at all, the 180 min anchor is returned unchanged so the Tier-1
    "dropped anchor" diagnosis keeps firing exactly as before."""
    rungs = keystone_ride_rungs(library, daily_time_caps)
    if not rungs:
        return KEYSTONE_RIDE_BASE_MIN
    ladder = KEYSTONE_RIDE_LADDER[normalize_distance(distance)]
    want = ladder[min(ride_ordinal, len(ladder) - 1)]
    above = [r for r in rungs if r > last_longest_ride]
    ceiling = above[0] if above else rungs[-1]
    ok = [r for r in rungs if r <= min(want, ceiling)]
    return ok[-1] if ok else rungs[0]


def apply_keystone_week(library, kind: str, run_minutes: float, ride_minutes: float = KEYSTONE_RIDE_BASE_MIN):
    """TASK-055: keystone anchors of one week. ``run``: only the long-run keystone (``RUN_AE2_LONG`` or the long-course run
    of ``run_minutes``) is an anchor, the ride anchor becomes optional. ``ride``: only the ride anchor stays mandatory
    (``BIKE_DUR_OUT_DEF`` or the long-course ride of ``ride_minutes``), the run anchor becomes optional. Every workout stays a candidate; only the Tier-1 obligation moves."""
    lib_list = list(library)
    has_canonical = any(getattr(w, "code", "") in KEYSTONE_RUN_CODES for w in lib_list)
    has_canonical_ride = any(getattr(w, "code", "") in KEYSTONE_RIDE_CODES for w in lib_list)
    # TASK-063: ``ride`` weeks anchor the keystone ride of ``ride_minutes``; a library with no ride of that length keeps
    # its existing Bike anchors (the pre-TASK-063 behavior).
    ride_rows = [w for w in lib_list if _is_keystone_ride(w, has_canonical_ride)]
    ride_swap = kind == "ride" and any(abs(float(w.duration) - ride_minutes) < 1e-6 for w in ride_rows)
    out = []
    run_anchor_set = False
    ride_anchor_set = False
    for w in lib_list:
        anchor = bool(getattr(w, "is_anchor", False))
        if kind == "run" and _is_keystone_run(w, has_canonical):
            anchor = (not run_anchor_set) and abs(float(w.duration) - run_minutes) < 1e-6
            if anchor:
                run_anchor_set = True
        elif kind == "run" and w.sport == "Run" and (getattr(w, "flags", None) or {}).get("long_run", 0):
            anchor = False
        elif kind == "run" and w.sport == "Bike":
            anchor = False
        elif kind == "ride" and w.sport == "Run":
            anchor = False
        elif ride_swap and w.sport == "Bike":
            if _is_keystone_ride(w, has_canonical_ride):
                anchor = (not ride_anchor_set) and abs(float(w.duration) - ride_minutes) < 1e-6
                if anchor:
                    ride_anchor_set = True
            else:
                anchor = False
        out.append(w if anchor == bool(getattr(w, "is_anchor", False)) else replace(w, is_anchor=anchor))
    return out


def apply_keystone_run_support(cfg: Any, keystone: Optional[str], ks_run: float) -> None:
    """TASK-062: allow up to 6 run sessions in 90+ min long-run keystone weeks when >= 5 two-session windows exist,
    so 5 standalone runs + 1 short easy run (or lift+run combo) can satisfy the 32% single-run volume ceiling
    (ADR-003) without exceeding the weekly run mechanical strain cap."""
    if keystone != "run" or float(ks_run) < 90.0 - 1e-9:
        return
    bounds = dict(getattr(cfg, "sport_freq_bounds", {}) or {})
    run_lo, run_hi = bounds.get("Run", (0, 0))
    windows = list(getattr(cfg, "two_session_windows", []) or [])
    if run_hi == 5 and sum(1 for w in windows if w) >= 5:
        bounds["Run"] = (run_lo, 6)
        cfg.sport_freq_bounds = bounds


PROGRESSION_GUARD_WINDOW = 3  # spans 1 recovery week + 1 alternating ride-keystone week (TASK-061)


def progression_guard_bounds(
    solved_rows: Sequence[Dict[str, Any]],
    max_ramp: float,
    window: int = PROGRESSION_GUARD_WINDOW,
    min_strain_floor: float = 150.0,
) -> Optional[Tuple[float, float]]:
    """Return ``(max_target_tss, max_weekly_run_mech_strain)`` from the best of the last ``window`` solved weeks."""
    recent = [r for r in solved_rows if r.get("Solved_TSS", 0.0) > 0][-max(1, int(window)):]
    if not recent:
        return None
    ref_tss = max(float(r["Solved_TSS"]) for r in recent)
    ref_mech = max(float(r.get("Total_Mech_Strain", 0.0)) for r in recent)
    return round(ref_tss * max_ramp, 0), round(max(ref_mech * max_ramp, min_strain_floor), 1)


@dataclass(frozen=True)
class SeasonWeek:
    week_num: int
    phase_name: str
    step_type: str
    tss_factor: float
    durability_w: float
    req_strength: int        # heavy-strength sessions requested by the phase (before profile caps)
    role: Phase              # BASE | BUILD | OVERLOAD | RECOVERY | TAPER | RACE (StrEnum: equals "base", ...)
    block: str               # "reentry" | "race"
    block_week: int          # 1-based week inside its block

    @property
    def is_recovery(self) -> bool:
        return self.role is Phase.RECOVERY

    @property
    def phase(self) -> Phase:
        return self.role

    @property
    def config_phase(self) -> Phase:
        """The ``Phase`` handed to ``profile.to_mesocycle_config`` (TASK-023: an enum, not a keyword string)."""
        return self.role

    def as_tuple(self):
        return (self.week_num, self.phase_name, self.step_type, self.tss_factor, self.durability_w,
                self.req_strength)


def normalize_distance(distance: str) -> str:
    d = distance.lower().strip()
    if "sprint" in d:
        return "sprint"
    if "70.3" in d or "half" in d:
        return "70.3"
    if "ironman" in d or "140.6" in d:
        return "ironman"
    return "olympic"


def _spread(idx: int, n_items: int, n_slots: int) -> int:
    return idx if n_slots <= 1 or n_items <= 1 else min(n_items - 1, round(idx * (n_items - 1) / (n_slots - 1)))


def _block_weeks(n_weeks: int, build: int, recovery: int, names: List[str], first_factor: float,
                 last_factor: float, dur_start: float, dur_end: float, block: str,
                 start_week: int) -> List[SeasonWeek]:
    period = build + recovery
    kinds = ["build" if (i % period) < build else "recovery" for i in range(n_weeks)]
    n_build = kinds.count("build")
    n_meso = -(-n_weeks // period)
    out: List[SeasonWeek] = []
    j = 0  # build-week counter
    prev_build_factor = first_factor
    for i, kind in enumerate(kinds):
        meso, k = divmod(i, period)
        phase = names[_spread(meso, len(names), n_meso)]
        if kind == "recovery":
            out.append(SeasonWeek(start_week + i, phase, "Recovery Deload (-30%)",
                                  round(prev_build_factor * RECOVERY_RATIO, 3), 0.0, 0, Phase.RECOVERY, block, i + 1))
            continue
        frac = j / (n_build - 1) if n_build > 1 else 1.0
        factor = round(first_factor + (last_factor - first_factor) * frac, 3)
        prev_build_factor = factor
        dur = round(dur_start + (dur_end - dur_start) * frac, 1)
        last_in_cycle = i + 1 >= n_weeks or kinds[i + 1] == "recovery"
        if k == 0 and meso == 0:
            role, step, heavy = Phase.BASE, "Base Load", 2   # base-phase default; the profile may cap it
        elif k == 0:
            role, step, heavy = Phase.BUILD, "Base Load", 1
        elif last_in_cycle:
            role, step, heavy = Phase.OVERLOAD, "Overload (+10%)", 1
        else:
            role, step, heavy = Phase.BUILD, "Build (+10%)", 1
        out.append(SeasonWeek(start_week + i, phase, step, factor, dur, heavy, role, block, i + 1))
        j += 1
    return out


def build_season_matrix(
    distance: str = "olympic",
    weeks_to_race: Optional[int] = None,
    build_weeks: int = 3,
    recovery_weeks: int = 1,
    reentry_weeks: int = 0,
) -> List[SeasonWeek]:
    """3:1 (profile-driven) season matrix: optional re-entry block, then the race block.

    Race block = 3:1 cycles + taper week(s) + race week. Re-entry block = 3:1 cycles ramping the TSS
    factor from REENTRY_START_FACTOR to 1.0 (strength + bike + run; the caller removes swim and caps
    heavy lifting per ADR-012 DECISION-023).
    """
    if build_weeks < 1 or recovery_weeks < 0:
        raise ValueError("build_weeks must be >= 1 and recovery_weeks >= 0")
    dist = normalize_distance(distance)
    total = int(weeks_to_race) if weeks_to_race else DEFAULT_RACE_WEEKS[dist]
    n_taper = _TAPER_WEEKS[dist]
    if total < n_taper + 2:
        raise ValueError(f"weeks_to_race={total} too short for {dist} (needs >= {n_taper + 2})")

    weeks: List[SeasonWeek] = []
    if reentry_weeks > 0:
        weeks += _block_weeks(reentry_weeks, build_weeks, recovery_weeks, _REENTRY_PHASES,
                              REENTRY_START_FACTOR, 1.0, 5.0, 5.0, "reentry", 1)

    start = len(weeks) + 1
    n_cycle = total - n_taper - 1
    race = _block_weeks(n_cycle, build_weeks, recovery_weeks, _PHASE_NAMES[dist], 1.0, _PEAK_FACTOR[dist],
                        10.0, _PEAK_DURABILITY[dist], "race", start)
    weeks += race
    peak = max(w.tss_factor for w in race)
    taper_f = [0.60, 0.50][:n_taper]
    for t in range(n_taper):
        weeks.append(SeasonWeek(start + n_cycle + t, "Taper & Sharpen",
                                f"Taper {t + 1} (-{round((1 - taper_f[t]) * 100)}%)",
                                round(peak * taper_f[t], 3), 10.0, 0, Phase.TAPER, "race", n_cycle + t + 1))
    weeks.append(SeasonWeek(start + total - 1, "Race Week", "Race Week", round(peak * 0.38, 3), 5.0, 0,
                            Phase.RACE, "race", total))
    return weeks


# ---------------------------------------------------------------------------
# TTE progression mode and the walk-jog re-entry block (TASK-023, ADR-036)
# ---------------------------------------------------------------------------
def tte_mode_for(role: Phase) -> str:
    """How the week's threshold-minute goal (``T^thr_w``) is posed: build / base / overload weeks PROGRESS
    (``T >= (1 + g) T_ref``), a recovery week CAPS (``T <= 0.6 T_{w-1}``), taper and race weeks leave it off (the
    taper keeps its sharpening intensity; ``ADR-023`` deload relief owns those weeks)."""
    role = Phase.parse(role)
    if role in (Phase.BASE, Phase.BUILD, Phase.OVERLOAD):
        return "progress"
    if role is Phase.RECOVERY:
        return "deload"
    return "off"


@dataclass(frozen=True)
class WalkJogStage:
    """One rung of the run/walk ladder: ``run_s`` running then ``walk_s`` walking, repeated, for ``session_min``."""
    stage: int
    run_s: int
    walk_s: int
    session_min: int

    @property
    def label(self) -> str:
        return f"{self.run_s}s:{self.walk_s}s x {self.session_min}min"

    @property
    def run_fraction(self) -> float:
        return self.run_s / (self.run_s + self.walk_s)

    def split_minutes(self, minutes: Optional[float] = None) -> Tuple[float, float]:
        """(run minutes, walk minutes) of a ``minutes`` long session at this rung's ratio."""
        m = float(self.session_min if minutes is None else minutes)
        return m * self.run_fraction, m * (1.0 - self.run_fraction)


# Return-to-run ladder for a detrained athlete (run CTL below the re-entry exit): 1:1 -> 2:1 -> 3:1 -> 4:1, session
# length growing 20 -> 35 min. One rung per BUILD week; recovery / taper / race weeks hold the rung. The ladder is
# the progression RULE; the run/walk library rows that realise it are TASK-058 (they take their load from
# ``physiology.load_model.run_walk_mechanical_strain``).
WALKJOG_LADDER: Tuple[WalkJogStage, ...] = (
    WalkJogStage(1, 60, 60, 20),
    WalkJogStage(2, 120, 60, 25),
    WalkJogStage(3, 180, 60, 30),
    WalkJogStage(4, 240, 60, 35),
)


def next_walkjog_stage(prev_stage: int, role: Phase, run_reentry: bool) -> int:
    """Walk-jog rung of the coming week: 0 when the athlete is not re-entering (run CTL >= the exit); the first rung
    on entry; one rung up per build / base / overload week (capped at the top of the ladder); held in deload weeks."""
    if not run_reentry:
        return 0
    role = Phase.parse(role)
    if prev_stage <= 0:
        return 1
    if role.is_deload:
        return prev_stage
    return min(prev_stage + 1, len(WALKJOG_LADDER))


def walkjog_stage(stage: int) -> Optional[WalkJogStage]:
    return WALKJOG_LADDER[stage - 1] if 1 <= stage <= len(WALKJOG_LADDER) else None


def filter_walkjog_workouts(workouts: Sequence[Any], stage: int) -> List[Any]:
    """Gate the ``RUN_RW_*`` run/walk rows (TASK-058) by the week's ladder rung. Only REMOVES candidates, so no solver row
    or bound changes. Rung 0 (the athlete is not re-entering: run CTL >= the exit) offers none, since a continuous run is
    then allowed; rung ``s`` offers a row only when its run share <= the rung's and its session is no longer than the
    rung's ``session_min``, so the ladder's 1:1 -> 4:1, 20 -> 35 min progression is enforced rather than merely reported."""
    from triplan.library.build import run_walk_split   # lazy: library.build must not be a control import-time dependency
    rung = walkjog_stage(stage)
    out: List[Any] = []
    for w in workouts:
        split = run_walk_split(str(getattr(w, "code", "")))
        if split is None:
            out.append(w)
        elif rung is not None:
            run_min, walk_min = split
            if run_min / (run_min + walk_min) <= rung.run_fraction + 1e-9 and run_min + walk_min <= rung.session_min + 1e-9:
                out.append(w)
    return out


# ---------------------------------------------------------------------------
# ADR-011 test lockouts
# ---------------------------------------------------------------------------
def is_cp_test_allowed(
    sport: str,
    week: SeasonWeek,
    last_test_week: Dict[str, int],
    *,
    reentry_weeks: int = 0,
    bike_lockout_weeks: int = 4,
    run_lockout_weeks: int = 6,
    min_gap_weeks: int = 8,
) -> bool:
    """May an all-out CP/CSS test of ``sport`` be scheduled in ``week``? (ADR-011 / ADR-012 DEC-005)."""
    n = week.week_num
    if n <= 1 or week.role in (Phase.TAPER, Phase.RACE):
        return False
    lockout = 0
    if reentry_weeks > 0:
        lockout = {"Bike": bike_lockout_weeks, "Run": run_lockout_weeks}.get(sport, 0)
    if n <= lockout:
        return False
    last = last_test_week.get(sport)
    if last is not None and n - last < min_gap_weeks:
        return False
    first_bike = reentry_weeks > 0 and sport == "Bike" and n == bike_lockout_weeks + 1  # DEC-005
    return week.is_recovery or first_bike


def filter_test_workouts(library: Iterable, week: SeasonWeek, last_test_week: Dict[str, int], **kw) -> list:
    """Pre-filter the candidate library: drop test workouts the lockout rules forbid this week."""
    return [w for w in library
            if not (w.flags.get("test") and not is_cp_test_allowed(w.sport, week, last_test_week, **kw))]
