import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import httpx2
import pytest

from email_finder import service
from email_finder.sheets_repo import CONTACTS_HEADERS

A = "https://linkedin.com/in/a"
B = "https://linkedin.com/in/b"
C = "https://linkedin.com/in/c"
D = "https://linkedin.com/in/d"
E = "https://linkedin.com/in/e"

SETTINGS = {
    "timezone": "America/Chicago",
    "max_urls_per_run": 20,
    "providers": ["prospeo", "apify"],
    "prospeo": {"timeout_seconds": 15, "min_interval_seconds": 3.1},
    "apify": {"actor_id": "vulnv/linkedin-email-finder", "timeout_seconds": 300},
    "apollo": {"batch_size": 10, "timeout_seconds": 15, "min_interval_seconds": 1.2},
    "outreach": {"model": "claude-sonnet-5-5", "max_tokens": 300},
}

NOW = datetime(2026, 10, 6, 21, 5, tzinfo=ZoneInfo("America/Chicago"))


def _match(slug: str, status: str = "verified") -> dict:
    return {
        "name": f"Person {slug.upper()}",
        "company": f"Company {slug.upper()}",
        "title": f"Title {slug.upper()}",
        "email": f"{slug}@example.com",
        "email_status": status,
    }


def _auth_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("denied", response=httpx2.Response(status, request=request), body=None)


class Harness:
    """Fakes every client and the sheet. `prospeo` maps URL -> match / None / Exception;
    `apify` is a callable(urls) -> dict or raises; `apollo` maps URL -> match (absent = no record)
    or is a callable(batch)."""

    def __init__(self, monkeypatch, tmp_path, *, settings=None, existing=(), prospeo=None,
                 apify=None, apollo=None, draft=None):
        self.settings = {**SETTINGS, **(settings or {})}
        self.existing = set(existing)
        self.prospeo = prospeo or {}
        self.apify = apify or (lambda urls: {url: None for url in urls})
        self.apollo = apollo if apollo is not None else {}
        self.draft = draft or (lambda **kw: f"line for {kw['name']}")
        self.events = []
        self.appends = []
        self.drafts = []
        self.candidate_reads = 0

        candidate = tmp_path / "candidate.md"
        candidate.write_text("CANDIDATE")
        self.default_file = tmp_path / "linkedin-profiles.txt"

        monkeypatch.setattr(service, "load_settings", lambda: self.settings)
        monkeypatch.setattr(service, "_CANDIDATE_PATH", candidate)
        monkeypatch.setattr(service, "DEFAULT_INPUT", self.default_file)
        monkeypatch.setattr(service, "_now", lambda tz_name: NOW)
        monkeypatch.setenv("SHEET_ID", "sheet-1")
        monkeypatch.setattr(service.sheets_repo, "get_columns", self._get_columns)
        monkeypatch.setattr(service.sheets_repo, "get_existing_keys", self._existing_keys)
        monkeypatch.setattr(service.sheets_repo, "append_contacts", self._append)
        monkeypatch.setattr(service.prospeo_client, "enrich_person", self._prospeo)
        monkeypatch.setattr(service.apify_client, "find_emails", self._apify)
        monkeypatch.setattr(service.apollo_client, "bulk_match", self._apollo)
        monkeypatch.setattr(service.outreach_client, "draft_outreach", self._draft)

    def _get_columns(self, sheet_id):
        self.events.append(("columns",))
        return {name: i for i, name in enumerate(CONTACTS_HEADERS)}

    def _existing_keys(self, sheet_id, columns):
        return set(self.existing), {"someone@example.com"}

    def _append(self, sheet_id, rows, columns):
        self.events.append(("append", len(rows)))
        self.appends.append(rows)

    def _prospeo(self, linkedin_url, timeout):
        self.events.append(("prospeo", linkedin_url, timeout))
        result = self.prospeo.get(linkedin_url)
        if isinstance(result, Exception):
            raise result
        return result

    def _apify(self, urls, actor_id, timeout):
        self.events.append(("apify", list(urls), actor_id, timeout))
        return self.apify(list(urls))

    def _apollo(self, linkedin_urls, timeout):
        self.events.append(("apollo", list(linkedin_urls), timeout))
        if callable(self.apollo):
            return self.apollo(list(linkedin_urls))
        return {url: self.apollo[url] for url in linkedin_urls if url in self.apollo}

    def _draft(self, **kwargs):
        self.drafts.append(kwargs)
        return self.draft(**kwargs)

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))

    def run(self, urls=(), file_path=None, draft=False):
        return service.run(list(urls), file_path, draft, sleep=self.sleep)

    def calls(self, kind):
        return [event for event in self.events if event[0] == kind]

    def rows_by_url(self):
        (rows,) = self.appends
        return {row["linkedin_url"]: row for row in rows}


