"""Integration and invariant tests for including 8020_library options in MILP pools."""
import highspy

from triplan.control.periodization import apply_keystone_week, keystone_run_rungs
from triplan.library.candidates import build_default_candidate_library, build_hybrid_candidate_library
from triplan.library.loader import load_candidates
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.solver.engine import UnifiedWeekEngine, hybrid_options, meso_options
from triplan.solver.pruning import filter_feasible_candidates, prune_8020_candidates
from triplan.solver.safety_constraints import SafetyLimits


def test_default_and_hybrid_libraries_include_8020_options():
    default_lib = build_default_candidate_library()
    hybrid_lib = build_hybrid_candidate_library()
    assert any(w.provenance == "8020_library" for w in default_lib)
    assert any(w.provenance == "8020_library" for w in hybrid_lib)
    assert len(build_default_candidate_library(include_8020=False)) == 55  # 52 + 3 TASK-059 short bricks
    assert len(build_hybrid_candidate_library(include_8020=False)) == 22


def test_feasible_prefilter_and_8020_pruning_preserve_core_and_anchors():
    lib = build_default_candidate_library()
    cfg = CURRENT_ATHLETE.to_mesocycle_config("Build", 160.0)
    eng = UnifiedWeekEngine(lib, cfg, meso_options())
    kept_codes = {w.code for w in eng.workouts}

    # Anchors remain present for Tier-1 reporting even when unhostable under 45-75m caps
    assert "BIKE_DUR_OUT_DEF" in kept_codes
    assert "RUN_AE2_LONG" in kept_codes
    # No unhostable non-anchor, non-test Swim or >75m endurance workout enters the MILP matrix
    assert not any(w.sport == "Swim" and not w.is_anchor and not w.flags.get("test") for w in eng.workouts)
    assert not any(w.duration > 75.0 and not w.is_anchor for w in eng.workouts)
    # 80/20 options are still offered in feasible Bike & Run buckets
    kept_8020_sports = {w.sport for w in eng.workouts if w.provenance == "8020_library" and not w.flags.get("test")}
    assert kept_8020_sports == {"Bike", "Run"}


def test_prune_8020_candidates_stratifies_across_durations_without_dropping_core():
    lib = build_default_candidate_library()
    pruned = prune_8020_candidates(lib, bucket_caps={"easy": 4, "tempo": 3, "hi": 3})
    core_codes = {w.code for w in load_candidates(library="default", include_8020=False)}
    assert core_codes <= {w.code for w in pruned}
    kept_8020 = [w for w in pruned if w.provenance == "8020_library" and not w.flags.get("test")]
    assert len(kept_8020) <= 3 * (4 + 3 + 3)
    assert any(w.sport == "Run" for w in kept_8020)
    assert any(w.sport == "Bike" for w in kept_8020)


def test_prune_8020_retains_rre9_support_run_and_drops_long_8020_easy_runs():
    from triplan.perf.season_validation import reference_profile
    from triplan.solver.engine.builders import calendar
    from triplan.solver.engine.config import WeekConfig, WorkoutTable

    lib = build_default_candidate_library()
    cfg = reference_profile().to_mesocycle_config("Build", 400.0)
    allowed = calendar.make_allowed(WorkoutTable(lib), WeekConfig(cfg), meso_options())
    feas = filter_feasible_candidates(lib, allowed, SafetyLimits.from_config(cfg), req_heavy_strength=1)
    pruned = prune_8020_candidates(feas, meso_options().max_8020_per_bucket)
    easy_8020_runs = [
        w for w in pruned
        if w.sport == "Run" and w.provenance == "8020_library" and not w.is_hard_endurance and not w.has_strides
    ]
    codes = {w.code for w in easy_8020_runs}
    assert "RRe9" in codes
    assert all(w.duration <= 70.0 + 1e-9 for w in easy_8020_runs)


def test_keystone_selection_does_not_duplicate_8020_runs_at_same_duration():
    lib = build_default_candidate_library()
    big_caps = [90.0, 120.0, 90.0, 120.0, 90.0, 300.0, 240.0]
    assert keystone_run_rungs(lib, big_caps) == [75.0, 90.0, 105.0, 120.0, 150.0]
    for dur, expected in ((75.0, "RUN_AE2_LONG"), (90.0, "RUN_LC_Z2_90"), (105.0, "RUN_LC_Z2_105")):
        anchors = [w.code for w in apply_keystone_week(lib, "run", dur) if w.is_anchor]
        assert anchors == [expected]


def test_sparse_run_share_keeps_model_compact_on_8020_pool():
    lib = build_default_candidate_library()
    cfg = CURRENT_ATHLETE.to_mesocycle_config("Build", 160.0)
    eng = UnifiedWeekEngine(lib, cfg, meso_options())
    ctx = eng.build_model()
    # Sparse r_tot formulation keeps nonzeros well below the old O(R^2) dense matrix
    assert ctx.model._idx.n < 30000
    plans = eng.solve(1, time_limit=3.5)
    assert plans and plans[0]["status"] in ("OPTIMAL", "TIME_LIMIT_FEASIBLE")


def test_hybrid_engine_solves_with_8020_options():
    lib = build_hybrid_candidate_library()
    eng = UnifiedWeekEngine(lib, CURRENT_ATHLETE.to_hybrid_config(), hybrid_options())
    plans = eng.solve(2, time_limit=5.0)
    assert len(plans) == 2
    assert all(p["status"] in ("OPTIMAL", "TIME_LIMIT_FEASIBLE") for p in plans)
