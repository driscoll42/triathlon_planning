"""Assertions over the 11 stress scenarios in src/stress_test_optimizer.py (TASK-008).

Known engine defects are marked ``xfail`` with the task that owns the fix. A strict xfail
fails as soon as the defect disappears, so the marker must be removed when the fix lands.
"""
import pytest

VALID_STATUSES = {"OPTIMAL", "TIME_LIMIT_FEASIBLE"}

MILP_SCENARIOS = [
    "1_baseline",
    "2a_strict_650",
    "2b_soft_650",
    "3b_time_starvation_realistic",
    "4_ironman",
    "5_orthopedic_rehab",
    "6a_pool_lockdown_conflict",
    "6b_pool_lockdown_adapted",
    "7_master_athlete",
    "8_large_library",
]

# Single-run share > cap with >= 4 runs (ADR-003). The cap is only applied to long-run ids (TASK-017).
SINGLE_RUN_CAP_KNOWN_BUG = {"1_baseline", "2b_soft_650", "8_large_library"}


def _plans(stress_results, name):
    res = stress_results[name]
    return res, res.get("plans") or []


def _runs(plan):
    return [w for d in range(7) for w in plan["schedule"][d] if w.sport == "Run"]


@pytest.mark.parametrize("name", MILP_SCENARIOS)
def test_scenario_returns_plan_with_real_status(stress_results, name):
    res, plans = _plans(stress_results, name)
    assert plans, f"{name}: no plan (status={res.get('status')})"
    for p in plans:
        assert p["status"] in VALID_STATUSES, p["status"]
        assert "mip_gap" in p
        if p["status"] == "OPTIMAL":
            assert p["mip_gap"] is not None and p["mip_gap"] <= 0.0101  # mip_rel_gap = 1%


def test_time_starvation_with_oversized_anchor_is_infeasible(stress_results):
    # The 180 min anchor cannot fit 90 min weekend caps, so the solver must report infeasibility.
    assert stress_results["3_time_starvation_anchor"]["status"] == "INFEASIBLE"


@pytest.mark.parametrize("name", MILP_SCENARIOS)
def test_single_run_share_cap(stress_results, name, request):
    if name in SINGLE_RUN_CAP_KNOWN_BUG:
        request.applymarker(pytest.mark.xfail(reason="TASK-017: 32% cap only applied to lrun_ids", strict=False))
    res, plans = _plans(stress_results, name)
    cap = res["cfg"].max_single_run_vol_ratio
    for p in plans:
        runs = _runs(p)
        if len(runs) < 4:
            continue  # pigeonhole: with <= 3 runs the largest share is >= 1/3 (TASK-017 diagnostic)
        total = sum(w.duration for w in runs)
        share = max(w.duration for w in runs) / total
        assert share <= cap + 1e-6, f"{name} rank {p['rank']}: single run {share:.3f} > cap {cap}"


@pytest.mark.parametrize("name", MILP_SCENARIOS)
def test_no_heavy_deadlift_within_24h_of_heavy_or_long_run(stress_results, name):
    """HEUR-004: heavy DL and threshold/long runs never on the same or adjacent days."""
    _, plans = _plans(stress_results, name)
    for p in plans:
        sched = p["schedule"]
        dl_days = {d for d in range(7) for w in sched[d] if (getattr(w, "flags", None) or {}).get("heavy_dl")}
        run_days = {d for d in range(7) for w in sched[d] if w.sport == "Run" and w.is_heavy_run}
        clashes = {(a, b) for a in dl_days for b in run_days if abs(a - b) <= 1}
        assert not clashes, f"{name} rank {p['rank']}: DL/heavy-run day pairs {sorted(clashes)}"


@pytest.mark.parametrize("name", MILP_SCENARIOS)
def test_facility_access_respected(stress_results, name):
    res, plans = _plans(stress_results, name)
    access = res["cfg_requested"].facility_access
    for p in plans:
        for d in range(7):
            for w in p["schedule"][d]:
                needs = ["Bike", "Run"] if w.sport == "Brick" else [w.sport]
                for s in needs:
                    assert access[s][d], f"{name}: {w.code} on day {d} without {s} access"


@pytest.mark.parametrize("name", MILP_SCENARIOS)
def test_sport_frequency_bounds(stress_results, name, request):
    if name == "6a_pool_lockdown_conflict":
        # Swim freq (2,2) with the pool closed must be reported as a conflict. Instead the scheduler silently
        # rewrites the bound to (0,0) (closed_loop_optimizer.py:702-706) and returns OPTIMAL with 0 swims.
        request.applymarker(pytest.mark.xfail(
            reason="TASK-016: silent frequency relaxation instead of Tier-1 slack + feasibility explainer", strict=True))
    res, plans = _plans(stress_results, name)
    bounds = res["cfg_requested"].sport_freq_bounds
    for p in plans:
        for sport, (lo, hi) in bounds.items():
            n = sum(1 for d in range(7) for w in p["schedule"][d] if w.sport == sport)
            assert lo <= n <= hi, f"{name}: {sport} count {n} outside [{lo}, {hi}]"


@pytest.mark.xfail(reason="TASK-013: TriathlonMILPScheduler mutates the caller's config in place", strict=True)
def test_solver_does_not_mutate_caller_config(stress_results):
    res = stress_results["6a_pool_lockdown_conflict"]
    assert res["cfg"].sport_freq_bounds == res["cfg_requested"].sport_freq_bounds


@pytest.mark.parametrize("name,k", [("1_baseline", 3), ("8_large_library", 3)])
def test_top_k_plans_are_distinct(stress_results, name, k):
    _, plans = _plans(stress_results, name)
    assert len(plans) == k
    layouts = {
        frozenset((d, w.code) for d in range(7) for w in p["schedule"][d]) for p in plans
    }
    assert len(layouts) == k, "No-Good cuts returned duplicate schedules"


def test_tier1_sick_athlete_downgrades_hard_session(stress_results, default_library):
    sick = stress_results["9_tier1_readiness"]["sick"]
    hard = next(w for w in default_library if w.code == "BIKE_ME3_OU")
    assert sick["status"] != "PROCEED"
    assert sick["adjusted_tss"] < hard.aerobic_tss
    assert sick["adjusted_duration"] < hard.duration


def test_tier2_recalibration_lowers_run_strain_ceiling(stress_results):
    r = stress_results["10_tier2_recalibration"]
    assert r["pre_odi"] > 1.35 and r["pre_acwr"] > 1.30
    assert r["recal_run_strain"] < 350.0


def test_hybrid_athlete_returns_two_split_archetypes(stress_results):
    r = stress_results["11_hybrid_athlete"]
    assert r["plans"] == 2
    assert r["status"] in VALID_STATUSES
