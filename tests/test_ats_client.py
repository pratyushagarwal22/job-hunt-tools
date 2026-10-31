import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from job_scanner import ats_client
from job_scanner.ats_client import (
    AtsError,
    get_ashby_board,
    get_greenhouse_job,
    get_lever_job,
    list_ashby_jobs,
    list_greenhouse_jobs,
    list_lever_jobs,
)

FIXTURES = Path(__file__).parent / "fixtures"
JOB_KEYS = {"title", "company", "location", "url", "posted_date", "jd_text", "source"}
NOW = datetime.now(timezone.utc)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _response(body, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.json.return_value = body
    return resp


def _call(fn, body, status: int = 200, lookback_days: int = 3650, **kwargs):
    with patch.object(
        ats_client.requests, "get", return_value=_response(body, status)
    ) as get:
        jobs = fn("acme", "Acme Inc", lookback_days=lookback_days, **kwargs)
    return jobs, get


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _ms(days_ago: float) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp() * 1000)


# --- field mapping ---


def test_greenhouse_field_mapping():
    raw = _load("greenhouse_list.json")
    jobs, get = _call(list_greenhouse_jobs, raw, timeout=7)

    get.assert_called_once()
    assert get.call_args.args[0] == (
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    )
    assert get.call_args.kwargs["timeout"] == 7
    assert len(jobs) == len(raw["jobs"]) == 3
    for job, item in zip(jobs, raw["jobs"]):
        assert set(job) == JOB_KEYS
        assert job["title"] == item["title"]
        assert job["company"] == "Acme Inc"
        assert job["location"] == item["location"]["name"]
        assert job["url"] == item["absolute_url"]
        assert job["posted_date"] == item["first_published"]
        assert job["source"] == "greenhouse"


def test_lever_field_mapping():
    raw = _load("lever_list.json")
    jobs, get = _call(list_lever_jobs, raw)

    assert get.call_args.args[0] == "https://api.lever.co/v0/postings/acme"
    assert len(jobs) == len(raw) == 3
    for job, item in zip(jobs, raw):
        assert set(job) == JOB_KEYS
        assert job["title"] == item["text"]
        assert job["company"] == "Acme Inc"
        assert job["location"] == item["categories"]["location"]
        assert job["url"] == item["hostedUrl"]
        assert job["posted_date"] == item["createdAt"]
        assert job["source"] == "lever"
        assert item["descriptionPlain"].strip()[:50] in job["jd_text"]
        for section in item["lists"]:
            assert section["text"] in job["jd_text"]
        assert "<" not in job["jd_text"]


def test_ashby_field_mapping():
    raw = _load("ashby_list.json")
    jobs, get = _call(list_ashby_jobs, raw)

    assert get.call_args.args[0] == "https://api.ashbyhq.com/posting-api/job-board/acme"
    assert len(jobs) == len(raw["jobs"]) == 3
    for job, item in zip(jobs, raw["jobs"]):
        assert set(job) == JOB_KEYS
        assert job["title"] == item["title"]
        assert job["company"] == "Acme Inc"
        assert job["location"] == item["location"]
        assert job["url"] == item["jobUrl"]
        assert job["posted_date"] == item["publishedAt"]
        assert job["jd_text"] == item["descriptionPlain"].strip()
        assert job["source"] == "ashby"


def test_ashby_falls_back_to_html_when_plain_missing():
    raw = _load("ashby_list.json")
    del raw["jobs"][0]["descriptionPlain"]
    jobs, _ = _call(list_ashby_jobs, raw)
    assert jobs[0]["jd_text"]
    assert "<" not in jobs[0]["jd_text"]


# --- HTML stripping ---


def test_greenhouse_unescapes_and_strips_html():
    raw = _load("greenhouse_list.json")
    assert "&lt;" in raw["jobs"][0]["content"]
    jobs, _ = _call(list_greenhouse_jobs, raw)

    for job in jobs:
        text = job["jd_text"]
        assert len(text) > 100
        assert "<" not in text and ">" not in text
        assert "&lt;" not in text and "&amp;" not in text and "&quot;" not in text
    assert jobs[0]["jd_text"].startswith("About Anthropic")


