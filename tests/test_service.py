import re
from datetime import datetime, timedelta, timezone

import anthropic
import httpx2
import pytest
import requests

from job_scanner import service
from job_scanner.ats_client import AtsError
from job_scanner.scoring_client import ScoreResult, Usage

TZ = "America/Chicago"
QUERY_A = 'site:jobs.example.com ("Software Engineer")'
QUERY_B = 'site:careers.example.org ("Software Engineer")'
USAGE_PER_CALL = Usage(input=1000, output=200, cache_read=4000, cache_write=500)


def _settings(**overrides) -> dict:
    settings = dict(
        max_jobs_per_run=50,
        max_pages_per_query=2,
        search=dict(tbs="qdr:test", gl="xx", num=7),
        ats_lookback_days=3,
        request_timeout_seconds=7,
        jd_min_chars=321,
        timezone=TZ,
        scoring=dict(model="test-model", max_tokens=111, max_jd_chars=2222, force_tool_choice=False),
        experience_filter=dict(max_years_required=2),
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
                 score=None, ashby_boards=None):
        self.search_pages = search_pages or {}
        self.ats_jobs = ats_jobs or {}
        self.ashby_boards = ashby_boards or {}
        self.ashby_calls = []
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
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.setattr(service, "_load_config", lambda: config)
        monkeypatch.setattr(service.search_client, "search", self._search)
        for ats in ("greenhouse", "lever", "ashby"):
            monkeypatch.setattr(service.ats_client, f"list_{ats}_jobs", self._ats(ats))
        monkeypatch.setattr(service.ats_client, "get_ashby_board", self._ashby_board)
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
            jobs = self.ats_jobs.get(slug, [])
            if isinstance(jobs, Exception):
                raise jobs
            return [dict(job, source=ats) for job in jobs]
        return list_jobs

    def _ashby_board(self, slug, timeout=15):
        self.ashby_calls.append((slug, timeout))
        board = self.ashby_boards.get(slug, AtsError("ashby", slug, "HTTP 404"))
        if isinstance(board, Exception):
            raise board
        return board

    def _fetch_jd(self, url, timeout, min_chars):
        self.jd_calls.append((url, timeout, min_chars))
        return self.jd.get(url, ("Fetched full JD for " + url, "full"))

    def _score(self, **kwargs):
        self.score_calls.append(kwargs)
        result = self.score_impl(**kwargs) if self.score_impl else _score_result()
        return result, USAGE_PER_CALL

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
        "https://jobs.example.com/snip",
    ]
    assert summary["filtered"] == 4
    assert summary["new"] == 8


def test_always_allow_and_us_signals_read_from_settings(monkeypatch):
    h = Harness(
        monkeypatch,
        settings=_settings(
            title_filter=dict(include=["Software Engineer"], exclude=["Senior", "Staff"],
                              always_allow=["Member of Technical Staff"]),
            location_filter=dict(exclude=["London", "Zurich"], us_signals=["New York"]),
        ),
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/mts", title="Member of Technical Staff"),
            _search_result("https://s.example.com/smts", title="Senior Member of Technical Staff"),
            _search_result("https://s.example.com/ny", title="Software Engineer, New York or Zurich"),
            _search_result("https://s.example.com/zh", title="Software Engineer, Zurich"),
            _search_result("https://s.example.com/snip", snippet="Offices in Zurich"),
        ]]},
        ats_jobs={"acme": [_ats_job("https://a.example.com/both", location="New York; London")]},
    )

    service.run_pipeline(None)

    assert sorted(h.urls()) == [
        "https://a.example.com/both",
        "https://s.example.com/mts",
        "https://s.example.com/ny",
        "https://s.example.com/snip",
    ]


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


ASHBY_ID_1 = "7133d205-5421-4336-a8e2-6037b5f55073"
ASHBY_ID_2 = "3136a5a5-06fd-4c82-8b72-a43467e6b128"
ASHBY_URL_1 = f"https://jobs.ashbyhq.com/cohere/{ASHBY_ID_1}"
ASHBY_URL_2 = f"https://jobs.ashbyhq.com/cohere/{ASHBY_ID_2}"


def _ashby_posting(job_id, jd_text):
    return dict(id=job_id, title="Software Engineer",
                url=f"https://jobs.ashbyhq.com/cohere/{job_id}", jd_text=jd_text)


