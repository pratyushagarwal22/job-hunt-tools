import pytest

from job_scanner.experience_filter import required_years


@pytest.mark.parametrize("text, expected", [
    ("3+ years", 3),
    ("2-5 years", 2),
    ("2 to 5 years of experience", 2),
    ("three years of experience", 3),
    ("a minimum of three (3) years of experience", 3),
    ("3+ years of experience required, 5+ years preferred", 3),
    ("5+ years of Java. 2+ years of Go.", 2),
    ("Bachelor's + 3 years or Master's + 1 year", 1),
    ("3&nbsp;years of experience", 3),
    ("<li>4 years</li><li>of professional experience</li>", 4),
    ("&lt;li&gt;6+ years&lt;/li&gt;", 6),
    ("At least 4 years' experience building APIs", 4),
    ("Minimum of 3 years in Python", 3),
    ("4 years with React and TypeScript", 4),
    ("You have 5 years building production systems and strong experience with AWS", 5),
    ("Experience: 3 years", 3),
    ("0-2 years of experience", 0),
    ("ten years of experience", 10),
    ("5+ years of experience", 5),
])
def test_required_years(text, expected):
    years, phrase = required_years(text)

    assert years == expected
    assert phrase


@pytest.mark.parametrize("text", [
    "founded 10 years ago",
    "Acme has been 10 years in business and is growing fast.",
    "We are a 5 year old startup.",
    "401k match vests over 4 years.",
    "Build backend services in Python. Great team.",
    "18 years of age or older",
    "Must be aged 18 years or older",
    "30 years of investing experience",
    "20+ years of experience",
    "",
    None,
])
def test_no_experience_requirement(text):
    assert required_years(text) == (None, None)


def test_unrelated_years_ignored_but_requirement_found():
    text = "Founded 10 years ago. You bring 3+ years of experience with Go."

    assert required_years(text)[0] == 3


def test_phrase_is_the_matched_requirement():
    years, phrase = required_years("Nice team. Requires 3+ years of backend experience. Perks.")

    assert years == 3
    assert phrase == "3+ years of backend experience"