def test_greenhouse_html_entities_inside_text_are_decoded():
    raw = _load("greenhouse_list.json")
    raw["jobs"][0]["content"] = html.escape("<p>R&amp;D <b>team</b></p>")
    jobs, _ = _call(list_greenhouse_jobs, raw)
    assert jobs[0]["jd_text"] == "R&D\nteam"


# --- lookback filter ---


def test_greenhouse_lookback_filter():
    raw = _load("greenhouse_list.json")
    for job, days_ago in zip(raw["jobs"], [1, 30, 2]):
        job["first_published"] = _iso(days_ago)
    jobs, _ = _call(list_greenhouse_jobs, raw, lookback_days=7)
    assert [j["url"] for j in jobs] == [
        raw["jobs"][0]["absolute_url"],
        raw["jobs"][2]["absolute_url"],
    ]


def test_lever_lookback_filter_uses_unix_ms():
    raw = _load("lever_list.json")
    for job, days_ago in zip(raw, [1, 30, 2]):
        job["createdAt"] = _ms(days_ago)
    jobs, _ = _call(list_lever_jobs, raw, lookback_days=7)
    assert [j["url"] for j in jobs] == [raw[0]["hostedUrl"], raw[2]["hostedUrl"]]


def test_ashby_lookback_filter():
    raw = _load("ashby_list.json")
    for job, days_ago in zip(raw["jobs"], [1, 30, 2]):
        job["publishedAt"] = _iso(days_ago).replace("+00:00", "Z")
    jobs, _ = _call(list_ashby_jobs, raw, lookback_days=7)
    assert [j["url"] for j in jobs] == [raw["jobs"][0]["jobUrl"], raw["jobs"][2]["jobUrl"]]


@pytest.mark.parametrize(
    "fn,fixture,date_key",
    [
        (list_greenhouse_jobs, "greenhouse_list.json", None),
        (list_lever_jobs, "lever_list.json", "createdAt"),
        (list_ashby_jobs, "ashby_list.json", "publishedAt"),
    ],
)
def test_jobs_without_date_are_dropped(fn, fixture, date_key):
    raw = _load(fixture)
    items = raw if isinstance(raw, list) else raw["jobs"]
    if date_key is None:
        del items[0]["first_published"]
        del items[0]["updated_at"]
    else:
        del items[0][date_key]
    jobs, _ = _call(fn, raw)
    assert len(jobs) == len(items) - 1


def test_unparseable_date_is_dropped():
    raw = _load("ashby_list.json")
    raw["jobs"][0]["publishedAt"] = "not a date"
    jobs, _ = _call(list_ashby_jobs, raw)
    assert len(jobs) == 2


# --- Greenhouse date fallback ---


def test_greenhouse_falls_back_to_updated_at():
    raw = _load("greenhouse_list.json")
    del raw["jobs"][0]["first_published"]
    raw["jobs"][0]["updated_at"] = _iso(1)
    raw["jobs"][1]["first_published"] = None
    raw["jobs"][1]["updated_at"] = _iso(2)
    raw["jobs"][2]["first_published"] = _iso(30)
    raw["jobs"][2]["updated_at"] = _iso(1)

    jobs, _ = _call(list_greenhouse_jobs, raw, lookback_days=7)

    assert [j["posted_date"] for j in jobs] == [
        raw["jobs"][0]["updated_at"],
        raw["jobs"][1]["updated_at"],
    ]


# --- bad slug / errors ---


@pytest.mark.parametrize("fn", [list_greenhouse_jobs, list_lever_jobs, list_ashby_jobs])
@pytest.mark.parametrize("status", [404, 500])
def test_non_2xx_raises_ats_error_with_status(fn, status):
    with pytest.raises(AtsError) as info:
        _call(fn, {"error": "not found"}, status=status)
    assert info.value.detail == f"HTTP {status}"
    assert "acme" in str(info.value)


@pytest.mark.parametrize("fn", [list_greenhouse_jobs, list_lever_jobs, list_ashby_jobs])
def test_request_exception_raises_ats_error_with_type(fn):
    with patch.object(
        ats_client.requests, "get", side_effect=requests.ConnectionError("boom")
    ):
        with pytest.raises(AtsError) as info:
            fn("acme", "Acme Inc", lookback_days=7)
    assert info.value.detail == "ConnectionError"
    assert "acme" in str(info.value)


