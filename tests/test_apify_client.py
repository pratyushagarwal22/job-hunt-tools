import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from email_finder import apify_client
from email_finder.apify_client import find_emails

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "apify-secret-token"
ACTOR = "vulnv/linkedin-email-finder"
URLS = ["https://linkedin.com/in/jane-doe-example", "https://linkedin.com/in/john-roe-example"]


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _response(body=None, status: int = 201, bad_json: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    if bad_json:
        resp.json.side_effect = ValueError("Expecting value")
    else:
        resp.json.return_value = body
    return resp


@pytest.fixture(autouse=True)
def api_token(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", TOKEN)


def _find(body=None, status: int = 201, urls=URLS, **kwargs):
    with patch.object(
        apify_client.requests, "post", return_value=_response(body, status, **kwargs)
    ) as post:
        result = find_emails(urls, actor_id=ACTOR, timeout=300)
    return result, post


def _both_items() -> list:
    return _load("apify_match.json") + _load("apify_nomatch.json")


def test_match_and_no_match_keyed_by_item_linkedin_url():
    result, _ = _find(_both_items())
    assert result == {
        "https://www.linkedin.com/in/jane-doe-example": {
            "name": "Jane Doe",
            "company": "Example Corp",
            "title": "",
            "email": "jane.doe@example.com",
            "email_status": "unverified",
        },
        "https://www.linkedin.com/in/john-roe-example": None,
    }


def test_missing_item_for_a_submitted_url_is_left_to_service():
    result, _ = _find(_load("apify_match.json"))
    assert list(result) == ["https://www.linkedin.com/in/jane-doe-example"]


def test_request_shape_and_token_only_in_header():
    _, post = _find(_both_items())
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == (
        "https://api.apify.com/v2/acts/vulnv~linkedin-email-finder/run-sync-get-dataset-items"
    )
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert kwargs["json"] == {"urls": URLS}
    assert kwargs["timeout"] == 300
    assert TOKEN not in args[0]
    assert TOKEN not in json.dumps(kwargs["json"])
    assert "params" not in kwargs


def test_any_2xx_accepted():
    result, _ = _find(_load("apify_match.json"), status=200)
    assert len(result) == 1


def test_empty_input_makes_no_request():
    result, post = _find([], urls=[])
    assert result == {}
    post.assert_not_called()


@pytest.mark.parametrize(
    "error", ["Rate limited by upstream", "no email found in response", "", None]
)
def test_other_found_false_errors_raise(error):
    item = {"linkedin_url": URLS[0], "found": False}
    if error is not None:
        item["error"] = error
    with pytest.raises(RuntimeError, match="Apify") as err:
        _find([item])
    if error:
        assert error not in str(err.value)


@pytest.mark.parametrize(
    "body",
    [
        {"items": []},
        ["not an object"],
        [{"found": True, "email": "jane.doe@example.com"}],
        [{"linkedin_url": "", "found": True, "email": "jane.doe@example.com"}],
        [{"linkedin_url": URLS[0], "email": "jane.doe@example.com"}],
        [{"linkedin_url": URLS[0], "found": "yes", "email": "jane.doe@example.com"}],
        [{"linkedin_url": URLS[0], "found": True, "email": ""}],
        [{"linkedin_url": URLS[0], "found": True}],
    ],
)
def test_malformed_body_raises(body):
    with pytest.raises(RuntimeError, match="Apify") as err:
        _find(body)
    assert "jane" not in str(err.value)


def test_malformed_json_raises():
    with pytest.raises(RuntimeError, match="Apify"):
        _find(bad_json=True)


@pytest.mark.parametrize("status", [400, 401, 403, 408, 429, 500])
def test_non_2xx_raises_with_status(status):
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as err:
        _find({"error": {"type": "x", "message": "jane.doe@example.com"}}, status=status)
    assert "jane" not in str(err.value)
    assert TOKEN not in str(err.value)


def test_error_type_included_when_it_looks_like_a_code():
    body = {"error": {"type": "token-not-valid", "message": "Authentication token is not valid."}}
    with pytest.raises(RuntimeError, match="HTTP 401 token-not-valid") as err:
        _find(body, status=401)
    assert "Authentication token" not in str(err.value)


@pytest.mark.parametrize("exc", [requests.Timeout, requests.ConnectionError])
def test_request_exception_raises_runtime_error(exc):
    with patch.object(apify_client.requests, "post", side_effect=exc("boom")):
        with pytest.raises(RuntimeError, match=exc.__name__) as err:
            find_emails(URLS, actor_id=ACTOR, timeout=300)
    assert TOKEN not in str(err.value)


def test_missing_token_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN")
    with patch.object(apify_client.requests, "post") as post:
        with pytest.raises(RuntimeError, match="APIFY_TOKEN"):
            find_emails(URLS, actor_id=ACTOR, timeout=300)
    post.assert_not_called()
