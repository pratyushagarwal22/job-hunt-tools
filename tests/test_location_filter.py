from pathlib import Path

import pytest
import yaml

from job_scanner.location_filter import location_drop_reason

EXCLUDE = ["London", "UK", "India", "Bangalore", "Taiwan", "Taipei", "New Delhi", "Tel Aviv"]


def ats(location):
    return location_drop_reason(location, "Software Engineer", "", EXCLUDE, True)


def search(title="Software Engineer", url="https://example.com/jobs/1"):
    return location_drop_reason(None, title, url, EXCLUDE, False)


def test_ats_london_fails():
    assert ats("London") == ("London", "location")


def test_ats_remote_passes():
    assert ats("Remote") is None


def test_ats_remote_us_passes():
    assert ats("Remote - US") is None


def test_ats_new_york_passes():
    assert ats("New York, NY") is None


def test_ats_blank_passes():
    assert ats("") is None


def test_ats_none_passes():
    assert ats(None) is None


def test_ats_bangalore_india_fails_on_first_keyword_in_list():
    assert ats("Bangalore, India") == ("India", "location")


def test_ats_case_insensitive():
    assert ats("LONDON, uk") == ("London", "location")


def test_ats_whole_word_only():
    assert ats("Indianapolis, IN") is None
    assert ats("Ukiah, CA") is None


def test_ats_multi_word_keyword():
    assert ats("Tel Aviv, Israel") == ("Tel Aviv", "location")
    assert ats("Aviv Street, Austin TX") is None


def test_ats_ignores_title_and_url():
    assert location_drop_reason(
        "San Francisco, CA",
        "Software Engineer - London",
        "https://example.com/jobs/Taiwan-Taipei",
        EXCLUDE,
        True,
    ) is None


def test_search_ignores_location_field():
    assert location_drop_reason(
        "London", "Software Engineer", "https://example.com/1", EXCLUDE, False
    ) is None


def test_search_title_location_fails():
    assert search(title="Software Engineer - London") == ("London", "title")


def test_search_title_reported_before_url():
    assert search(title="SWE London", url="https://x.com/Taiwan") == ("London", "title")


def test_search_url_path_location_fails():
    url = "https://nvidia.wd5.myworkdayjobs.com/en-US/jobs/job/Taiwan-Taipei/Software-Engineer_JR123"
    assert search(url=url) == ("Taiwan", "url")


def test_search_url_query_location_fails():
    url = "https://www.google.com/about/careers/applications/jobs/results?location=India"
    assert search(url=url) == ("India", "url")


def test_search_url_encoded_location_fails():
    assert search(url="https://example.com/jobs?city=New%20Delhi") == ("New Delhi", "url")
    assert search(url="https://example.com/jobs?city=New+Delhi") == ("New Delhi", "url")


def test_search_us_job_passes():
    assert search(
        title="Software Engineer, New Grad",
        url="https://job-boards.greenhouse.io/acme/jobs/123",
    ) is None


def test_search_blank_everything_passes():
    assert location_drop_reason(None, "", "", EXCLUDE, False) is None


def test_search_none_fields_pass():
    assert location_drop_reason(None, None, None, EXCLUDE, False) is None


def test_empty_exclude_list_passes():
    assert location_drop_reason("London", "", "", [], True) is None


# --- US signals (settings lists) ---

SETTINGS = Path(__file__).parent.parent / "job_scanner" / "settings.yaml"
LOCATION_CFG = yaml.safe_load(SETTINGS.read_text())["location_filter"]


def ats_cfg(location):
    return location_drop_reason(
        location, "Software Engineer", "", LOCATION_CFG["exclude"], True,
        us_signals=LOCATION_CFG["us_signals"],
    )


def search_cfg(title="Software Engineer", url="https://example.com/jobs/1"):
    return location_drop_reason(
        None, title, url, LOCATION_CFG["exclude"], False,
        us_signals=LOCATION_CFG["us_signals"],
    )


def test_title_with_us_city_and_foreign_city_kept():
    assert search_cfg(title="Software Engineer - New York or Zurich") is None


def test_title_with_only_foreign_location_dropped():
    assert search_cfg(title="Software Engineer - Zurich, Switzerland") == ("Switzerland", "title")


def test_join_us_is_not_a_us_signal():
    assert search_cfg(title="Join us in London - Software Engineer") == ("London", "title")


def test_ats_foreign_city_dropped():
    assert ats_cfg("Toronto") == ("Toronto", "location")


def test_ats_us_and_foreign_location_kept():
    assert ats_cfg("San Francisco; London") is None


def test_us_signal_in_url_keeps_title_mention():
    assert search_cfg(title="SWE, London team", url="https://example.com/jobs?loc=United+States") is None


def test_us_signal_in_title_keeps_url_mention():
    assert search_cfg(title="Software Engineer (Remote - US)", url="https://x.com/jobs/London") is None


@pytest.mark.parametrize("signal", ["U.S.", "USA", "Remote (US)", "US Remote", "Texas", "Seattle"])
def test_us_signal_variants_keep_job(signal):
    assert ats_cfg(f"London or {signal}") is None


def test_ats_location_only_checked_for_signals():
    assert location_drop_reason(
        "London", "Software Engineer, New York", "https://x.com/usa", ["London"], True,
        us_signals=["New York", "USA"],
    ) == ("London", "location")


def test_us_signals_have_no_bare_us_state_codes_or_georgia():
    lowered = {s.lower() for s in LOCATION_CFG["us_signals"]}
    assert "us" not in lowered
    assert "georgia" not in lowered
    assert not [s for s in LOCATION_CFG["us_signals"] if len(s) == 2]