def test_invalid_json_raises_ats_error():
    resp = _response(None)
    resp.json.side_effect = ValueError("no json")
    with patch.object(ats_client.requests, "get", return_value=resp):
        with pytest.raises(AtsError) as info:
            list_ashby_jobs("acme", "Acme Inc", lookback_days=7)
    assert info.value.detail == "invalid JSON"


# --- single Ashby board (JD lookup) ---


def test_get_ashby_board_returns_all_jobs_ignoring_age():
    raw = _load("ashby_list.json")
    for job, days_ago in zip(raw["jobs"], [1, 365, 3000]):
        job["publishedAt"] = _iso(days_ago)
    del raw["jobs"][1]["descriptionPlain"]
    with patch.object(ats_client.requests, "get", return_value=_response(raw)) as get:
        board = get_ashby_board("cohere", timeout=7)

    assert get.call_args.args[0] == "https://api.ashbyhq.com/posting-api/job-board/cohere"
    assert get.call_args.kwargs["timeout"] == 7
    assert [b["id"] for b in board] == [j["id"] for j in raw["jobs"]]
    assert [b["url"] for b in board] == [j["jobUrl"] for j in raw["jobs"]]
    assert board[0]["jd_text"] == raw["jobs"][0]["descriptionPlain"].strip()
    assert board[1]["jd_text"] and "<" not in board[1]["jd_text"]
    assert [b["location"] for b in board] == [j["location"] for j in raw["jobs"]]


def test_get_ashby_board_errors_raise_ats_error():
    with patch.object(ats_client.requests, "get", return_value=_response({}, 404)):
        with pytest.raises(AtsError) as info:
            get_ashby_board("acme")
    assert info.value.detail == "HTTP 404"
    with patch.object(ats_client.requests, "get", side_effect=requests.Timeout("slow")):
        with pytest.raises(AtsError) as info:
            get_ashby_board("acme")
    assert info.value.detail == "Timeout"


def test_get_greenhouse_job_parses_jd_and_location():
    item = _load("greenhouse_list.json")["jobs"][0]
    with patch.object(ats_client.requests, "get", return_value=_response(item)) as get:
        job = get_greenhouse_job("anthropic", item["id"], timeout=7)

    assert get.call_args.args[0] == (
        f"https://boards-api.greenhouse.io/v1/boards/anthropic/jobs/{item['id']}"
    )
    assert get.call_args.kwargs["timeout"] == 7
    assert job["location"] == item["location"]["name"]
    assert job["jd_text"] and "<" not in job["jd_text"]
    assert "Anthropic" in job["jd_text"]


def test_get_lever_job_parses_jd_and_location():
    item = _load("lever_list.json")[0]
    with patch.object(ats_client.requests, "get", return_value=_response(item)) as get:
        job = get_lever_job("palantir", item["id"], timeout=9)

    assert get.call_args.args[0] == (
        f"https://api.lever.co/v0/postings/palantir/{item['id']}"
    )
    assert get.call_args.kwargs["timeout"] == 9
    assert job["location"] == item["categories"]["location"]
    assert "World-Changing Company" in job["jd_text"]
    assert "Provide administrative support" in job["jd_text"]


@pytest.mark.parametrize("fn,job_id", [(get_greenhouse_job, 123), (get_lever_job, "abc")])
def test_single_job_errors_raise_ats_error(fn, job_id):
    with patch.object(ats_client.requests, "get", return_value=_response({}, 404)):
        with pytest.raises(AtsError) as info:
            fn("acme", job_id)
    assert info.value.detail == "HTTP 404"
    with patch.object(ats_client.requests, "get", side_effect=requests.Timeout("slow")):
        with pytest.raises(AtsError) as info:
            fn("acme", job_id)
    assert info.value.detail == "Timeout"


def test_empty_board_returns_empty_list():
    jobs, _ = _call(list_greenhouse_jobs, {"jobs": []})
    assert jobs == []
