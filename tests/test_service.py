import re
from datetime import datetime, timedelta, timezone

import anthropic
import httpx2
import pytest

from job_scanner import service
from job_scanner.scoring_client import ScoreResult

TZ = "America/Chicago"
QUERY_A = 'site:jobs.example.com ("Software Engineer")'
QUERY_B = 'site:careers.example.org ("Software Engineer")'


def _settings(**overrides) -> dict:
    settings = dict(
        max_jobs_per_run=50,
        max_pages_per_query=2,
        search=dict(tbs="qdr:test", gl="xx", num=7),
        ats_lookback_days=3,
        request_timeout_seconds=7,
        jd_min_chars=321,
        timezone=TZ,
        scoring=dict(model="test-model", max_tokens=111, max_jd_chars=2222),
        title_filter=dict(include=["Software Engineer"], exclude=["Senior"]),
        location_filter=dict(exclude=["London", "India", "Bangalore"]),
    )
    settings.update(overrides)
    return settings


def _search_result(link, title="Software Engineer", snippet="Build things.", date=None):
    return dict(title=title, link=link, snippet=snippet, date=date, position=1)


def _ats_job(url, title="Software Engineer", location="Remote", posted=None, jd_text="Full ATS JD"):
    if posted is None:
        posted = datetime.now(timezone.utc).isoformat()
    return dict(
        title=title,
        company="Acme",
        location=location,
        url=url,
        posted_date=posted,
        jd_text=jd_text,
        source="greenhouse",
    )


def _hours_ago_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _score_result(**overrides) -> ScoreResult:
    fields = dict(
        match_score=7.5,
        title="Haiku Title",
        company="Haiku Co",
        location="Haiku Loc",
        visa_mention="Not mentioned",
        core_requirements="Python",
        reason="Good fit",
    )
    fields.update(overrides)
    return ScoreResult(**fields)


class Harness:
    def __init__(self, monkeypatch, *, settings=None, queries=None, company_sites=None,
                 companies=None, search_pages=None, ats_jobs=None, existing=(), jd=None,
                 score=None):
        self.search_pages = search_pages or {}
        self.ats_jobs = ats_jobs or {}
        self.existing = set(existing)
        self.jd = jd or {}
        self.score_impl = score
        self.search_calls = []
        self.jd_calls = []
        self.score_calls = []
        self.appends = []

        config = dict(
            settings=settings or _settings(),
            queries=queries if queries is not None else [
                dict(name="A", query=QUERY_A, enabled=True)
            ],
            company_sites=company_sites or [],
            companies=companies if companies is not None else [
                dict(name="Acme", ats="greenhouse", slug="acme")
            ],
            candidate_md="CANDIDATE",
            profile_yaml="PROFILE",
        )
        monkeypatch.setenv("SHEET_ID", "sheet-123")
        monkeypatch.setattr(service, "_load_config", lambda: config)
        monkeypatch.setattr(service.search_client, "search", self._search)
        for ats in ("greenhouse", "lever", "ashby"):
            monkeypatch.setattr(service.ats_client, f"list_{ats}_jobs", self._ats(ats))
        monkeypatch.setattr(service.jd_client, "fetch_jd", self._fetch_jd)
        monkeypatch.setattr(service.scoring_client, "score_job", self._score)
        monkeypatch.setattr(service.sheets_repo, "get_existing_urls", self._existing)
        monkeypatch.setattr(service.sheets_repo, "append_jobs", self._append)

    def _search(self, query, tbs, gl, num, page, timeout=15):
        self.search_calls.append((query, page))
        self.search_params = (tbs, gl, num, timeout)
        pages = self.search_pages.get(query, [])
        if isinstance(pages, Exception):
            raise pages
        return pages[page - 1] if page <= len(pages) else []

    def _ats(self, ats):
        def list_jobs(slug, company, lookback_days, timeout=15):
            return [dict(job, source=ats) for job in self.ats_jobs.get(slug, [])]
        return list_jobs

    def _fetch_jd(self, url, timeout, min_chars):
        self.jd_calls.append((url, timeout, min_chars))
        return self.jd.get(url, ("Fetched full JD for " + url, "full"))

    def _score(self, **kwargs):
        self.score_calls.append(kwargs)
        if self.score_impl:
            return self.score_impl(**kwargs)
        return _score_result()

    def _existing(self, sheet_id):
        assert sheet_id == "sheet-123"
        return set(self.existing)

    def _append(self, sheet_id, rows):
        assert sheet_id == "sheet-123"
        self.appends.append(rows)

    @property
    def rows(self):
        assert len(self.appends) == 1
        return self.appends[0]

    def urls(self):
        return [row[9] for row in self.rows]


