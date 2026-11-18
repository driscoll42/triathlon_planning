"""Selection-based Top-K cuts, dominance pruning and symmetry breaking (TASK-020, ADR-018)."""
import functools

import pytest

import triplan.solver.engine.engine as engine_mod
from triplan.model.candidates import CandidateWorkout
from triplan.physiology.athlete_profile import CURRENT_ATHLETE
from triplan.library.candidates import build_hybrid_candidate_library
from triplan.solver.engine import hybrid_options, meso_options
from triplan.solver.engine.config import EngineOptions
from triplan.solver.engine.engine import UnifiedWeekEngine
from triplan.solver.hybrid import HybridAthleteMILPOptimizer
from triplan.solver.pruning import prune_dominated_candidates
from triplan.solver.scheduler import TriathlonMILPScheduler

from tests.test_safety_constraints import ALL, cfg, wk  # noqa: F401  (shared synthetic builders)


def _codes(plan):
    return frozenset(w.code for d in range(7) for w in plan["schedule"][d])


def _layout(plan):
    return frozenset((w.code, d) for d in range(7) for w in plan["schedule"][d])


def _run_lib():
    """Six distinct runs plus two bikes: many distinct selections exist."""
    runs = [wk(i, f"R{i}", "Run", 30 + 5 * i, tss=20.0 + 3 * i, mech=8.0 + i) for i in range(6)]
    bikes = [wk(6 + i, f"B{i}", "Bike", 40 + 10 * i, tss=30.0 + 5 * i) for i in range(2)]
    return runs + bikes


def _meso_cfg(**kw):
    return cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 2), "Run": (2, 4), "Strength": (0, 0)},
               target_weekly_tss=140.0, **kw)


# ----------------------------------------------------------------------------- selection cuts

def test_defaults_are_selection_cuts_for_both_facades():
    assert meso_options().cut_mode == "selection" and hybrid_options().cut_mode == "selection"
    assert meso_options().cut_min_diff == 1


def test_hybrid_top_k_returns_more_than_two_distinct_selections():
    """The retired z_split flip equality capped hybrid Top-K at 2 plans."""
    opt = HybridAthleteMILPOptimizer(build_hybrid_candidate_library(), CURRENT_ATHLETE.to_hybrid_config())
    plans = opt.solve_top_k_plans(k_solutions=4, time_limit=10.0)
    assert len(plans) == 4
    sels = [_codes(p) for p in plans]
    assert len(set(sels)) == 4                       # pairwise distinct in WHICH workouts are chosen
    assert [p["option"] for p in plans] == ["A", "B", "C", "D"]
    assert all(p["split_architecture"] in ("Upper/Lower Barbell + DB Split", "Full-Body HLM / Texas Method Split")
               for p in plans)
    assert all(not (a <= b) for i, a in enumerate(sels) for j, b in enumerate(sels) if i < j)  # no later superset


def test_meso_selection_cut_plans_differ_in_selection_not_just_layout():
    s = TriathlonMILPScheduler(_run_lib(), _meso_cfg())
    plans = s.solve_top_k_plans(k_plans=4, time_limit=10.0)
    assert len(plans) == 4
    sels = [_codes(p) for p in plans]
    assert len(set(sels)) == 4
    for i in range(4):
        for j in range(i + 1, 4):
            assert len(sels[i] - sels[j]) >= 1       # later plan drops at least m=1 of an earlier plan's workouts


def test_cut_min_diff_requires_dropping_m_workouts():
    opt = meso_options()
    opt.cut_min_diff = 2
    s = TriathlonMILPScheduler(_run_lib(), _meso_cfg())
    eng = UnifiedWeekEngine(s.workouts, s.cfg, opt, notes=s.notes)
    plans = eng.solve(3, 10.0)
    sels = [_codes(p) for p in plans]
    assert len(plans) >= 2
    for i in range(len(sels)):
        for j in range(i + 1, len(sels)):
            assert len(sels[i] - sels[j]) >= 2


def test_layout_mode_keeps_day_permutation_cuts():
    opt = meso_options()
    opt.cut_mode = "layout"
    s = TriathlonMILPScheduler(_run_lib(), _meso_cfg())
    plans = UnifiedWeekEngine(s.workouts, s.cfg, opt, notes=s.notes).solve(3, 10.0)
    assert len(plans) == 3 and len({_layout(p) for p in plans}) == 3


def test_unknown_cut_mode_is_rejected():
    opt = meso_options()
    opt.cut_mode = "bogus"
    s = TriathlonMILPScheduler(_run_lib(), _meso_cfg())
    with pytest.raises(ValueError):
        UnifiedWeekEngine(s.workouts, s.cfg, opt, notes=s.notes).solve(2, 10.0)


def test_top_k_stops_cleanly_when_no_other_selection_exists():
    lib = [wk(0, "R0", "Run", 30), wk(1, "R1", "Run", 30), wk(2, "R2", "Run", 30)]
    c = cfg(sport_freq_bounds={"Swim": (0, 0), "Bike": (0, 0), "Run": (3, 3), "Strength": (0, 0)},
            target_weekly_tss=60.0, max_48h_ans_cost=1000.0)
    plans = TriathlonMILPScheduler(lib, c).solve_top_k_plans(k_plans=3, time_limit=10.0)
    assert len(plans) == 1                           # exactly 3 runs required and available: one selection


