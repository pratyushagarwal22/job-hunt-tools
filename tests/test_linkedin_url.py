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


@pytest.mark.parametrize(
    "url",
    [
        "https://uk.linkedin.com/in/jane-doe",
        "https://m.linkedin.com/in/jane-doe/",
        "https://DE.LinkedIn.com/in/Jane-Doe?trk=x",
        "uk.linkedin.com/in/jane-doe",
    ],
)
def test_any_linkedin_subdomain_collapses_to_linkedin_com(url):
    assert normalize_linkedin_url(url) == "https://linkedin.com/in/jane-doe"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/jane-doe/recent-activity/",
        "https://www.linkedin.com/in/jane-doe/overlay/contact-info/",
        "https://www.linkedin.com/in/Jane-Doe/details/experience/?x=1",
        "https://uk.linkedin.com/in/jane-doe/recent-activity/all/#top",
    ],
)
def test_extra_path_after_in_slug_is_trimmed(url):
    assert normalize_linkedin_url(url) == "https://linkedin.com/in/jane-doe"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.linkedin.com/pub/jane-doe/1/2/3", "https://linkedin.com/pub/jane-doe/1/2/3"),
        ("https://www.linkedin.com/company/example/about/", "https://linkedin.com/company/example/about"),
        ("https://www.linkedin.com/in/", "https://linkedin.com/in"),
        ("https://example.com/in/jane-doe/details", "https://example.com/in/jane-doe/details"),
    ],
)
def test_non_in_paths_are_not_trimmed(url, expected):
    assert normalize_linkedin_url(url) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://notlinkedin.com/in/jane-doe", "https://notlinkedin.com/in/jane-doe"),
        ("https://linkedin.com.example.com/in/jane-doe", "https://linkedin.com.example.com/in/jane-doe"),
    ],
)
def test_lookalike_hosts_are_not_collapsed(url, expected):
    assert normalize_linkedin_url(url) == expected


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