def test_ashby_search_url_gets_jd_from_api_board_cached(monkeypatch, caplog):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[_search_result(ASHBY_URL_1),
                                 _search_result(ASHBY_URL_2 + "/application?utm_source=x")]]},
        ashby_boards={"cohere": [_ashby_posting(ASHBY_ID_1, "Ashby JD one"),
                                 _ashby_posting(ASHBY_ID_2, "Ashby JD two")]},
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(None)

    assert h.ashby_calls == [("cohere", 7)]
    assert h.jd_calls == []
    assert [c["jd_text"] for c in h.score_calls] == ["Ashby JD one", "Ashby JD two"]
    assert all(c["jd_source"] == "full" for c in h.score_calls)
    assert [row[2] for row in h.rows] == ["full", "full"]
    assert _messages(caplog, "JD") == ["JD full fetched 2 | snippet fallback 0 | ATS full 0"]


def test_ashby_job_missing_from_board_falls_back_to_jd_client(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[_search_result(ASHBY_URL_1), _search_result(ASHBY_URL_2)]]},
        ashby_boards={"cohere": [_ashby_posting(ASHBY_ID_1, "")]},
        jd={ASHBY_URL_2: (None, "snippet")},
    )

    service.run_pipeline(None)

    assert h.ashby_calls == [("cohere", 7)]
    assert [url for url, _, _ in h.jd_calls] == [ASHBY_URL_1, ASHBY_URL_2]
    first, second = h.score_calls
    assert first["jd_text"] == "Fetched full JD for " + ASHBY_URL_1
    assert second["jd_source"] == "snippet"


def test_ashby_board_error_falls_back_and_is_not_refetched(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[_search_result(ASHBY_URL_1), _search_result(ASHBY_URL_2)]]},
        ashby_boards={"cohere": AtsError("ashby", "cohere", "HTTP 500")},
    )

    summary = service.run_pipeline(None)

    assert h.ashby_calls == [("cohere", 7)]
    assert [url for url, _, _ in h.jd_calls] == [ASHBY_URL_1, ASHBY_URL_2]
    assert summary["scored"] == 2


def test_non_ashby_search_url_skips_ashby_api(monkeypatch):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[_search_result("https://jobs.lever.co/acme/abc"),
                                 _search_result("https://jobs.ashbyhq.com/cohere")]]},
    )

    service.run_pipeline(None)

    assert h.ashby_calls == []
    assert [url for url, _, _ in h.jd_calls] == [
        "https://jobs.lever.co/acme/abc", "https://jobs.ashbyhq.com/cohere",
    ]


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
        assert call["force_tool_choice"] is False
    assert ats_call["title_hint"] == "Software Engineer"
    assert ats_call["company_hint"] == "Acme"
    assert ats_call["jd_text"] == "Full ATS JD"
    assert not search_call.get("title_hint")
    assert not search_call.get("company_hint")


def test_experience_filter_threshold_drops_before_scoring(monkeypatch, caplog):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/two"),
            _search_result("https://s.example.com/snip", snippet="Requires 5+ years of experience."),
            _search_result("https://s.example.com/none"),
        ]]},
        ats_jobs={"acme": [
            _ats_job("https://a.example.com/three", jd_text="3+ years of experience with Go."),
        ]},
        jd={
            "https://s.example.com/two": ("You have 2 years of experience. " * 30, "full"),
            "https://s.example.com/snip": (None, "snippet"),
        },
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        summary = service.run_pipeline(None)

    assert sorted(h.urls()) == ["https://s.example.com/none", "https://s.example.com/two"]
    assert len(h.score_calls) == 2
    assert all("3+ years" not in c["jd_text"] and "5+ years" not in c["jd_text"]
               for c in h.score_calls)
    assert summary["filtered"] == 2
    assert summary["scored"] == 2
    drops = [r.getMessage() for r in caplog.records if r.getMessage().startswith("DROP experience")]
    assert sorted(drops) == [
        "DROP experience 3 | 3+ years of experience | Software Engineer | https://a.example.com/three",
        "DROP experience 5 | 5+ years of experience | Software Engineer | https://s.example.com/snip",
    ]


def test_experience_threshold_read_from_settings(monkeypatch):
    h = Harness(
        monkeypatch,
        settings=_settings(experience_filter=dict(max_years_required=3)),
        queries=[],
        ats_jobs={"acme": [_ats_job("https://a.example.com/three", jd_text="3+ years of experience.")]},
    )

    summary = service.run_pipeline(None)

    assert h.urls() == ["https://a.example.com/three"]
    assert summary["filtered"] == 0


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


