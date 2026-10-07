import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from email_finder import prospeo_client
from email_finder.prospeo_client import enrich_person

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "prospeo-secret-key"
PROFILE = "https://linkedin.com/in/jane-doe-example"


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
    monkeypatch.setenv("PROSPEO_API_KEY", KEY)


def _enrich(body=None, status: int = 200, **kwargs):
    with patch.object(
        prospeo_client.requests, "post", return_value=_response(body, status, **kwargs)
    ) as post:
        result = enrich_person(PROFILE, timeout=9)
    return result, post


def _with_email(**email_fields) -> dict:
    body = _load("prospeo_enrich_match.json")
    body["person"]["email"].update(email_fields)
    return body


def test_match_returns_the_five_fields():
    result, _ = _enrich(_load("prospeo_enrich_match.json"))
    assert result == {
        "name": "Jane Doe",
        "company": "Example Corp",
        "title": "Co-Founder",
        "email": "jane.doe@example.com",
        "email_status": "verified",
    }


def test_email_status_lowercased():
    result, _ = _enrich(_with_email(status="Catch_All"))
    assert result["email_status"] == "catch_all"


@pytest.mark.parametrize("status", [200, 400])
def test_no_match_error_code_is_none(status):
    result, _ = _enrich(_load("prospeo_enrich_nomatch.json"), status=status)
    assert result is None


@pytest.mark.parametrize("email", ["", "   ", None])
def test_match_with_empty_email_is_no_match(email):
    result, _ = _enrich(_with_email(email=email))
    assert result is None


def test_match_with_null_email_object_is_no_match():
    body = _load("prospeo_enrich_match.json")
    body["person"]["email"] = None
    result, _ = _enrich(body)
    assert result is None


def test_unrevealed_email_is_no_match():
    result, _ = _enrich(_with_email(revealed=False))
    assert result is None


def test_masked_email_is_no_match():
    result, _ = _enrich(_with_email(email="j***@example.com"))
    assert result is None


def test_missing_company_and_title_are_blank():
    body = _load("prospeo_enrich_match.json")
    body["company"] = None
    body["person"]["current_job_title"] = None
    result, _ = _enrich(body)
    assert result["company"] == ""
    assert result["title"] == ""
    assert result["email"] == "jane.doe@example.com"


class _NoMobile(dict):
    def __getitem__(self, key):
        if key == "mobile":
            raise AssertionError("person.mobile was read")
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key == "mobile":
            raise AssertionError("person.mobile was read")
        return super().get(key, default)


def test_mobile_is_never_read():
    body = _load("prospeo_enrich_match.json")
    body["person"] = _NoMobile(body["person"])
    result, _ = _enrich(body)
    assert result["email"] == "jane.doe@example.com"
    assert "555" not in json.dumps(result)


def test_request_shape_and_key_only_in_header():
    _, post = _enrich(_load("prospeo_enrich_match.json"))
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://api.prospeo.io/enrich-person"
    assert kwargs["headers"]["X-KEY"] == KEY
    assert kwargs["json"] == {"data": {"linkedin_url": PROFILE}}
    assert kwargs["timeout"] == 9
    assert KEY not in args[0]
    assert KEY not in json.dumps(kwargs["json"])
    assert "params" not in kwargs


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (401, {"error": True, "error_code": "INVALID_API_KEY"}, "HTTP 401.*INVALID_API_KEY"),
        (403, {"error": True, "error_code": "FORBIDDEN"}, "HTTP 403"),
        (429, {"error": True, "error_code": "RATE_LIMITED"}, "HTTP 429.*RATE_LIMITED"),
        (500, {"error": True}, "HTTP 500"),
        (400, {"error": True, "error_code": "INVALID_DATAPOINTS"}, "INVALID_DATAPOINTS"),
        (200, {"error": True, "error_code": "INSUFFICIENT_CREDITS"}, "INSUFFICIENT_CREDITS"),
    ],
)
def test_other_errors_raise_with_status_or_code(status, body, expected):
    with pytest.raises(RuntimeError, match=expected) as err:
        _enrich(body, status=status)
    assert "Prospeo" in str(err.value)
    assert KEY not in str(err.value)


def test_error_message_never_echoes_body_text():
    body = {"error": True, "error_code": "jane.doe@example.com leaked"}
    with pytest.raises(RuntimeError) as err:
        _enrich(body, status=400)
    assert "jane" not in str(err.value)


def test_non_json_body_raises():
    with pytest.raises(RuntimeError, match="HTTP 502"):
        _enrich(status=502, bad_json=True)


@pytest.mark.parametrize(
    "body",
    [
        [],
        "not an object",
        {"error": False},
        {"error": False, "person": "x"},
        {"error": False, "person": {"email": "jane.doe@example.com"}},
        {"person": {}},
    ],
)
def test_malformed_body_raises(body):
    with pytest.raises(RuntimeError, match="Prospeo"):
        _enrich(body)


def test_malformed_json_on_2xx_raises():
    with pytest.raises(RuntimeError, match="Prospeo"):
        _enrich(status=200, bad_json=True)


@pytest.mark.parametrize("exc", [requests.Timeout, requests.ConnectionError])
def test_request_exception_raises_runtime_error(exc):
    with patch.object(prospeo_client.requests, "post", side_effect=exc("boom")):
        with pytest.raises(RuntimeError, match=exc.__name__) as err:
            enrich_person(PROFILE, timeout=9)
    assert KEY not in str(err.value)


def test_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("PROSPEO_API_KEY")
    with patch.object(prospeo_client.requests, "post") as post:
        with pytest.raises(RuntimeError, match="PROSPEO_API_KEY"):
            enrich_person(PROFILE, timeout=9)
    post.assert_not_called()
