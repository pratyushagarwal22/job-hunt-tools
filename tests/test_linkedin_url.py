import pytest

from shared.linkedin_url import normalize_linkedin_url


def test_plan_example():
    url = "https://www.LinkedIn.com/in/Jane-Doe/?utm_source=foo"
    assert normalize_linkedin_url(url) == "https://linkedin.com/in/jane-doe"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/jane-doe",
        "https://linkedin.com/in/jane-doe/",
        "https://WWW.LINKEDIN.COM/IN/JANE-DOE",
        "https://www.linkedin.com/in/jane-doe?trk=public_profile&originalSubdomain=us",
        "https://www.linkedin.com/in/jane-doe/#experience",
        "http://www.linkedin.com/in/jane-doe",
        "www.linkedin.com/in/jane-doe/",
        "  https://www.linkedin.com/in/jane-doe/  ",
    ],
)
def test_variants_of_one_profile_normalize_to_the_same_string(url):
    assert normalize_linkedin_url(url) == "https://linkedin.com/in/jane-doe"


def test_different_profiles_stay_different():
    a = normalize_linkedin_url("https://www.linkedin.com/in/jane-doe")
    b = normalize_linkedin_url("https://www.linkedin.com/in/jane-doe-2")
    assert a != b


def test_idempotent():
    once = normalize_linkedin_url("https://www.LinkedIn.com/in/Jane-Doe/?x=1")
    assert normalize_linkedin_url(once) == once


def test_non_profile_path_kept():
    url = "https://www.linkedin.com/company/Example-Corp/"
    assert normalize_linkedin_url(url) == "https://linkedin.com/company/example-corp"


def test_blank_stays_blank():
    assert normalize_linkedin_url("   ") == ""
