"""Regression tests for TASK-006 (triVariables attributes, TSS filter, custom-row preservation)."""
import pandas as pd
import pytest

from classes import triVariables as triv


@pytest.mark.parametrize("attr", ["high_duration_head", "low_duration_head", "intensity_factor", "normalized_power"])
def test_trivariables_has_column_aliases(attr):
    assert isinstance(getattr(triv, attr), str)


def test_load_workouts_filters_tss_by_tss_caps_not_time_caps():
    from util import load_workouts

    wkend, wkday = load_workouts()
    assert wkend["TSS"].max() <= triv.max_tss_weekend
    assert wkday["TSS"].max() <= triv.max_tss_weekday
    assert wkend["Duration"].max() <= triv.max_time_weekend
    # Before the fix, TSS was capped at max_time_weekend (140), dropping legitimate 140-200 TSS sessions.
    assert (wkend["TSS"] > triv.max_time_weekend).any()


def test_preserve_custom_rows_reappends_missing_custom_codes(tmp_path):
    from clean_file import preserve_custom_rows

    path = tmp_path / "Run.xlsx"
    existing = pd.DataFrame({
        "Code": ["RAe1", "RF2", "RUN_SS1_STR", "RUN_VO2_MAP"],
        "Duration": [30, 45, 55, 60],
        "TSS": [20.0, 35.0, 46.0, 70.0],
    })
    existing.to_excel(path, engine="openpyxl")  # index column written, like rebuild_all_files

    scraped = pd.DataFrame({"Code": ["RAe1", "RF2", "RF3"], "Duration": [31, 45, 50], "TSS": [21.0, 35.0, 40.0]})
    merged = preserve_custom_rows(scraped, str(path))

    assert list(merged["Code"]) == ["RAe1", "RF2", "RF3", "RUN_SS1_STR", "RUN_VO2_MAP"]
    assert merged.loc[merged["Code"] == "RAe1", "Duration"].item() == 31  # scraped rows win
    assert not any(str(c).startswith("Unnamed") for c in merged.columns)


def test_preserve_custom_rows_no_existing_file(tmp_path):
    from clean_file import preserve_custom_rows

    scraped = pd.DataFrame({"Code": ["RAe1"], "Duration": [30], "TSS": [20.0]})
    assert preserve_custom_rows(scraped, str(tmp_path / "missing.xlsx")).equals(scraped)
