from pathlib import Path

import pytest
import yaml

from job_scanner.title_filter import passes_title_filter

SETTINGS = Path(__file__).parent.parent / "job_scanner" / "settings.yaml"

INCLUDE = ["Software Engineer", "SWE", "New Grad", "C++", "AI/ML", "SDE I"]
EXCLUDE = ["Staff", "Lead", "Intern", "Head of"]


def test_exact_match_passes():
    assert passes_title_filter("Software Engineer", INCLUDE, EXCLUDE)


def test_match_inside_longer_title_passes():
    assert passes_title_filter("Senior Software Engineer, Payments", INCLUDE, EXCLUDE)


def test_case_insensitive():
    assert passes_title_filter("software engineer", INCLUDE, EXCLUDE)
    assert passes_title_filter("SOFTWARE ENGINEER", INCLUDE, EXCLUDE)


def test_no_include_match_fails():
    assert not passes_title_filter("Product Designer", INCLUDE, EXCLUDE)


def test_exclude_overrides_include():
    assert not passes_title_filter("Staff Software Engineer", INCLUDE, EXCLUDE)


def test_exclude_partial_word_does_not_match():
    assert passes_title_filter("Software Engineer, Leadership Tools", INCLUDE, EXCLUDE)


def test_exclude_whole_word_matches():
    assert not passes_title_filter("Tech Lead, Software Engineer", INCLUDE, EXCLUDE)


def test_include_partial_word_does_not_match():
    assert not passes_title_filter("SWEeper Operator", INCLUDE, EXCLUDE)


def test_internship_not_matched_by_intern_keyword_alone():
    assert passes_title_filter("Software Engineer (Internal Tools)", INCLUDE, EXCLUDE)


def test_multi_word_keyword_needs_whole_phrase():
    assert not passes_title_filter("Software Quality Engineer", ["Software Engineer"], [])


def test_multi_word_keyword_tolerates_extra_whitespace():
    assert passes_title_filter("Software   Engineer", ["Software Engineer"], [])


def test_multi_word_exclude():
    assert not passes_title_filter("Head of Software Engineering, SWE", INCLUDE, EXCLUDE)


def test_symbol_keyword_cpp():
    assert passes_title_filter("C++ Developer", ["C++"], [])
    assert passes_title_filter("Developer (C++)", ["C++"], [])
    assert not passes_title_filter("C Developer", ["C++"], [])


def test_symbol_keyword_slash():
    assert passes_title_filter("AI/ML Engineer", ["AI/ML"], [])
    assert not passes_title_filter("AI Engineer", ["AI/ML"], [])


def test_roman_numeral_level_does_not_match_higher_level():
    assert passes_title_filter("SDE I", ["SDE I"], [])
    assert not passes_title_filter("SDE II", ["SDE I"], [])


def test_hyphen_counts_as_boundary():
    assert passes_title_filter("Full-Stack Engineer", ["Full"], [])
    assert passes_title_filter("New Grad-2027 Software", ["New Grad"], [])


def test_empty_title_fails():
    assert not passes_title_filter("", INCLUDE, EXCLUDE)


def test_empty_include_list_fails():
    assert not passes_title_filter("Software Engineer", [], EXCLUDE)


def _settings_lists():
    settings = yaml.safe_load(SETTINGS.read_text())["title_filter"]
    return settings["include"], settings["exclude"]


@pytest.mark.parametrize("title", [
    "Senior Software Engineer",
    "Sr. Software Engineer II",
    "Sr Software Engineer, Backend",
    "New Grad RN - Home Infusion",
    "Nurse Software Engineer",
    "Nursing Informatics Software Developer",
])
def test_settings_exclude_senior_and_nursing(title):
    include, exclude = _settings_lists()
    assert not passes_title_filter(title, include, exclude)


@pytest.mark.parametrize("title", [
    "Software Engineer, SRE",
    "Software Engineer, New Grad",
    "Backend Software Engineer (Surgical Robotics)",
])
def test_settings_keep_early_career_titles(title):
    include, exclude = _settings_lists()
    assert passes_title_filter(title, include, exclude)


def test_settings_exclude_has_no_duplicates():
    _, exclude = _settings_lists()
    lowered = [k.lower() for k in exclude]
    assert len(lowered) == len(set(lowered))
