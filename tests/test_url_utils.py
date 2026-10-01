from job_scanner.url_utils import normalize_url


def test_strips_utm_params():
    url = "https://example.com/jobs/1?utm_source=google&utm_medium=cpc&utm_campaign=x"
    assert normalize_url(url) == "https://example.com/jobs/1"


def test_strips_ref():
    assert normalize_url("https://example.com/jobs/1?ref=abc") == "https://example.com/jobs/1"


def test_strips_source():
    assert normalize_url("https://example.com/jobs/1?source=linkedin") == "https://example.com/jobs/1"


def test_strips_gh_src():
    assert normalize_url("https://example.com/jobs/1?gh_src=abc123") == "https://example.com/jobs/1"


def test_keeps_other_params_in_order():
    url = "https://example.com/jobs?b=2&utm_source=x&a=1"
    assert normalize_url(url) == "https://example.com/jobs?b=2&a=1"


def test_keeps_gh_jid():
    url = "https://company.com/careers?gh_jid=123&gh_src=abc"
    assert normalize_url(url) == "https://company.com/careers?gh_jid=123"


def test_different_gh_jid_stay_different():
    a = normalize_url("https://company.com/jobs?gh_jid=123")
    b = normalize_url("https://company.com/jobs?gh_jid=456")
    assert a != b
    assert a == "https://company.com/jobs?gh_jid=123"
    assert b == "https://company.com/jobs?gh_jid=456"


def test_strips_trailing_slash():
    assert normalize_url("https://example.com/jobs/1/") == "https://example.com/jobs/1"


def test_strips_multiple_trailing_slashes():
    assert normalize_url("https://example.com/jobs/1///") == "https://example.com/jobs/1"


def test_strips_trailing_slash_before_query():
    assert normalize_url("https://example.com/jobs/?gh_jid=1") == "https://example.com/jobs?gh_jid=1"


def test_strips_fragment():
    assert normalize_url("https://example.com/jobs/1#apply") == "https://example.com/jobs/1"


def test_lowercases_domain_keeps_path_case():
    url = "https://Job-Boards.Greenhouse.IO/Anthropic/jobs/123"
    assert normalize_url(url) == "https://job-boards.greenhouse.io/Anthropic/jobs/123"


def test_lowercases_scheme():
    assert normalize_url("HTTPS://example.com/a") == "https://example.com/a"


def test_tracking_param_names_case_insensitive():
    assert normalize_url("https://example.com/a?UTM_Source=x&Ref=y") == "https://example.com/a"


def test_keeps_param_values_verbatim():
    url = "https://example.com/search?q=software%20engineer&loc=New+York"
    assert normalize_url(url) == url


def test_idempotent():
    urls = [
        "https://Example.com/Jobs/1/?utm_source=x&gh_jid=9&ref=y#frag",
        "https://example.com/",
        "https://example.com/a?b=1",
    ]
    for url in urls:
        once = normalize_url(url)
        assert normalize_url(once) == once


def test_mixed_everything():
    url = "https://WWW.Example.COM/Careers/SWE/?utm_campaign=a&gh_jid=42&source=b&gh_src=c&ref=d#top"
    assert normalize_url(url) == "https://www.example.com/Careers/SWE?gh_jid=42"