def test_dedup_against_sheet_and_in_run_ats_wins(monkeypatch):
    h = Harness(
        monkeypatch,
        existing={"https://jobs.example.com/old"},
        search_pages={QUERY_A: [[
            _search_result("https://jobs.example.com/old/?utm_source=x"),
            _search_result("https://jobs.example.com/both/"),
            _search_result("https://jobs.example.com/both?ref=abc"),
        ]]},
        ats_jobs={"acme": [_ats_job("https://JOBS.example.com/both")]},
    )

    summary = service.run_pipeline(None)

    assert h.urls() == ["https://jobs.example.com/both"]
    (row,) = h.rows
    assert row[2] == "full"
    assert row[12] == "greenhouse:Acme"
    assert h.jd_calls == []
    assert summary["found"] == 4
    assert summary["new"] == 1


def test_title_and_location_filters_applied(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://jobs.example.com/keep"),
            _search_result("https://jobs.example.com/senior", title="Senior Software Engineer"),
            _search_result("https://jobs.example.com/pm", title="Product Manager"),
            _search_result("https://jobs.example.com/snip", snippet="Based in Bangalore"),
            _search_result("https://jobs.example.com/x?location=India"),
        ]]},
        ats_jobs={"acme": [
            _ats_job("https://ats.example.com/london", location="London, UK"),
            _ats_job("https://ats.example.com/ok", location="New York, NY"),
            _ats_job("https://ats.example.com/india-title", title="Software Engineer India",
                     location="Remote"),
        ]},
    )

    summary = service.run_pipeline(None)

    assert sorted(h.urls()) == [
        "https://ats.example.com/india-title",
        "https://ats.example.com/ok",
        "https://jobs.example.com/keep",
    ]
    assert summary["filtered"] == 5
    assert summary["new"] == 8


def test_sorted_newest_first_undated_last_in_original_order(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/undated1"),
            _search_result("https://s.example.com/2days", date="2 days ago"),
            _search_result("https://s.example.com/garbage", date="sometime"),
            _search_result("https://s.example.com/1hour", date="1 hour ago"),
            _search_result("https://s.example.com/undated2"),
        ]]},
        ats_jobs={"acme": [_ats_job("https://a.example.com/5hours", posted=_hours_ago_iso(5))]},
    )

    service.run_pipeline(None)

    assert h.urls() == [
        "https://s.example.com/1hour",
        "https://a.example.com/5hours",
        "https://s.example.com/2days",
        "https://s.example.com/undated1",
        "https://s.example.com/garbage",
        "https://s.example.com/undated2",
    ]


def test_cap_keeps_newest_and_counts_skipped(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/old", date="3 days ago"),
            _search_result("https://s.example.com/new", date="1 hour ago"),
            _search_result("https://s.example.com/undated"),
            _search_result("https://s.example.com/mid", date="1 day ago"),
        ]]},
        ats_jobs={},
    )

    summary = service.run_pipeline(2)

    assert h.urls() == ["https://s.example.com/new", "https://s.example.com/mid"]
    assert len(h.score_calls) == 2
    assert summary["skipped_cap"] == 2
    assert summary["scored"] == 2


def test_cap_defaults_to_settings(monkeypatch):
    h = Harness(
        monkeypatch,
        settings=_settings(max_jobs_per_run=1),
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/a"),
            _search_result("https://s.example.com/b"),
        ]]},
    )

    summary = service.run_pipeline(None)

    assert h.urls() == ["https://s.example.com/a"]
    assert summary["skipped_cap"] == 1


