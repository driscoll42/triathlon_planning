from pathlib import Path

import triplan.paths as paths
from triplan.cli import season_summary_path


def test_repo_root_has_pyproject():
    assert (paths.REPO_ROOT / "pyproject.toml").is_file()


def test_training_plan_path_creates_dir(monkeypatch, tmp_path):
    target = tmp_path / "x"
    monkeypatch.setattr(paths, "TRAINING_PLANS_DIR", target)
    p = paths.training_plan_path("a.csv")
    assert target.is_dir()
    assert p == target / "a.csv"


def test_cli_save_path_repo_anchored_regardless_of_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = season_summary_path("Olympic")
    assert p.is_absolute()
    assert p == paths.REPO_ROOT / "training_plans" / "closed_loop_olympic_season_summary.csv"


def test_legacy_path_constants_match():
    from triplan.library.loader import ROOT, LIBRARY_PATH
    from triplan.physiology.athlete_profile import PROFILE_PATH
    assert Path(ROOT) == paths.REPO_ROOT
    assert Path(LIBRARY_PATH) == paths.DATA_DIR / "workout_library.csv"
    assert Path(PROFILE_PATH) == paths.CONFIG_DIR / "athlete_profile.yaml"
