import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from email_finder import apollo_client
from email_finder.apollo_client import bulk_match

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "apollo-secret-key"
JANE = "https://linkedin.com/in/jane-doe-example"
ALEX = "https://linkedin.com/in/alex-poe-example"
NOBODY = "https://linkedin.com/in/no-record-example"
URLS = [JANE, NOBODY, ALEX]


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _response(body=None, status: int = 200, bad_json: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    if bad_json:
        resp.json.side_effect = ValueError("Expecting value")
    else:
        resp.json.return_value = body
    return resp


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", KEY)


def _match(body=None, status: int = 200, urls=URLS, **kwargs):
    with patch.object(
        apollo_client.requests, "post", return_value=_response(body, status, **kwargs)
    ) as post:
        result = bulk_match(urls, timeout=15)
    return result, post


def test_records_keyed_by_normalized_linkedin_url():
    result, _ = _match(_load("apollo_bulk_match.json"))
    assert result == {
        JANE: {
            "name": "Jane Doe",
            "company": "Example Corp",
            "title": "Software Engineer",
            "email": "jane.doe@example.com",
            "email_status": "verified",
        },
        ALEX: {
            "name": "Alex Poe",
            "company": "Sample Labs",
            "title": "Engineering Manager",
            "email": "alex.poe@example.org",
            "email_status": "extrapolated",
        },
    }


def test_submitted_url_with_no_record_is_no_match():
    result, _ = _match(_load("apollo_bulk_match.json"))
    assert NOBODY not in result


def test_email_status_lowercased():
    body = _load("apollo_bulk_match.json")
    body["matches"][0]["email_status"] = "Verified"
    result, _ = _match(body)
    assert result[JANE]["email_status"] == "verified"


def test_name_falls_back_to_first_and_last():
    body = _load("apollo_bulk_match.json")
    del body["matches"][0]["name"]
    result, _ = _match(body)
    assert result[JANE]["name"] == "Jane Doe"


def test_record_without_email_or_url_is_not_a_match():
    body = _load("apollo_bulk_match.json")
    body["matches"][0]["email"] = None
    body["matches"][2]["linkedin_url"] = None
    result, _ = _match(body)
    assert result == {}


def test_request_shape_key_only_in_header_and_no_phone():
    _, post = _match(_load("apollo_bulk_match.json"))
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://api.apollo.io/api/v1/people/bulk_match"
    assert kwargs["headers"]["x-api-key"] == KEY
    assert kwargs["json"] == {
        "details": [{"linkedin_url": url} for url in URLS],
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
    }
    assert kwargs["timeout"] == 15
    assert KEY not in args[0]
    assert KEY not in json.dumps(kwargs["json"])
    assert "params" not in kwargs


def test_empty_input_makes_no_request():
    result, post = _match(_load("apollo_bulk_match.json"), urls=[])
    assert result == {}
    post.assert_not_called()


def test_free_plan_403_raises_with_code():
    with pytest.raises(RuntimeError, match="HTTP 403 API_INACCESSIBLE") as err:
        _match(_load("apollo_403.json"), status=403)
    assert "Free plan" not in str(err.value)
    assert KEY not in str(err.value)


@pytest.mark.parametrize("status", [400, 401, 422, 429, 500])
def test_non_2xx_raises_with_status(status):
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as err:
        _match({"error": "jane.doe@example.com"}, status=status)
    assert "jane" not in str(err.value)


def test_non_json_error_body_raises_with_status():
    with pytest.raises(RuntimeError, match="HTTP 503"):
        _match(status=503, bad_json=True)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"status": "success"},
        {"matches": "x"},
        {"matches": ["not an object"]},
        {"matches": [{"linkedin_url": 5, "email": "jane.doe@example.com"}]},
    ],
)
def test_malformed_body_raises(body):
    with pytest.raises(RuntimeError, match="Apollo") as err:
        _match(body)
    assert "jane" not in str(err.value)


def test_malformed_json_raises():
    with pytest.raises(RuntimeError, match="Apollo"):
        _match(bad_json=True)


@pytest.mark.parametrize("exc", [requests.Timeout, requests.ConnectionError])
def test_request_exception_raises_runtime_error(exc):
    with patch.object(apollo_client.requests, "post", side_effect=exc("boom")):
        with pytest.raises(RuntimeError, match=exc.__name__) as err:
            bulk_match(URLS, timeout=15)
    assert KEY not in str(err.value)


def test_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY")
    with patch.object(apollo_client.requests, "post") as post:
        with pytest.raises(RuntimeError, match="APOLLO_API_KEY"):
            bulk_match(URLS, timeout=15)
    post.assert_not_called()