# ----------------------------------------------------------------------------- dominance pruning

def _dup_lib(n_dup=6):
    lib = [wk(i, f"D{i}", "Run", 40, tss=30.0) for i in range(n_dup)]
    lib += [wk(n_dup, "B0", "Bike", 50, tss=35.0), wk(n_dup + 1, "S0", "Swim", 30, tss=20.0)]
    return lib


def test_dominance_prunes_only_beyond_the_per_sport_multiplicity_cap():
    lib = _dup_lib(6)
    kept, rep = prune_dominated_candidates(lib, {"Run": (0, 4)})
    assert rep.total == 8 and rep.kept == 6 and rep.pruned == 2
    assert sum(w.sport == "Run" for w in kept) == 4
    kept_no_bound, rep2 = prune_dominated_candidates(lib)      # cap 7 >= 6 duplicates: nothing pruned
    assert rep2.pruned == 0 and len(kept_no_bound) == 8


def test_dominance_keeps_cheapest_clones_and_never_anchors():
    lib = [wk(0, "A", "Run", 40, is_anchor=True, preference_penalty=0.9),
           wk(1, "B", "Run", 40, preference_penalty=0.9), wk(2, "C", "Run", 40, preference_penalty=0.1),
           wk(3, "D", "Run", 40, preference_penalty=0.5)]
    kept, rep = prune_dominated_candidates(lib, {"Run": (0, 2)})
    assert {w.code for w in kept} == {"A", "C", "D"}           # anchor untouched; worst-penalty clone B dropped
    assert rep.pruned_codes == ["B"]


def test_dominance_never_prunes_non_identical_workouts():
    """Shorter / cheaper-looking workouts are NOT dominated: goals are two-sided."""
    lib = [wk(i, f"R{i}", "Run", 30 + i, tss=20.0 + i) for i in range(10)]
    kept, rep = prune_dominated_candidates(lib, {"Run": (0, 1)})
    assert rep.pruned == 0 and len(kept) == 10


def test_dominance_pruning_preserves_rank1_objective():
    lib = _dup_lib(6)
    c = cfg(sport_freq_bounds={"Swim": (0, 1), "Bike": (0, 1), "Run": (2, 4), "Strength": (0, 0)}, target_weekly_tss=160.0)
    objs = {}
    for flag in (True, False):
        opt = meso_options()
        opt.prune_dominated = flag
        eng = UnifiedWeekEngine(lib, c, opt)
        plans = eng.solve(1, 10.0)
        objs[flag] = (plans[0]["objective"], plans[0]["tier1_penalty"], eng.dominance.pruned if flag else None)
    assert objs[True][0] == pytest.approx(objs[False][0], abs=1e-6)
    assert objs[True][1] == pytest.approx(objs[False][1], abs=1e-6)
    assert objs[True][2] == 2                       # 6 identical runs, max 4 per week -> 2 pruned


# ----------------------------------------------------------------------------- symmetry breaking

def _sym_lib():
    return [wk(0, "DL", "Strength", 45, is_heavy_strength=True, flags={"heavy_dl": 1, "heavy_sq": 0}),
            wk(1, "LONG", "Run", 60, flags={"long_run": 1}),
            wk(2, "EASY", "Bike", 30), wk(3, "TEMPO", "Run", 40, tss=35.0), wk(4, "SW", "Swim", 35)]


def _hy(caps=None):
    return {"daily_duration_caps": caps or [120] * 7, "pool_open_days": list(range(7)),
            "outdoor_bike_days": list(range(7)),
            "target_tss": {"Swim": 20.0, "Bike": 20.0, "Run": 40.0, "Strength": 20.0},
            "sport_freq_bounds": {"Run": (1, 7), "Strength": (1, 7)}, "min_rest_days": 1, "max_rest_days": 7}


def _exact(monkeypatch):
    monkeypatch.setattr(engine_mod, "lexicographic_solve", functools.partial(engine_mod.lexicographic_solve, tier2_gap=0.0))


def test_reversal_symmetry_row_added_only_for_palindromic_weeks():
    sym = UnifiedWeekEngine(_sym_lib(), _hy(), hybrid_options())
    sym.solve(1, 10.0)
    assert sym.symmetry_row is True
    asym = UnifiedWeekEngine(_sym_lib(), _hy([120, 120, 75, 120, 120, 220, 180]), hybrid_options())
    asym.solve(1, 10.0)
    assert asym.symmetry_row is False
    meso = TriathlonMILPScheduler(_run_lib(), _meso_cfg())
    meso.solve_top_k_plans(1, 10.0)
    assert meso.engine.symmetry_row is False        # weekend brick / weekday caps break the symmetry
    layout = hybrid_options()
    layout.cut_mode = "layout"
    lay = UnifiedWeekEngine(_sym_lib(), _hy(), layout)
    lay.solve(1, 10.0)
    assert lay.symmetry_row is False                # layout cuts treat mirrored layouts as distinct