def test_failed_jobs_not_written_and_counted(monkeypatch):
    def score(**kwargs):
        if "bad-validation" in kwargs["jd_text"]:
            raise ValueError("Invalid score result")
        if "bad-api" in kwargs["jd_text"]:
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))
        if "bad-other" in kwargs["jd_text"]:
            raise KeyError("unexpected")
        return _score_result()

    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/good"),
            _search_result("https://s.example.com/bad-validation"),
            _search_result("https://s.example.com/bad-api"),
            _search_result("https://s.example.com/bad-other"),
        ]]},
        score=score,
    )

    summary = service.run_pipeline(None)

    assert h.urls() == ["https://s.example.com/good"]
    assert summary["scored"] == 1
    assert summary["failed"] == 3


def test_rows_have_13_columns_in_sheet_order(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/job?utm_medium=x", date="just now"),
        ]]},
        ats_jobs={"acme": [
            _ats_job("https://a.example.com/job", title="Software Engineer, New Grad",
                     location="Austin, TX", posted="2026-01-15T12:00:00Z"),
            _ats_job("https://a.example.com/noloc", location="", posted="2026-01-14T12:00:00Z"),
        ]},
        score=lambda **kw: _score_result(match_score=8.2),
    )

    service.run_pipeline(None)

    assert all(len(row) == 13 for row in h.rows)
    search_row, ats_row, noloc_row = h.rows
    today = datetime.now(service.ZoneInfo(TZ)).strftime("%Y-%m-%d")

    assert search_row[0] == ""
    assert search_row[:10] == [
        "", 8.2, "full", "Haiku Title", "Haiku Co", "Haiku Loc", "Not mentioned", "Python",
        "Good fit", "https://s.example.com/job",
    ]
    assert search_row[10] == today
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", search_row[11])
    assert search_row[12] == QUERY_A

    assert ats_row == [
        "", 8.2, "full", "Software Engineer, New Grad", "Acme", "Austin, TX", "Not mentioned",
        "Python", "Good fit", "https://a.example.com/job", "2026-01-15", search_row[11],
        "greenhouse:Acme",
    ]
    assert noloc_row[5] == "Haiku Loc"
    assert noloc_row[10] == "2026-01-14"


def test_undated_job_has_blank_posted_date(monkeypatch):
    h = Harness(monkeypatch, search_pages={QUERY_A: [[_search_result("https://s.example.com/a")]]})

    service.run_pipeline(None)

    assert h.rows[0][10] == ""


def test_search_snippet_fallback(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/js", title="Software Engineer at JSCo",
                           snippet="We build apps."),
        ]]},
        jd={"https://s.example.com/js": (None, "snippet")},
    )

    service.run_pipeline(None)

    assert h.jd_calls == [("https://s.example.com/js", 7, 321)]
    (call,) = h.score_calls
    assert call["jd_source"] == "snippet"
    assert "Software Engineer at JSCo" in call["jd_text"]
    assert "We build apps." in call["jd_text"]
    assert h.rows[0][2] == "snippet"


def test_ats_empty_jd_scored_from_title_and_location(monkeypatch):
    h = Harness(
        monkeypatch,
        queries=[],
        ats_jobs={"acme": [_ats_job("https://a.example.com/empty", location="Denver, CO",
                                    jd_text="")]},
    )

    service.run_pipeline(None)

    (call,) = h.score_calls
    assert call["jd_source"] == "snippet"
    assert "Software Engineer" in call["jd_text"]
    assert "Denver, CO" in call["jd_text"]
    assert h.rows[0][2] == "snippet"


def test_scoring_settings_and_hints_passed(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[_search_result("https://s.example.com/a", date="1 hour ago")]]},
        ats_jobs={"acme": [_ats_job("https://a.example.com/a", posted=_hours_ago_iso(100))]},
    )

    service.run_pipeline(None)

    search_call, ats_call = h.score_calls
    for call in (search_call, ats_call):
        assert call["model"] == "test-model"
        assert call["max_tokens"] == 111
        assert call["max_jd_chars"] == 2222
        assert call["candidate_md"] == "CANDIDATE"
        assert call["profile_yaml"] == "PROFILE"
    assert ats_call["title_hint"] == "Software Engineer"
    assert ats_call["company_hint"] == "Acme"
    assert ats_call["jd_text"] == "Full ATS JD"
    assert not search_call.get("title_hint")
    assert not search_call.get("company_hint")


