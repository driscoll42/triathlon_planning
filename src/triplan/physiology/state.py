"""Running exponential-convolution physiological state (CTL/ATL, run-strain ODI, mechanical ACWR)."""

from dataclasses import dataclass
import logging
from typing import Optional

from triplan.physiology.load_model import TAU_ATL_DAYS, TAU_CTL_DAYS, banister_alpha


logger = logging.getLogger(__name__)

# Smoothing constants, the single source for ``step_day`` AND the in-model linearised ACWR / CTL-ramp rows
# (``solver.engine.builders.load_dynamics``, TASK-018).
# Running ACWR keeps the published ``2/(N+1)`` EWMA convention (Williams et al.), a ratio of two EWMAs of the same load.
ALPHA_ACUTE = 2.0 / (7.0 + 1.0)     # running mechanical acute load, N = 7 d
ALPHA_CHRONIC = 2.0 / (28.0 + 1.0)  # running mechanical chronic load, N = 28 d
# Banister PMC (TASK-021, ADR-034): exact one-day decay ``1 - exp(-1/tau)``. ``2/(N+1)`` made the effective time
# constants about half (21 d CTL, 3.5 d ATL): constant 100 TSS/day for 42 days gave CTL 86.5 instead of 63.2.
ALPHA_ATL = banister_alpha(TAU_ATL_DAYS)   # tau = 7 d  (0.1331)
ALPHA_CTL = banister_alpha(TAU_CTL_DAYS)   # tau = 42 d (0.0235)

# Orthopedic Dissociation Index (TASK-022, ADR-035): ODI^run = F / S, two EWMAs of the SAME daily run strain,
# F (tau = 35 d, the cardio-remodeling horizon) and S (tau = 120 d, collagen; 210 d for masters). Both are in run-strain
# units, so the ratio is dimensionless and steady-state training reads 1.0. The old M(t) was all-sport TSS over a
# run-strain C(t): cutting run strain lowered C(t) but not M(t), so the controller drove ODI UP (wrong sign).
TAU_ODI_FAST_DAYS = 35.0
TAU_ODI_SLOW_DAYS = 120.0
TAU_ODI_SLOW_MASTERS_DAYS = 210.0
ALPHA_ODI_FAST = banister_alpha(TAU_ODI_FAST_DAYS)


def alpha_odi_slow(masters_mode: bool = False) -> float:
    """One-day decay weight of the slow (collagen) run-strain EWMA: tau 120 d, or 210 d for masters."""
    return banister_alpha(TAU_ODI_SLOW_MASTERS_DAYS if masters_mode else TAU_ODI_SLOW_DAYS)


@dataclass
class AthletePhysiologicalState:
    """Tracks running exponential convolution states across days."""
    day_index: int = 0
    run_strain_fast: float = 45.0               # F(t): EWMA of daily run strain, tau = 35d
    run_strain_slow: float = 45.0               # S(t): EWMA of daily run strain, tau = 120d (or 210d for masters)
    running_acute_mech: float = 40.0            # Acute mechanical load, tau = 7d
    running_chronic_mech: float = 38.0          # Chronic mechanical load, tau = 28d
    masters_mode: bool = False                  # When True, tau_c=210d and stricter ACWR caution

    # Single-Sport & Composite Banister PMC
    bike_ctl: float = 55.0
    bike_atl: float = 50.0
    run_ctl: float = 35.0
    run_atl: float = 32.0
    swim_ctl: float = 25.0
    swim_atl: float = 22.0
    total_ctl: float = 115.0
    total_atl: float = 104.0

    @property
    def orthopedic_decoupling_index(self) -> float:
        """ODI^run = EWMA_35(run strain) / EWMA_120(run strain). Safe <= 1.35 (<= 1.20 in masters mode).

        Reads > 1 only while run load is ramping faster than the collagen matrix can follow; cutting run strain
        lowers the fast EWMA first, so the index falls (right-sign feedback; TASK-022)."""
        return self.run_strain_fast / max(self.run_strain_slow, 1.0)

    @property
    def running_mechanical_acwr(self) -> float:
        """EWMA ACWR = Acute / Chronic. Sweet spot 0.80 - 1.30 (0.80 - 1.15 in masters mode)."""
        return self.running_acute_mech / max(self.running_chronic_mech, 1.0)

    @property
    def total_tsb(self) -> float:
        """Training Stress Balance = CTL - ATL."""
        return self.total_ctl - self.total_atl

    def step_day(self, daily_aerobic_tss: float, daily_run_mech: float, daily_bike_tss: float, daily_run_tss: float,
                 daily_swim_tss: float, daily_strength_tss: Optional[float] = None):
        """Updates all convolution and EWMA states for 1 elapsed calendar day.

        ``daily_aerobic_tss`` is the day's all-sport aerobic TSS. The composite ``total_ctl`` / ``total_atl``
        integrate it: bike + run + swim + strength, where strength is ``daily_strength_tss`` or, when omitted, the
        remainder ``daily_aerobic_tss - (bike + run + swim)`` (TASK-021). It no longer feeds ODI (TASK-022):
        ``daily_run_mech`` drives both ODI EWMAs, so ODI is a ratio of like units."""
        self.day_index += 1
        # Masters athlete tendon and bone remodeling constant is slower (tau_c = 210 days vs 120 days)
        lambda_f, lambda_s = ALPHA_ODI_FAST, alpha_odi_slow(self.masters_mode)
        self.run_strain_fast = (1.0 - lambda_f) * self.run_strain_fast + lambda_f * daily_run_mech
        self.run_strain_slow = (1.0 - lambda_s) * self.run_strain_slow + lambda_s * daily_run_mech

        # EWMA Mechanical ACWR
        alpha_a, alpha_c = ALPHA_ACUTE, ALPHA_CHRONIC
        self.running_acute_mech = alpha_a * daily_run_mech + (1.0 - alpha_a) * self.running_acute_mech
        self.running_chronic_mech = alpha_c * daily_run_mech + (1.0 - alpha_c) * self.running_chronic_mech

        # Banister PMC (ATL tau=7d, CTL tau=42d)
        alpha_atl, alpha_ctl = ALPHA_ATL, ALPHA_CTL
        self.bike_atl = alpha_atl * daily_bike_tss + (1.0 - alpha_atl) * self.bike_atl
        self.bike_ctl = alpha_ctl * daily_bike_tss + (1.0 - alpha_ctl) * self.bike_ctl
        self.run_atl = alpha_atl * daily_run_tss + (1.0 - alpha_atl) * self.run_atl
        self.run_ctl = alpha_ctl * daily_run_tss + (1.0 - alpha_ctl) * self.run_ctl
        self.swim_atl = alpha_atl * daily_swim_tss + (1.0 - alpha_atl) * self.swim_atl
        self.swim_ctl = alpha_ctl * daily_swim_tss + (1.0 - alpha_ctl) * self.swim_ctl

        endurance_tss = daily_bike_tss + daily_run_tss + daily_swim_tss
        if daily_strength_tss is None:
            daily_strength_tss = max(0.0, daily_aerobic_tss - endurance_tss)
        tot_tss = endurance_tss + daily_strength_tss
        self.total_atl = alpha_atl * tot_tss + (1.0 - alpha_atl) * self.total_atl
        self.total_ctl = alpha_ctl * tot_tss + (1.0 - alpha_ctl) * self.total_ctl

