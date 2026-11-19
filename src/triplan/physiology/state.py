"""Running exponential-convolution physiological state (CTL/ATL, ODI, mechanical ACWR)."""

from dataclasses import dataclass
import logging
import math
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


@dataclass
class AthletePhysiologicalState:
    """Tracks running exponential convolution states across days."""
    day_index: int = 0
    metabolic_fitness_state: float = 50.0       # M(t), tau = 35d
    connective_tissue_state: float = 45.0       # C(t), tau = 120d (or 210d for masters)
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
        """ODI = M(t) / C(t). Safe <= 1.35 (<= 1.20 in masters mode)."""
        return self.metabolic_fitness_state / max(self.connective_tissue_state, 1.0)

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

        ``daily_aerobic_tss`` is the day's all-sport aerobic TSS (drives ``M(t)``). The composite ``total_ctl`` /
        ``total_atl`` integrate the SAME all-sport load: bike + run + swim + strength, where strength is
        ``daily_strength_tss`` or, when omitted, the remainder ``daily_aerobic_tss - (bike + run + swim)`` (TASK-021)."""
        self.day_index += 1
        lambda_m = 1.0 - math.exp(-1.0 / 35.0)
        # Masters athlete tendon and bone remodeling constant is slower (tau_c = 210 days vs 120 days)
        tau_c = 210.0 if self.masters_mode else 120.0
        lambda_c = 1.0 - math.exp(-1.0 / tau_c)
        self.metabolic_fitness_state = (1.0 - lambda_m) * self.metabolic_fitness_state + lambda_m * daily_aerobic_tss
        self.connective_tissue_state = (1.0 - lambda_c) * self.connective_tissue_state + lambda_c * daily_run_mech

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