# --- provider chain -------------------------------------------------------------------


def test_chain_order_prospeo_no_matches_go_to_apify(monkeypatch, tmp_path):
    h = Harness(
        monkeypatch, tmp_path,
        prospeo={A: _match("a"), B: None, C: None},
        apify=lambda urls: {
            "https://www.linkedin.com/in/b/": {**_match("b", "unverified"), "title": ""},
            C: None,
        },
    )

    result = h.run([A, B, C])

    assert [e[1] for e in h.calls("prospeo")] == [A, B, C]
    assert [e[1] for e in h.calls("apify")] == [[B, C]]
    assert h.calls("apify")[0][2:] == ("vulnv/linkedin-email-finder", 300)
    assert result["exit_code"] == 0
    assert result["lines"] == [
        f"{A} found via prospeo verified",
        f"{B} found via apify unverified",
        f"{C} not found",
    ]
    rows = h.rows_by_url()
    assert list(rows) == [A, B, C]
    assert rows[A]["email_source"] == "prospeo"
    assert rows[A]["email"] == "a@example.com"
    assert rows[B]["email_source"] == "apify"
    assert rows[B]["email_status"] == "unverified"
    assert result["summary"] == {
        "input": 3, "ignored": 0, "duplicate": 0, "existing": 0,
        "found": {"prospeo": 1, "apify": 1}, "not_found": 1, "pending": 0,
    }


