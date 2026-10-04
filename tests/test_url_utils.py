import pytest

from job_scanner.url_utils import normalize_url, platform_for_url


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


def test_strips_trailing_apply_lever():
    url = "https://jobs.lever.co/cx2/003bd199-3191-4b97-8ee6-4ace916cb446/apply"
    assert normalize_url(url) == "https://jobs.lever.co/cx2/003bd199-3191-4b97-8ee6-4ace916cb446"


def test_strips_trailing_apply_workable_with_slash():
    url = "https://apply.workable.com/tiger-analytics/j/13C9E82229/apply/"
    assert normalize_url(url) == "https://apply.workable.com/tiger-analytics/j/13C9E82229"


def test_strips_trailing_apply_nvidia_keeps_path_case():
    url = (
        "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/"
        "Tegra-System-Software-Engineer_JR2026588/apply"
    )
    assert normalize_url(url) == (
        "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/"
        "Tegra-System-Software-Engineer_JR2026588"
    )


def test_apply_variant_matches_plain_url():
    plain = "https://jobs.lever.co/acme/1234"
    assert normalize_url(plain + "/apply") == normalize_url(plain)


def test_apply_only_stripped_as_last_segment():
    url = "https://example.com/apply/jobs/1"
    assert normalize_url(url) == url
    assert normalize_url("https://example.com/jobs/reapply") == "https://example.com/jobs/reapply"


def test_strips_team_page_location_action():
    url = "https://example.com/jobs/1?team=SFTWR&page=32&location=India&action=apply"
    assert normalize_url(url) == "https://example.com/jobs/1"


def test_strips_new_params_keeps_others():
    url = "https://www.google.com/about/careers/applications/jobs/results/9449-design?A&page=32"
    assert normalize_url(url) == "https://www.google.com/about/careers/applications/jobs/results/9449-design?A"


def test_keeps_snap_id_param():
    url = "https://careers.snap.com/job?id=R0047005"
    assert normalize_url(url) == url


def test_keeps_id_and_gh_jid_with_new_params():
    url = "https://company.com/careers/apply?gh_jid=123&team=x&id=9&location=US"
    assert normalize_url(url) == "https://company.com/careers?gh_jid=123&id=9"


def test_idempotent():
    urls = [
        "https://Example.com/Jobs/1/?utm_source=x&gh_jid=9&ref=y#frag",
        "https://example.com/",
        "https://example.com/a?b=1",
        "https://jobs.lever.co/acme/1234/apply/",
        "https://example.com/jobs/1/apply/apply",
        "https://example.com/jobs/1?team=x&id=5&page=2",
    ]
    for url in urls:
        once = normalize_url(url)
        assert normalize_url(once) == once


def test_mixed_everything():
    url = "https://WWW.Example.COM/Careers/SWE/?utm_campaign=a&gh_jid=42&source=b&gh_src=c&ref=d#top"
    assert normalize_url(url) == "https://www.example.com/Careers/SWE?gh_jid=42"


@pytest.mark.parametrize("url, platform", [
    ("https://jobs.ashbyhq.com/cohere/7133d205-5421-4336-a8e2-6037b5f55073", "ashby"),
    ("https://job-boards.greenhouse.io/anthropic/jobs/123", "greenhouse"),
    ("https://boards.greenhouse.io/stripe/jobs/456", "greenhouse"),
    ("https://stripe.com/jobs/listing/swe/789?gh_jid=789", "greenhouse"),
    ("https://jobs.lever.co/acme/abc-123", "lever"),
    ("https://apply.workable.com/acme/j/ABC123/", "workable"),
    ("https://wellfound.com/jobs/123-software-engineer", "wellfound"),
    ("https://www.ycombinator.com/companies/acme/jobs/abc-swe", "workatastartup"),
    ("https://www.welcometothejungle.com/en/companies/acme/jobs/swe", "welcometothejungle"),
    ("https://jobs.smartrecruiters.com/Acme/123-software-engineer", "smartrecruiters"),
    ("https://jobs.jobvite.com/acme/job/oABC", "jobvite"),
    ("https://acme.breezy.hr/p/abc-software-engineer", "breezy"),
    ("https://jobs.a16z.com/jobs/acme/swe", "a16z"),
    ("https://acme.wd1.myworkdayjobs.com/en-US/External/job/SWE_R123", "workday"),
    ("https://www.amazon.jobs/en/jobs/123/sde", "company_site"),
    ("https://www.google.com/about/careers/applications/jobs/results/123-swe", "company_site"),
    ("https://jobs.apple.com/en-us/details/200-123/swe", "company_site"),
    ("https://jobs.careers.microsoft.com/global/en/job/123", "company_site"),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/SWE_JR1", "company_site"),
    ("https://www.tesla.com/careers/search/job/swe-123", "company_site"),
    ("https://www.metacareers.com/jobs/123/", "company_site"),
    ("https://explore.jobs.netflix.net/careers/job/123", "company_site"),
    ("https://careers.snap.com/job?id=R123", "company_site"),
    ("https://jobs.sequoiacap.com/companies/acme/jobs/1", "other"),
])
def test_platform_for_url(url, platform):
    assert platform_for_url(url) == platform


def test_platform_host_is_case_insensitive():
    assert platform_for_url("HTTPS://JOBS.LEVER.CO/Acme/1") == "lever"
    assert platform_for_url("https://Acme.Breezy.HR/p/1") == "breezy"


def test_platform_gh_jid_param_case_insensitive_on_company_domain():
    assert platform_for_url("https://careers.example.com/open?GH_JID=42") == "greenhouse"


def test_platform_lookalike_hosts_are_other():
    assert platform_for_url("https://notgreenhouse.io/jobs/1") == "other"
    assert platform_for_url("https://jobs.lever.co.evil.com/x") == "other"
    assert platform_for_url("") == "other"