# --- run logging ---

PRICES = {"test-model": dict(input=1.0, output=10.0, cache_read=0.1, cache_write=2.0)}
LONG_ERROR = "x" * 300 + "\nsecond line"


def _messages(caplog, prefix: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


def _funnel_harness(monkeypatch, extra_pages=None, **overrides):
    """found 11 = sheet 1 + in-run 1 + no URL 1 + new 8; new 8 = title 1 + location 1 + eligible 6;
    eligible 6 = over cap 1 + experience 1 + scored 3 + failed 1 (with cap 5)."""
    scores = {"keep-a": 9.0, "dup1": 6.0, "keep-b": 2.9}

    def score(**kwargs):
        if "/fail" in kwargs["jd_text"]:
            raise ValueError(LONG_ERROR)
        name = kwargs["jd_text"].rsplit("/", 1)[-1]
        return _score_result(match_score=scores[name])

    params = dict(
        settings=_settings(prices=PRICES),
        existing={"https://s.example.com/old"},
        companies=[],
        search_pages={QUERY_A: [
            [
                _search_result("https://s.example.com/old"),
                _search_result("https://s.example.com/dup1"),
                _search_result("https://s.example.com/dup1?ref=x"),
                _search_result(""),
                _search_result("https://s.example.com/senior", title="Senior Software Engineer"),
                _search_result("https://s.example.com/london", title="Software Engineer London"),
                _search_result("https://s.example.com/keep-a", date="1 hour ago"),
                _search_result("https://s.example.com/keep-b"),
            ],
            [
                _search_result("https://s.example.com/exp", snippet="Requires 5+ years of experience."),
                _search_result("https://s.example.com/fail"),
                _search_result("https://s.example.com/keep-c"),
            ],
        ]},
        jd={"https://s.example.com/exp": (None, "snippet")},
        score=score,
    )
    params["search_pages"].update(extra_pages or {})
    params.update(overrides)
    return Harness(monkeypatch, **params)


def test_funnel_numbers_add_up(monkeypatch, caplog):
    _funnel_harness(monkeypatch)

    with caplog.at_level("INFO", logger="job_scanner"):
        summary = service.run_pipeline(5)

    assert _messages(caplog, "FUNNEL") == [
        "FUNNEL found 11 = dupes (sheet) 1 + dupes (in-run) 1 + no URL 1 + new 8"
        " | new = title dropped 1 + location dropped 1 + eligible 6"
        " | eligible = over cap 1 + experience dropped 1 + scored 3 + failed 1"
    ]
    assert summary == dict(found=11, new=8, filtered=3, scored=3, failed=1, skipped_cap=1)


def test_drop_lines_per_filtered_job(monkeypatch, caplog):
    _funnel_harness(monkeypatch)

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(5)

    assert _messages(caplog, "DROP") == [
        "DROP title Senior | Senior Software Engineer | https://s.example.com/senior",
        "DROP location London (title) | Software Engineer London | https://s.example.com/london",
        "DROP cap | Software Engineer | https://s.example.com/keep-c",
        "DROP experience 5 | 5+ years of experience | Software Engineer | https://s.example.com/exp",
    ]


def test_drop_title_no_include_match_and_location_where(monkeypatch, caplog):
    Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/pm", title="Product Designer"),
            _search_result("https://s.example.com/x?location=India"),
        ]]},
        ats_jobs={"acme": [_ats_job("https://a.example.com/ldn", location="London, UK")]},
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(None)

    assert sorted(_messages(caplog, "DROP")) == [
        "DROP location India (url) | Software Engineer | https://s.example.com/x",
        "DROP location London (location) | Software Engineer | https://a.example.com/ldn",
        "DROP title no include match | Product Designer | https://s.example.com/pm",
    ]


def test_snippet_only_non_us_mention_is_not_dropped(monkeypatch, caplog):
    h = Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/snip", snippet="Join our team in Bangalore."),
        ]]},
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(None)

    assert h.urls() == ["https://s.example.com/snip"]
    assert _messages(caplog, "DROP location") == []


