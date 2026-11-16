"""
Repository-root-anchored paths (TASK-031).

Every reader and writer resolves files from ``REPO_ROOT`` instead of the current working
directory, so ``triplan`` / ``python -m triplan`` / ``scripts/*.py`` behave the same from any cwd.
"""

from __future__ import annotations

from pathlib import Path

# src/triplan/paths.py -> src/triplan -> src -> <repo root>
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
CONFIG_DIR: Path = REPO_ROOT / "config"
DATA_DIR: Path = REPO_ROOT / "data"
TRAINING_PLANS_DIR: Path = REPO_ROOT / "training_plans"


def training_plan_path(filename: str) -> Path:
    """Return ``training_plans/<filename>`` under the repo root, creating the directory if missing."""
    TRAINING_PLANS_DIR.mkdir(parents=True, exist_ok=True)
    return TRAINING_PLANS_DIR / filename