def test_matched_urls_never_reach_the_next_provider(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a"), B: _match("b")})
    h.run([A, B])
    assert h.calls("apify") == []


def test_row_contents(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a", "verified"), B: None})

    h.run([A, B])

    rows = h.rows_by_url()
    assert set(rows[A]) == set(CONTACTS_HEADERS)
    assert rows[A] == {
        "contacted": "", "name": "Person A", "company": "Company A", "title": "Title A",
        "email": "a@example.com", "email_status": "verified", "email_source": "prospeo",
        "linkedin_url": A, "notes": "", "outreach_line": "", "date_found": "2026-10-06 21:05",
    }
    assert rows[B] == {
        "contacted": "", "name": "", "company": "", "title": "", "email": "",
        "email_status": "not found", "email_source": "", "linkedin_url": B, "notes": "",
        "outreach_line": "", "date_found": "2026-10-06 21:05",
    }


def test_one_append_at_the_end(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a")})
    h.run([A, B])
    assert h.calls("append") == [("append", 2)]
    assert h.events[-1] == ("append", 2)


def test_blank_status_line_has_no_trailing_space(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a", "")})
    result = h.run([A])
    assert result["lines"] == [f"{A} found via prospeo"]


# --- spacing ----------------------------------------------------------------------------


def test_prospeo_calls_spaced_by_min_interval(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a"), B: _match("b"), C: _match("c")})

    h.run([A, B, C])

    sequence = [e[0] if e[0] != "sleep" else ("sleep", e[1]) for e in h.events
                if e[0] in ("prospeo", "sleep")]
    assert sequence == ["prospeo", ("sleep", 3.1), "prospeo", ("sleep", 3.1), "prospeo"]
    assert all(e[2] == 15 for e in h.calls("prospeo"))


def test_apollo_batches_spaced_by_min_interval(monkeypatch, tmp_path):
    h = Harness(
        monkeypatch, tmp_path,
        settings={"providers": ["apollo"],
                  "apollo": {"batch_size": 2, "timeout_seconds": 15, "min_interval_seconds": 1.2}},
        apollo={A: _match("a"), D: _match("d", "Extrapolated".lower())},
    )

    result = h.run([A, B, C, D, E])

    assert [e[1] for e in h.calls("apollo")] == [[A, B], [C, D], [E]]
    assert all(e[2] == 15 for e in h.calls("apollo"))
    sequence = [e[0] if e[0] != "sleep" else ("sleep", e[1]) for e in h.events
                if e[0] in ("apollo", "sleep")]
    assert sequence == ["apollo", ("sleep", 1.2), "apollo", ("sleep", 1.2), "apollo"]
    assert result["summary"]["found"] == {"apollo": 2}
    assert result["summary"]["not_found"] == 3
    assert h.rows_by_url()[D]["email_source"] == "apollo"


def test_no_sleep_for_a_single_lookup(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a")})
    h.run([A])
    assert h.calls("sleep") == []


# --- stop on provider errors -------------------------------------------------------------


def test_prospeo_error_appends_resolved_rows_and_reports_pending(monkeypatch, tmp_path, caplog):
    h = Harness(
        monkeypatch, tmp_path,
        prospeo={A: _match("a"), B: RuntimeError("Prospeo enrich-person failed: HTTP 429 RATE_LIMITED")},
    )

    result = h.run([A, B, C])

    assert [e[1] for e in h.calls("prospeo")] == [A, B]
    assert h.calls("apify") == []
    assert result["exit_code"] == 1
    assert list(h.rows_by_url()) == [A]
    assert result["lines"] == [f"{A} found via prospeo verified", f"{B} pending", f"{C} pending"]
    assert result["summary"]["pending"] == 2
    assert "HTTP 429" in caplog.text


def test_no_matches_waiting_for_a_later_provider_are_pending_on_stop(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: None, B: RuntimeError("Prospeo: HTTP 500")})

    result = h.run([A, B, C])

    assert result["exit_code"] == 1
    assert h.appends == []
    assert result["lines"] == [f"{A} pending", f"{B} pending", f"{C} pending"]


def test_apify_error_leaves_its_urls_pending(monkeypatch, tmp_path):
    def apify(urls):
        raise RuntimeError("Apify actor run failed: HTTP 401")

    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a"), B: None}, apify=apify)

    result = h.run([A, B])

    assert result["exit_code"] == 1
    assert list(h.rows_by_url()) == [A]
    assert result["lines"] == [f"{A} found via prospeo verified", f"{B} pending"]


def test_apify_missing_item_stops_but_keeps_returned_items(monkeypatch, tmp_path, caplog):
    h = Harness(
        monkeypatch, tmp_path,
        prospeo={A: None, B: None, C: None},
        apify=lambda urls: {A: {**_match("a", "unverified"), "title": ""}, B: None},
    )

    result = h.run([A, B, C])

    assert result["exit_code"] == 1
    rows = h.rows_by_url()
    assert list(rows) == [A, B]
    assert rows[B]["email_status"] == "not found"
    assert result["lines"] == [f"{A} found via apify unverified", f"{B} not found", f"{C} pending"]
    assert "no item" in caplog.text


def test_apollo_error_in_a_later_batch_keeps_earlier_batches(monkeypatch, tmp_path):
    def apollo(batch):
        if batch[0] == C:
            raise RuntimeError("Apollo bulk_match failed: HTTP 403 API_INACCESSIBLE")
        return {A: _match("a")}

    h = Harness(
        monkeypatch, tmp_path,
        settings={"providers": ["apollo"],
                  "apollo": {"batch_size": 2, "timeout_seconds": 15, "min_interval_seconds": 1.2}},
        apollo=apollo,
    )

    result = h.run([A, B, C, D])

    assert result["exit_code"] == 1
    assert list(h.rows_by_url()) == [A, B]
    assert result["lines"] == [
        f"{A} found via apollo verified", f"{B} not found", f"{C} pending", f"{D} pending",
    ]


def test_unexpected_exception_type_also_stops(monkeypatch, tmp_path, caplog):
    h = Harness(monkeypatch, tmp_path, prospeo={A: KeyError("secret-detail")})
    result = h.run([A])
    assert result["exit_code"] == 1
    assert result["lines"] == [f"{A} pending"]
    assert "KeyError" in caplog.text
    assert "secret-detail" not in caplog.text


# --- input handling ----------------------------------------------------------------------


def test_ignored_duplicate_and_existing_lines(monkeypatch, tmp_path):
    path = tmp_path / "in.txt"
    path.write_text(
        "# comment\n"
        "https://www.linkedin.com/in/A/?x=1  # recruiter note\n"
        "https://linkedin.com/company/example  # company note\n"
        "https://uk.linkedin.com/in/a\n"
        "https://linkedin.com/in/b/\n"
        "https://linkedin.com/in/c # met at meetup\n"
        "not a url at all\n"
    )
    h = Harness(monkeypatch, tmp_path, existing={B}, prospeo={A: _match("a"), C: None})

    result = h.run(file_path=str(path))

    assert result["lines"] == [
        f"{A} found via prospeo verified",
        "ignored https://linkedin.com/company/example (not a profile URL)",
        f"duplicate {A}",
        f"{B} exists",
        f"{C} not found",
        "ignored not a url at all (not a profile URL)",
    ]
    assert result["summary"] == {
        "input": 6, "ignored": 2, "duplicate": 1, "existing": 1,
        "found": {"prospeo": 1, "apify": 0}, "not_found": 1, "pending": 0,
    }
    rows = h.rows_by_url()
    assert rows[A]["notes"] == "recruiter note"
    assert rows[C]["notes"] == "met at meetup"
    assert [e[1] for e in h.calls("prospeo")] == [A, C]


@pytest.mark.parametrize(
    "url",
    [
        "https://linkedin.com/in/",
        "https://linkedin.com/pub/a",
        "https://example.com/in/a",
    ],
)
def test_non_profile_urls_are_ignored(monkeypatch, tmp_path, url):
    h = Harness(monkeypatch, tmp_path)
    result = h.run([url])
    assert result["lines"][0].startswith("ignored ")
    assert result["summary"]["ignored"] == 1
    assert h.calls("prospeo") == []


def test_extra_profile_path_is_trimmed_and_looked_up(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, existing={B}, prospeo={A: _match("a")})

    result = h.run([
        "https://www.linkedin.com/in/a/details/experience/",
        "https://linkedin.com/in/a/recent-activity/",
        "https://linkedin.com/in/b/overlay/contact-info/",
    ])

    assert result["lines"] == [f"{A} found via prospeo verified", f"duplicate {A}", f"{B} exists"]
    assert [e[1] for e in h.calls("prospeo")] == [A]
    assert list(h.rows_by_url()) == [A]


def test_cli_urls_then_file(monkeypatch, tmp_path):
    path = tmp_path / "in.txt"
    path.write_text(f"{B}\n{A}\n")
    h = Harness(monkeypatch, tmp_path, prospeo={A: None, B: None})

    result = h.run([A], file_path=str(path))

    assert result["lines"] == [f"{A} not found", f"{B} not found", f"duplicate {A}"]


def test_default_file_when_no_urls_and_no_file(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: None})
    h.default_file.write_text(f"{A}\n")
    result = h.run()
    assert result["lines"] == [f"{A} not found"]


def test_all_existing_makes_no_lookups_and_no_append(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, existing={A, B})
    result = h.run([A, "https://www.linkedin.com/in/b/"])
    assert result["exit_code"] == 0
    assert result["lines"] == [f"{A} exists", f"{B} exists"]
    assert h.calls("prospeo") == [] and h.calls("apify") == []
    assert h.appends == []


# --- cap ---------------------------------------------------------------------------------


def test_cap_counted_after_existing_and_duplicates(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, settings={"max_urls_per_run": 2}, existing={C})
    result = h.run([A, B, C, A])
    assert result["exit_code"] == 0
    assert len(h.calls("prospeo")) == 2


def test_cap_exceeded_raises_before_any_lookup(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, settings={"max_urls_per_run": 2})
    with pytest.raises(service.TooManyUrlsError, match="3 URLs to look up.*max_urls_per_run 2"):
        h.run([A, B, C])
    assert h.calls("prospeo") == [] and h.calls("apify") == []
    assert h.appends == []


def test_unknown_provider_raises_before_any_lookup(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, settings={"providers": ["prospeo", "hunter"]})
    with pytest.raises(ValueError, match="hunter"):
        h.run([A])
    assert h.calls("prospeo") == []


# --- drafting ----------------------------------------------------------------------------


def test_no_draft_flag_means_no_drafting(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a")})
    h.run([A])
    assert h.drafts == []


def test_draft_for_found_rows_only(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a"), B: None})

    result = h.run([A, B], draft=True)

    assert result["exit_code"] == 0
    assert h.drafts == [{
        "name": "Person A", "company": "Company A", "title": "Title A",
        "candidate_md": "CANDIDATE", "model": "claude-sonnet-5-5", "max_tokens": 300,
    }]
    rows = h.rows_by_url()
    assert rows[A]["outreach_line"] == "line for Person A"
    assert rows[B]["outreach_line"] == ""
    assert all("line for" not in line for line in result["lines"])


def test_draft_failure_leaves_line_blank_warns_and_continues(monkeypatch, tmp_path, caplog):
    def draft(**kw):
        if kw["name"] == "Person A":
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))
        return "drafted"

    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a"), B: _match("b")}, draft=draft)

    result = h.run([A, B], draft=True)

    assert result["exit_code"] == 0
    rows = h.rows_by_url()
    assert rows[A]["outreach_line"] == ""
    assert rows[B]["outreach_line"] == "drafted"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Person A" not in caplog.text and "a@example.com" not in caplog.text


@pytest.mark.parametrize(
    "cls, status",
    [(anthropic.AuthenticationError, 401), (anthropic.PermissionDeniedError, 403)],
)
def test_anthropic_auth_error_stops_drafting_appends_all_rows_exit_1(
    monkeypatch, tmp_path, cls, status
):
    def draft(**kw):
        raise _auth_error(cls, status)

    h = Harness(
        monkeypatch, tmp_path, prospeo={A: _match("a"), B: _match("b"), C: None}, draft=draft
    )

    result = h.run([A, B, C], draft=True)

    assert result["exit_code"] == 1
    assert len(h.drafts) == 1
    rows = h.rows_by_url()
    assert list(rows) == [A, B, C]
    assert all(row["outreach_line"] == "" for row in rows.values())


def test_drafting_still_runs_for_found_rows_after_a_provider_stop(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, prospeo={A: _match("a"), B: RuntimeError("HTTP 429")})
    result = h.run([A, B], draft=True)
    assert result["exit_code"] == 1
    assert h.rows_by_url()[A]["outreach_line"] == "line for Person A"


# --- privacy -----------------------------------------------------------------------------


def test_no_names_emails_or_notes_in_lines_or_logs(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    path = tmp_path / "in.txt"
    path.write_text(f"{A} # secret-note\n{B}\n")
    h = Harness(
        monkeypatch, tmp_path,
        prospeo={A: _match("a"), B: None},
        apify=lambda urls: {B: _match("b", "unverified")},
    )

    result = h.run(file_path=str(path), draft=True)

    text = "\n".join(result["lines"]) + caplog.text
    for secret in ("Person", "Company", "Title", "@example.com", "secret-note", "line for"):
        assert secret not in text


# --- env check ---------------------------------------------------------------------------


def test_required_env_vars_follow_enabled_providers_and_draft():
    settings = {**SETTINGS, "providers": ["prospeo", "apify"]}
    assert service.required_env_vars(settings, draft=False) == [
        "SHEET_ID", "GOOGLE_APPLICATION_CREDENTIALS", "PROSPEO_API_KEY", "APIFY_TOKEN",
    ]
    assert service.required_env_vars({**settings, "providers": ["apollo"]}, draft=True) == [
        "SHEET_ID", "GOOGLE_APPLICATION_CREDENTIALS", "APOLLO_API_KEY", "ANTHROPIC_API_KEY",
    ]
