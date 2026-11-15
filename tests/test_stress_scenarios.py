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


def test_time_starvation_reports_dropped_anchor_instead_of_failing(stress_results):
    # TASK-016: the 180 min anchor cannot fit 90 min weekend caps. The week still solves, and the dropped
    # anchor is a named Tier-1 relaxation plus a pre-solve diagnostic (no silent de-anchoring).
    _, plans = _plans(stress_results, "3_time_starvation_anchor")
    assert plans
    p = plans[0]
    assert any(r["target"] == "anchor BIKE_DUR_OUT" for r in p["relaxations"])
    assert any("BIKE_DUR_OUT" in m and "cannot be scheduled" in m for m in p["diagnostics"])


@pytest.mark.parametrize("name", MILP_SCENARIOS + ["3_time_starvation_anchor"])
def test_single_run_share_cap(stress_results, name):
    """ADR-003: <= 32% with >= 4 runs; explicit, reported pigeonhole caps (40% / 60%) with 3 / 2 runs."""
    res, plans = _plans(stress_results, name)
    cap = res["cfg_requested"].max_single_run_vol_ratio
    pigeon = res["cfg_requested"].pigeonhole_run_share
    for p in plans:
        runs = _runs(p)
        if len(runs) < 2:
            continue
        total = sum(w.duration for w in runs)
        share = max(w.duration for w in runs) / total
        limit = cap if len(runs) >= 4 else pigeon[len(runs)]
        assert share <= limit + 1e-6, f"{name} rank {p['rank']}: single run {share:.3f} > {limit} ({len(runs)} runs)"
        assert p["run_share"]["applied_cap"] == pytest.approx(limit)
        if len(runs) < 4:
            assert p["run_share"]["note"] and "Pigeonhole" in p["run_share"]["note"]


@pytest.mark.parametrize("name", MILP_SCENARIOS + ["3_time_starvation_anchor"])
def test_rolling_48h_and_spinal_caps_are_hard(stress_results, name):
    """Tier 0 (TASK-016): 48h TSS/ANS/strain and daily/weekly axial & neuro caps are never exceeded."""
    res, plans = _plans(stress_results, name)
    c = res["cfg_requested"]
    for p in plans:
        def tot(d, a):
            return sum(float(getattr(w, a, 0.0) or 0.0) for w in p["schedule"][d])
        for d in range(6):
            assert tot(d, "aerobic_tss") + tot(d + 1, "aerobic_tss") <= c.max_48h_aerobic_tss + 1e-6
            assert tot(d, "ans_cost") + tot(d + 1, "ans_cost") <= c.max_48h_ans_cost + 1e-6
            assert tot(d, "mechanical_strain") + tot(d + 1, "mechanical_strain") <= c.max_48h_mech_strain + 1e-6
        for d in range(7):
            assert tot(d, "axial_load") <= c.max_daily_axial_load + 1e-6
            assert tot(d, "neuromuscular_load") <= c.max_daily_neuro_load + 1e-6
        assert sum(tot(d, "axial_load") for d in range(7)) <= c.max_weekly_axial_load + 1e-6
        assert sum(tot(d, "neuromuscular_load") for d in range(7)) <= c.max_weekly_neuro_load + 1e-6


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
def test_sport_frequency_bounds(stress_results, name):
    """Max frequency is hard; any shortfall below the minimum must be a named, reported Tier-1 relaxation."""
    res, plans = _plans(stress_results, name)
    bounds = res["cfg_requested"].sport_freq_bounds
    for p in plans:
        relaxed = {r["category"].split(":", 1)[1]: r["amount"] for r in p["relaxations"]
                   if r["category"].startswith("reduce_frequency:")}
        for sport, (lo, hi) in bounds.items():
            days = sum(1 for d in range(7) if any(w.sport == sport for w in p["schedule"][d]))
            assert days <= hi, f"{name}: {sport} on {days} days > max {hi}"
            assert days + relaxed.get(sport, 0) >= lo, f"{name}: {sport} {days} < {lo} without a reported relaxation"


def test_pool_lockdown_conflict_is_reported_not_silently_rewritten(stress_results):
    _, plans = _plans(stress_results, "6a_pool_lockdown_conflict")
    p = plans[0]
    assert [r for r in p["relaxations"] if r["category"] == "reduce_frequency:Swim" and r["amount"] == 2]
    assert any("Swim needs at least 2" in m for m in p["diagnostics"])


@pytest.mark.parametrize("name", MILP_SCENARIOS)
def test_solver_does_not_mutate_caller_config(stress_results, name):
    res = stress_results[name]
    assert res["cfg"] == res["cfg_requested"]


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
