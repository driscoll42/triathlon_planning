"""Unified load model (TASK-021, ADR-034): ANS cost, mechanical strain, strength TSS and exact Banister decay.

One definition per quantity, computed from zone minutes and never typed in. Pure functions with no repo imports so that
``library.build`` (library rows), ``model.classes`` / ``physiology.athlete_profile`` (zone tables) and
``physiology.state`` (PMC) all read the same constants.

* **ANS cost** (``SPEC.md`` §2): ``ANS_i = sum_z w_z t_z + gamma * max(0, D - D0)`` with the 7-zone weight vector
  ``w = (0, 0, 0.7, 1.0, 1.4, 1.8, 3.5)`` over (Z1, Z2, ZX, Z3, ZY, Z4, Z5), alactic strides (<= 20 s, Zone 5) exempt
  (weight 0), and a long-duration surcharge so a very long easy session is not free.
* **Mechanical strain**: zone-additive power model ``S = kappa * sum_z t_z p_z^beta`` with ``p_z`` the zone midpoint as a
  fraction of CP (run, bike) or CSS (swim). ``(kappa, beta)`` are chosen so a uniform-intensity session reproduces the
  pre-TASK-021 closed forms exactly (run ``TSS x IF``, bike ``0.10 TSS IF^0.2``, swim ``0.05 TSS``): the safety caps keep
  their meaning. Strength strain is set-intensity based: ``S = kappa_s sum_e c_e sets reps p_e^beta_s`` (``p_e`` = fraction of 1RM).
* **Strength TSS**: 10-20 aerobic TSS per hour (strength is not aerobic work); its real cost is strain / axial / neuro.
* **Banister PMC**: ``alpha = 1 - exp(-1 / tau)`` (exact one-day discretisation), ``tau = 42`` (CTL) and ``7`` (ATL).
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ZONES7: Tuple[str, ...] = ("Z1", "Z2", "ZX", "Z3", "ZY", "Z4", "Z5")

# ---------------------------------------------------------------------------------------------------------------------
# ANS cost
# ---------------------------------------------------------------------------------------------------------------------
ANS_WEIGHTS: Tuple[float, ...] = (0.0, 0.0, 0.7, 1.0, 1.4, 1.8, 3.5)   # SPEC.md §2 (ADR-009), order = ZONES7
ANS_DURATION_START_MIN = 90.0    # D0: below this a Z1/Z2 session carries no ANS cost
ANS_DURATION_SURCHARGE = 0.35    # gamma, ANS per minute beyond D0 (= half a Zone X minute; a calibration prior, EXP-006)


def to_zones7(z: Sequence[float]) -> List[float]:
    """Zone minutes as the 7-vector (Z1, Z2, ZX, Z3, ZY, Z4, Z5). A 5-vector is the legacy library layout
    (Z1, Z2, Z3, Z4, Z5); X / Y minutes are then 0."""
    z = [float(x or 0.0) for x in z]
    if len(z) == 7:
        return z
    if len(z) == 5:
        return [z[0], z[1], 0.0, z[2], 0.0, z[3], z[4]]
    raise ValueError(f"expected 5 or 7 zone minutes, got {len(z)}")


def ans_cost(z: Sequence[float], has_strides: bool = False, duration: Optional[float] = None) -> float:
    """Unified ANS cost of a session from its zone minutes (5- or 7-vector).

    ``has_strides``: the Zone 5 minutes are alactic (<= 20 s) strides, which carry no ANS cost (charter §2).
    ``duration``: total minutes (default ``sum(z)``) for the long-duration surcharge."""
    z7 = to_zones7(z)
    w = list(ANS_WEIGHTS)
    if has_strides:
        w[6] = 0.0
    total = duration if duration is not None else sum(z7)
    return float(sum(wk * tk for wk, tk in zip(w, z7))
                 + ANS_DURATION_SURCHARGE * max(0.0, float(total) - ANS_DURATION_START_MIN))


# ---------------------------------------------------------------------------------------------------------------------
# Zone midpoints (fraction of CP / CSS) on the 7-zone scale: Z1, Z2, ZX, Z3, ZY, Z4, Z5
# ---------------------------------------------------------------------------------------------------------------------
RUN_MID7: Tuple[float, ...] = (0.63, 0.82, 0.91, 0.97, 1.015, 1.115, 1.35)
BIKE_MID7: Tuple[float, ...] = (0.60, 0.765, 0.87, 0.955, 1.01, 1.06, 1.30)
SWIM_MID7: Tuple[float, ...] = (0.795, 0.875, 0.9275, 0.98, 1.01, 1.04, 1.10)

# ---------------------------------------------------------------------------------------------------------------------
# Mechanical strain  S = kappa * sum_z t_z p_z^beta   (minutes x midpoint^beta)
# ---------------------------------------------------------------------------------------------------------------------
# kappa is per minute so a uniform session of IF p lasting T min gives kappa T p^beta, which equals the closed forms the
# caps were tuned against:  run  (100/60) T IF^3 = TSS x IF;  bike (10/60) T IF^2.2 = 0.10 TSS IF^0.2;  swim (5/60) T IF^3 =
# 0.05 sTSS.  beta is a sensitivity-bounded prior (no injury-labelled data, EXP-006 go/no-go rule 3): beta = 3 keeps run
# intensity weighted as before; beta = 2 would LOOSEN every high-intensity run (Tier-0, charter §2), so it is not adopted.
STRAIN_PARAMS: Dict[str, Tuple[float, float]] = {
    "Run": (100.0 / 60.0, 3.0),
    "Bike": (10.0 / 60.0, 2.2),
    "Swim": (5.0 / 60.0, 3.0),
}
_MID7 = {"Run": RUN_MID7, "Bike": BIKE_MID7, "Swim": SWIM_MID7}


def zone_strain(sport: str, z: Sequence[float]) -> float:
    """Mechanical strain of an endurance session (or of one leg of a brick) from its zone minutes (5- or 7-vector)."""
    kappa, beta = STRAIN_PARAMS[sport]
    mid = _MID7[sport]
    return float(kappa * sum(t * p ** beta for t, p in zip(to_zones7(z), mid)))


def uniform_strain(sport: str, duration: float, intensity_factor: float) -> float:
    """Strain of a constant-intensity session (one zone at midpoint = IF); the closed form of ``zone_strain``."""
    kappa, beta = STRAIN_PARAMS[sport]
    return float(kappa * duration * intensity_factor ** beta)


def brick_strain(z_total: Sequence[float], z_run: Sequence[float]) -> float:
    """Whole-brick strain: the run leg on the run model plus the rest (bike, or swim + bike) on the bike model."""
    zt, zr = to_zones7(z_total), to_zones7(z_run)
    z_bike = [max(0.0, a - b) for a, b in zip(zt, zr)]
    return zone_strain("Run", zr) + zone_strain("Bike", z_bike)


# ---------------------------------------------------------------------------------------------------------------------
# Strength: aerobic TSS and set-intensity strain
# ---------------------------------------------------------------------------------------------------------------------
STRENGTH_TSS_PER_HOUR = (10.0, 20.0)   # (rest-dominated, continuous / plyometric)


def strength_tss(duration: float, z: Sequence[float]) -> float:
    """Aerobic TSS of a strength session: 10 TSS/h when all Zone 1, rising to 20 TSS/h when none of it is Zone 1."""
    z7 = to_zones7(z)
    tot = sum(z7)
    lo, hi = STRENGTH_TSS_PER_HOUR
    rate = lo + (hi - lo) * (1.0 - z7[0] / tot) if tot > 0 else lo
    return round(rate * duration / 60.0, 2)


def strength_intensity_factor(tss: float, duration: float) -> float:
    return round(math.sqrt(tss / (duration / 60.0 * 100.0)), 3) if tss > 0 and duration > 0 else 0.0


# Movement-class weights c_e (tissue demand per unit set-intensity work). Loaded lower-body lifts load tendon / bone;
# upper-body work and trunk work load them far less; plyometric contacts load tendon like heavy lower work.
STRENGTH_CLASS_WEIGHT: Dict[str, float] = {"lower": 1.0, "upper": 0.4, "core": 0.15, "plyo": 1.0}
# kappa_s anchors the model at the TASK-033 cap-compatible heavy session (STR_HV1_SQUAT: 3x3 @ 87.5% back squat, 2x5 @ 85%
# bench, 2x8 DB rows), whose strain 145.8 the engine and the daily / 48h caps were verified against.
STRENGTH_STRAIN_BETA = 4.0
STRENGTH_STRAIN_KAPPA = 16.348   # mean strain of the four cap-compatible heavy variants (HV1..HV4) = 145.8

# (class, sets, reps, fraction of 1RM). Per-leg reps are doubled; seconds-held work counts 1 rep per 10 s.
StrengthSet = Tuple[str, float, float, float]
STRENGTH_SET_SPECS: Dict[str, List[StrengthSet]] = {
    "STR_AA1": [("lower", 3, 17.5, 0.55), ("lower", 3, 17.5, 0.55), ("upper", 3, 17.5, 0.55), ("core", 3, 6, 0.5),
                ("core", 3, 6, 0.5)],
    "STR_MS1_HEAVY": [("lower", 3, 5, 0.85), ("lower", 2, 5, 0.85), ("upper", 3, 5, 0.85)],
    "STR_MS2_HEAVY": [("lower", 4, 4, 0.875), ("lower", 3, 6, 0.80), ("upper", 3, 5, 0.85)],
    "STR_SM1_MAINT": [("lower", 2, 5, 0.825), ("lower", 2, 5, 0.80), ("upper", 2, 5, 0.825), ("core", 3, 4.5, 0.5)],
    "STR_SM2_MAINT": [("lower", 2, 5, 0.85), ("upper", 2, 6, 0.60), ("lower", 2, 6, 0.60)],
    "STR_PLYO1": [("plyo", 4, 5, 0.70), ("plyo", 4, 10, 0.50), ("plyo", 4, 5, 0.65)],
    "STR_UPPER_HYP": [("upper", 3, 9, 0.70), ("upper", 3, 7, 0.70), ("upper", 3, 15, 0.30), ("upper", 3, 12, 0.30),
                      ("core", 3, 3, 0.5)],
    "STR_LOWER_UNI": [("lower", 3, 8, 0.65), ("lower", 3, 8, 0.60), ("lower", 2, 10, 0.55), ("lower", 3, 6, 0.50)],
    "STR_POST_CORE": [("lower", 3, 8, 0.75), ("lower", 3, 6, 0.70), ("core", 3, 10, 0.30), ("core", 3, 10, 0.40)],
    "STR_DELOAD": [("core", 3, 24, 0.10), ("lower", 2, 10, 0.30), ("core", 3, 16, 0.20)],
    "STR_MS1_HEAVY_DEF": [("lower", 4, 4, 0.85), ("lower", 2, 4, 0.80), ("lower", 2, 8, 0.60), ("plyo", 2, 5, 0.60)],
    "STR_SM1_CORE": [("lower", 2, 8, 0.60), ("core", 3, 10, 0.40), ("core", 3, 6, 0.50)],
    "UL_BB_LOWER_SQ": [("lower", 4, 5, 0.83), ("lower", 3, 10, 0.50)],
    "UL_BB_UPPER_ME": [("upper", 4, 5, 0.84), ("upper", 3, 5, 0.80), ("upper", 3, 8, 0.70), ("upper", 3, 10, 0.60)],
    "UL_BB_POST_DL": [("lower", 1, 5, 0.88), ("lower", 3, 6, 0.70), ("lower", 3, 8, 0.50), ("upper", 3, 10, 0.30)],
    "HLM_BB_VOL": [("lower", 5, 5, 0.76), ("upper", 5, 5, 0.76), ("upper", 3, 10, 0.60)],
    "HLM_LGT_DB_DE": [("lower", 6, 2, 0.60), ("upper", 3, 5, 0.80), ("lower", 3, 10, 0.50), ("upper", 3, 10, 0.60)],
    "HLM_BB_INT": [("lower", 1, 5, 0.90), ("lower", 1, 5, 0.88), ("upper", 3, 6, 0.70), ("lower", 3, 8, 0.50)],
    "STR_HV1_SQUAT": [("lower", 3, 3, 0.875), ("upper", 2, 5, 0.85), ("upper", 2, 8, 0.60)],
    "STR_HV2_DEADLIFT": [("lower", 3, 3, 0.875), ("upper", 2, 5, 0.85), ("upper", 2, 6, 0.70)],
    "STR_HV3_SQUAT_SL": [("lower", 2, 4, 0.875), ("upper", 3, 5, 0.85), ("lower", 3, 8, 0.55)],
    "STR_HV4_DEADLIFT_SL": [("lower", 2, 4, 0.875), ("upper", 3, 5, 0.85), ("lower", 3, 6, 0.50)],
    "STR_PREHAB_25": [("lower", 3, 12, 0.50), ("lower", 2, 5, 0.60), ("core", 2, 12, 0.30), ("core", 2, 15, 0.20)],
}


def set_work(specs: Iterable[StrengthSet]) -> float:
    """``sum_e c_e sets reps p^beta_s`` (class-weighted set-intensity work, per limb: ``reps`` are per leg / arm)."""
    return float(sum(STRENGTH_CLASS_WEIGHT[k] * s * r * p ** STRENGTH_STRAIN_BETA for k, s, r, p in specs))


def strength_strain(code_or_specs) -> float:
    """Strength mechanical strain from a spec code in ``STRENGTH_SET_SPECS`` (or an explicit list of sets)."""
    specs = STRENGTH_SET_SPECS[code_or_specs] if isinstance(code_or_specs, str) else code_or_specs
    return round(STRENGTH_STRAIN_KAPPA * set_work(specs), 1)


# ---------------------------------------------------------------------------------------------------------------------
# Banister PMC
# ---------------------------------------------------------------------------------------------------------------------
TAU_CTL_DAYS = 42.0
TAU_ATL_DAYS = 7.0


def banister_alpha(tau_days: float) -> float:
    """Exact one-day decay ``alpha = 1 - exp(-1/tau)`` of ``X_t = X_{t-1} e^{-1/tau} + load_t (1 - e^{-1/tau})``."""
    return 1.0 - math.exp(-1.0 / float(tau_days))
