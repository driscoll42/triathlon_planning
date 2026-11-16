"""Running exponential-convolution physiological state (CTL/ATL, ODI, mechanical ACWR)."""

from dataclasses import dataclass
import logging



logger = logging.getLogger(__name__)

# EWMA smoothing constants, the single source for ``step_day`` AND the in-model linearised ACWR / CTL-ramp
# rows (``solver.engine.builders.load_dynamics``, TASK-018). TASK-021 (exact Banister decay) changes them here.
ALPHA_ACUTE = 2.0 / (7.0 + 1.0)     # running mechanical acute load, tau = 7 d
ALPHA_CHRONIC = 2.0 / (28.0 + 1.0)  # running mechanical chronic load, tau = 28 d
ALPHA_ATL = 2.0 / (7.0 + 1.0)       # Banister ATL
ALPHA_CTL = 2.0 / (42.0 + 1.0)      # Banister CTL


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

    def step_day(self, daily_aerobic_tss: float, daily_run_mech: float, daily_bike_tss: float, daily_run_tss: float, daily_swim_tss: float):
        """Updates all convolution and EWMA states for 1 elapsed calendar day."""
        self.day_index += 1
        lambda_m = 1.0 / 35.0
        # Masters athlete tendon and bone remodeling constant is slower (tau_c = 210 days vs 120 days)
        tau_c = 210.0 if self.masters_mode else 120.0
        lambda_c = 1.0 / tau_c
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

        tot_tss = daily_bike_tss + daily_run_tss + daily_swim_tss
        self.total_atl = alpha_atl * tot_tss + (1.0 - alpha_atl) * self.total_atl
        self.total_ctl = alpha_ctl * tot_tss + (1.0 - alpha_ctl) * self.total_ctl

