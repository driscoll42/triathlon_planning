"""Smoke test for scripts/simulate_level0_comparison.py (TASK-029)."""
import importlib.util
import math

from triplan.paths import REPO_ROOT


def _load_script():
    path = REPO_ROOT / "scripts" / "simulate_level0_comparison.py"
    spec = importlib.util.spec_from_file_location("simulate_level0_comparison", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_comparison_sprint_smoke():
    mod = _load_script()
    df = mod.run_comparison(distances=["Sprint"], time_limit_per_week=0.5)

    assert len(df) == 2
    assert set(df["Plan"]) == {"80/20 Official Level 0", "Closed-Loop Optimizer"}
    for col in ("Distance", "Weeks", "Final_CTL (Fitness)", "Race_Day_ODI (Remodeling)",
                "Race_Day_ACWR", "Total_Durability_Stimulus", "Consec_Hard_Days"):
        assert col in df.columns
    cl = df[df["Plan"] == "Closed-Loop Optimizer"].iloc[0]
    for col in ("Final_CTL (Fitness)", "Peak_CTL", "Final_ATL (Fatigue)", "Race_Day_ODI (Remodeling)",
                "Race_Day_ACWR", "Total_Durability_Stimulus"):
        assert math.isfinite(cl[col])
    assert cl["Final_CTL (Fitness)"] > 0
    assert cl["Total_Durability_Stimulus"] > 0
