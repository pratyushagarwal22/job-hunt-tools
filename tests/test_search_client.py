import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_scanner import search_client
from job_scanner.search_client import search

FIXTURES = Path(__file__).parent / "fixtures"
RESULT_KEYS = {"title", "link", "snippet", "date", "position"}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _response(body: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.json.return_value = body
    return resp


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-key")


def _search(body: dict, status: int = 200):
    with patch.object(search_client.requests, "post", return_value=_response(body, status)) as post:
        results = search("q", tbs="qdr:d", gl="us", num=10, page=1)
    return results, post


@pytest.mark.parametrize(
    "fixture,domain",
    [
        ("serper_site_query.json", "greenhouse.io"),
        ("serper_company_sites_query.json", "amazon.jobs"),
    ],
)
def test_parses_organic_results(fixture, domain):
    raw = _load(fixture)
    results, _ = _search(raw)

    assert len(results) == len(raw["organic"]) > 0
    for result, item in zip(results, raw["organic"]):
        assert set(result) == RESULT_KEYS
        assert result == {k: item[k] for k in RESULT_KEYS}
        assert domain in result["link"]


def test_drops_extra_fields_from_results():
    raw = _load("serper_site_query.json")
    raw["organic"][0]["sitelinks"] = [{"title": "x", "link": "y"}]
    results, _ = _search(raw)
    assert set(results[0]) == RESULT_KEYS


def test_missing_date_is_none():
    raw = _load("serper_site_query.json")
    del raw["organic"][0]["date"]
    results, _ = _search(raw)
    assert results[0]["date"] is None
    assert results[1]["date"] == raw["organic"][1]["date"]


def test_no_organic_returns_empty_list():
    raw = _load("serper_site_query.json")
    del raw["organic"]
    results, _ = _search(raw)
    assert results == []


def test_http_error_raises_with_status():
    with pytest.raises(RuntimeError, match="403"):
        _search({"message": "Unauthorized."}, status=403)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY")
    with patch.object(search_client.requests, "post") as post:
        with pytest.raises(RuntimeError, match="SERPER_API_KEY"):
            search("q", tbs="qdr:d", gl="us", num=10, page=1)
    post.assert_not_called()


def test_request_body_and_headers():
    query = 'site:job-boards.greenhouse.io ("Software Engineer" OR "New Grad")'
    with patch.object(
        search_client.requests, "post", return_value=_response({"organic": []})
    ) as post:
        search(query, tbs="qdr:d", gl="us", num=10, page=2, timeout=7)

    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://google.serper.dev/search"
    assert kwargs["headers"]["X-API-KEY"] == "test-key"
    assert kwargs["json"] == {"q": query, "tbs": "qdr:d", "gl": "us", "num": 10, "page": 2}
    assert kwargs["timeout"] == 7
