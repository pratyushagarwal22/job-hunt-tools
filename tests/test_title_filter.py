from pathlib import Path

import pytest
import yaml

from job_scanner.title_filter import NO_INCLUDE_MATCH, title_drop_reason

SETTINGS = Path(__file__).parent.parent / "job_scanner" / "settings.yaml"

INCLUDE = ["Software Engineer", "SWE", "New Grad", "C++", "AI/ML", "SDE I"]
EXCLUDE = ["Staff", "Lead", "Intern", "Head of"]


def reason(title, include=INCLUDE, exclude=EXCLUDE):
    return title_drop_reason(title, include, exclude)


def test_exact_match_passes():
    assert reason("Software Engineer") is None


def test_match_inside_longer_title_passes():
    assert reason("Senior Software Engineer, Payments") is None


def test_case_insensitive():
    assert reason("software engineer") is None
    assert reason("SOFTWARE ENGINEER") is None


def test_no_include_match_fails():
    assert reason("Product Designer") == NO_INCLUDE_MATCH
    assert NO_INCLUDE_MATCH == "no include match"


def test_exclude_overrides_include():
    assert reason("Staff Software Engineer") == "Staff"


def test_exclude_reported_even_without_include_match():
    assert reason("Staff Designer") == "Staff"


def test_exclude_partial_word_does_not_match():
    assert reason("Software Engineer, Leadership Tools") is None


def test_exclude_whole_word_matches():
    assert reason("Tech Lead, Software Engineer") == "Lead"


def test_include_partial_word_does_not_match():
    assert reason("SWEeper Operator") == NO_INCLUDE_MATCH


def test_internship_not_matched_by_intern_keyword_alone():
    assert reason("Software Engineer (Internal Tools)") is None


def test_multi_word_keyword_needs_whole_phrase():
    assert reason("Software Quality Engineer", ["Software Engineer"], []) == NO_INCLUDE_MATCH


def test_multi_word_keyword_tolerates_extra_whitespace():
    assert reason("Software   Engineer", ["Software Engineer"], []) is None


def test_multi_word_exclude():
    assert reason("Head of Software Engineering, SWE") == "Head of"


def test_symbol_keyword_cpp():
    assert reason("C++ Developer", ["C++"], []) is None
    assert reason("Developer (C++)", ["C++"], []) is None
    assert reason("C Developer", ["C++"], []) == NO_INCLUDE_MATCH


def test_symbol_keyword_slash():
    assert reason("AI/ML Engineer", ["AI/ML"], []) is None
    assert reason("AI Engineer", ["AI/ML"], []) == NO_INCLUDE_MATCH


def test_roman_numeral_level_does_not_match_higher_level():
    assert reason("SDE I", ["SDE I"], []) is None
    assert reason("SDE II", ["SDE I"], []) == NO_INCLUDE_MATCH


def test_hyphen_counts_as_boundary():
    assert reason("Full-Stack Engineer", ["Full"], []) is None
    assert reason("New Grad-2027 Software", ["New Grad"], []) is None


def test_empty_title_fails():
    assert reason("") == NO_INCLUDE_MATCH


def test_empty_include_list_fails():
    assert reason("Software Engineer", [], EXCLUDE) == NO_INCLUDE_MATCH


def _settings_lists():
    settings = yaml.safe_load(SETTINGS.read_text())["title_filter"]
    return settings["include"], settings["exclude"]


@pytest.mark.parametrize("title, keyword", [
    ("Senior Software Engineer", "Senior"),
    ("Sr. Software Engineer II", "Sr"),
    ("Sr Software Engineer, Backend", "Sr"),
    ("New Grad RN - Home Infusion", "RN"),
    ("Nurse Software Engineer", "Nurse"),
    ("Nursing Informatics Software Developer", "Nursing"),
])
def test_settings_exclude_senior_and_nursing(title, keyword):
    include, exclude = _settings_lists()
    assert title_drop_reason(title, include, exclude) == keyword


@pytest.mark.parametrize("title", [
    "Software Engineer, SRE",
    "Software Engineer, New Grad",
    "Backend Software Engineer (Surgical Robotics)",
])
def test_settings_keep_early_career_titles(title):
    include, exclude = _settings_lists()
    assert title_drop_reason(title, include, exclude) is None


MTS = ["Member of Technical Staff"]


def test_always_allow_phrase_passes_despite_exclude_word_inside_it():
    assert title_drop_reason("Member of Technical Staff", ["Software Engineer"], ["Staff"], MTS) is None


def test_always_allow_counts_as_include_match():
    assert title_drop_reason("Member of Technical Staff, Applied AI", [], ["Staff"], MTS) is None


def test_always_allow_does_not_hide_other_exclude_words():
    assert title_drop_reason(
        "Senior Member of Technical Staff", ["Software Engineer"], ["Senior", "Staff"], MTS
    ) == "Senior"


def test_always_allow_is_case_insensitive_and_whole_phrase():
    assert title_drop_reason("member of technical staff", [], ["Staff"], MTS) is None
    assert title_drop_reason("Technical Staff Engineer", [], ["Staff"], MTS) == "Staff"


def _settings_title_cfg():
    return yaml.safe_load(SETTINGS.read_text())["title_filter"]


@pytest.mark.parametrize("title, expected", [
    ("Member of Technical Staff", None),
    ("Senior Member of Technical Staff", "Senior"),
    ("Java Developer", None),
    ("Frontend Engineer", None),
    ("Front End Engineer", None),
    ("Front-End Engineer", None),
    ("UI Engineer", None),
    ("Mobile Engineer", None),
    ("iOS Engineer", None),
    ("Android Engineer", None),
    ("Developer Advocate", "Developer Advocate"),
    ("Developer Relations Engineer", "Developer Relations"),
    ("Business Developer", "Business Developer"),
])
def test_settings_title_lists(title, expected):
    cfg = _settings_title_cfg()
    assert title_drop_reason(title, cfg["include"], cfg["exclude"], cfg["always_allow"]) == expected


def test_settings_exclude_has_no_duplicates():
    _, exclude = _settings_lists()
    lowered = [k.lower() for k in exclude]
    assert len(lowered) == len(set(lowered))