@pytest.mark.parametrize("target_run", [20.0, 40.0, 80.0])
def test_symmetry_breaking_does_not_change_the_optimum(monkeypatch, target_run):
    _exact(monkeypatch)
    conf = _hy()
    conf["target_tss"]["Run"] = target_run
    res = {}
    for flag in (True, False):
        opt = hybrid_options()
        opt.symmetry_breaking = flag
        eng = UnifiedWeekEngine(_sym_lib(), conf, opt)
        plans = eng.solve(1, 20.0)
        assert plans and eng.symmetry_row is flag
        res[flag] = plans[0]["objective_value"] + 1e6 * plans[0]["tier1_penalty"]
    assert res[True] == pytest.approx(res[False], abs=1e-6)


# ----------------------------------------------------------------------------- TASK-060 pruning & combo caps

def test_dominance_and_8020_pruning_deduplicate_cre1_against_bike_ae1_20():
    from dataclasses import replace
    from triplan.library.candidates import build_default_candidate_library
    from triplan.solver.pruning import prune_8020_candidates

    lib = build_default_candidate_library()
    by_code = {w.code: w for w in lib}
    assert "BIKE_AE1_20" in by_code and "CRe1" in by_code
    # Exact dominance across provenance prefers curated non-8020 row on tie
    kept_dom, rep = prune_dominated_candidates([by_code["CRe1"], by_code["BIKE_AE1_20"]], {"Bike": (0, 1)})
    assert [w.code for w in kept_dom] == ["BIKE_AE1_20"] and rep.pruned_codes == ["CRe1"]
    # 8020 pre-filter drops CRe1 / CRe3 near-duplicates of BIKE_AE1_20 / BIKE_AE1_30
    kept_8020 = {w.code for w in prune_8020_candidates(lib)}
    assert {"BIKE_AE1_20", "BIKE_AE1_30"} <= kept_8020
    assert not ({"CRe1", "CRe3"} & kept_8020)


def test_unschedulable_combos_and_zero_req_heavy_lifts_are_pruned():
    from dataclasses import replace
    from triplan.library.candidates import build_default_candidate_library
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE

    lib = build_default_candidate_library()
    # Re-entry / deload week: required_heavy_strength_sessions == 0 -> all heavy lifts & their combos pruned
    cfg_reentry = CURRENT_ATHLETE.to_mesocycle_config("base", 160.0)
    assert cfg_reentry.required_heavy_strength_sessions == 0
    eng_re = UnifiedWeekEngine(lib, cfg_reentry, meso_options())
    assert not any(w.is_heavy_strength for w in eng_re.workouts)
    assert not any(w.code.startswith("STR_HV") for w in eng_re.workouts)

    # Active build week (req_heavy_strength == 1): STR_HV1_SQUAT fits daily caps (61.8 <= 65),
    # but STR_HV1_SQUAT+EZ_Bike20 (axial=66.8 > 65, neuro=76.7 > 75) is unschedulable and pruned.
    cfg_build = replace(cfg_reentry, required_heavy_strength_sessions=1)
    eng_b = UnifiedWeekEngine(lib, cfg_build, meso_options())
    codes_b = {w.code for w in eng_b.workouts}
    assert "STR_HV1_SQUAT" in codes_b and "STR_HV3_SQUAT_SL+EZ_Bike20" in codes_b
    assert "STR_HV1_SQUAT+EZ_Bike20" not in codes_b
    assert "STR_HV2_DEADLIFT+EZ_Bike20" not in codes_b


def test_unused_non_heavy_lift_and_endurance_row_cap_combo_growth():
    from dataclasses import replace
    from triplan.library.candidates import build_default_candidate_library
    from triplan.physiology.athlete_profile import CURRENT_ATHLETE

    lib = build_default_candidate_library()
    by_code = {w.code: w for w in lib}
    extra_lift = replace(by_code["STR_SM1_CORE"], id=9901, code="STR_MAINT_EXTRA", name="Extra Maint Lift",
                         duration=42.0, preference_penalty=0.95, durability_score=0.5)
    extra_endur = replace(by_code["CRe1"], id=9902, code="CRe1_EXTRA", name="Extra 20m Easy Spin")
    cfg_w = CURRENT_ATHLETE.to_mesocycle_config("Recovery Deload (-20%)", 140.0)

    eng_base = UnifiedWeekEngine(lib, cfg_w, meso_options())
    eng_plus = UnifiedWeekEngine(lib + [extra_lift, extra_endur], cfg_w, meso_options())
    assert len(eng_plus.workouts) - len(eng_base.workouts) <= 1
    assert not any(c.startswith("STR_MAINT_EXTRA+") for c in eng_plus.combo_codes)
    assert "CRe1_EXTRA" not in {w.code for w in eng_plus.workouts}
    p_base = eng_base.solve(1, 5.0)[0]
    p_plus = eng_plus.solve(1, 5.0)[0]
    assert p_plus["objective"] == pytest.approx(p_base["objective"], abs=1e-6)

