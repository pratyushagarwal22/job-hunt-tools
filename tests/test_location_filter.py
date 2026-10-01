from job_scanner.location_filter import passes_location_filter

EXCLUDE = ["London", "UK", "India", "Bangalore", "Taiwan", "Taipei", "New Delhi", "Tel Aviv"]


def ats(location):
    return passes_location_filter(location, "Software Engineer", "", "", EXCLUDE, True)


def search(title="Software Engineer", snippet="", url="https://example.com/jobs/1"):
    return passes_location_filter(None, title, snippet, url, EXCLUDE, False)


def test_ats_london_fails():
    assert not ats("London")


def test_ats_remote_passes():
    assert ats("Remote")


def test_ats_remote_us_passes():
    assert ats("Remote - US")


def test_ats_new_york_passes():
    assert ats("New York, NY")


def test_ats_blank_passes():
    assert ats("")


def test_ats_none_passes():
    assert ats(None)


def test_ats_bangalore_india_fails():
    assert not ats("Bangalore, India")


def test_ats_case_insensitive():
    assert not ats("LONDON, uk")


def test_ats_whole_word_only():
    assert ats("Indianapolis, IN")
    assert ats("Ukiah, CA")


def test_ats_multi_word_keyword():
    assert not ats("Tel Aviv, Israel")
    assert ats("Aviv Street, Austin TX")


def test_ats_ignores_title_snippet_and_url():
    assert passes_location_filter(
        "San Francisco, CA",
        "Software Engineer - London",
        "Based in India",
        "https://example.com/jobs/Taiwan-Taipei",
        EXCLUDE,
        True,
    )


def test_search_ignores_location_field():
    assert passes_location_filter(
        "London", "Software Engineer", "Seattle, WA", "https://example.com/1", EXCLUDE, False
    )


def test_search_title_location_fails():
    assert not search(title="Software Engineer - London")


def test_search_snippet_location_fails():
    assert not search(snippet="Join our team in Bangalore, India.")


def test_search_url_path_location_fails():
    assert not search(url="https://nvidia.wd5.myworkdayjobs.com/en-US/jobs/job/Taiwan-Taipei/Software-Engineer_JR123")


def test_search_url_query_location_fails():
    assert not search(url="https://www.google.com/about/careers/applications/jobs/results?location=India")


def test_search_url_encoded_location_fails():
    assert not search(url="https://example.com/jobs?city=New%20Delhi")
    assert not search(url="https://example.com/jobs?city=New+Delhi")


def test_search_us_job_passes():
    assert search(
        title="Software Engineer, New Grad",
        snippet="Seattle, WA. Hybrid. We sponsor visas.",
        url="https://job-boards.greenhouse.io/acme/jobs/123",
    )


def test_search_blank_everything_passes():
    assert passes_location_filter(None, "", "", "", EXCLUDE, False)


def test_search_none_fields_pass():
    assert passes_location_filter(None, None, None, None, EXCLUDE, False)


def test_empty_exclude_list_passes():
    assert passes_location_filter("London", "", "", "", [], True)