def test_pipe_in_title_replaced_in_drop_lines(monkeypatch, caplog):
    Harness(
        monkeypatch,
        search_pages={QUERY_A: [[
            _search_result("https://s.example.com/krk", title="Software Engineer (Hybrid | London)"),
            _search_result("https://s.example.com/sr", title="Senior Software Engineer | Payments"),
            _search_result("https://s.example.com/cap1", title="Software Engineer | A"),
            _search_result("https://s.example.com/cap2", title="Software Engineer | B"),
        ]]},
        jd={"https://s.example.com/cap1": ("Requires 5+ years of experience. " * 20, "full")},
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(1)

    assert _messages(caplog, "DROP") == [
        "DROP location London (title) | Software Engineer (Hybrid / London) | https://s.example.com/krk",
        "DROP title Senior | Senior Software Engineer / Payments | https://s.example.com/sr",
        "DROP cap | Software Engineer / B | https://s.example.com/cap2",
        "DROP experience 5 | 5+ years of experience | Software Engineer / A | https://s.example.com/cap1",
    ]


def test_no_cap_drop_lines_when_under_cap(monkeypatch, caplog):
    _funnel_harness(monkeypatch)

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(None)

    assert _messages(caplog, "DROP cap") == []


def test_fail_job_line_has_url_type_and_truncated_message(monkeypatch, caplog):
    _funnel_harness(monkeypatch)

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(5)

    (line,) = _messages(caplog, "FAIL job")
    prefix = "FAIL job https://s.example.com/fail | ValueError: "
    assert line.startswith(prefix)
    message = line[len(prefix):]
    assert len(message) == 150
    assert message.endswith("...")
    assert "\n" not in line


def test_short_fail_message_kept_whole(monkeypatch, caplog):
    def score(**kwargs):
        raise KeyError("unexpected")

    Harness(monkeypatch, search_pages={QUERY_A: [[_search_result("https://s.example.com/a")]]},
            score=score)

    with caplog.at_level("INFO", logger="job_scanner"), pytest.raises(RuntimeError):
        service.run_pipeline(None)

    assert _messages(caplog, "FAIL job") == ["FAIL job https://s.example.com/a | KeyError: 'unexpected'"]


def test_query_lines_and_serper_call_total(monkeypatch, caplog):
    _funnel_harness(
        monkeypatch,
        company_sites=[dict(name="B", query=QUERY_B, enabled=True),
                       dict(name="C", query="site:c.example.com", enabled=True)],
        extra_pages={"site:c.example.com": requests.ConnectTimeout("timed out")},
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(5)

    assert _messages(caplog, "QUERY") == [
        "QUERY A | pages 2 | results 11 | new 8 | passed 6 | new+passed from page 2+ 3",
        "QUERY B | pages 1 | results 0 | new 0 | passed 0 | new+passed from page 2+ 0",
        "QUERY C | pages 0 | results 0 | new 0 | passed 0 | new+passed from page 2+ 0",
        "QUERY total Serper calls 4",
    ]
    assert _messages(caplog, "FAIL search") == ["FAIL search C page 1 | ConnectTimeout"]


def test_fail_search_line_has_http_status(monkeypatch, caplog):
    Harness(
        monkeypatch,
        company_sites=[dict(name="B", query=QUERY_B, enabled=True)],
        search_pages={QUERY_A: RuntimeError("Serper search failed: HTTP 500"),
                      QUERY_B: [[_search_result("https://s.example.com/b")]]},
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(None)

    assert _messages(caplog, "FAIL search") == ["FAIL search A page 1 | HTTP 500"]


def test_ats_lines_list_empty_and_errored_companies(monkeypatch, caplog):
    Harness(
        monkeypatch,
        queries=[],
        companies=[
            dict(name="Acme", ats="greenhouse", slug="acme"),
            dict(name="Beta", ats="lever", slug="beta"),
            dict(name="Gamma", ats="ashby", slug="gamma"),
            dict(name="Delta", ats="workday", slug="delta"),
        ],
        ats_jobs={"acme": [_ats_job("https://a.example.com/1")],
                  "gamma": AtsError("ashby", "gamma", "HTTP 404")},
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        summary = service.run_pipeline(None)

    assert _messages(caplog, "ATS") == [
        "ATS polled 4 companies | 1 jobs within 3-day lookback",
        "ATS 0 jobs (1): Beta",
        "ATS errors (2): ashby:gamma (HTTP 404), workday:delta (unknown ats)",
    ]
    assert summary["scored"] == 1


def test_jd_scores_and_cost_lines(monkeypatch, caplog):
    _funnel_harness(monkeypatch)

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(5)

    assert _messages(caplog, "JD") == ["JD full fetched 4 | snippet fallback 1 | ATS full 0"]
    assert _messages(caplog, "SCORES") == ["SCORES 8-10: 1 | 6-7.9: 1 | 3-5.9: 0 | 1-2.9: 1"]
    # 3 calls: (3000*1 + 600*10 + 12000*0.1 + 1500*2) / 1e6
    assert _messages(caplog, "COST") == [
        "COST input 3000 | output 600 | cache read 12000 | cache write 1500 | est $0.0132 (test-model)"
    ]


def test_jd_counts_ats_full_and_ats_snippet(monkeypatch, caplog):
    Harness(
        monkeypatch,
        queries=[],
        ats_jobs={"acme": [_ats_job("https://a.example.com/full"),
                           _ats_job("https://a.example.com/empty", jd_text="")]},
    )

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(None)

    assert _messages(caplog, "JD") == ["JD full fetched 0 | snippet fallback 1 | ATS full 1"]


@pytest.mark.parametrize("prices", [
    None,
    {},
    {"other-model": PRICES["test-model"]},
    {"test-model": dict(input=1.0, output=10.0)},
    {"test-model": "free"},
])
def test_cost_unknown_when_model_has_no_valid_price(monkeypatch, caplog, prices):
    settings = _settings() if prices is None else _settings(prices=prices)
    h = Harness(monkeypatch, settings=settings,
                search_pages={QUERY_A: [[_search_result("https://s.example.com/a")]]})

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(None)

    (line,) = _messages(caplog, "COST")
    assert line.endswith("| cost unknown (test-model)")
    assert len(h.rows) == 1


def test_run_start_and_end_lines(monkeypatch, caplog):
    _funnel_harness(monkeypatch)

    with caplog.at_level("INFO", logger="job_scanner"):
        service.run_pipeline(5)

    (start,) = _messages(caplog, "RUN start")
    assert re.fullmatch(
        r"RUN start \d{4}-\d{2}-\d{2} \d{2}:\d{2} C[DS]T \| cap 5 \| ats_lookback_days 3"
        r" \| model test-model \| existing URLs 1",
        start,
    )
    (end,) = _messages(caplog, "RUN end")
    assert re.fullmatch(r"RUN end \| elapsed \d+\.\ds", end)


def test_report_logged_even_when_every_scoring_attempt_fails(monkeypatch, caplog):
    def score(**kwargs):
        raise ValueError("bad")

    Harness(monkeypatch, search_pages={QUERY_A: [[_search_result("https://s.example.com/a")]]},
            score=score)

    with caplog.at_level("INFO", logger="job_scanner"), pytest.raises(RuntimeError):
        service.run_pipeline(None)

    assert len(_messages(caplog, "FUNNEL")) == 1
    assert len(_messages(caplog, "COST")) == 1


def test_logs_never_contain_jd_or_personal_content(monkeypatch, caplog):
    _funnel_harness(monkeypatch)

    with caplog.at_level("DEBUG", logger="job_scanner"):
        service.run_pipeline(5)

    for secret in ("Fetched full JD", "CANDIDATE", "PROFILE"):
        assert secret not in caplog.text


def test_step_summary_written_when_env_set(monkeypatch, tmp_path):
    _funnel_harness(monkeypatch)
    summary_file = tmp_path / "summary.md"
    summary_file.write_text("existing\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    service.run_pipeline(5)

    text = summary_file.read_text()
    assert text.startswith("existing\n")
    assert "## Job Scanner run" in text
    assert "| Found | 11 |" in text
    assert "| Experience dropped | 1 |" in text
    assert "| A | 2 | 11 | 8 | 6 | 3 |" in text
    assert "| 8-10 | 6-7.9 | 3-5.9 | 1-2.9 |" in text
    assert "| 1 | 1 | 0 | 1 |" in text
    assert "est $0.0132 (test-model)" in text


def test_step_summary_write_failure_is_not_fatal(monkeypatch, tmp_path, caplog):
    h = _funnel_harness(monkeypatch)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))

    with caplog.at_level("WARNING", logger="job_scanner"):
        service.run_pipeline(5)

    assert "GITHUB_STEP_SUMMARY" in caplog.text
    assert len(h.rows) == 3