def test_pagination_stops_early_on_empty_page(monkeypatch):
    h = Harness(
        monkeypatch,
        settings=_settings(max_pages_per_query=3),
        queries=[dict(name="A", query=QUERY_A, enabled=True),
                 dict(name="Off", query="disabled", enabled=False)],
        company_sites=[dict(name="B", query=QUERY_B, enabled=True)],
        search_pages={
            QUERY_A: [[_search_result("https://s.example.com/1")],
                      [_search_result("https://s.example.com/2")],
                      [_search_result("https://s.example.com/3")]],
            QUERY_B: [[]],
        },
    )

    summary = service.run_pipeline(None)

    assert h.search_calls == [(QUERY_A, 1), (QUERY_A, 2), (QUERY_A, 3), (QUERY_B, 1)]
    assert summary["found"] == 3


def test_search_params_read_from_settings(monkeypatch):
    h = Harness(monkeypatch, search_pages={QUERY_A: [[]]})

    service.run_pipeline(None)

    assert h.search_params == ("qdr:test", "xx", 7, 7)


def test_single_append_with_all_scored_rows(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[_search_result(f"https://s.example.com/{i}") for i in range(3)]]},
    )

    service.run_pipeline(None)

    assert len(h.appends) == 1
    assert len(h.rows) == 3


def test_all_scoring_failed_raises(monkeypatch):
    def score(**kwargs):
        raise ValueError("Invalid score result")

    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[_search_result("https://s.example.com/a")]]},
        score=score,
    )

    with pytest.raises(RuntimeError, match="scor"):
        service.run_pipeline(None)
    assert h.appends == []


def test_nothing_to_score_is_not_an_error(monkeypatch):
    h = Harness(monkeypatch, search_pages={QUERY_A: [[]]})

    summary = service.run_pipeline(None)

    assert summary == dict(found=0, new=0, filtered=0, scored=0, failed=0, skipped_cap=0)
    assert h.appends == []


@pytest.mark.parametrize("error_cls, status", [
    (anthropic.AuthenticationError, 401),
    (anthropic.PermissionDeniedError, 403),
])
def test_anthropic_auth_errors_are_fatal(monkeypatch, error_cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = error_cls("denied", response=httpx2.Response(status, request=request), body=None)

    def score(**kwargs):
        raise err

    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[_search_result("https://s.example.com/a"),
                                 _search_result("https://s.example.com/b")]]},
        score=score,
    )

    with pytest.raises(error_cls):
        service.run_pipeline(None)
    assert len(h.score_calls) == 1
    assert h.appends == []


def test_every_search_query_failed_is_fatal(monkeypatch):
    Harness(
        monkeypatch,
        company_sites=[dict(name="B", query=QUERY_B, enabled=True)],
        search_pages={QUERY_A: RuntimeError("Serper search failed: HTTP 403"),
                      QUERY_B: RuntimeError("Serper search failed: HTTP 403")},
    )

    with pytest.raises(RuntimeError, match="search"):
        service.run_pipeline(None)


def test_one_search_query_failing_is_not_fatal(monkeypatch):
    h = Harness(
        monkeypatch,
        company_sites=[dict(name="B", query=QUERY_B, enabled=True)],
        search_pages={QUERY_A: RuntimeError("Serper search failed: HTTP 500"),
                      QUERY_B: [[_search_result("https://s.example.com/b")]]},
    )

    service.run_pipeline(None)

    assert h.urls() == ["https://s.example.com/b"]


def test_sheet_read_error_is_fatal(monkeypatch):
    Harness(monkeypatch)

    def boom(sheet_id):
        raise RuntimeError("Google Sheets read failed: HTTP 403")

    monkeypatch.setattr(service.sheets_repo, "get_existing_urls", boom)

    with pytest.raises(RuntimeError, match="Sheets"):
        service.run_pipeline(None)


def test_sheet_append_error_is_fatal(monkeypatch):
    Harness(monkeypatch, search_pages={QUERY_A: [[_search_result("https://s.example.com/a")]]})

    def boom(sheet_id, rows):
        raise RuntimeError("Google Sheets append failed: HTTP 500")

    monkeypatch.setattr(service.sheets_repo, "append_jobs", boom)

    with pytest.raises(RuntimeError, match="Sheets"):
        service.run_pipeline(None)
